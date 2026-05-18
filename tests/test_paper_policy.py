from __future__ import annotations

import json
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
    execution_price_cap_for_row,
    simulate_ladder_fill,
)
from weather_trader.execution.liquidity import selected_side_execution_modes
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


def test_simulate_ladder_fill_vwap_cap_allows_partial_before_breach() -> None:
    fill = simulate_ladder_fill(
        book=_book("no", asks=[(0.5, 10), (0.8, 100)]),
        limit_price=0.5,
        execution_price_cap=0.55,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FAK,
        min_fill_usd=1,
    )

    assert fill.final_state == PaperPolicyFinalState.PARTIAL
    assert fill.avg_price <= 0.55
    assert fill.cost_usd > 5
    assert fill.levels_consumed[-1]["price"] == 0.8


def test_simulate_ladder_fill_never_spends_above_target_notional() -> None:
    fill = simulate_ladder_fill(
        book=_book("no", asks=[(0.55, 100)]),
        limit_price=0.5,
        execution_price_cap=0.55,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FAK,
        min_fill_usd=1,
    )

    assert fill.cost_usd <= 10


def test_simulate_ladder_fill_rejects_below_minimum_and_fok_rejects_partial_depth() -> None:
    fak = simulate_ladder_fill(
        book=_book("no", asks=[(0.5, 3)]),
        limit_price=0.5,
        execution_price_cap=0.55,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FAK,
        min_fill_usd=1,
    )
    fok = simulate_ladder_fill(
        book=_book("no", asks=[(0.5, 3)]),
        limit_price=0.5,
        execution_price_cap=0.55,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FOK,
        min_fill_usd=1,
    )
    tiny = simulate_ladder_fill(
        book=_book("no", asks=[(0.5, 1)]),
        limit_price=0.5,
        execution_price_cap=0.55,
        target_notional_usd=10,
        order_mode=PaperPolicyOrderMode.FAK,
        min_fill_usd=1,
    )

    assert fak.final_state == PaperPolicyFinalState.PARTIAL
    assert fok.final_state == PaperPolicyFinalState.FOK_NOT_FILLED
    assert tiny.final_state == PaperPolicyFinalState.REJECTED
    assert tiny.reason == "INSUFFICIENT_DEPTH"


def test_execution_price_cap_respects_slippage_and_post_edge_caps() -> None:
    row = {"entry_price": 0.5, "entry_fair": 0.58}
    assert execution_price_cap_for_row(row, max_slippage_cents=0.05, min_post_slippage_edge=0.05) == 0.53
    assert execution_price_cap_for_row(row, max_slippage_cents=0.02, min_post_slippage_edge=0.01) == 0.52
    assert execution_price_cap_for_row(
        {"entry_price": 0.5, "entry_fair": None},
        max_slippage_cents=0.05,
        min_post_slippage_edge=0.05,
    ) == 0.55


def test_selected_side_execution_modes_split_sweep_and_bid_ladder() -> None:
    modes = selected_side_execution_modes(
        _book("no", asks=[(0.60, 40), (0.65, 40), (0.70, 100)], bids=[(0.45, 100)]),
        selected_side=str(TradeAction.BUY_NO),
        fair=0.85,
        entry_edge=0.25,
    )

    sweep = modes["ask_sweep"]
    ladder = modes["bid_ladder"]
    assert sweep["eligible"] is True
    assert sweep["price_cap"] == 0.65
    assert sweep["depth_to_cap"] == 50
    assert sweep["targets"]["25"]["fully_fillable"] is True
    assert sweep["targets"]["50"]["fully_fillable"] is True
    assert sweep["targets"]["100"]["fully_fillable"] is False
    assert ladder["eligible"] is True
    assert ladder["edge_max_bid"] == 0.70
    assert ladder["post_only_top_bid"] == 0.59
    assert ladder["low_bid"] == 0.49
    assert ladder["level_count"] == 10
    assert ladder["total_notional_usd"] == 500
    assert ladder["levels"][0]["edge_after_fill"] == 0.26
    assert ladder["levels"][0]["distance_from_ask"] == 0.01
    assert ladder["levels"][0]["distance_from_best_bid"] == 0.14
    assert ladder["levels"][0]["would_be_best_bid"] is True


def test_selected_side_execution_modes_records_ineligible_reason() -> None:
    modes = selected_side_execution_modes(
        _book("no", asks=[(0.60, 40)], bids=[(0.45, 100)]),
        selected_side=str(TradeAction.BUY_NO),
        fair=0.80,
        entry_edge=0.20,
    )

    assert modes["ask_sweep"]["eligible"] is False
    assert modes["ask_sweep"]["reason"] == "EDGE_BELOW_SIGNAL_GATE"
    assert modes["bid_ladder"]["eligible"] is False
    assert modes["bid_ladder"]["reason"] == "EDGE_BELOW_SIGNAL_GATE"


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


def test_paper_policy_trader_promotes_by_bucket_policy_rows_independently(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    policy_name = "pm_us12_consensus_hc_15m_entry_25_75_by_bucket_side_delay_first"
    store.upsert_market(_market(market_id="m2", yes_token_id="yes2", no_token_id="no2"))
    store.insert_research_policy_position(
        _policy_position(
            policy_name,
            selected_side=TradeAction.BUY_NO,
            scope_key="station_date_bucket_side_obs_delay:74-75F:BUY_NO:15m",
            selected_bucket="74-75F",
            selected_market_id="m1",
        )
    )
    store.insert_research_policy_position(
        _policy_position(
            policy_name,
            selected_side=TradeAction.BUY_NO,
            scope_key="station_date_bucket_side_obs_delay:76-77F:BUY_NO:15m",
            selected_bucket="76-77F",
            selected_market_id="m2",
        )
    )
    store.insert_research_policy_position(
        _policy_position(
            policy_name,
            selected_side=TradeAction.BUY_NO,
            scope_key="station_date_bucket_side_obs_delay:74-75F:BUY_NO:10m",
            selected_bucket="74-75F",
            selected_market_id="m1",
        )
    )

    result = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(promoted_policies=(policy_name,)),
        book_client=FakeBookClient(
            {
                "no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)]),
                "no2": _book("no2", asks=[(0.5, 100)], bids=[(0.45, 100)]),
            }
        ),
    ).run_once()

    assert result.candidates == 3
    assert result.filled == 2
    positions = store.connection.execute(
        "select selected_bucket, selected_side from paper_policy_positions order by id"
    ).fetchall()
    assert [(row["selected_bucket"], row["selected_side"]) for row in positions] == [
        ("74-75F", "BUY_NO"),
        ("76-77F", "BUY_NO"),
    ]
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


def test_paper_policy_insufficient_depth_stays_pending_and_retries_same_position(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    research_id = store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    assert research_id is not None
    books = {"no": _book("no", asks=[(0.5, 1)])}
    trader = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(retry_cooldown_seconds=0),
        book_client=FakeBookClient(books),
    )

    first = trader.run_once()
    position = store.connection.execute("select id, state from paper_policy_positions").fetchone()
    assert first.attempts == 1
    assert first.rejected == 0
    assert position["state"] == "RESERVED"

    books["no"] = _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])
    second = trader.run_once()

    attempts = store.connection.execute(
        "select attempt_seq, final_state from paper_policy_order_attempts order by attempt_seq"
    ).fetchall()
    events = [
        row["event_type"]
        for row in store.connection.execute("select event_type from paper_policy_trade_events order by id").fetchall()
    ]
    assert second.attempts == 1
    assert second.filled == 1
    assert [(row["attempt_seq"], row["final_state"]) for row in attempts] == [(1, "REJECTED"), (2, "FILLED")]
    assert store.connection.execute("select count(*) n from paper_policy_positions").fetchone()["n"] == 1
    assert "ENTRY_RETRY" in events


def test_paper_policy_pending_entry_expires_on_max_attempts(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))
    trader = PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(retry_cooldown_seconds=0, max_attempts=2),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 1)])}),
    )

    trader.run_once()
    result = trader.run_once()

    row = store.connection.execute("select state, raw_json from paper_policy_positions").fetchone()
    assert result.rejected == 1
    assert row["state"] == "EXPIRED_NO_LIQUIDITY"
    assert json.loads(row["raw_json"])["last_attempt_seq"] == 2


def test_paper_policy_attempt_raw_payload_records_vwap_audit_fields(tmp_path) -> None:
    store = _store_with_market(tmp_path)
    store.insert_research_policy_position(_policy_position(DEFAULT_PROMOTED_POLICIES[0], selected_side=TradeAction.BUY_NO))

    PaperPolicyTrader(
        store=store,
        config=PaperPolicyExecutionConfig(),
        book_client=FakeBookClient({"no": _book("no", asks=[(0.5, 100)], bids=[(0.45, 100)])}),
    ).run_once()

    attempt = store.latest_paper_policy_attempts(1)[0]
    payload = json.loads(attempt["raw_payload"])["raw_payload"]
    assert payload["vwap_price"] == 0.5
    assert payload["execution_price_cap"] == 0.55
    assert payload["post_slippage_edge"] == 0.3
    assert payload["fillable_notional_usd"] == 20


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
    selected_bucket: str = "74-75F",
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
        selected_bucket=selected_bucket,
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
