from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from weather_trader.ui.dashboard_rollups import _build_live_policy_view, _build_policy_view, _build_position_view, _bucket_label
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    MarketSnapshot,
    PredictionResult,
    PredictionSnapshot,
    ResearchPolicyPosition,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.store import ExecutionStore


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
    assert view["policy_rows"][0]["live_rr"] == pytest.approx(2.2)
    assert view["policy_rows"][0]["live_minus_exp"] == pytest.approx(0.7)
    assert view["policy_rows"][1]["mtm"] == pytest.approx(0.16)
    assert view["rows"] == view["policy_rows"]
    assert view["position_rows"][0]["policy"] == "pm_us12_mvp_hc_15m_first"
    assert view["position_rows"][0]["live_minus_exp"] == pytest.approx((0.95 - 0.79) / 0.79 - (0.88 - 0.79) / 0.79)
    assert view["exposure_rows"][0]["fair"] == pytest.approx(0.25)
    assert view["exposure_rows"][0]["edge"] == pytest.approx(0.15)
    assert view["exposure_rows"][0]["expected_rr"] == pytest.approx(1.5)
    assert [row["station"] for row in view["policy_station_rows"]] == ["KDAL", "KATL"]


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

    view = _build_live_policy_view(live_rows)

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
            "high_so_far": 79.0,
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
    assert exposure["weather_high"] == 79.0
    assert exposure["weather_pnl"] == 0.0


def test_live_policy_view_handles_empty_input() -> None:
    view = _build_live_policy_view([])

    assert view["raw_count"] == 0
    assert view["unique_count"] == 0
    assert view["policy_rows"] == []
    assert view["rows"] == []


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
