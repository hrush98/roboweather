from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.shadow_collection_report import milestone_failures
from weather_trader.execution.clob_collector import collect_candidate_clob_events
from weather_trader.execution.contracts import LiveOrderMode, LiveQuoteIntent, LiveQuoteState, MarketFamily, TradeAction
from weather_trader.execution.quote_engine import phase2_shadow_quote_specs
from weather_trader.execution.shadow_outcomes import label_shadow_quote_outcome
from weather_trader.execution.store import ExecutionStore


class FakeTransport:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        self.subscribed: list[str] = []

    async def stream(self, token_ids: list[str]):
        self.subscribed = list(token_ids)
        for message in self.messages:
            yield message


def test_phase2_shadow_specs_cover_useful_50_and_100_sizes() -> None:
    specs = phase2_shadow_quote_specs()
    assert len(specs) == 24
    assert {spec.quote_size_usd for spec in specs} == {50.0, 100.0}
    assert all(spec.quote_spec_id.startswith("qspec_") for spec in specs)


def test_candidate_clob_collector_subscribes_to_candidate_tokens(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    _insert_candidate(store, token_id="token-1")
    transport = FakeTransport(
        [
            {
                "event_type": "last_trade_price",
                "asset_id": "token-1",
                "price": "0.38",
                "size": "250",
                "best_bid": "0.37",
                "best_ask": "0.40",
            }
        ]
    )

    stats = asyncio.run(collect_candidate_clob_events(store, transport=transport, max_messages=1))

    assert transport.subscribed == ["token-1"]
    assert stats.subscribed_tokens == 1
    assert stats.messages == 1
    assert stats.events == 1
    row = store.connection.execute("select token_id, event_type from clob_feed_events").fetchone()
    assert dict(row) == {"token_id": "token-1", "event_type": "last_trade_price"}


def test_shadow_outcome_label_persists_fill_scenarios_and_markouts(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    _insert_candidate(store, token_id="token-1")
    quote = _insert_quote(store, quote_size_usd=50.0)
    store.insert_clob_feed_event(
        channel="market",
        event_type="last_trade_price",
        token_id="token-1",
        price=0.38,
        size=300.0,
        best_bid=0.37,
        best_ask=0.40,
        received_at="2026-06-15T18:00:20+00:00",
        raw_payload={"event_type": "last_trade_price"},
    )
    store.insert_clob_feed_event(
        channel="market",
        event_type="best_bid_ask",
        token_id="token-1",
        best_bid=0.41,
        best_ask=0.43,
        received_at="2026-06-15T18:10:00+00:00",
        raw_payload={"event_type": "best_bid_ask"},
    )
    item = store.shadow_quote_label_inputs()[0]

    label = label_shadow_quote_outcome(
        item["quote"],
        feed_events=item["feed_events"],
        book_snapshots=item["book_snapshots"],
        labeled_at="2026-06-15T18:04:00+00:00",
    )
    store.upsert_live_shadow_quote_outcome(label)

    row = store.connection.execute("select * from live_shadow_quote_outcomes where quote_id = ?", (quote.quote_id,)).fetchone()
    assert row["intended_notional_usd"] == pytest.approx(50.0)
    assert row["conservative_fill"] == 1
    assert row["base_fill"] == 1
    assert row["optimistic_fill"] == 1
    assert row["feed_event_count"] == 1
    assert label["markouts"]["10m"]["status"] == "available"


def test_shadow_report_flags_missing_feed_and_tiny_size_coverage() -> None:
    health = {
        "policy_candidates": 1,
        "candidates_with_token": 1,
        "candidates_with_quotes": 1,
        "candidates_with_feed_events": 0,
        "useful_50_quote_intents": 0,
        "useful_100_quote_intents": 0,
        "candidates_with_50_quotes": 0,
        "candidates_with_100_quotes": 0,
        "shadow_outcomes": 0,
        "quote_intents": 1,
    }

    failures = milestone_failures(health, None)

    assert "MISSING_CLOB_FEED_COVERAGE" in failures
    assert "ONLY_TINY_SIZE_COVERAGE" in failures
    assert "MISSING_SHADOW_OUTCOME_LABELS" in failures


def _insert_candidate(store: ExecutionStore, *, token_id: str) -> None:
    store.insert_live_candidate_snapshot(
        candidate_id="candidate-1",
        cycle_timestamp="2026-06-15T18:00:00+00:00",
        local_receipt_timestamp="2026-06-15T18:00:00+00:00",
        source_stage="POLICY_CANDIDATE",
        strategy_name="strategy",
        policy_name="policy",
        station="KATL",
        market_date="2026-06-15",
        market_family="HIGH_TEMP",
        source_prediction_snapshot_ids=[],
        selected_market_id="market-1",
        selected_token_id=token_id,
        selected_side="BUY_NO",
        selected_bucket="90-91",
        quote_features={"best_bid": 0.37, "best_ask": 0.40, "spread": 0.03},
        raw_payload={},
    )


def _insert_quote(store: ExecutionStore, *, quote_size_usd: float) -> LiveQuoteIntent:
    quote = LiveQuoteIntent(
        timestamp="2026-06-15T18:00:00+00:00",
        quote_id="quote-1",
        live_candidate_id="candidate-1",
        price_sheet_version="phase1",
        strategy_name="strategy",
        station="KATL",
        market_date=date(2026, 6, 15),
        market_family=MarketFamily.HIGH_TEMP,
        selected_market_id="market-1",
        selected_token_id="token-1",
        selected_side=TradeAction.BUY_NO,
        selected_bucket="90-91",
        order_mode=LiveOrderMode.GTD,
        quote_price=0.38,
        quote_size_usd=quote_size_usd,
        quote_shares=quote_size_usd / 0.38,
        post_only=True,
        batch_group_id="batch-1",
        gtd_expiry="2026-06-15T18:03:00+00:00",
        state=LiveQuoteState.SHADOW_POSTABLE,
        quote_spec_id="qspec-test",
        fair_source="phase1_capped_haircut_fair",
        quote_rule="min(max_quote_price,best_ask-1c)",
        cancel_rule="ttl_or_fair_book_cross_or_stale_feed",
        would_post=True,
        raw_json={
            "initial_depth_context": {
                "queue_ahead_usd": 0.0,
                "quote_size_usd": quote_size_usd,
            },
            "markout_hooks": {"windows": ["10s", "30s", "2m", "10m", "next_weather_update", "close", "settlement"]},
        },
    )
    store.insert_live_quote_intent(quote)
    return quote
