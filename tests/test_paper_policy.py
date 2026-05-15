from __future__ import annotations

from datetime import date

import pytest

from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    MarketSnapshot,
    PaperPolicyFinalState,
    PaperPolicyOrderMode,
    ResearchPolicyPosition,
    StationDateOutcome,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.paper_policy import (
    DEFAULT_PROMOTED_POLICIES,
    FixedFractionSizingModel,
    PaperPolicyExecutionConfig,
    PaperPolicyRiskConfig,
    PaperPolicyTrader,
    simulate_ladder_fill,
)
from weather_trader.execution.store import ExecutionStore


class FakeBookClient:
    def __init__(self, books: dict[str, BookSnapshot]) -> None:
        self.books = books

    def fetch_book(self, token_id: str) -> BookSnapshot:
        return self.books[token_id]


def test_fixed_fraction_sizing_applies_order_station_and_total_caps() -> None:
    sizing = FixedFractionSizingModel(
        PaperPolicyRiskConfig(
            bankroll_usd=1000,
            fixed_fraction=0.10,
            max_usd_per_order=25,
            max_exposure_per_station_date=40,
            max_total_open_risk=100,
        )
    )

    decision = sizing.size(
        {"station": "KATL", "market_date": "2026-05-07", "entry_fair": 0.8, "entry_price": 0.6},
        {"open_risk_usd": 90, "station_date_exposure_usd": {"KATL:2026-05-07": 30}},
    )

    assert decision.target_notional_usd == 10
    assert decision.cap_reason == "station_date_remaining"


def test_simulate_ladder_fill_fok_full_and_not_filled() -> None:
    book = _book("no", asks=[(0.4, 10), (0.5, 10), (0.6, 100)])

    fill = simulate_ladder_fill(
        book=book,
        limit_price=0.5,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FOK,
        min_fill_usd=1,
    )
    assert fill.final_state == PaperPolicyFinalState.FILLED
    assert fill.filled_shares == 20
    assert fill.cost_usd == 9
    assert fill.avg_price == 0.45

    miss = simulate_ladder_fill(
        book=book,
        limit_price=0.5,
        target_notional_usd=20,
        order_mode=PaperPolicyOrderMode.FOK,
        min_fill_usd=1,
    )
    assert miss.final_state == PaperPolicyFinalState.FOK_NOT_FILLED
    assert miss.cost_usd == 0


def test_simulate_ladder_fill_fak_partial() -> None:
    fill = simulate_ladder_fill(
        book=_book("no", asks=[(0.4, 5)]),
        limit_price=0.5,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FAK,
        min_fill_usd=1,
    )

    assert fill.final_state == PaperPolicyFinalState.PARTIAL
    assert fill.filled_shares == 5
    assert fill.cost_usd == 2


def test_paper_policy_trader_promotes_allowlisted_policy_only(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    promoted_id = store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    skipped_id = store.insert_research_policy_position(_policy_position("pm_us12_mvp_hc_first", selected_side=TradeAction.BUY_NO))
    assert promoted_id is not None and skipped_id is not None

    result = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    ).run_once()

    assert result.candidates == 1
    assert result.filled == 1
    rows = store.connection.execute("select research_policy_position_id, state from paper_policy_positions").fetchall()
    assert [(row["research_policy_position_id"], row["state"]) for row in rows] == [(promoted_id, "FILLED")]


def test_paper_policy_trader_defaults_to_latest_research_market_date(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.upsert_market(_market(market_id="m2", yes_token_id="yes2", no_token_id="no2", market_date=date(2026, 5, 8)))
    old_id = store.insert_research_policy_position(
        _policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO, scope_key="old")
    )
    latest_id = store.insert_research_policy_position(
        _policy_position(
            DEFAULT_PROMOTED_POLICIES[0],
            selected_side=TradeAction.BUY_NO,
            scope_key="latest",
            market_date=date(2026, 5, 8),
            selected_market_id="m2",
        )
    )
    assert old_id is not None and latest_id is not None

    result = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no2": _book("no2", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    ).run_once()

    assert result.candidates == 1
    rows = store.connection.execute("select research_policy_position_id from paper_policy_positions").fetchall()
    assert [row["research_policy_position_id"] for row in rows] == [latest_id]


def test_paper_policy_trader_keeps_promoted_policy_books_independent(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[1], selected_side=TradeAction.BUY_NO))

    result = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    ).run_once()

    assert result.candidates == 2
    assert result.filled == 2
    assert store.connection.execute("select count(*) n from paper_policy_positions").fetchone()["n"] == 2
    policies = {
        row["policy_name"]
        for row in store.connection.execute("select policy_name from paper_policy_positions").fetchall()
    }
    assert policies == {DEFAULT_PROMOTED_POLICIES[0], DEFAULT_PROMOTED_POLICIES[1]}


def test_paper_policy_trader_blocks_duplicate_bucket_side_inside_same_policy(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(
        _policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO, scope_key="first")
    )
    store.insert_research_policy_position(
        _policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO, scope_key="second")
    )

    result = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    ).run_once()

    assert result.candidates == 2
    assert result.filled == 1
    assert store.connection.execute("select count(*) n from paper_policy_positions").fetchone()["n"] == 1
    events = [row["message"] for row in store.connection.execute("select message from paper_policy_trade_events").fetchall()]
    assert "duplicate exposure blocked" in events


def test_paper_policy_trader_records_stale_book_and_unknown_attempt(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    stale = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(stale_book_probability=1.0),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.6, 100)])}),
    ).run_once()

    assert stale.rejected == 1
    assert store.latest_paper_policy_attempts(1)[0]["final_state"] == "STALE_BOOK"

    store = _store_with_market(tmp_path / "unknown")
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    unknown = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(unknown_probability=1.0),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.6, 100)])}),
    ).run_once()

    assert unknown.unknown == 1
    attempt = store.latest_paper_policy_attempts(1)[0]
    assert attempt["final_state"] == "UNKNOWN"
    assert attempt["not_found_count"] == 1


def test_paper_policy_settles_from_station_outcome(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    trader = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    )
    trader.run_once()
    store.upsert_station_date_outcome(
        StationDateOutcome(
            timestamp=utc_now_iso(),
            station="KATL",
            market_date=date(2026, 5, 7),
            final_high_tmpf=80,
            source="test",
            resolved_at=utc_now_iso(),
        )
    )

    assert trader.settle_resolved_positions() == 1
    row = store.connection.execute("select state, realized_pnl, realized_rr from paper_policy_positions").fetchone()
    assert row["state"] == "SETTLED"
    assert row["realized_pnl"] == pytest.approx(20.0)
    assert row["realized_rr"] == pytest.approx(1.0)


def _store_with_market(tmp_path) -> ExecutionStore:
    store = ExecutionStore(tmp_path / "paper.sqlite")
    store.upsert_market(_market())
    return store


def _market(
    market_id: str = "m1",
    yes_token_id: str = "yes",
    no_token_id: str = "no",
    market_date: date = date(2026, 5, 7),
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id=f"c-{market_id}",
        question="Will temp be between 74-75F?",
        slug=f"slug-{market_id}",
        city="Atlanta",
        station="KATL",
        market_date=market_date,
        lower_f=74,
        upper_f=75,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        end_date="",
        resolution_source="",
        discovered_at=utc_now_iso(),
    )


def _policy_position(
    policy_name: str,
    selected_side: TradeAction,
    scope_key: str | None = None,
    market_date: date = date(2026, 5, 7),
    selected_market_id: str = "m1",
) -> ResearchPolicyPosition:
    return ResearchPolicyPosition(
        timestamp=utc_now_iso(),
        policy_name=policy_name,
        station="KATL",
        market_date=market_date,
        scope_key=scope_key or f"scope:{policy_name}",
        model_group="consensus_pm_active_us12_dynamic_mvp",
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        obs_delay_bucket="15m",
        selected_market_id=selected_market_id,
        selected_side=selected_side,
        selected_bucket="74-75F",
        entry_price=0.5 if selected_side == TradeAction.BUY_NO else 0.6,
        entry_edge=0.2,
        entry_fair=0.8,
        source_prediction_snapshot_ids=[1, 2],
        raw_policy={"policy": {"name": policy_name}},
    )


def _book(token_id: str, asks: list[tuple[float, float]], bids: list[tuple[float, float]] | None = None) -> BookSnapshot:
    return BookSnapshot(
        token_id=token_id,
        bids=[BookLevel(price, size) for price, size in (bids or [])],
        asks=[BookLevel(price, size) for price, size in asks],
        timestamp=utc_now_iso(),
    )
