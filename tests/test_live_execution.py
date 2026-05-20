from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from weather_trader.execution.clob_executor import AllowanceCheck, OrderSubmission
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    LivePositionState,
    LiveStrategy,
    MarketFamily,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.store import ExecutionStore
from weather_trader.live.execution import (
    CATBOOST_MODEL,
    DYNAMIC_TUNED_MODEL,
    LIVE_MODEL_GROUP,
    LIVE_POLICY_NAME,
    LiveExecutionConfig,
    LiveExecutionEngine,
    default_live_strategy,
    live_policy_spec,
)
from weather_trader.research.policies import ResearchPolicyEvaluator


def test_live_strategy_is_registered_in_separate_store(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    strategy = default_live_strategy(max_notional_usd=3.0)

    store.upsert_live_strategy(strategy)

    rows = store.live_strategies()
    assert len(rows) == 1
    assert rows[0]["name"] == LIVE_POLICY_NAME
    assert rows[0]["model_group"] == LIVE_MODEL_GROUP
    assert rows[0]["max_notional_usd"] == 3.0


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
        ]
    )

    filtered = evaluator._candidates_for_policy(spec, [], rows)
    selected = evaluator._first_by_scope(spec, filtered)

    assert len(selected) == 1
    assert selected[0]["selected_bucket"] == "72-73F"


def test_live_position_insert_is_idempotent_by_scope(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    position = _live_position()

    first_id = store.insert_live_policy_position(position)
    second_id = store.insert_live_policy_position(position)

    assert first_id is not None
    assert second_id is None
    assert len(store.live_open_positions()) == 1


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


class FakeSubmitter:
    def check_kill_switch(self) -> bool:
        return False

    def check_allowance_buy(self, required_usdc: float) -> AllowanceCheck:
        return AllowanceCheck(True, 100.0, 100.0)

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        assert token_id == "no-token"
        assert side == "BUY"
        assert amount == pytest.approx(3.0)
        return OrderSubmission(True, "order-1", "matched", None, {"success": True, "status": "matched"})


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
    decision_time_local: str = "2026-05-20T12:30:00-04:00",
) -> dict:
    return {
        "id": id,
        "timestamp": f"2026-05-20T16:30:{id:02d}+00:00",
        "station": "KATL",
        "market_date": "2026-05-20",
        "market_family": "HIGH_TEMP",
        "obs_delay_bucket": obs_delay_bucket,
        "strategy_bucket": "HIGH_CONVICTION",
        "selected_side": selected_side,
        "selected_market_id": selected_market_id,
        "selected_bucket": selected_bucket,
        "selected_edge": 0.5,
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
