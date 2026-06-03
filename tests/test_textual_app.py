from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from textual.widgets import Button, DataTable, Input, Static, TabPane

from weather_trader.ui.process_supervisor import ProcessSnapshot, ProcessSpec, ProcessSupervisor
from weather_trader.ui.textual_app import RoboWeatherTUI, _live_position_risk_value, _live_start_label
from weather_trader.ui.dashboard_rollups import _build_live_policy_view, _build_policy_view, _build_position_view, _bucket_label
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    LivePolicyPosition,
    LivePositionState,
    LiveStrategy,
    MarketFamily,
    MarketSnapshot,
    PredictionResult,
    PredictionSnapshot,
    ResearchPolicyPosition,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.store import ExecutionStore


async def _wait_for_ui(predicate, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for UI condition")


def test_position_view_rolls_up_unique_exposures_and_station_totals() -> None:
    open_positions = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "station": "KATL",
            "market_date": "2026-05-11",
            "side": "BUY_NO",
            "lower_f": 74,
            "upper_f": 75,
            "shares": 100,
            "cost": 79.0,
            "mark_value": 80.0,
            "unrealized_pnl": 1.0,
            "current_bid": 0.80,
            "effective_status": "LIVE",
        },
        {
            "timestamp": "2026-05-11T18:12:00Z",
            "station": "KATL",
            "market_date": "2026-05-11",
            "side": "BUY_NO",
            "lower_f": 74,
            "upper_f": 75,
            "shares": 50,
            "cost": 40.0,
            "mark_value": 40.0,
            "unrealized_pnl": 0.5,
            "current_bid": 0.95,
            "effective_status": "EFFECTIVELY_WON",
        },
        {
            "timestamp": "2026-05-11T18:13:00Z",
            "station": "KDAL",
            "market_date": "2026-05-11",
            "side": "BUY_YES",
            "lower_f": 80,
            "upper_f": 81,
            "shares": 25,
            "cost": 10.0,
            "mark_value": 8.0,
            "unrealized_pnl": -2.0,
            "current_bid": 0.32,
            "effective_status": "LIVE",
        },
    ]

    view = _build_position_view(open_positions)

    assert view["raw_count"] == 3
    assert view["unique_count"] == 2
    assert view["buy_yes"] == 1
    assert view["buy_no"] == 2
    assert view["in_money"] == 2
    assert view["done"] == 1
    assert view["raw_mtm"] == pytest.approx(-0.5)
    assert view["unique_mtm"] == pytest.approx(-0.5)

    atl = next(row for row in view["station_rows"] if row["station"] == "KATL")
    assert atl["raw_count"] == 2
    assert atl["unique_count"] == 1
    assert atl["done"] == 1
    assert atl["raw_mtm"] == pytest.approx(1.5)

    exposure = next(row for row in view["exposure_rows"] if row["station"] == "KATL")
    assert exposure["bucket"] == "74-75F"
    assert exposure["entry"] == pytest.approx(0.7933333333)
    assert exposure["mark"] == pytest.approx(0.8)
    assert exposure["pnl"] == pytest.approx(1.5)
    assert exposure["status"] == "DONE"


def test_policy_view_ranks_independent_rows_by_current_mtm() -> None:
    exposure_index = {
        ("KATL", "2026-05-11", "BUY_NO|74-75F"): {
            "pnl": 1.5,
            "max_bid": 0.95,
        },
        ("KDAL", "2026-05-11", "BUY_YES|80-81F"): {
            "pnl": -2.0,
            "max_bid": 0.32,
        },
    }
    policy_rows = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "policy_name": "policy_a",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.79,
        },
        {
            "timestamp": "2026-05-11T18:12:00Z",
            "policy_name": "policy_a",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.79,
        },
        {
            "timestamp": "2026-05-11T18:13:00Z",
            "policy_name": "policy_b",
            "station": "KDAL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_YES",
            "selected_bucket": "80-81F",
            "entry_price": 0.10,
        },
    ]

    view = _build_policy_view(policy_rows, exposure_index)

    assert view["latest_time"] == "18:13:00"
    assert [row["policy"] for row in view["rows"]] == ["policy_a", "policy_b"]

    policy_a = view["rows"][0]
    assert policy_a["rows"] == 2
    assert policy_a["wins"] == 2
    assert policy_a["done"] == 2
    assert policy_a["mtm"] == pytest.approx(3.0)
    assert policy_a["win_rate"] == pytest.approx(1.0)
    assert policy_a["avg_pnl"] == pytest.approx(1.5)

    policy_b = view["rows"][1]
    assert policy_b["rows"] == 1
    assert policy_b["wins"] == 0
    assert policy_b["mtm"] == pytest.approx(-2.0)


def test_policy_view_normalizes_max_so_far_model_group() -> None:
    exposure_index = {
        ("KATL", "2026-05-11", "BUY_NO|74-75F"): {
            "pnl": 0.5,
            "max_bid": 0.96,
        }
    }
    policy_rows = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "policy_name": "max_so_far_15m_first",
            "model_group": "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
            "raw_policy": {
                "policy": {
                    "source": "max_so_far",
                    "model_name": None,
                }
            },
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.01,
        }
    ]

    view = _build_policy_view(policy_rows, exposure_index)

    assert view["rows"][0]["model_group"] == "max_so_far"


def test_live_policy_view_uses_open_positions_and_policy_silos() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.79,
            "entry_fair": 0.88,
            "entry_edge": 0.09,
            "target_notional_usd": 3.0,
            "filled_shares": 3.797468,
            "cost_usd": 3.0,
            "state": "FILLED",
            "current_bid": 0.95,
            "unrealized_pnl": 0.16,
        },
        {
            "timestamp": "2026-05-11T18:13:00Z",
            "policy_name": "pm_us12_dynamic_hc_15m_first",
            "model_group": "dynamic_bucket_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KDAL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_YES",
            "selected_bucket": "80-81F",
            "entry_price": 0.10,
            "entry_fair": 0.25,
            "entry_edge": 0.15,
            "target_notional_usd": 3.0,
            "filled_shares": 30.0,
            "cost_usd": 3.0,
            "state": "FILLED",
            "current_bid": 0.32,
            "unrealized_pnl": 0.22,
        },
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc))

    assert [row["policy"] for row in view["policy_rows"]] == [
        "pm_us12_dynamic_hc_15m_first",
        "pm_us12_mvp_hc_15m_first",
    ]
    assert view["raw_count"] == 2
    assert view["buy_no"] == 1
    assert view["buy_yes"] == 1
    assert view["done"] == 1
    assert view["policy_rows"][0]["open_positions"] == 1
    assert view["policy_rows"][0]["mtm"] == pytest.approx(0.22)
    assert view["policy_rows"][0]["avg_entry"] == pytest.approx(0.10)
    assert view["policy_rows"][0]["avg_fair"] == pytest.approx(0.25)
    assert view["policy_rows"][0]["avg_edge"] == pytest.approx(0.15)
    assert view["policy_rows"][0]["avg_bid"] == pytest.approx(0.32)
    assert view["policy_rows"][0]["expected_rr"] == pytest.approx(1.5)
    assert view["policy_rows"][0]["risk"] == pytest.approx(3.0)
    assert view["policy_rows"][0]["live_rr"] == pytest.approx(0.22 / 3.0)
    assert view["policy_rows"][0]["live_minus_exp"] == pytest.approx(0.22 / 3.0 - 1.5)
    assert view["policy_rows"][1]["mtm"] == pytest.approx(0.16)
    assert view["policy_rows"][1]["risk"] == pytest.approx(3.0)
    assert view["rows"] == view["policy_rows"]
    assert view["position_rows"][0]["policy"] == "pm_us12_mvp_hc_15m_first"
    assert view["position_rows"][0]["live_minus_exp"] == pytest.approx((0.95 - 0.79) / 0.79 - (0.88 - 0.79) / 0.79)
    assert view["exposure_rows"][0]["fair"] == pytest.approx(0.25)
    assert view["exposure_rows"][0]["edge"] == pytest.approx(0.15)
    assert view["exposure_rows"][0]["expected_rr"] == pytest.approx(1.5)
    assert view["station_rows"][0]["risk"] == pytest.approx(3.0)
    assert [row["station"] for row in view["policy_station_rows"]] == ["KDAL", "KATL"]
    assert view["exposure_rows"][0]["cost"] == pytest.approx(3.0)
    assert view["exposure_rows"][0]["shares"] == pytest.approx(30.0)
    assert view["exposure_rows"][0]["mark"] == pytest.approx(0.32)
    assert view["exposure_rows"][0]["live_rr"] == pytest.approx(0.22 / 3.0)


def test_live_policy_view_aggregates_simple_contract_exposure() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "policy_name": "strategy_a",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.79,
            "entry_fair": 0.88,
            "entry_edge": 0.09,
            "target_notional_usd": 80.0,
            "filled_shares": 100.0,
            "avg_entry_price": 0.79,
            "cost_usd": 79.0,
            "state": "FILLED",
            "current_bid": 0.80,
            "unrealized_pnl": 1.0,
        },
        {
            "timestamp": "2026-05-11T18:12:00Z",
            "policy_name": "strategy_b",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.80,
            "entry_fair": 0.90,
            "entry_edge": 0.10,
            "target_notional_usd": 40.0,
            "filled_shares": 50.0,
            "avg_entry_price": 0.80,
            "cost_usd": 40.0,
            "state": "PARTIAL",
            "current_bid": 0.82,
            "unrealized_pnl": 1.0,
        },
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc))

    assert len(view["exposure_rows"]) == 1
    exposure = view["exposure_rows"][0]
    assert exposure["rows"] == 2
    assert exposure["target"] == pytest.approx(120.0)
    assert exposure["cost"] == pytest.approx(119.0)
    assert exposure["shares"] == pytest.approx(150.0)
    assert exposure["entry"] == pytest.approx((0.79 * 100.0 + 0.80 * 50.0) / 150.0)
    assert exposure["mark"] == pytest.approx((0.80 * 100.0 + 0.82 * 50.0) / 150.0)
    assert exposure["pnl"] == pytest.approx(2.0)
    assert exposure["live_rr"] == pytest.approx(2.0 / 119.0)
    assert exposure["status"] == "ITM"


def test_live_performance_summary_builds_cumulative_series(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "tui.sqlite")
    try:
        first_id = store.insert_live_policy_position(
            LivePolicyPosition(
                timestamp="2026-05-10T12:00:00+00:00",
                strategy_name="perf-a",
                station="KATL",
                market_date=date(2026, 5, 10),
                market_family=MarketFamily.HIGH_TEMP,
                scope_key="perf-a",
                selected_market_id="market-a",
                selected_token_id="token-a",
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                obs_delay_bucket="15m",
                entry_price=0.4,
                entry_fair=0.6,
                entry_edge=0.2,
                target_notional_usd=4.0,
                target_shares=10.0,
                state=LivePositionState.RESERVED,
                source_prediction_snapshot_ids=[1],
            )
        )
        second_id = store.insert_live_policy_position(
            LivePolicyPosition(
                timestamp="2026-05-11T12:00:00+00:00",
                strategy_name="perf-b",
                station="KDAL",
                market_date=date(2026, 5, 11),
                market_family=MarketFamily.HIGH_TEMP,
                scope_key="perf-b",
                selected_market_id="market-b",
                selected_token_id="token-b",
                selected_side=TradeAction.BUY_YES,
                selected_bucket="80-81F",
                obs_delay_bucket="15m",
                entry_price=0.5,
                entry_fair=0.65,
                entry_edge=0.15,
                target_notional_usd=5.0,
                target_shares=10.0,
                state=LivePositionState.RESERVED,
                source_prediction_snapshot_ids=[2],
            )
        )
        assert first_id is not None and second_id is not None
        store.update_live_policy_position_settlement(
            first_id,
            resolved_at="2026-05-10T20:00:00+00:00",
            resolution_source="iem",
            winning_token_id="token-a",
            winning_side=TradeAction.BUY_NO,
            settlement_value_usd=4.0,
            realized_pnl=1.5,
            realized_rr=0.375,
        )
        store.update_live_policy_position_settlement(
            second_id,
            resolved_at="2026-05-11T20:00:00+00:00",
            resolution_source="iem",
            winning_token_id="token-b",
            winning_side=TradeAction.BUY_YES,
            settlement_value_usd=5.0,
            realized_pnl=-0.5,
            realized_rr=-0.1,
        )
        summary = store.live_performance_summary()
    finally:
        store.close()

    assert [row["utc_date"] for row in summary["daily_rows"]] == ["2026-05-10", "2026-05-11"]
    assert summary["daily_rows"][0]["daily_pnl"] == pytest.approx(1.5)
    assert summary["daily_rows"][0]["cumulative_pnl"] == pytest.approx(1.5)
    assert summary["daily_rows"][1]["daily_pnl"] == pytest.approx(-0.5)
    assert summary["daily_rows"][1]["cumulative_pnl"] == pytest.approx(1.0)
    assert summary["last_7_days"] == summary["daily_rows"]
    assert summary["total_pnl"] == pytest.approx(1.0)


def test_live_policy_view_scores_prelim_weather_when_books_are_missing() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-11T23:11:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.60,
            "current_bid": None,
            "unrealized_pnl": None,
            "high_so_far": 79.0,
        },
        {
            "timestamp": "2026-05-11T23:12:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KDAL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_YES",
            "selected_bucket": "80-81F",
            "entry_price": 0.20,
            "current_bid": None,
            "unrealized_pnl": None,
            "high_so_far": 79.0,
        },
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc))

    policy = view["policy_rows"][0]
    assert policy["book_status"] == "NO_BOOK_MARK"
    assert policy["weather_status"] == "MIXED"
    assert policy["weather_wins"] == 1
    assert policy["weather_losses"] == 1
    assert policy["weather_rr"] == pytest.approx(0.25)

    atl = next(row for row in view["exposure_rows"] if row["station"] == "KATL")
    assert atl["weather_status"] == "PRELIM_WIN"
    assert atl["weather_pnl"] == pytest.approx(0.40)
    assert atl["book_status"] == "NO_BOOK_MARK"

    kdal = next(row for row in view["exposure_rows"] if row["station"] == "KDAL")
    assert kdal["weather_status"] == "PRELIM_LOSS"
    assert kdal["weather_pnl"] == pytest.approx(-0.20)


def test_live_policy_view_counts_sub_cent_ask_without_bid_as_marked_zero() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-11T23:11:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.40,
            "current_bid": None,
            "current_ask": 0.001,
            "filled_shares": 30.725,
            "cost_usd": 12.29,
            "mark_value": 0.0,
            "unrealized_pnl": -12.29,
            "high_so_far": 79.0,
        }
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 11, 23, 30, tzinfo=timezone.utc))

    policy = view["policy_rows"][0]
    exposure = view["exposure_rows"][0]
    position = view["position_rows"][0]
    assert policy["book_status"] == "MARKED"
    assert policy["mark_pct"] == pytest.approx(1.0)
    assert policy["avg_bid"] == pytest.approx(0.0)
    assert exposure["book_status"] == "MARKED"
    assert exposure["mark"] == pytest.approx(0.0)
    assert position["bid"] == pytest.approx(0.0)
    assert position["live_rr"] == pytest.approx(-1.0)


def test_live_position_risk_value_prefers_filled_cost_over_target() -> None:
    assert _live_position_risk_value({"target_notional_usd": 24.50, "cost_usd": 12.29}) == pytest.approx(12.29)
    assert _live_position_risk_value({"target_notional_usd": 24.50, "cost_usd": 0.0}) == pytest.approx(24.50)


def test_live_policy_view_marks_prelim_loss_before_cutoff_when_high_has_cleared_bucket() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-11T18:11:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KATL",
            "market_date": "2026-05-11",
            "selected_side": "BUY_YES",
            "selected_bucket": "74-75F",
            "entry_price": 0.60,
            "current_bid": None,
            "unrealized_pnl": None,
            "high_so_far": 76.0,
        }
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 11, 18, 30, tzinfo=timezone.utc))

    policy = view["policy_rows"][0]
    exposure = view["exposure_rows"][0]
    assert policy["weather_status"] == "PRELIM_LOSS"
    assert policy["weather_wins"] == 0
    assert policy["weather_losses"] == 1
    assert exposure["weather_status"] == "PRELIM_LOSS"
    assert exposure["weather_pnl"] == pytest.approx(-0.60)
    assert exposure["book_status"] == "NO_BOOK_MARK"


def test_live_policy_view_keeps_same_day_weather_live_before_evening_cutoff() -> None:
    live_rows = [
        {
            "timestamp": "2026-05-14T22:20:00Z",
            "policy_name": "pm_us12_mvp_hc_15m_first",
            "model_group": "mvp_pm_active_us12_obs_2022_2025",
            "strategy_bucket": "HIGH_CONVICTION",
            "obs_delay_bucket": "15m",
            "station": "KATL",
            "market_date": "2026-05-14",
            "selected_side": "BUY_NO",
            "selected_bucket": "74-75F",
            "entry_price": 0.60,
            "current_bid": None,
            "unrealized_pnl": None,
            "high_so_far": 74.5,
        }
    ]

    view = _build_live_policy_view(live_rows, as_of_utc=datetime(2026, 5, 14, 22, 30, tzinfo=timezone.utc))

    policy = view["policy_rows"][0]
    assert policy["weather_status"] == "LIVE"
    assert policy["weather_wins"] == 0
    assert policy["weather_losses"] == 0
    assert policy["weather_rr"] is None

    exposure = view["exposure_rows"][0]
    assert exposure["weather_status"] == "LIVE"
    assert exposure["weather_high"] == 74.5
    assert exposure["weather_pnl"] == 0.0


def test_live_policy_view_handles_empty_input() -> None:
    view = _build_live_policy_view([])

    assert view["raw_count"] == 0
    assert view["unique_count"] == 0
    assert view["policy_rows"] == []
    assert view["rows"] == []


def test_tui_strategies_table_shows_registered_strategy_without_positions(tmp_path) -> None:
    db_path = tmp_path / "tui.sqlite"
    store = ExecutionStore(db_path)
    try:
        store.upsert_live_strategy(
            LiveStrategy(
                name="idle_strategy",
                active=True,
                source="model",
                model_group="model-a",
                model_names=["model-a"],
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                market_family=MarketFamily.HIGH_TEMP,
                local_decision_start="12:00",
                local_decision_end="15:00",
                entry_price_min=0.05,
                uniqueness_key_mode="station_date_bucket_side_obs_delay",
                max_notional_usd=1.0,
                raw_payload={},
            )
        )
    finally:
        store.close()

    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
        )
        app = RoboWeatherTUI(db_path, process_supervisor=supervisor)
        async with app.run_test(size=(140, 40)):
            strategy_table = app.query_one("#live-strategies", DataTable)
            assert strategy_table.row_count == 1

    asyncio.run(scenario())


def test_tui_process_supervisor_tab_mounts(tmp_path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        async with app.run_test(size=(140, 40)):
            process_table = app.query_one("#process-table", DataTable)
            assert process_table.row_count == 2
            assert app.query_one("#start-research", Button).disabled is False
            assert app.query_one("#stop-research", Button).disabled is True
            assert app.query_one("#start-live", Button).disabled is False
            assert app.query_one("#stop-live", Button).disabled is True

    asyncio.run(scenario())


def test_live_start_label_reflects_supervisor_live_mode() -> None:
    assert _live_start_label({}) == "Start Live Dry Run"
    assert _live_start_label({"LIVE_MODE": "dry-run"}) == "Start Live Dry Run"
    assert _live_start_label({"LIVE_MODE": "live"}) == "Start Live Run"


def test_tui_refresh_target_date_advances_to_newer_live_date(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "tui.sqlite")
    try:
        _insert_live_position(store, date(2026, 5, 25))
        app = RoboWeatherTUI(tmp_path / "tui.sqlite")
        app.target_date = "2026-05-22"

        app._refresh_target_date(store)

        assert app.target_date == "2026-05-25"
    finally:
        store.close()


def test_tui_refresh_target_date_does_not_move_backward(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "tui.sqlite")
    try:
        _insert_live_position(store, date(2026, 5, 22))
        app = RoboWeatherTUI(tmp_path / "tui.sqlite")
        app.target_date = "2026-05-25"

        app._refresh_target_date(store)

        assert app.target_date == "2026-05-25"
    finally:
        store.close()


def test_tui_first_refresh_polls_live_resolution(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeLiveResolutionService:
        def __init__(self, store: ExecutionStore) -> None:
            self.store = store

        def resolve_due(self, *, as_of_utc=None):
            calls.append(as_of_utc)
            return SimpleNamespace(candidates=2, resolved=1, pending=1, skipped=0, errors=[])

    monkeypatch.setattr("weather_trader.ui.textual_app.LiveResolutionService", FakeLiveResolutionService)

    async def scenario() -> None:
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=_test_supervisor(tmp_path))
        async with app.run_test(size=(140, 40)):
            assert len(calls) == 1
            assert app._last_live_resolution_summary is not None
            assert app._last_live_resolution_summary["resolved"] == 1

    asyncio.run(scenario())


def test_tui_live_resolution_poll_is_throttled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeLiveResolutionService:
        def __init__(self, store: ExecutionStore) -> None:
            self.store = store

        def resolve_due(self, *, as_of_utc=None):
            calls.append(as_of_utc)
            return SimpleNamespace(candidates=0, resolved=0, pending=0, skipped=0, errors=[])

    monkeypatch.setattr("weather_trader.ui.textual_app.LiveResolutionService", FakeLiveResolutionService)

    async def scenario() -> None:
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=_test_supervisor(tmp_path))
        async with app.run_test(size=(140, 40)):
            app.refresh_table()
            assert len(calls) == 1

    asyncio.run(scenario())


def test_tui_live_resolution_exception_is_captured_and_dashboard_renders(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLiveResolutionService:
        def __init__(self, store: ExecutionStore) -> None:
            self.store = store

        def resolve_due(self, *, as_of_utc=None):
            raise RuntimeError("resolver unavailable")

    monkeypatch.setattr("weather_trader.ui.textual_app.LiveResolutionService", FakeLiveResolutionService)

    async def scenario() -> None:
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=_test_supervisor(tmp_path))
        async with app.run_test(size=(140, 40)):
            live_summary = app.query_one("#live-summary", DataTable)
            assert live_summary.row_count > 0
            assert app._last_live_resolution_summary is not None
            assert app._last_live_resolution_summary["errors"] == 1
            assert "resolver unavailable" in app._last_live_resolution_summary["detail"]

    asyncio.run(scenario())


def test_tui_process_actions_start_and_stop_supervised_process(tmp_path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-u", "-c", "import time; print('research ready'); time.sleep(60)")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            stop_timeout_seconds=1.0,
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        async with app.run_test(size=(140, 40)):
            await app._start_process("research")
            assert supervisor.snapshot("research").status == "RUNNING"
            assert app.query_one("#start-research", Button).disabled is True
            assert app.query_one("#stop-research", Button).disabled is False

            await app._stop_process("research")
            assert supervisor.snapshot("research").status in {"EXITED", "FAILED"}
            assert app.query_one("#start-research", Button).disabled is False
            assert app.query_one("#stop-research", Button).disabled is True

    asyncio.run(scenario())


def test_tui_dry_run_live_start_does_not_prompt(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "dry-run"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)

        async def fail_prompt():
            raise AssertionError("dry-run live start should not prompt")

        monkeypatch.setattr(app, "_prompt_live_passphrase", fail_prompt)
        async with app.run_test(size=(140, 40)):
            await app._start_process("live")
            for _ in range(50):
                if supervisor.snapshot("live").status == "EXITED":
                    break
                await asyncio.sleep(0.02)
            assert supervisor.snapshot("live").exit_code == 0

    asyncio.run(scenario())


def test_tui_live_start_button_accepts_passphrase_and_starts_with_private_key_fd(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "live"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        captured: dict[str, object] = {}

        def unlock(passphrase: str) -> str:
            captured["passphrase"] = passphrase
            return "0xlivekey"

        async def fake_start(name: str, *, extra_env=None, pass_fds=()):
            captured["name"] = name
            captured["extra_env"] = dict(extra_env or {})
            captured["pass_fds"] = pass_fds
            fd = int(captured["extra_env"]["POLYMARKET_PRIVATE_KEY_FD"])
            captured["fd_key"] = os.read(fd, 100).decode()
            return ProcessSnapshot(name, "Live Loop", "RUNNING", 123, None, None, None, 1, "", "live")

        monkeypatch.setattr(app, "_unlock_and_verify_live_key", unlock)
        monkeypatch.setattr(supervisor, "start", fake_start)

        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#start-live", Button).press()
            await _wait_for_ui(lambda: type(app.screen).__name__ == "PassphraseScreen")
            await _wait_for_ui(lambda: app.screen.query_one("#passphrase-input", Input).has_focus is True)
            passphrase_input = app.screen.query_one("#passphrase-input", Input)
            assert passphrase_input.has_focus is True

            await pilot.press(*list("passphrase"), "enter")
            await _wait_for_ui(lambda: captured.get("fd_key") == "0xlivekey")

        assert captured["passphrase"] == "passphrase"
        assert captured["name"] == "live"
        assert captured["pass_fds"] == (int(captured["extra_env"]["POLYMARKET_PRIVATE_KEY_FD"]),)

    asyncio.run(scenario())


def test_tui_live_start_button_escape_cancels_without_starting(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "live"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        starts = 0

        async def fake_start(name: str, *, extra_env=None, pass_fds=()):
            nonlocal starts
            starts += 1
            return ProcessSnapshot(name, "Live Loop", "RUNNING", 123, None, None, None, 1, "", "live")

        monkeypatch.setattr(supervisor, "start", fake_start)

        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#start-live", Button).press()
            await _wait_for_ui(lambda: type(app.screen).__name__ == "PassphraseScreen")
            await pilot.press("escape")
            await _wait_for_ui(lambda: type(app.screen).__name__ != "PassphraseScreen")

        assert starts == 0

    asyncio.run(scenario())


def test_tui_live_start_button_ignores_overlapping_starts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "live"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        starts = 0

        def unlock(passphrase: str) -> str:
            return "0xlivekey"

        async def fake_start(name: str, *, extra_env=None, pass_fds=()):
            nonlocal starts
            starts += 1
            fd = int(extra_env["POLYMARKET_PRIVATE_KEY_FD"])
            os.read(fd, 100)
            return ProcessSnapshot(name, "Live Loop", "RUNNING", 123, None, None, None, 1, "", "live")

        monkeypatch.setattr(app, "_unlock_and_verify_live_key", unlock)
        monkeypatch.setattr(supervisor, "start", fake_start)

        async with app.run_test(size=(140, 40)) as pilot:
            start_button = app.query_one("#start-live", Button)
            start_button.press()
            start_button.press()
            await _wait_for_ui(lambda: type(app.screen).__name__ == "PassphraseScreen")
            await pilot.press(*list("passphrase"), "enter")
            await _wait_for_ui(lambda: starts == 1)
            await pilot.pause(0.1)

        assert starts == 1

    asyncio.run(scenario())


def test_tui_live_start_unlocks_and_starts_with_private_key_fd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "live"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        captured: dict[str, object] = {}

        async def prompt():
            return "passphrase"

        def unlock(passphrase: str) -> str:
            captured["passphrase"] = passphrase
            return "0xlivekey"

        async def fake_start(name: str, *, extra_env=None, pass_fds=()):
            captured["name"] = name
            captured["extra_env"] = dict(extra_env or {})
            captured["pass_fds"] = pass_fds
            fd = int(captured["extra_env"]["POLYMARKET_PRIVATE_KEY_FD"])
            captured["fd_key"] = os.read(fd, 100).decode()
            return ProcessSnapshot(name, "Live Loop", "RUNNING", 123, None, None, None, 1, "", "live")

        monkeypatch.setattr(app, "_prompt_live_passphrase", prompt)
        monkeypatch.setattr(app, "_unlock_and_verify_live_key", unlock)
        monkeypatch.setattr(supervisor, "start", fake_start)

        async with app.run_test(size=(140, 40)):
            await app._start_process("live")

        assert captured["passphrase"] == "passphrase"
        assert captured["name"] == "live"
        assert captured["fd_key"] == "0xlivekey"
        assert captured["pass_fds"] == (int(captured["extra_env"]["POLYMARKET_PRIVATE_KEY_FD"]),)

    asyncio.run(scenario())


def test_tui_live_start_cancel_or_failure_does_not_start(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
                ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
            ],
            cwd=tmp_path,
            env={"LIVE_MODE": "live"},
        )
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=supervisor)
        starts = 0

        async def fake_start(name: str, *, extra_env=None, pass_fds=()):
            nonlocal starts
            starts += 1
            return ProcessSnapshot(name, "Live Loop", "RUNNING", 123, None, None, None, 1, "", "live")

        async def cancel_prompt():
            return None

        monkeypatch.setattr(supervisor, "start", fake_start)
        monkeypatch.setattr(app, "_prompt_live_passphrase", cancel_prompt)
        async with app.run_test(size=(140, 40)):
            await app._start_process("live")
            assert starts == 0

            async def pass_prompt():
                return "passphrase"

            def fail_unlock(passphrase: str) -> str:
                raise RuntimeError("bad unlock")

            monkeypatch.setattr(app, "_prompt_live_passphrase", pass_prompt)
            monkeypatch.setattr(app, "_unlock_and_verify_live_key", fail_unlock)
            await app._start_process("live")
            assert starts == 0

    asyncio.run(scenario())


def test_live_research_policy_positions_can_be_scoped_to_market_date(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    try:
        store.upsert_market(
            MarketSnapshot(
                market_id="m_today",
                condition_id=None,
                question="Today",
                slug="today",
                city="Atlanta",
                station="KATL",
                market_date=date(2026, 5, 13),
                lower_f=74,
                upper_f=75,
                yes_token_id="yes_today",
                no_token_id="no_today",
                end_date="2026-05-14T00:00:00Z",
                resolution_source="IEM",
                discovered_at="2026-05-13T15:00:00Z",
            )
        )
        store.upsert_market(
            MarketSnapshot(
                market_id="m_old",
                condition_id=None,
                question="Old",
                slug="old",
                city="Dallas",
                station="KDAL",
                market_date=date(2026, 5, 12),
                lower_f=80,
                upper_f=81,
                yes_token_id="yes_old",
                no_token_id="no_old",
                end_date="2026-05-13T00:00:00Z",
                resolution_source="IEM",
                discovered_at="2026-05-12T15:00:00Z",
            )
        )
        store.insert_book_snapshot(BookSnapshot(token_id="no_today", bids=[BookLevel(price=0.72, size=10)], asks=[], timestamp="2026-05-13T18:00:00Z"))
        store.insert_book_snapshot(BookSnapshot(token_id="no_old", bids=[BookLevel(price=0.10, size=10)], asks=[], timestamp="2026-05-12T18:00:00Z"))
        store.insert_research_policy_position(
            ResearchPolicyPosition(
                timestamp="2026-05-13T17:00:00Z",
                policy_name="pm_us12_policy_today",
                station="KATL",
                market_date=date(2026, 5, 13),
                scope_key="station_date",
                model_group="model",
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                obs_delay_bucket="15m",
                selected_market_id="m_today",
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                entry_price=0.60,
                entry_edge=0.20,
                entry_fair=0.80,
                source_prediction_snapshot_ids=[],
                raw_policy={"name": "pm_us12_policy_today"},
            )
        )
        store.insert_research_policy_position(
            ResearchPolicyPosition(
                timestamp="2026-05-12T17:00:00Z",
                policy_name="pm_us12_policy_old",
                station="KDAL",
                market_date=date(2026, 5, 12),
                scope_key="station_date",
                model_group="model",
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                obs_delay_bucket="15m",
                selected_market_id="m_old",
                selected_side=TradeAction.BUY_NO,
                selected_bucket="80-81F",
                entry_price=0.50,
                entry_edge=0.10,
                entry_fair=0.70,
                source_prediction_snapshot_ids=[],
                raw_policy={"name": "pm_us12_policy_old"},
            )
        )

        rows = store.live_research_policy_positions(market_date=date(2026, 5, 13))

        assert store.latest_research_market_date() == "2026-05-13"
        assert len(rows) == 1
        assert rows[0]["policy_name"] == "pm_us12_policy_today"
        assert rows[0]["current_bid"] == pytest.approx(0.72)
        assert rows[0]["unrealized_pnl"] == pytest.approx(0.12)
        overview = store.research_status_overview(date(2026, 5, 13))
        assert overview["policy_positions_today"] == 1
        assert overview["market_stations"] == [
            {"station": "KATL", "markets": 1, "tokenized": 1, "min_low": 74.0, "max_bucket": 75.0}
        ]
    finally:
        store.close()


def test_latest_insights_returns_decoded_report_rows(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "insights.sqlite")
    try:
        store.connection.execute(
            """
            insert into hermes_insights (created_at, insight_type, target_date, severity, title, body, metrics_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-14T02:15:50Z",
                "policy_leaderboard",
                "2026-05-13",
                "info",
                "Policy Leaderboard",
                "body",
                '{"top_policy": "policy_a"}',
                '{"leaderboard": []}',
            ),
        )
        store.connection.commit()

        rows = store.latest_insights(limit=1)

        assert rows[0]["title"] == "Policy Leaderboard"
        assert rows[0]["metrics"]["top_policy"] == "policy_a"
        assert rows[0]["raw"]["leaderboard"] == []
    finally:
        store.close()


def test_bucket_label_handles_open_ended_buckets() -> None:
    assert _bucket_label(80, 81) == "80-81F"
    assert _bucket_label(86, None) == ">=86F"
    assert _bucket_label(None, 72) == "<=72F"


def test_policy_performance_summary_rolls_up_resolved_policy_silos(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "policy.sqlite")
    try:
        snap1 = PredictionSnapshot(
            timestamp="2026-05-11T18:00:00Z",
            station="KATL",
            market_date=date(2026, 5, 11),
            decision_time_utc="2026-05-11T18:00:00Z",
            decision_time_local="2026-05-11T11:00:00-07:00",
            latest_obs_time_utc="2026-05-11T17:45:00Z",
            latest_obs_time_local="2026-05-11T10:45:00-07:00",
            obs_age_minutes=15.0,
            obs_delay_bucket="15m",
            current_temp=75.0,
            high_so_far=74.0,
            hrrr_remaining_max=None,
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_market_id="m1",
            selected_bucket="74-75F",
            selected_side=TradeAction.BUY_NO,
            selected_edge=0.2,
            selected_fair_yes=0.2,
            selected_fair_no=0.8,
            selected_yes_ask=0.2,
            selected_no_ask=0.6,
            model_name="model_a",
            high_conviction=True,
            skip_reason=None,
            candidate_count=1,
            candidate_distribution=[],
        )
        snap1_id = store.insert_prediction_snapshot(snap1)
        assert snap1_id is not None
        store.upsert_prediction_result(
            PredictionResult(
                timestamp="2026-05-11T20:00:00Z",
                prediction_snapshot_id=snap1_id,
                station="KATL",
                market_date=date(2026, 5, 11),
                obs_delay_bucket="15m",
                selected_market_id="m1",
                selected_bucket="74-75F",
                selected_side=TradeAction.BUY_NO,
                final_high_tmpf=76.0,
                winning_side=TradeAction.BUY_NO,
                correct=True,
                entry_price=0.6,
                paper_pnl=0.4,
                edge=0.2,
                decision_time_local="2026-05-11T11:00:00-07:00",
                obs_age_minutes=15.0,
                resolved_at="2026-05-11T20:00:00Z",
            )
        )
        store.insert_research_policy_position(
            ResearchPolicyPosition(
                timestamp="2026-05-11T18:00:00Z",
                policy_name="policy_a",
                station="KATL",
                market_date=date(2026, 5, 11),
                scope_key="station_date",
                model_group="model_a_group",
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                obs_delay_bucket="15m",
                selected_market_id="m1",
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                entry_price=0.6,
                entry_edge=0.2,
                entry_fair=0.8,
                source_prediction_snapshot_ids=[snap1_id],
                raw_policy={"name": "policy_a"},
            )
        )

        snap2 = PredictionSnapshot(
            timestamp="2026-05-11T18:10:00Z",
            station="KDAL",
            market_date=date(2026, 5, 11),
            decision_time_utc="2026-05-11T18:10:00Z",
            decision_time_local="2026-05-11T11:10:00-07:00",
            latest_obs_time_utc="2026-05-11T17:55:00Z",
            latest_obs_time_local="2026-05-11T10:55:00-07:00",
            obs_age_minutes=15.0,
            obs_delay_bucket="15m",
            current_temp=81.0,
            high_so_far=80.0,
            hrrr_remaining_max=None,
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_market_id="m2",
            selected_bucket="80-81F",
            selected_side=TradeAction.BUY_YES,
            selected_edge=0.1,
            selected_fair_yes=0.7,
            selected_fair_no=0.3,
            selected_yes_ask=0.6,
            selected_no_ask=0.4,
            model_name="model_a",
            high_conviction=True,
            skip_reason=None,
            candidate_count=1,
            candidate_distribution=[],
        )
        snap2_id = store.insert_prediction_snapshot(snap2)
        assert snap2_id is not None
        store.upsert_prediction_result(
            PredictionResult(
                timestamp="2026-05-11T20:10:00Z",
                prediction_snapshot_id=snap2_id,
                station="KDAL",
                market_date=date(2026, 5, 11),
                obs_delay_bucket="15m",
                selected_market_id="m2",
                selected_bucket="80-81F",
                selected_side=TradeAction.BUY_YES,
                final_high_tmpf=79.0,
                winning_side=TradeAction.BUY_NO,
                correct=False,
                entry_price=0.6,
                paper_pnl=-0.6,
                edge=0.1,
                decision_time_local="2026-05-11T11:10:00-07:00",
                obs_age_minutes=15.0,
                resolved_at="2026-05-11T20:10:00Z",
            )
        )
        store.insert_research_policy_position(
            ResearchPolicyPosition(
                timestamp="2026-05-11T18:10:00Z",
                policy_name="policy_a",
                station="KDAL",
                market_date=date(2026, 5, 11),
                scope_key="station_date",
                model_group="model_a_group",
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                obs_delay_bucket="15m",
                selected_market_id="m2",
                selected_side=TradeAction.BUY_YES,
                selected_bucket="80-81F",
                entry_price=0.6,
                entry_edge=0.1,
                entry_fair=0.7,
                source_prediction_snapshot_ids=[snap2_id],
                raw_policy={"name": "policy_a"},
            )
        )

        rows = store.policy_performance_summary()
        assert len(rows) == 1
        row = rows[0]
        assert row["policy_name"] == "policy_a"
        assert row["model_group"] == "model_a_group"
        assert row["strategy_bucket"] == "HIGH_CONVICTION"
        assert row["obs_delay_bucket"] == "15m"
        assert row["resolved_positions"] == 2
        assert row["station_days"] == 2
        assert row["wins"] == 1
        assert row["hit_rate"] == pytest.approx(0.5)
        assert row["total_pnl"] == pytest.approx(-0.2)
        assert row["avg_entry"] == pytest.approx(0.6)
        assert row["avg_edge"] == pytest.approx(0.15)

        station_rows = store.policy_station_performance_summary()
        assert len(station_rows) == 2
        assert {row["station"] for row in station_rows} == {"KATL", "KDAL"}

        daily_rows = store.policy_daily_summary()
        assert len(daily_rows) == 1
        assert daily_rows[0]["market_date"] == "2026-05-11"
        assert daily_rows[0]["total_pnl"] == pytest.approx(-0.2)
    finally:
        store.close()


def _test_supervisor(tmp_path) -> ProcessSupervisor:
    return ProcessSupervisor(
        [
            ProcessSpec("research", "Research Loop", (sys.executable, "-c", "print('research')")),
            ProcessSpec("live", "Live Loop", (sys.executable, "-c", "print('live')")),
        ],
        cwd=tmp_path,
    )


def _insert_live_position(store: ExecutionStore, market_date: date) -> None:
    position_id = store.insert_live_policy_position(
        LivePolicyPosition(
            timestamp="2026-05-25T12:00:00Z",
            strategy_name=f"live-{market_date.isoformat()}",
            station="KATL",
            market_date=market_date,
            market_family=MarketFamily.HIGH_TEMP,
            scope_key=f"station_date:{market_date.isoformat()}",
            selected_market_id=f"market-{market_date.isoformat()}",
            selected_token_id=f"token-{market_date.isoformat()}",
            selected_side=TradeAction.BUY_NO,
            selected_bucket="72-73F",
            obs_delay_bucket="15m",
            entry_price=0.4,
            entry_fair=0.7,
            entry_edge=0.3,
            target_notional_usd=3.0,
            target_shares=7.5,
            state=LivePositionState.RESERVED,
            source_prediction_snapshot_ids=[1],
        )
    )
    assert position_id is not None


def test_tui_config_tab_renders_effective_live_sizing_values(tmp_path) -> None:
    async def scenario() -> None:
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=_test_supervisor(tmp_path))
        async with app.run_test(size=(140, 40)):
            config = app.query_one("#live-config", DataTable)
            text = "\n".join(" ".join(str(cell) for cell in config.get_row_at(index)) for index in range(config.row_count))
            assert "Sizing bankroll $2000.00" in text
            assert "Sizing base notional $10.00" in text
            assert "Risk caps total open $450.00" in text
            assert "Strategy tiers consensus multiplier 0.60x" in text
            assert "Price bands < 0.10 0.25x except moonshot" in text

    asyncio.run(scenario())


def test_tui_live_summary_and_positions_show_sizing_caps(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "tui.sqlite")
    try:
        position_id = store.insert_live_policy_position(
            LivePolicyPosition(
                timestamp="2026-05-25T12:00:00+00:00",
                strategy_name="live-sizing-test",
                station="KATL",
                market_date=date(2026, 5, 25),
                market_family=MarketFamily.HIGH_TEMP,
                scope_key="station_date_bucket_side_obs_delay:72-73F:BUY_NO:15m",
                selected_market_id="market-sizing",
                selected_token_id="token-sizing",
                selected_side=TradeAction.BUY_NO,
                selected_bucket="72-73F",
                obs_delay_bucket="15m",
                entry_price=0.4,
                entry_fair=0.9,
                entry_edge=0.5,
                target_notional_usd=4.5,
                target_shares=11.25,
                state=LivePositionState.RESERVED,
                source_prediction_snapshot_ids=[1],
                raw_json={
                    "limit_price": 0.4,
                    "sizing": {
                        "base_notional_usd": 10.0,
                        "policy_multiplier": 1.0,
                        "price_multiplier": 1.0,
                        "pre_cap_target_usd": 10.0,
                        "final_target_notional_usd": 4.5,
                        "blocked_reason": "RISK_TOTAL_OPEN_CAP",
                        "caps": {"RISK_TOTAL_OPEN_CAP": {"applied_usd": 4.5, "remaining_usd": 4.5}},
                    },
                },
            )
        )
        assert position_id is not None
        older_position_id = store.insert_live_policy_position(
            LivePolicyPosition(
                timestamp="2026-05-24T12:00:00+00:00",
                strategy_name="older-live-performance-test",
                station="KDAL",
                market_date=date(2026, 5, 24),
                market_family=MarketFamily.HIGH_TEMP,
                scope_key="older-station-date-bucket-side-obs-delay:80-81F:BUY_YES:15m",
                selected_market_id="older-market-sizing",
                selected_token_id="older-token-sizing",
                selected_side=TradeAction.BUY_YES,
                selected_bucket="80-81F",
                obs_delay_bucket="15m",
                entry_price=0.5,
                entry_fair=0.8,
                entry_edge=0.3,
                target_notional_usd=5.0,
                target_shares=10.0,
                state=LivePositionState.RESERVED,
                source_prediction_snapshot_ids=[2],
            )
        )
        assert older_position_id is not None
        store.update_live_policy_position_settlement(
            older_position_id,
            resolved_at="2026-05-24T20:00:00+00:00",
            resolution_source="iem",
            winning_token_id="older-token-sizing",
            winning_side=TradeAction.BUY_YES,
            settlement_value_usd=10.0,
            realized_pnl=5.0,
            realized_rr=1.0,
        )
    finally:
        store.close()

    async def scenario() -> None:
        app = RoboWeatherTUI(tmp_path / "tui.sqlite", process_supervisor=_test_supervisor(tmp_path))
        async with app.run_test(size=(160, 40)):
            summary = app.query_one("#live-summary", DataTable)
            summary_text = "\n".join(" ".join(str(cell) for cell in summary.get_row_at(index)) for index in range(summary.row_count))
            assert "open risk $4.50 / $450.00" in summary_text
            assert "largest station/date $4.50 / $125.00 KATL:2026-05-25" in summary_text
            contracts = app.query_one("#live-contracts", DataTable)
            contract_text = "\n".join(" ".join(str(cell) for cell in contracts.get_row_at(index)) for index in range(contracts.row_count))
            assert "KATL" in contract_text
            assert "$4.50" in contract_text

            app.query_one("#performance-tab", TabPane)
            performance_line = app.query_one("#live-performance-line", Static)
            performance_bars = app.query_one("#live-performance-bars", Static)
            performance_table = app.query_one("#live-performance-table", DataTable)
            assert "Cumulative PnL by day" in performance_line.content
            assert "Days" in performance_line.content
            assert "scale" in performance_line.content
            assert "Last 7 days by day" in performance_bars.content
            assert performance_table.row_count >= 2
            assert str(performance_table.get_row_at(0)[0]) == "2026-05-25"
            assert str(performance_table.get_row_at(1)[0]) == "2026-05-24"

            positions = app.query_one("#live-positions", DataTable)
            position_text = "\n".join(" ".join(str(cell) for cell in positions.get_row_at(index)) for index in range(positions.row_count))
            assert "$10.00" in position_text
            assert "RISK_TOTAL_OPEN_CAP" in position_text
            assert "p1.00 x px1.00" in position_text

    asyncio.run(scenario())
