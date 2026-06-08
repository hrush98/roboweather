from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import weather_trader.live.execution as live_execution
from weather_trader.execution.clob_executor import AllowanceCheck, CancelSubmission, OrderSubmission
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    LiveOrderAttempt,
    LiveOrderMode,
    LivePositionState,
    LiveStrategy,
    MarketFamily,
    MarketSnapshot,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.fair_value import FairValueResult
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState
from weather_trader.live.execution import (
    CATBOOST_MODEL,
    DYNAMIC_TUNED_MODEL,
    CONSENSUS_NOTIONAL_USD,
    EDGE_CORE_OBS_DELAY_BUCKET,
    EDGE_CORE_NOTIONAL_USD,
    EDGE_CORE_POLICY_NAME,
    LIVE_MODEL_GROUP,
    DEFAULT_LIVE_ENTRY_PRICE_MAX,
    LIVE_POLICY_NAME,
    MOONSHOT_MIN_EDGE,
    MOONSHOT_POLICY_NAME,
    NGBOOST_BEST_BUY_YES_NOTIONAL_USD,
    NGBOOST_BEST_BUY_YES_POLICY_NAME,
    LiveExecutionConfig,
    LiveExecutionEngine,
    edge_core_policy_spec,
    default_live_strategy,
    live_policy_spec,
    live_strategy_plans,
    moonshot_edge_policy_spec,
    moonshot_policy_spec,
    ngboost_best_buy_yes_policy_spec,
)
from weather_trader.research.policies import NGBOOST_MODEL, ResearchPolicyEvaluator


def test_live_strategy_is_registered_in_separate_store(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    strategy = default_live_strategy(max_notional_usd=3.0)

    store.upsert_live_strategy(strategy)

    rows = store.live_strategies()
    assert len(rows) == 1
    assert rows[0]["name"] == LIVE_POLICY_NAME
    assert rows[0]["model_group"] == LIVE_MODEL_GROUP
    assert rows[0]["max_notional_usd"] == 3.0


def test_live_execution_config_defaults_to_fifty_cent_entry_cap() -> None:
    assert LiveExecutionConfig().max_entry_price == pytest.approx(DEFAULT_LIVE_ENTRY_PRICE_MAX)
    assert LiveExecutionConfig().resting_fallback_ttl_seconds == pytest.approx(120.0)


def test_live_strategy_plans_include_moonshot_and_ngboost_medium() -> None:
    plans = live_strategy_plans(LiveExecutionConfig(max_entry_price=0.40))

    assert [plan.strategy.name for plan in plans] == [
        LIVE_POLICY_NAME,
        EDGE_CORE_POLICY_NAME,
        MOONSHOT_POLICY_NAME,
        NGBOOST_BEST_BUY_YES_POLICY_NAME,
    ]
    consensus = plans[0]
    assert consensus.target_notional_usd == pytest.approx(CONSENSUS_NOTIONAL_USD)
    assert consensus.strategy.max_notional_usd == pytest.approx(CONSENSUS_NOTIONAL_USD)
    assert consensus.policies[0].entry_price_max == pytest.approx(0.40)

    edge_core = plans[1]
    assert edge_core.target_notional_usd == pytest.approx(EDGE_CORE_NOTIONAL_USD)
    assert edge_core.strategy.max_notional_usd == pytest.approx(EDGE_CORE_NOTIONAL_USD)
    assert len(edge_core.policies) == 1
    assert edge_core.policies[0].model_group == LIVE_MODEL_GROUP
    assert edge_core.policies[0].model_name is None
    assert edge_core.policies[0].obs_delay_bucket == EDGE_CORE_OBS_DELAY_BUCKET
    assert edge_core.policies[0].edge_min is None
    assert edge_core.policies[0].entry_price_min == pytest.approx(0.0)
    assert edge_core.policies[0].entry_price_max == pytest.approx(0.40)
    assert edge_core.selected_side is None
    assert edge_core.min_entry_price == pytest.approx(0.0)

    moonshot = plans[2]
    assert moonshot.target_notional_usd == pytest.approx(2.0)
    assert moonshot.strategy.max_notional_usd == pytest.approx(2.0)
    assert moonshot.strategy.entry_price_min == pytest.approx(0.05)
    assert len(moonshot.policies) == 2
    assert moonshot.policies[0].model_name == DYNAMIC_TUNED_MODEL
    assert moonshot.policies[0].entry_price_min == pytest.approx(0.05)
    assert moonshot.policies[0].entry_price_max == pytest.approx(0.10)
    assert moonshot.policies[1].model_name == DYNAMIC_TUNED_MODEL
    assert moonshot.policies[1].entry_price_min == pytest.approx(0.05)
    assert moonshot.policies[1].entry_price_max == pytest.approx(0.40)
    assert moonshot.policies[1].edge_min == pytest.approx(MOONSHOT_MIN_EDGE)
    assert moonshot.selected_side == TradeAction.BUY_NO
    assert moonshot.min_entry_price == pytest.approx(0.05)

    ngboost = plans[3]
    assert ngboost.target_notional_usd == pytest.approx(NGBOOST_BEST_BUY_YES_NOTIONAL_USD)
    assert ngboost.strategy.max_notional_usd == pytest.approx(NGBOOST_BEST_BUY_YES_NOTIONAL_USD)
    assert ngboost.strategy.strategy_bucket == StrategyBucket.BEST_BUCKET
    assert len(ngboost.policies) == 1
    assert ngboost.policies[0].model_name == NGBOOST_MODEL
    assert ngboost.policies[0].strategy_bucket == StrategyBucket.BEST_BUCKET
    assert ngboost.policies[0].entry_price_min == pytest.approx(0.05)
    assert ngboost.policies[0].entry_price_max == pytest.approx(0.40)
    assert ngboost.policies[0].local_decision_start == "12:00"
    assert ngboost.policies[0].local_decision_end == "15:00"
    assert ngboost.selected_side == TradeAction.BUY_YES
    assert ngboost.min_entry_price == pytest.approx(0.05)


def test_consensus_requires_same_side_market_bucket_and_delay(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    evaluator = ResearchPolicyEvaluator(store, (live_policy_spec(LiveExecutionConfig()),))
    base = _snapshot(DYNAMIC_TUNED_MODEL, selected_side="BUY_NO", selected_bucket="72-73F", obs_delay_bucket="15m")
    agree = _snapshot(CATBOOST_MODEL, selected_side="BUY_NO", selected_bucket="72-73F", obs_delay_bucket="15m", id=2)
    disagree_side = _snapshot(CATBOOST_MODEL, selected_side="BUY_YES", selected_bucket="72-73F", obs_delay_bucket="15m", id=3)

    assert len(evaluator._build_consensus([base, agree])) == 1
    assert evaluator._build_consensus([base, disagree_side]) == []


def test_live_policy_spec_filters_late_no_tiny_and_bucket_side_delay(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    spec = live_policy_spec(LiveExecutionConfig())
    evaluator = ResearchPolicyEvaluator(store, (spec,))
    rows = evaluator._build_consensus(
        [
            _snapshot(DYNAMIC_TUNED_MODEL, id=1, selected_no_ask=0.05, decision_time_local="2026-05-20T12:30:00-04:00"),
            _snapshot(CATBOOST_MODEL, id=2, selected_no_ask=0.05, decision_time_local="2026-05-20T12:30:00-04:00"),
            _snapshot(DYNAMIC_TUNED_MODEL, id=3, selected_bucket="74-75F", selected_no_ask=0.04, decision_time_local="2026-05-20T12:30:00-04:00"),
            _snapshot(CATBOOST_MODEL, id=4, selected_bucket="74-75F", selected_no_ask=0.04, decision_time_local="2026-05-20T12:30:00-04:00"),
            _snapshot(DYNAMIC_TUNED_MODEL, id=5, selected_bucket="76-77F", selected_no_ask=0.06, decision_time_local="2026-05-20T11:59:00-04:00"),
            _snapshot(CATBOOST_MODEL, id=6, selected_bucket="76-77F", selected_no_ask=0.06, decision_time_local="2026-05-20T11:59:00-04:00"),
            _snapshot(DYNAMIC_TUNED_MODEL, id=7, selected_bucket="78-79F", selected_no_ask=0.51, decision_time_local="2026-05-20T12:31:00-04:00"),
            _snapshot(CATBOOST_MODEL, id=8, selected_bucket="78-79F", selected_no_ask=0.51, decision_time_local="2026-05-20T12:31:00-04:00"),
        ]
    )

    filtered = evaluator._candidates_for_policy(spec, [], rows)
    selected = evaluator._first_by_scope(spec, filtered)

    assert len(selected) == 1
    assert selected[0]["selected_bucket"] == "72-73F"


def test_edge_core_policy_spec_filters_late_15m_consensus_entries(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    spec = edge_core_policy_spec(LiveExecutionConfig())
    evaluator = ResearchPolicyEvaluator(store, (spec,))
    consensus = [
        _snapshot(DYNAMIC_TUNED_MODEL, id=1, selected_no_ask=0.40, selected_edge=0.25, obs_delay_bucket="15m", decision_time_local="2026-05-20T12:30:00-04:00"),
        _snapshot(CATBOOST_MODEL, id=2, selected_no_ask=0.40, selected_edge=0.50, obs_delay_bucket="15m", decision_time_local="2026-05-20T12:30:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=3, selected_bucket="74-75F", selected_no_ask=0.60, obs_delay_bucket="15m", decision_time_local="2026-05-20T12:31:00-04:00"),
        _snapshot(CATBOOST_MODEL, id=4, selected_bucket="74-75F", selected_no_ask=0.60, obs_delay_bucket="15m", decision_time_local="2026-05-20T12:31:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=5, selected_bucket="76-77F", selected_no_ask=0.40, obs_delay_bucket="10m", decision_time_local="2026-05-20T12:32:00-04:00"),
        _snapshot(CATBOOST_MODEL, id=6, selected_bucket="76-77F", selected_no_ask=0.40, obs_delay_bucket="10m", decision_time_local="2026-05-20T12:32:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=7, selected_bucket="78-79F", selected_no_ask=0.40, obs_delay_bucket="15m", decision_time_local="2026-05-20T11:59:00-04:00"),
        _snapshot(CATBOOST_MODEL, id=8, selected_bucket="78-79F", selected_no_ask=0.40, obs_delay_bucket="15m", decision_time_local="2026-05-20T11:59:00-04:00"),
    ]

    filtered = evaluator._candidates_for_policy(spec, [], evaluator._build_consensus(consensus))
    selected = evaluator._first_by_scope(spec, filtered)

    assert [item["selected_bucket"] for item in selected] == ["72-73F"]


def test_moonshot_policy_spec_filters_late_five_to_ten_cent_entries(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    spec = moonshot_policy_spec()
    evaluator = ResearchPolicyEvaluator(store, (spec,))
    rows = [
        _snapshot(DYNAMIC_TUNED_MODEL, id=1, selected_no_ask=0.05, decision_time_local="2026-05-20T12:30:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=2, selected_bucket="74-75F", selected_no_ask=0.10, decision_time_local="2026-05-20T12:31:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=3, selected_bucket="76-77F", selected_no_ask=0.11, decision_time_local="2026-05-20T12:32:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=4, selected_bucket="78-79F", selected_no_ask=0.08, decision_time_local="2026-05-20T11:59:00-04:00"),
    ]

    filtered = evaluator._candidates_for_policy(spec, rows, [])
    selected = evaluator._first_by_scope(spec, filtered)

    assert [item["selected_bucket"] for item in selected] == ["72-73F", "74-75F"]


def test_moonshot_edge_policy_spec_filters_late_high_edge_entries(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    spec = moonshot_edge_policy_spec(LiveExecutionConfig())
    evaluator = ResearchPolicyEvaluator(store, (spec,))
    rows = [
        _snapshot(DYNAMIC_TUNED_MODEL, id=1, selected_no_ask=0.04, selected_edge=0.90, decision_time_local="2026-05-20T12:30:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=2, selected_bucket="74-75F", selected_no_ask=0.04, selected_edge=0.89, decision_time_local="2026-05-20T12:31:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=3, selected_bucket="76-77F", selected_no_ask=0.05, selected_edge=0.90, decision_time_local="2026-05-20T12:32:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=4, selected_bucket="78-79F", selected_no_ask=0.12, selected_edge=0.95, decision_time_local="2026-05-20T12:33:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=5, selected_bucket="80-81F", selected_no_ask=0.04, selected_edge=0.95, decision_time_local="2026-05-20T11:59:00-04:00"),
        _snapshot(DYNAMIC_TUNED_MODEL, id=6, selected_bucket="82-83F", selected_no_ask=0.51, selected_edge=0.95, decision_time_local="2026-05-20T12:34:00-04:00"),
    ]

    filtered = evaluator._candidates_for_policy(spec, rows, [])
    selected = evaluator._first_by_scope(spec, filtered)

    assert [item["selected_bucket"] for item in selected] == ["76-77F", "78-79F"]


def test_ngboost_best_buy_yes_policy_spec_filters_late_medium_entries(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    spec = ngboost_best_buy_yes_policy_spec(LiveExecutionConfig())
    evaluator = ResearchPolicyEvaluator(store, (spec,))
    rows = [
        _snapshot(
            NGBOOST_MODEL,
            id=1,
            strategy_bucket=str(StrategyBucket.BEST_BUCKET),
            selected_side="BUY_YES",
            selected_yes_ask=0.05,
            decision_time_local="2026-05-20T12:30:00-04:00",
        ),
        _snapshot(
            NGBOOST_MODEL,
            id=2,
            strategy_bucket=str(StrategyBucket.BEST_BUCKET),
            selected_bucket="74-75F",
            selected_side="BUY_YES",
            selected_yes_ask=0.04,
            decision_time_local="2026-05-20T12:31:00-04:00",
        ),
        _snapshot(
            NGBOOST_MODEL,
            id=3,
            strategy_bucket=str(StrategyBucket.BEST_BUCKET),
            selected_bucket="76-77F",
            selected_side="BUY_YES",
            selected_yes_ask=0.08,
            decision_time_local="2026-05-20T11:59:00-04:00",
        ),
        _snapshot(
            NGBOOST_MODEL,
            id=4,
            strategy_bucket=str(StrategyBucket.HIGH_CONVICTION),
            selected_bucket="78-79F",
            selected_side="BUY_YES",
            selected_yes_ask=0.08,
            decision_time_local="2026-05-20T12:33:00-04:00",
        ),
        _snapshot(
            DYNAMIC_TUNED_MODEL,
            id=5,
            strategy_bucket=str(StrategyBucket.BEST_BUCKET),
            selected_bucket="80-81F",
            selected_side="BUY_YES",
            selected_yes_ask=0.08,
            decision_time_local="2026-05-20T12:34:00-04:00",
        ),
        _snapshot(
            NGBOOST_MODEL,
            id=6,
            strategy_bucket=str(StrategyBucket.BEST_BUCKET),
            selected_bucket="82-83F",
            selected_side="BUY_YES",
            selected_yes_ask=0.51,
            decision_time_local="2026-05-20T12:35:00-04:00",
        ),
    ]

    filtered = evaluator._candidates_for_policy(spec, rows, [])
    filtered = [item for item in filtered if item["selected_side"] == str(TradeAction.BUY_YES)]
    selected = evaluator._first_by_scope(spec, filtered)

    assert [item["selected_bucket"] for item in selected] == ["72-73F"]


def test_live_build_candidates_includes_ngboost_best_bucket(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"),
    )
    engine.fair_value_engines = [StaticFairValueEngine()]
    as_of_utc = datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc)
    weather = StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 20),
        latest_obs_time="2026-05-20T16:15:00+00:00",
        latest_obs_age_minutes=15.0,
        current_temp=74.0,
        high_so_far=75.0,
        low_so_far=61.0,
        hour_local=12,
        day_of_year=140,
        temp_change_1h=1.0,
        temp_change_3h=3.0,
        dewpoint=60.0,
        wind_speed=4.0,
        wind_dir_sin=0.0,
        wind_dir_cos=1.0,
        cloud_cover_code=1.0,
        hrrr_current_temp=None,
        hrrr_remaining_max=None,
        hrrr_remaining_min=None,
        stale=False,
    )
    markets = [
        _candidate_market("market-1", 72, 73, "yes-1", "no-1"),
        _candidate_market("market-2", 74, 75, "yes-2", "no-2"),
        _candidate_market("market-3", 76, 77, "yes-3", "no-3"),
    ]
    books = {
        "yes-1": _book("yes-1", ask=0.11),
        "no-1": _book("no-1", ask=0.82),
        "yes-2": _book("yes-2", ask=0.13),
        "no-2": _book("no-2", ask=0.80),
        "yes-3": _book("yes-3", ask=0.12),
        "no-3": _book("no-3", ask=0.85),
    }

    candidates = engine._build_candidates(markets, books, {"KATL": weather}, as_of_utc, errors=[])

    ngboost = [candidate for candidate in candidates if candidate.plan.strategy.name == NGBOOST_BEST_BUY_YES_POLICY_NAME]
    assert len(ngboost) == 1
    position = ngboost[0].position
    assert position.model_group == NGBOOST_MODEL
    assert position.strategy_bucket == StrategyBucket.BEST_BUCKET
    assert position.selected_side == TradeAction.BUY_YES
    assert position.selected_market_id == "market-2"
    assert position.selected_bucket == "74-75F"
    assert position.entry_price == pytest.approx(0.13)
    assert position.entry_edge == pytest.approx(0.31)


def test_live_position_insert_is_idempotent_by_scope(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    position = _live_position()

    first_id = store.insert_live_policy_position(position)
    second_id = store.insert_live_policy_position(position)

    assert first_id is not None
    assert second_id is None
    assert len(store.live_open_positions()) == 1


def test_reconcile_live_policy_positions_from_attempts_repairs_target_fill_overwrite(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    position_id = store.insert_live_policy_position(_live_position())
    assert position_id is not None
    store.update_live_policy_position_execution(
        position_id,
        state=str(LivePositionState.FILLED),
        filled_shares=7.5,
        avg_entry_price=0.4,
        cost_usd=3.0,
    )
    store.insert_live_order_attempt(
        LiveOrderAttempt(
            timestamp="2026-05-20T18:00:00+00:00",
            live_position_id=position_id,
            attempt_seq=1,
            token_id="no-token",
            side=TradeAction.BUY_NO,
            order_mode=LiveOrderMode.FAK,
            limit_price=0.4,
            target_notional_usd=3.0,
            target_shares=7.5,
            external_order_id="order-1",
            external_status="matched",
            final_state=LivePositionState.PARTIAL,
            final_reason="matched",
            filled_shares=2.0,
            avg_price=0.35,
            cost_usd=0.7,
            raw_payload={"success": True},
        )
    )

    repaired = store.reconcile_live_policy_positions_from_attempts()

    assert repaired == 1
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["filled_shares"] == pytest.approx(2.0)
    assert row["cost_usd"] == pytest.approx(0.7)
    assert row["avg_entry_price"] == pytest.approx(0.35)
    raw = json.loads(row["raw_json"])
    assert raw["reconciled_from_order_attempts"]["filled_shares"] == pytest.approx(2.0)


def test_live_dashboard_positions_join_strategy_books_and_marks(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    store.upsert_live_strategy(default_live_strategy(max_notional_usd=3.0))
    position_id = store.insert_live_policy_position(_live_position())
    assert position_id is not None
    store.update_live_policy_position_execution(
        position_id,
        state=str(LivePositionState.FILLED),
        filled_shares=7.5,
        avg_entry_price=0.4,
        cost_usd=3.0,
    )
    store.insert_book_snapshot(
        BookSnapshot(
            token_id="no-token",
            bids=[BookLevel(price=0.62, size=100.0)],
            asks=[BookLevel(price=0.64, size=100.0)],
            timestamp="2026-05-20T18:00:00+00:00",
        )
    )

    rows = store.live_dashboard_positions(market_date=date(2026, 5, 20))

    assert len(rows) == 1
    assert rows[0]["strategy_name"] == LIVE_POLICY_NAME
    assert rows[0]["policy_name"] == LIVE_POLICY_NAME
    assert rows[0]["model_group"] == LIVE_MODEL_GROUP
    assert rows[0]["current_bid"] == pytest.approx(0.62)
    assert rows[0]["mark_value"] == pytest.approx(4.65)
    assert rows[0]["unrealized_pnl"] == pytest.approx(1.65)
    assert store.latest_live_market_date() == "2026-05-20"


def test_live_dashboard_marks_no_bid_sub_cent_ask_as_zero(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    position_id = store.insert_live_policy_position(_live_position())
    assert position_id is not None
    store.update_live_policy_position_execution(
        position_id,
        state=str(LivePositionState.PARTIAL),
        filled_shares=12.29,
        avg_entry_price=0.4,
        cost_usd=4.916,
    )
    store.insert_book_snapshot(
        BookSnapshot(
            token_id="no-token",
            bids=[],
            asks=[BookLevel(price=0.001, size=100.0)],
            timestamp="2026-05-20T18:00:00+00:00",
        )
    )

    rows = store.live_dashboard_positions(market_date=date(2026, 5, 20))

    assert rows[0]["current_bid"] is None
    assert rows[0]["current_ask"] == pytest.approx(0.001)
    assert rows[0]["mark_value"] == pytest.approx(0.0)
    assert rows[0]["unrealized_pnl"] == pytest.approx(-4.916)


def test_live_submit_uses_three_dollar_fak_buy(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"),
        submitter=FakeSubmitter(),
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position)

    assert state == LivePositionState.FILLED
    attempt = store.connection.execute("select * from live_order_attempts").fetchone()
    assert attempt["target_notional_usd"] == pytest.approx(3.0)
    assert attempt["order_mode"] == "FAK"
    row = store.live_open_positions()[0]
    assert row["cost_usd"] == pytest.approx(3.0)


def test_default_submitter_reads_private_key_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"key": 0, "executor": 0}

    def fake_private_key(settings):
        calls["key"] += 1
        return "0xkey"

    class FakeExecutor(FakeSubmitter):
        def __init__(self, *, private_key, settings):
            calls["executor"] += 1
            assert private_key == "0xkey"

    monkeypatch.setattr(live_execution, "private_key_from_env_or_keyfile", fake_private_key)
    monkeypatch.setattr(live_execution, "ClobExecutor", FakeExecutor)
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), mode="live"))

    first = engine._default_submitter()
    second = engine._default_submitter()

    assert first is second
    assert calls == {"key": 1, "executor": 1}


class FakeSubmitter:
    def check_kill_switch(self) -> bool:
        return False

    def check_allowance_buy(self, required_usdc: float) -> AllowanceCheck:
        return AllowanceCheck(True, 100.0, 100.0)

    def get_order(self, order_id: str) -> dict[str, object]:
        return {"success": True, "status": "matched", "makingAmount": "3.0", "takingAmount": "7.5"}

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        assert token_id == "no-token"
        assert side == "BUY"
        assert amount == pytest.approx(3.0)
        return OrderSubmission(True, "order-1", "matched", None, {"success": True, "status": "matched", "makingAmount": "3.0", "takingAmount": "7.5"})

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        raise AssertionError("unexpected GTC order")

    def cancel_order(self, order_id: str) -> CancelSubmission:
        raise AssertionError("unexpected cancel")


class StaticFairValueEngine:
    model_name = NGBOOST_MODEL

    def supports_market_family(self, market_family: str | MarketFamily) -> bool:
        return str(market_family) == str(MarketFamily.HIGH_TEMP)

    def price_markets(self, markets: list[MarketSnapshot], weather: StationWeatherState) -> dict[str, FairValueResult]:
        fair_yes_by_market = {"market-1": 0.20, "market-2": 0.44, "market-3": 0.28}
        return {
            market.market_id: FairValueResult(
                fair_yes=fair_yes_by_market[market.market_id],
                fair_no=1.0 - fair_yes_by_market[market.market_id],
                reason_codes=["MODEL_PROBABILITY", "HRRR_MISSING_LOG"],
                model_name=self.model_name,
                model_features_hash="test-hash",
            )
            for market in markets
        }


def _candidate_market(market_id: str, lower_f: int, upper_f: int, yes_token_id: str, no_token_id: str) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id=None,
        question="q",
        slug=market_id,
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 20),
        lower_f=lower_f,
        upper_f=upper_f,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        end_date="2026-05-21T00:00:00Z",
        resolution_source="test",
        discovered_at="2026-05-20T16:00:00+00:00",
    )


def _book(token_id: str, *, ask: float) -> BookSnapshot:
    return BookSnapshot(
        token_id=token_id,
        bids=[BookLevel(price=ask - 0.02, size=100.0)],
        asks=[BookLevel(price=ask, size=100.0)],
        timestamp="2026-05-20T16:30:00+00:00",
    )


def _snapshot(
    model_name: str,
    *,
    id: int = 1,
    selected_side: str = "BUY_NO",
    selected_bucket: str = "72-73F",
    obs_delay_bucket: str = "15m",
    selected_market_id: str = "market-1",
    selected_yes_ask: float = 0.6,
    selected_no_ask: float = 0.4,
    selected_edge: float = 0.5,
    decision_time_local: str = "2026-05-20T12:30:00-04:00",
    strategy_bucket: str = "HIGH_CONVICTION",
) -> dict:
    return {
        "id": id,
        "timestamp": f"2026-05-20T16:30:{id:02d}+00:00",
        "station": "KATL",
        "market_date": "2026-05-20",
        "market_family": "HIGH_TEMP",
        "obs_delay_bucket": obs_delay_bucket,
        "strategy_bucket": strategy_bucket,
        "selected_side": selected_side,
        "selected_market_id": selected_market_id,
        "selected_bucket": selected_bucket,
        "selected_edge": selected_edge,
        "selected_fair_yes": 0.4,
        "selected_fair_no": 0.9,
        "selected_yes_ask": selected_yes_ask,
        "selected_no_ask": selected_no_ask,
        "selected_sweep_depth_to_cap": 10.0,
        "selected_sweep_price_cap": selected_no_ask if selected_side == "BUY_NO" else selected_yes_ask,
        "selected_book_age_seconds": 1.0,
        "decision_time_local": decision_time_local,
        "model_name": model_name,
    }


def _live_position():
    from weather_trader.execution.contracts import LivePolicyPosition

    return LivePolicyPosition(
        timestamp=utc_now_iso(),
        strategy_name=LIVE_POLICY_NAME,
        station="KATL",
        market_date=date(2026, 5, 20),
        market_family=MarketFamily.HIGH_TEMP,
        scope_key="station_date_bucket_side_obs_delay:72-73F:BUY_NO:15m",
        selected_market_id="market-1",
        selected_token_id="no-token",
        selected_side=TradeAction.BUY_NO,
        selected_bucket="72-73F",
        obs_delay_bucket="15m",
        entry_price=0.4,
        entry_fair=0.9,
        entry_edge=0.5,
        target_notional_usd=3.0,
        target_shares=7.5,
        state=LivePositionState.RESERVED,
        source_prediction_snapshot_ids=[1, 2],
        raw_json={"limit_price": 0.4},
    )


def test_live_position_uses_sizing_decision_and_persists_json(tmp_path: Path) -> None:
    from weather_trader.execution.contracts import MarketSnapshot, ResearchPolicyPosition

    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"),
        submitter=SizingSubmitter(EDGE_CORE_NOTIONAL_USD),
    )
    plan = live_strategy_plans(LiveExecutionConfig())[1]
    source = ResearchPolicyPosition(
        timestamp="2026-05-25T18:00:00+00:00",
        policy_name=EDGE_CORE_POLICY_NAME,
        station="KATL",
        market_date=date(2026, 5, 25),
        scope_key="station_date_bucket_side_obs_delay:72-73F:BUY_NO:15m",
        model_group=DYNAMIC_TUNED_MODEL,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        obs_delay_bucket="15m",
        selected_market_id="market-1",
        selected_side=TradeAction.BUY_NO,
        selected_bucket="72-73F",
        entry_price=0.4,
        entry_edge=0.5,
        entry_fair=0.9,
        source_prediction_snapshot_ids=[1],
        raw_policy={},
        selected_sweep_price_cap=0.4,
        selected_sweep_depth_to_cap=100.0,
        selected_book_age_seconds=1.0,
    )
    candidate = live_execution.LiveCandidate(plan, source)
    market = MarketSnapshot(
        market_id="market-1",
        condition_id=None,
        question="q",
        slug="s",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 25),
        lower_f=72,
        upper_f=73,
        yes_token_id="yes-token",
        no_token_id="no-token",
        end_date="2026-05-26T00:00:00Z",
        resolution_source="test",
        discovered_at="2026-05-25T18:00:00+00:00",
    )

    sizing = engine._size_candidate(candidate, as_of_utc=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc))
    position = engine._live_position(candidate, market, reject_reason=None, sizing=sizing)
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None
    state = engine._submit(position_id, position)

    assert state == LivePositionState.FILLED
    row = store.live_open_positions()[0]
    assert row["target_notional_usd"] == pytest.approx(EDGE_CORE_NOTIONAL_USD)
    raw = store.connection.execute("select raw_json from live_policy_positions where id = ?", (position_id,)).fetchone()["raw_json"]
    assert '"sizing"' in raw
    assert f'"final_target_notional_usd": {EDGE_CORE_NOTIONAL_USD:.1f}' in raw


def test_blocked_sizing_can_be_recorded_as_rejected_position(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=()))
    position = _live_position()
    position = position.__class__(**{**position.__dict__, "target_notional_usd": 0.5, "target_shares": 1.25, "raw_json": {"limit_price": 0.4, "sizing": {"blocked_reason": "RISK_MIN_ORDER_NOTIONAL"}}})
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    engine._record_rejected(position_id, position, "RISK_MIN_ORDER_NOTIONAL")

    row = store.connection.execute("select state, raw_json from live_policy_positions where id = ?", (position_id,)).fetchone()
    assert row["state"] == "REJECTED"
    assert '"final_reason": "RISK_MIN_ORDER_NOTIONAL"' in row["raw_json"]
    attempt = store.connection.execute("select final_reason from live_order_attempts").fetchone()
    assert attempt["final_reason"] == "RISK_MIN_ORDER_NOTIONAL"


class SizingSubmitter(FakeSubmitter):
    def __init__(self, expected_amount: float) -> None:
        self.expected_amount = expected_amount

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        assert amount == pytest.approx(self.expected_amount)
        return OrderSubmission(True, "order-1", "matched", None, {"success": True, "status": "matched"})


class RestingFallbackSubmitter(FakeSubmitter):
    def __init__(self, *, after_ttl: dict[str, object]) -> None:
        self.after_ttl = after_ttl
        self.gtc_calls: list[tuple[str, str, float, float]] = []
        self.get_order_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        return OrderSubmission(False, None, "rejected", "insufficient depth", {"success": False, "status": "rejected", "errorMsg": "insufficient depth"})

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.gtc_calls.append((token_id, side, price, amount))
        return OrderSubmission(True, "gtc-1", "live", None, {"success": True, "status": "live", "orderId": "gtc-1"})

    def get_order(self, order_id: str) -> dict[str, object]:
        self.get_order_calls.append(order_id)
        return self.after_ttl

    def cancel_order(self, order_id: str) -> CancelSubmission:
        self.cancel_calls.append(order_id)
        return CancelSubmission(True, [order_id], None, None, {"canceled": [order_id]})


class RetrySubmitter(FakeSubmitter):
    def __init__(self) -> None:
        self.place_calls: list[float] = []
        self.get_order_calls: list[str] = []

    def get_order(self, order_id: str) -> dict[str, object]:
        self.get_order_calls.append(order_id)
        return {"success": True, "status": "live"}

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.place_calls.append(amount)
        if len(self.place_calls) == 1:
            return OrderSubmission(True, "order-1", "live", None, {"success": True, "status": "live"})
        assert amount == pytest.approx(1.22)
        return OrderSubmission(
            True,
            "order-2",
            "matched",
            None,
            {"success": True, "status": "matched", "makingAmount": "1.22", "takingAmount": "2.75"},
        )


class RefreshThenFillSubmitter(FakeSubmitter):
    def __init__(self) -> None:
        self.place_calls: list[float] = []
        self.get_order_calls: list[str] = []

    def get_order(self, order_id: str) -> dict[str, object]:
        self.get_order_calls.append(order_id)
        return {"success": True, "status": "matched", "makingAmount": "3.0", "takingAmount": "7.5"}

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.place_calls.append(amount)
        return OrderSubmission(True, "order-1", "live", None, {"success": True, "status": "live"})


class PartialRemainderSubmitter(FakeSubmitter):
    def __init__(self) -> None:
        self.place_calls: list[float] = []
        self.get_order_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.gtc_calls: list[tuple[str, str, float, float]] = []
        self._gtc_amount: float = 0.0
        self._gtc_price: float = 0.0

    def get_order(self, order_id: str) -> dict[str, object]:
        self.get_order_calls.append(order_id)
        if order_id == "gtc-1":
            filled = self._gtc_amount / 2.0
            return {"success": True, "status": "partial", "makingAmount": str(filled), "takingAmount": str(filled / self._gtc_price)}
        return {"success": True, "status": "live"}

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.place_calls.append(amount)
        if len(self.place_calls) == 1:
            return OrderSubmission(True, "order-1", "partial", None, {"success": True, "status": "partial", "makingAmount": "0.1", "takingAmount": "0.2"})
        return OrderSubmission(True, "order-2", "partial", None, {"success": True, "status": "partial", "makingAmount": "0.1", "takingAmount": "0.2"})

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.gtc_calls.append((token_id, side, price, amount))
        self._gtc_amount = amount
        self._gtc_price = price
        return OrderSubmission(True, "gtc-1", "live", None, {"success": True, "status": "live", "orderId": "gtc-1"})

    def cancel_order(self, order_id: str) -> CancelSubmission:
        self.cancel_calls.append(order_id)
        return CancelSubmission(True, [order_id], None, None, {"canceled": [order_id]})


class MatchedUnderfillSubmitter(PartialRemainderSubmitter):
    def get_order(self, order_id: str) -> dict[str, object]:
        if order_id == "order-1":
            self.get_order_calls.append(order_id)
            return {"success": True, "status": "matched", "makingAmount": "0.1", "takingAmount": "0.2"}
        return super().get_order(order_id)

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.place_calls.append(amount)
        if len(self.place_calls) == 1:
            return OrderSubmission(True, "order-1", "matched", None, {"success": True, "status": "matched", "makingAmount": "0.1", "takingAmount": "0.2"})
        return OrderSubmission(True, "order-2", "partial", None, {"success": True, "status": "partial", "makingAmount": "0.1", "takingAmount": "0.2"})


class PolymarketRefreshUnderfillSubmitter(PartialRemainderSubmitter):
    def get_order(self, order_id: str) -> dict[str, object]:
        if order_id == "order-1":
            self.get_order_calls.append(order_id)
            return {
                "success": True,
                "status": "matched",
                "original_size": "25",
                "price": "0.4",
                "size_matched": "0.2",
            }
        return super().get_order(order_id)

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.place_calls.append(amount)
        if len(self.place_calls) == 1:
            return OrderSubmission(True, "order-1", "matched", None, {"success": True, "status": "matched", "makingAmount": "0.1", "takingAmount": "0.2"})
        return OrderSubmission(True, "order-2", "partial", None, {"success": True, "status": "partial", "makingAmount": "0.1", "takingAmount": "0.2"})


class StaticBookClient:
    def __init__(self, book: BookSnapshot) -> None:
        self.book = book
        self.calls: list[list[str]] = []

    def fetch_books(self, token_ids: list[str]) -> dict[str, BookSnapshot]:
        self.calls.append(token_ids)
        return {self.book.token_id: self.book}


def _retry_market():
    from weather_trader.execution.contracts import MarketSnapshot

    return MarketSnapshot(
        market_id="market-1",
        condition_id=None,
        question="q",
        slug="s",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 20),
        lower_f=72,
        upper_f=73,
        yes_token_id="yes-token",
        no_token_id="no-token",
        end_date="2026-05-21T00:00:00Z",
        resolution_source="test",
        discovered_at="2026-05-20T18:00:00+00:00",
    )


def _retry_book(price: float = 0.5, size: float = 2.44) -> BookSnapshot:
    return BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=price - 0.02, size=100.0)],
        asks=[BookLevel(price=price, size=size)],
        timestamp="2026-05-20T18:00:05+00:00",
    )


def test_live_submit_retries_once_after_wait_with_fresh_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = RetrySubmitter()
    book_client = StaticBookClient(_retry_book())
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.FILLED
    assert sleeps == [5.0]
    assert submitter.place_calls == pytest.approx([3.0, 1.22])
    assert submitter.get_order_calls == ["order-1"]
    assert book_client.calls == [["no-token"]]
    attempts = store.connection.execute("select attempt_seq, external_order_id, target_notional_usd, final_state, raw_payload from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["external_order_id"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "order-1", 3.0, "SUBMITTED"),
        (2, "order-2", 1.22, "FILLED"),
    ]
    first_payload = json.loads(attempts[0]["raw_payload"])["raw_payload"]
    retry_payload = json.loads(attempts[1]["raw_payload"])["raw_payload"]
    assert first_payload["execution"]["attempt_label"] == "initial"
    assert retry_payload["execution"]["attempt_label"] == "retry"
    assert retry_payload["execution"]["retry"]["wait_seconds"] == 5.0
    assert retry_payload["execution"]["retry"]["first_attempt_order_id"] == "order-1"
    assert retry_payload["execution"]["retry"]["retry_book_timestamp"] == "2026-05-20T18:00:05+00:00"
    row = store.live_open_positions()[0]
    assert row["cost_usd"] == pytest.approx(1.22)
    assert row["filled_shares"] == pytest.approx(2.75)

def test_live_submit_partial_fak_rolls_remainder_into_resting_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = PartialRemainderSubmitter()
    book_client = StaticBookClient(_retry_book(price=0.35, size=2.44))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("LIVE_MIN_ORDER_NOTIONAL", "0.01")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.PARTIAL
    assert sleeps == [5.0, 120.0]
    assert len(submitter.place_calls) == 2
    assert submitter.place_calls[0] == pytest.approx(3.0)
    assert submitter.place_calls[1] > 0
    assert submitter.get_order_calls == ["order-1", "gtc-1"]
    assert submitter.cancel_calls == ["gtc-1"]
    assert len(submitter.gtc_calls) == 1
    assert submitter.gtc_calls[0][0] == "no-token"
    assert submitter.gtc_calls[0][1] == "BUY"
    assert submitter.gtc_calls[0][3] == pytest.approx(2.8)
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
    ]
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["cost_usd"] == pytest.approx(1.6)




def test_live_submit_matched_underfill_rolls_remainder_into_resting_fallback_for_any_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = MatchedUnderfillSubmitter()
    book_client = StaticBookClient(_retry_book(price=0.35, size=2.44))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("LIVE_MIN_ORDER_NOTIONAL", "0.01")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0),
        book_client=book_client,
        submitter=submitter,
    )
    base_position = _live_position()
    position = base_position.__class__(
        **{
            **base_position.__dict__,
            "strategy_name": EDGE_CORE_POLICY_NAME,
            "target_notional_usd": 7.0,
            "target_shares": 14.0,
        }
    )
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.PARTIAL
    assert sleeps == [5.0, 120.0]
    assert len(submitter.place_calls) == 2
    assert submitter.get_order_calls == ["order-1", "gtc-1"]
    assert submitter.cancel_calls == ["gtc-1"]
    assert len(submitter.gtc_calls) == 1
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
    ]
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["cost_usd"] == pytest.approx(3.6)



def test_live_submit_polymarket_refresh_underfill_reaches_resting_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = PolymarketRefreshUnderfillSubmitter()
    book_client = StaticBookClient(_retry_book(price=0.35, size=2.44))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("LIVE_MIN_ORDER_NOTIONAL", "0.01")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0),
        book_client=book_client,
        submitter=submitter,
    )
    base_position = _live_position()
    position = base_position.__class__(
        **{
            **base_position.__dict__,
            "target_notional_usd": 10.0,
            "target_shares": 25.0,
        }
    )
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.PARTIAL
    assert sleeps == [5.0, 120.0]
    assert submitter.get_order_calls == ["order-1", "gtc-1"]
    assert len(submitter.gtc_calls) == 1
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
    ]


def test_live_submit_does_not_retry_when_first_order_fills_during_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = RefreshThenFillSubmitter()
    book_client = StaticBookClient(_retry_book(price=0.35, size=2.44))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.FILLED
    assert sleeps == [5.0]
    assert submitter.place_calls == pytest.approx([3.0])
    assert submitter.get_order_calls == ["order-1"]
    assert book_client.calls == []
    assert store.connection.execute("select count(*) count from live_order_attempts").fetchone()["count"] == 1
    row = store.live_open_positions()[0]
    assert row["cost_usd"] == pytest.approx(3.0)
    assert row["filled_shares"] == pytest.approx(7.5)


def test_resting_fallback_places_gtc_for_full_remaining_notional_and_cancels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = RestingFallbackSubmitter(after_ttl={"success": True, "status": "live"})
    book_client = StaticBookClient(BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.33, size=100.0)],
        asks=[BookLevel(price=0.42, size=100.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    ))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", resting_fallback_ttl_seconds=60.0),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.REJECTED
    assert sleeps == [60.0]
    assert book_client.calls == [["no-token"]]
    assert submitter.gtc_calls == [("no-token", "BUY", 0.37, 3.0)]
    assert submitter.get_order_calls == ["gtc-1"]
    assert submitter.cancel_calls == ["gtc-1"]
    attempts = store.connection.execute("select attempt_seq, order_mode, external_order_id, limit_price, target_notional_usd, final_state, raw_payload from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["external_order_id"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "FAK", None, 3.0, "REJECTED"),
        (2, "GTC", "gtc-1", 3.0, "CANCELLED"),
    ]
    resting_payload = json.loads(attempts[1]["raw_payload"])["raw_payload"]["execution"]["resting_fallback"]
    assert attempts[1]["limit_price"] == pytest.approx(0.37)
    assert resting_payload["ttl_seconds"] == pytest.approx(60.0)
    assert resting_payload["notional_fraction"] == pytest.approx(1.0)
    assert resting_payload["best_bid"] == pytest.approx(0.33)
    assert resting_payload["best_ask"] == pytest.approx(0.42)
    assert resting_payload["cancel_response"] == {"canceled": ["gtc-1"]}


def test_resting_fallback_updates_position_when_filled_during_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = RestingFallbackSubmitter(after_ttl={"success": True, "status": "matched", "makingAmount": "3.0", "takingAmount": "8.1"})
    book_client = StaticBookClient(BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.33, size=100.0)],
        asks=[BookLevel(price=0.42, size=100.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    ))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", resting_fallback_ttl_seconds=60.0),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.FILLED
    assert sleeps == [60.0]
    assert submitter.cancel_calls == []
    row = store.live_open_positions()[0]
    assert row["state"] == "FILLED"
    assert row["cost_usd"] == pytest.approx(3.0)
    assert row["filled_shares"] == pytest.approx(8.1)
    assert row["avg_entry_price"] == pytest.approx(3.0 / 8.1)
    attempt = store.connection.execute("select order_mode, final_state, cost_usd, filled_shares from live_order_attempts order by attempt_seq desc limit 1").fetchone()
    assert attempt["order_mode"] == "GTC"
    assert attempt["final_state"] == "FILLED"
    assert attempt["cost_usd"] == pytest.approx(3.0)
    assert attempt["filled_shares"] == pytest.approx(8.1)


def test_resting_fallback_is_enabled_for_all_live_strategies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: None)
    submitter = RestingFallbackSubmitter(after_ttl={"success": True, "status": "live"})
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"),
        book_client=StaticBookClient(BookSnapshot(
            token_id="no-token",
            bids=[BookLevel(price=0.33, size=100.0)],
            asks=[BookLevel(price=0.42, size=100.0)],
            timestamp="2026-05-20T18:00:06+00:00",
        )),
        submitter=submitter,
    )
    position = _live_position().__class__(**{**_live_position().__dict__, "strategy_name": EDGE_CORE_POLICY_NAME})
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.REJECTED
    assert len(submitter.gtc_calls) == 1
    assert store.connection.execute("select count(*) count from live_order_attempts").fetchone()["count"] == 2


def test_resting_order_parameters_respect_entry_and_fair_caps(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), mode="live"))
    position = _live_position().__class__(**{**_live_position().__dict__, "entry_fair": 0.48, "raw_json": {"limit_price": 0.36}})
    book = BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.31, size=100.0)],
        asks=[BookLevel(price=0.50, size=100.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    )

    limit_price, target_notional, reason = engine._resting_order_parameters(position, book, current_cost_usd=0.0)

    assert reason is None
    assert limit_price == pytest.approx(0.33)
    assert target_notional == pytest.approx(3.0)


def test_live_submit_uses_exchange_returned_fill_amounts(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"),
        submitter=ResponseFillSubmitter(),
    )
    position = _live_position()
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position)

    assert state == LivePositionState.FILLED
    row = store.live_open_positions()[0]
    assert row["state"] == "FILLED"
    assert row["cost_usd"] == pytest.approx(2.4)
    assert row["filled_shares"] == pytest.approx(8.0)
    assert row["avg_entry_price"] == pytest.approx(0.3)
    attempt = store.connection.execute("select cost_usd, filled_shares, avg_price from live_order_attempts").fetchone()
    assert attempt["cost_usd"] == pytest.approx(2.4)
    assert attempt["filled_shares"] == pytest.approx(8.0)
    assert attempt["avg_price"] == pytest.approx(0.3)


class ResponseFillSubmitter(FakeSubmitter):
    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        return OrderSubmission(
            True,
            "order-actual-fill",
            "matched",
            None,
            {"success": True, "status": "matched", "makingAmount": "2.4", "takingAmount": "8.0"},
        )
