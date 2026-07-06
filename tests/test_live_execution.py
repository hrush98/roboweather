from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import weather_trader.live.execution as live_execution
from weather_trader.execution.clob_executor import AllowanceCheck, CancelSubmission, OrderSubmission
from weather_trader.execution.clob_feed import record_clob_message
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    LiveOrderAttempt,
    LiveOrderMode,
    LivePositionState,
    LiveQuoteState,
    LiveStrategy,
    MarketFamily,
    MarketSnapshot,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.fair_value import FairValueResult
from weather_trader.execution.price_maker import PHASE1_PRICE_SHEET_VERSION, build_phase1_price_sheet
from weather_trader.execution.quote_engine import build_post_only_quote_intent, phase2_shadow_quote_specs
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
    LiveExecutionConfig,
    LiveExecutionEngine,
    LiveWeatherFeatureService,
    edge_core_policy_spec,
    default_live_strategy,
    hrrr_inland_disagreement_policy_spec,
    metar_hrrr_inland_disagreement_policy_spec,
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


def test_live_strategy_registry_deactivates_removed_strategies(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    store.upsert_live_strategy(default_live_strategy(max_notional_usd=3.0))
    store.upsert_live_strategy(
        LiveStrategy(
            name="old_dynamic_core",
            active=True,
            source="model",
            model_group="old_group",
            model_names=["old_model"],
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            market_family=MarketFamily.HIGH_TEMP,
            local_decision_start="12:00",
            local_decision_end="15:00",
            entry_price_min=0.05,
            uniqueness_key_mode="station_date_bucket_side_obs_delay",
            max_notional_usd=50.0,
        )
    )

    deactivated = store.deactivate_live_strategies_except({LIVE_POLICY_NAME})

    assert deactivated == 1
    active = store.live_strategies()
    all_rows = store.live_strategies(active_only=False)
    assert [row["name"] for row in active] == [LIVE_POLICY_NAME]
    assert {row["name"] for row in all_rows} == {LIVE_POLICY_NAME, "old_dynamic_core"}
    assert next(row for row in all_rows if row["name"] == "old_dynamic_core")["active"] == 0


def test_live_weather_feature_service_routes_global_stations_to_celsius_service() -> None:
    class RecordingService:
        def __init__(self, label: str) -> None:
            self.label = label
            self.calls: list[str] = []

        def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
            self.calls.append(station_id)
            return StationWeatherState(
                station=station_id,
                local_date=date(2026, 6, 9),
                latest_obs_time=as_of_utc.isoformat(),
                latest_obs_age_minutes=1.0,
                current_temp=20.0,
                high_so_far=21.0,
                low_so_far=19.0,
                hour_local=1,
                day_of_year=160,
                temp_change_1h=0.0,
                temp_change_3h=0.0,
                dewpoint=10.0,
                wind_speed=3.0,
                wind_dir_sin=0.0,
                wind_dir_cos=1.0,
                cloud_cover_code=0.0,
            )

    us_service = RecordingService("us")
    global_service = RecordingService("global")
    service = LiveWeatherFeatureService(us_service=us_service, global_service=global_service)
    as_of = datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc)

    service.get_state("KATL", as_of)
    service.get_state("RJTT", as_of)

    assert us_service.calls == ["KATL"]
    assert global_service.calls == ["RJTT"]


def test_live_execution_config_defaults_to_fifty_cent_entry_cap() -> None:
    assert LiveExecutionConfig().max_entry_price == pytest.approx(DEFAULT_LIVE_ENTRY_PRICE_MAX)
    assert LiveExecutionConfig().consensus_notional_usd == pytest.approx(50.0)
    assert LiveExecutionConfig().resting_fallback_ttl_seconds == pytest.approx(420.0)


def test_live_strategy_plans_include_promoted_high_temp_candidates() -> None:
    plans = live_strategy_plans(LiveExecutionConfig(max_entry_price=0.40))

    assert [plan.strategy.name for plan in plans] == [
        LIVE_POLICY_NAME,
        live_execution.METAR_HRRR_RICH_CATBOOST_MVP_POLICY_NAME,
        live_execution.HRRR_V2_THREE_MODEL_CONSENSUS_POLICY_NAME,
        MOONSHOT_POLICY_NAME,
        live_execution.HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        live_execution.METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        live_execution.GLOBAL_LOW_MVP_BUY_NO_POLICY_NAME,
    ]
    assert live_execution.GLOBAL_LOW_CANARY_POLICY_NAME not in {plan.strategy.name for plan in plans}
    consensus = plans[0]
    assert consensus.target_notional_usd == pytest.approx(CONSENSUS_NOTIONAL_USD)
    assert consensus.strategy.max_notional_usd == pytest.approx(CONSENSUS_NOTIONAL_USD)
    assert consensus.policies[0].entry_price_max == pytest.approx(0.40)

    metar_hrrr = plans[1]
    assert metar_hrrr.target_notional_usd == pytest.approx(50.0)
    assert metar_hrrr.strategy.max_notional_usd == pytest.approx(50.0)
    assert metar_hrrr.strategy.source == "consensus"
    assert metar_hrrr.strategy.market_family == MarketFamily.HIGH_TEMP
    assert len(metar_hrrr.policies) == 1
    assert metar_hrrr.policies[0].model_group == "metar_hrrr_rich_catboost_mvp"
    assert metar_hrrr.policies[0].entry_price_min == pytest.approx(0.05)
    assert metar_hrrr.policies[0].entry_price_max == pytest.approx(0.50)
    assert metar_hrrr.min_entry_price == pytest.approx(0.05)

    hrrr_v2 = plans[2]
    assert hrrr_v2.target_notional_usd == pytest.approx(50.0)
    assert hrrr_v2.strategy.max_notional_usd == pytest.approx(50.0)
    assert hrrr_v2.strategy.source == "consensus"
    assert hrrr_v2.strategy.market_family == MarketFamily.HIGH_TEMP
    assert len(hrrr_v2.policies) == 1
    assert hrrr_v2.policies[0].model_group == "hrrr_v2_three_model_consensus"
    assert hrrr_v2.policies[0].entry_price_min == pytest.approx(0.05)
    assert hrrr_v2.policies[0].entry_price_max == pytest.approx(0.50)
    assert hrrr_v2.min_entry_price == pytest.approx(0.05)

    moonshot = plans[3]
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

    hrrr = plans[4]
    assert hrrr.target_notional_usd == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD)
    assert hrrr.strategy.max_notional_usd == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD)
    assert hrrr.strategy.market_family == MarketFamily.HIGH_TEMP
    assert hrrr.strategy.source == "model"
    assert hrrr.strategy.local_decision_start == "12:00"
    assert hrrr.strategy.local_decision_end == "15:00"
    assert len(hrrr.policies) == 1
    assert hrrr.policies[0].model_name == live_execution.HRRR_RICH_DYNAMIC_TUNED_MODEL
    assert hrrr.policies[0].station_allow_set == live_execution.HRRR_INLAND_STATIONS
    assert hrrr.policies[0].entry_price_min == pytest.approx(0.0)
    assert hrrr.policies[0].entry_price_max == pytest.approx(live_execution.DEFAULT_LIVE_ENTRY_PRICE_MAX)
    assert hrrr.policies[0].edge_min == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_EDGE_MIN)
    assert hrrr.policies[0].hrrr_disagreement_min == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_MIN)
    assert hrrr.policies[0].obs_edge_max == pytest.approx(live_execution.HRRR_INLAND_OBS_EDGE_MAX)
    assert hrrr.min_entry_price == pytest.approx(0.0)

    metar = plans[5]
    assert metar.target_notional_usd == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD)
    assert metar.strategy.max_notional_usd == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD)
    assert metar.strategy.market_family == MarketFamily.HIGH_TEMP
    assert metar.strategy.source == "model"
    assert metar.strategy.local_decision_start == "12:00"
    assert metar.strategy.local_decision_end == "15:00"
    assert len(metar.policies) == 1
    assert metar.policies[0].model_name == live_execution.METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL
    assert metar.policies[0].station_allow_set == live_execution.HRRR_INLAND_STATIONS
    assert metar.policies[0].hrrr_disagreement_min == pytest.approx(live_execution.HRRR_INLAND_DISAGREEMENT_MIN)
    assert metar.min_entry_price == pytest.approx(0.0)





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



def test_hrrr_inland_disagreement_policy_spec_filters_inland_stations_and_disagreement(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    from weather_trader.research.policies import HRRR_RICH_DYNAMIC_TUNED_MODEL

    spec = hrrr_inland_disagreement_policy_spec()
    evaluator = ResearchPolicyEvaluator(store, (spec,))

    # Build obs consensus for HRRR disagreement lookup
    obs_consensus = evaluator._build_consensus([
        _snapshot(
            live_execution.DYNAMIC_TUNED_MODEL,
            id=1, station="KATL",
            selected_no_ask=0.20, selected_edge=0.70,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
        _snapshot(
            live_execution.CATBOOST_MODEL,
            id=2, station="KATL",
            selected_no_ask=0.20, selected_edge=0.70,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
    ])

    # Build HRRR snapshots manually to control fair values for disagreement testing
    hr_rows = [
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=10, station="KATL",
                       selected_no_ask=0.30, selected_edge=0.20,
                       selected_bucket="92-93F", selected_market_id="m2",
                       decision_time_local="2026-06-10T12:30:00-04:00"),
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=11, station="KATL",
                       selected_no_ask=0.10, selected_edge=0.30,
                       selected_bucket="94-95F", selected_market_id="m3",
                       decision_time_local="2026-06-10T12:30:00-04:00"),
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=12, station="KDAL",
                       selected_no_ask=0.15, selected_edge=0.35,
                       selected_bucket="96-97F", selected_market_id="m4",
                       decision_time_local="2026-06-10T12:30:00-05:00"),
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=13, station="KBOS",
                       selected_no_ask=0.10, selected_edge=0.40,
                       selected_bucket="80-81F", selected_market_id="m5",
                       decision_time_local="2026-06-10T12:30:00-04:00"),
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=14, station="KORD",
                       selected_no_ask=0.25, selected_edge=0.30,
                       selected_bucket="88-89F", selected_market_id="m6",
                       decision_time_local="2026-06-10T11:59:00-05:00"),
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=15, station="KORD",
                       selected_no_ask=0.51, selected_edge=0.29,
                       selected_bucket="90-91F", selected_market_id="m7",
                       decision_time_local="2026-06-10T12:30:00-05:00"),
        },
    ]

    filtered = evaluator._candidates_for_policy(spec, hr_rows, obs_consensus)
    selected = evaluator._first_by_scope(spec, filtered)

    # id=10: edge 0.20 < 0.25 -> filtered
    # id=11: KATL inland, edge 0.30 >= 0.25, entry 0.10 in [0,0.50], time ok,
    #        no obs baseline for 94-95F -> uses HRRR edge (fair - entry) >= 0.15 -> passes
    # id=12: KDAL inland, edge 0.35 >= 0.25 -> passes (no obs baseline)
    # id=13: KBOS NOT inland -> filtered by station_allow_set
    # id=14: KORD inland, edge 0.30 >= 0.25, but time 11:59 < 12:00 -> filtered
    # id=15: KORD inland, entry 0.51 > 0.50 -> filtered
    assert len(selected) == 2
    stations = {item["station"] for item in selected}
    assert stations == {"KATL", "KDAL"}


def test_metar_hrrr_inland_disagreement_policy_spec_filters_metar_model(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    from weather_trader.research.policies import METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL

    spec = metar_hrrr_inland_disagreement_policy_spec()
    evaluator = ResearchPolicyEvaluator(store, (spec,))

    rows = [
        _snapshot(
            METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
            id=1, station="KATL",
            selected_no_ask=0.20, selected_edge=0.30,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
        _snapshot(
            live_execution.HRRR_RICH_DYNAMIC_TUNED_MODEL,
            id=2, station="KATL",
            selected_no_ask=0.20, selected_edge=0.30,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
    ]

    filtered = evaluator._candidates_for_policy(spec, rows, [])
    selected = evaluator._first_by_scope(spec, filtered)

    assert len(selected) == 1
    assert selected[0]["model_name"] == METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL


def test_hrrr_disagreement_rejects_when_obs_has_strong_edge(tmp_path: Path) -> None:
    """When obs consensus has edge >= obs_edge_max (0.10), HRRR candidate is rejected."""
    store = ExecutionStore(tmp_path / "live.sqlite")
    from weather_trader.research.policies import HRRR_RICH_DYNAMIC_TUNED_MODEL

    spec = hrrr_inland_disagreement_policy_spec()
    evaluator = ResearchPolicyEvaluator(store, (spec,))

    # Build obs consensus with a strong edge on the same opportunity
    obs_consensus = evaluator._build_consensus([
        _snapshot(
            live_execution.DYNAMIC_TUNED_MODEL,
            id=1, station="KATL",
            selected_no_ask=0.50, selected_edge=0.35,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
        _snapshot(
            live_execution.CATBOOST_MODEL,
            id=2, station="KATL",
            selected_no_ask=0.50, selected_edge=0.35,
            selected_bucket="90-91F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-04:00",
        ),
    ])

    hr_rows = [
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=10, station="KATL",
                       selected_no_ask=0.40, selected_edge=0.50,
                       selected_bucket="90-91F", selected_market_id="m1",
                       decision_time_local="2026-06-10T12:30:00-04:00"),
        },
    ]

    filtered = evaluator._candidates_for_policy(spec, hr_rows, obs_consensus)
    assert len(filtered) == 0


def test_hrrr_disagreement_accepts_when_obs_is_absent_or_weak(tmp_path: Path) -> None:
    """No obs baseline: uses HRRR edge. Weak obs edge: checks fair disagreement."""
    store = ExecutionStore(tmp_path / "live.sqlite")
    from weather_trader.research.policies import HRRR_RICH_DYNAMIC_TUNED_MODEL

    spec = hrrr_inland_disagreement_policy_spec()
    evaluator = ResearchPolicyEvaluator(store, (spec,))

    # obs consensus with weak edge on this bucket
    obs_consensus = evaluator._build_consensus([
        _snapshot(
            live_execution.DYNAMIC_TUNED_MODEL,
            id=1, station="KDAL",
            selected_no_ask=0.45, selected_edge=0.05,
            selected_bucket="92-93F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-05:00",
        ),
        _snapshot(
            live_execution.CATBOOST_MODEL,
            id=2, station="KDAL",
            selected_no_ask=0.45, selected_edge=0.05,
            selected_bucket="92-93F", selected_market_id="m1",
            decision_time_local="2026-06-10T12:30:00-05:00",
        ),
    ])

    hr_rows = [
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=10, station="KDAL",
                       selected_no_ask=0.40, selected_edge=0.32,
                       selected_bucket="92-93F", selected_market_id="m1",
                       decision_time_local="2026-06-10T12:30:00-05:00"),
            "selected_fair_no": 0.72,
        },
        {
            **_snapshot(HRRR_RICH_DYNAMIC_TUNED_MODEL, id=11, station="KDAL",
                       selected_no_ask=0.40, selected_edge=0.30,
                       selected_bucket="94-95F", selected_market_id="m2",
                       decision_time_local="2026-06-10T12:30:00-05:00"),
        },
    ]

    filtered = evaluator._candidates_for_policy(spec, hr_rows, obs_consensus)
    selected = evaluator._first_by_scope(spec, filtered)

    # id=10: obs edge 0.05 < 0.10 -> not blocked; hrrr fair_no 0.72 - obs fair_no 0.9 = -0.18 < 0.15 -> rejected
    # id=11: no obs baseline for 94-95F; uses HRRR edge = fair - entry = 0.9 - 0.40 = 0.50 >= 0.15 -> passes
    assert len(selected) == 1
    assert selected[0]["selected_bucket"] == "94-95F"

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


def test_clob_feed_recorder_persists_price_change_rows_and_summary(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")

    ids = record_clob_message(
        store,
        channel="market",
        received_at="2026-05-20T16:30:01+00:00",
        message={
            "event_type": "price_change",
            "market": "condition-1",
            "timestamp": "1757908892351",
            "price_changes": [
                {
                    "asset_id": "no-token",
                    "price": "0.40",
                    "size": "100",
                    "side": "SELL",
                    "best_bid": "0.38",
                    "best_ask": "0.40",
                },
                {
                    "asset_id": "no-token",
                    "price": "0.40",
                    "size": "0",
                    "side": "SELL",
                    "best_bid": "0.37",
                    "best_ask": "0.41",
                },
            ],
        },
        live_candidate_id="model_abc",
    )

    assert len(ids) == 2
    row = store.connection.execute("select * from clob_feed_events where id = ?", (ids[0],)).fetchone()
    assert row["event_type"] == "price_change"
    assert row["token_id"] == "no-token"
    assert row["live_candidate_id"] == "model_abc"
    summary = store.clob_feed_summary("no-token", received_before="2026-05-20T16:31:00+00:00")
    assert summary["event_count"] == 2
    assert summary["top_level_add_count"] == 1
    assert summary["top_level_cancel_count"] == 1


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

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None, post_only: bool = False) -> OrderSubmission:
        raise AssertionError("unexpected GTC order")

    def cancel_order(self, order_id: str) -> CancelSubmission:
        raise AssertionError("unexpected cancel")


class StaticFairValueEngine:
    def __init__(self, model_name: str = NGBOOST_MODEL) -> None:
        self.model_name = model_name

    def supports_market_family(self, market_family: str | MarketFamily) -> bool:
        return str(market_family) in {str(MarketFamily.HIGH_TEMP), str(MarketFamily.LOW_TEMP)}

    def price_markets(self, markets: list[MarketSnapshot], weather: StationWeatherState) -> dict[str, FairValueResult]:
        fair_yes_by_market = {
            "market-1": 0.20,
            "market-2": 0.44,
            "market-3": 0.28,
            "low-market-1": 0.88,
            "low-market-2": 0.83,
        }
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


def test_live_build_candidates_persists_prefilter_universe_and_links_position(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    as_of = datetime(2026, 5, 20, 16, 30, tzinfo=timezone.utc)
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=store.path, model_paths=(), mode="dry-run", bucket_calibration_mode="off"),
        submitter=FakeSubmitter(),
    )
    engine.fair_value_engines = [StaticFairValueEngine(DYNAMIC_TUNED_MODEL), StaticFairValueEngine(CATBOOST_MODEL)]

    markets = [
        _candidate_market("market-1", 72, 73, "yes-token-1", "no-token-1"),
        _candidate_market("market-2", 74, 75, "yes-token-2", "no-token-2"),
        _candidate_market("market-3", 76, 77, "yes-token-3", "no-token-3"),
    ]
    books = {
        "yes-token-1": _book("yes-token-1", ask=0.80),
        "no-token-1": _book("no-token-1", ask=0.40),
        "yes-token-2": _book("yes-token-2", ask=0.80),
        "no-token-2": _book("no-token-2", ask=0.50),
        "yes-token-3": _book("yes-token-3", ask=0.80),
        "no-token-3": _book("no-token-3", ask=0.60),
    }
    weather = StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 20),
        latest_obs_time="2026-05-20T16:20:00+00:00",
        latest_obs_age_minutes=10.0,
        current_temp=72.0,
        high_so_far=72.0,
        low_so_far=68.0,
        hour_local=12,
        day_of_year=140,
        temp_change_1h=0.0,
        temp_change_3h=0.0,
        dewpoint=60.0,
        wind_speed=5.0,
        wind_dir_sin=0.0,
        wind_dir_cos=1.0,
        cloud_cover_code=0.0,
    )

    candidates = engine._build_candidates(markets, books, {"KATL": weather}, as_of, [], {})

    assert candidates
    model_rows = store.connection.execute(
        "select candidate_id, prediction_snapshot_id, quote_features_json from live_candidate_snapshots where source_stage = 'MODEL_SNAPSHOT'"
    ).fetchall()
    policy_rows = store.connection.execute(
        "select candidate_id, source_prediction_snapshot_ids from live_candidate_snapshots where source_stage = 'POLICY_CANDIDATE'"
    ).fetchall()
    assert len(model_rows) >= 2
    assert policy_rows
    assert all(row["prediction_snapshot_id"] is not None for row in model_rows)
    assert json.loads(model_rows[0]["quote_features_json"])["top_book_age_seconds"] == pytest.approx(0.0)
    price_sheet = store.connection.execute("select * from live_price_sheets order by id limit 1").fetchone()
    assert price_sheet is not None
    assert price_sheet["version"] == PHASE1_PRICE_SHEET_VERSION
    assert price_sheet["live_candidate_id"] in {row["candidate_id"] for row in policy_rows}
    assert price_sheet["selected_side"] == "BUY_NO"
    assert price_sheet["eligible"] == 1
    assert price_sheet["max_quote_price"] < price_sheet["calibrated_fair"]
    quote_rows = store.connection.execute("select * from live_quote_intents where live_candidate_id = ? order by id", (price_sheet["live_candidate_id"],)).fetchall()
    quote_intent = quote_rows[0] if quote_rows else None
    assert quote_intent is not None
    assert len(quote_rows) == len(phase2_shadow_quote_specs())
    assert {row["quote_spec_id"] for row in quote_rows} == {spec.quote_spec_id for spec in phase2_shadow_quote_specs()}
    assert quote_intent["live_candidate_id"] == price_sheet["live_candidate_id"]
    assert quote_intent["order_mode"] == "GTD"
    assert quote_intent["post_only"] == 1
    assert quote_intent["state"] == "SHADOW_POSTABLE"
    assert quote_intent["would_post"] == 1
    assert quote_intent["fair_source"] == "phase1_capped_haircut_fair"
    assert quote_intent["quote_price"] < 0.40

    candidate = next(item for item in candidates if item.live_candidate_id == policy_rows[0]["candidate_id"])
    assert candidate.position.raw_policy["phase1_price_sheet_id"] == price_sheet["id"]
    assert candidate.position.raw_policy["phase2_quote_intent_id"] == quote_intent["id"]
    assert len(candidate.position.raw_policy["shadow_quote_intent_ids"]) == len(phase2_shadow_quote_specs())
    assert len(candidate.position.raw_policy["shadow_quote_intents"]) == len(phase2_shadow_quote_specs())
    health = store.shadow_collection_health()
    assert health["unique_quote_specs"] == len(phase2_shadow_quote_specs())
    reconstruction = store.shadow_candidate_reconstruction(price_sheet["live_candidate_id"])
    assert reconstruction is not None
    assert len(reconstruction["quote_intents"]) == len(phase2_shadow_quote_specs())
    assert reconstruction["markout_windows"] == ["10s", "30s", "2m", "10m", "next_weather_update", "close", "settlement"]
    sizing = engine._size_candidate(candidate, as_of)
    market_by_id = {market.market_id: market for market in markets}
    position = engine._live_position(candidate, market_by_id[candidate.position.selected_market_id], reject_reason=None, sizing=sizing)
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None
    store.link_live_candidate_position(candidate.live_candidate_id, position_id)
    store.link_live_quote_position(candidate.live_candidate_id, position_id)
    engine._record_rejected(position_id, position, "TEST_REJECT")

    linked = store.connection.execute(
        """
        select lpp.live_candidate_id, loa.live_candidate_id, lcs.live_position_id, lqi.live_position_id quote_position_id
        from live_policy_positions lpp
        join live_order_attempts loa on loa.live_position_id = lpp.id
        join live_candidate_snapshots lcs on lcs.candidate_id = lpp.live_candidate_id
        join live_quote_intents lqi on lqi.live_candidate_id = lpp.live_candidate_id
        where lpp.id = ?
        """,
        (position_id,),
    ).fetchone()
    assert linked["live_candidate_id"] == candidate.live_candidate_id
    assert linked["live_position_id"] == position_id
    assert linked["quote_position_id"] == position_id


def test_phase1_price_sheet_caps_extreme_fair_and_applies_haircuts(tmp_path: Path) -> None:
    candidate = _calibration_candidate(target=50.0)
    source = candidate.position.__class__(
        **{
            **candidate.position.__dict__,
            "entry_fair": 0.99,
            "raw_policy": {
                "model_fairs": {
                    DYNAMIC_TUNED_MODEL: 0.99,
                    CATBOOST_MODEL: 0.97,
                },
                "model_bucket_calibration": {
                    DYNAMIC_TUNED_MODEL: {"decision": "TRADE"},
                    CATBOOST_MODEL: {"decision": "TRADE"},
                },
            },
            "selected_best_bid": 0.31,
            "selected_best_ask": 0.40,
            "selected_spread": 0.09,
        }
    )

    sheet = build_phase1_price_sheet(
        live_candidate_id="policy-test",
        strategy_name=LIVE_POLICY_NAME,
        policy_name=LIVE_POLICY_NAME,
        source=source,
        selected_token_id="no-token",
        quote_features={
            "best_bid": 0.31,
            "best_ask": 0.40,
            "spread": 0.09,
            "top_level_cancel_count_5m": 2,
            "recent_trade_count_5m": 3,
        },
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        target_notional_usd=50.0,
    )

    assert sheet.raw_model_fair == pytest.approx(0.98)
    assert sheet.calibrated_fair == pytest.approx(0.90)
    assert sheet.uncertainty_haircut > 0.04
    assert sheet.adverse_selection_haircut > 0.06
    assert sheet.min_required_edge >= 0.12
    assert sheet.max_quote_price <= 0.77
    assert sheet.quote_size_cap == pytest.approx(5.0)
    assert sheet.eligible is True


def test_phase1_price_sheet_marks_buy_yes_out_of_scope(tmp_path: Path) -> None:
    candidate = _calibration_candidate(target=50.0)
    source = candidate.position.__class__(
        **{
            **candidate.position.__dict__,
            "selected_side": TradeAction.BUY_YES,
            "entry_fair": 0.75,
            "raw_policy": {"model_fairs": {DYNAMIC_TUNED_MODEL: 0.75, CATBOOST_MODEL: 0.72}},
        }
    )

    sheet = build_phase1_price_sheet(
        live_candidate_id="policy-test-buy-yes",
        strategy_name=LIVE_POLICY_NAME,
        policy_name=LIVE_POLICY_NAME,
        source=source,
        selected_token_id="yes-token",
        quote_features={},
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        target_notional_usd=50.0,
    )

    assert sheet.eligible is False
    assert sheet.reject_reason == "PHASE1_SIDE_OUT_OF_SCOPE"


def test_phase2_quote_intent_clamps_below_ask_and_uses_gtd(tmp_path: Path) -> None:
    candidate = _calibration_candidate(target=50.0)
    source = candidate.position.__class__(
        **{
            **candidate.position.__dict__,
            "entry_fair": 0.99,
            "raw_policy": {"model_fairs": {DYNAMIC_TUNED_MODEL: 0.99, CATBOOST_MODEL: 0.97}},
        }
    )
    sheet = build_phase1_price_sheet(
        live_candidate_id="policy-test",
        strategy_name=LIVE_POLICY_NAME,
        policy_name=LIVE_POLICY_NAME,
        source=source,
        selected_token_id="no-token",
        quote_features={"best_bid": 0.30, "best_ask": 0.40, "spread": 0.10},
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        target_notional_usd=50.0,
    )

    quote = build_post_only_quote_intent(
        sheet=sheet,
        book=BookSnapshot(
            token_id="no-token",
            bids=[BookLevel(price=0.30, size=100.0)],
            asks=[BookLevel(price=0.40, size=100.0)],
            timestamp="2026-06-15T18:00:00+00:00",
        ),
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        min_quote_notional_usd=1.0,
    )

    assert quote.state == LiveQuoteState.SHADOW_POSTABLE
    assert quote.order_mode == LiveOrderMode.GTD
    assert quote.post_only is True
    assert quote.quote_price == pytest.approx(0.39)
    assert quote.quote_price < 0.40
    assert quote.quote_size_usd == pytest.approx(50.0)
    assert quote.quote_shares > 0.0
    assert quote.quote_spec_id is not None
    assert quote.fair_source == "phase1_capped_haircut_fair"
    assert quote.quote_rule == "min(max_quote_price,best_ask-1c)"
    assert quote.cancel_rule == "ttl_or_fair_book_cross_or_stale_feed"
    assert quote.would_post is True
    assert quote.raw_json["markout_hooks"]["windows"] == ["10s", "30s", "2m", "10m", "next_weather_update", "close", "settlement"]
    assert quote.raw_json["initial_depth_context"]["quote_size_usd"] == pytest.approx(50.0)


def test_shadow_quote_reconcile_expires_open_quote(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), mode="dry-run"))
    candidate = _calibration_candidate(target=50.0)
    sheet = build_phase1_price_sheet(
        live_candidate_id="policy-expiring",
        strategy_name=LIVE_POLICY_NAME,
        policy_name=LIVE_POLICY_NAME,
        source=candidate.position,
        selected_token_id="no-token",
        quote_features={"best_bid": 0.30, "best_ask": 0.40, "spread": 0.10},
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        target_notional_usd=50.0,
    )
    quote = build_post_only_quote_intent(
        sheet=sheet,
        book=BookSnapshot(
            token_id="no-token",
            bids=[BookLevel(price=0.30, size=100.0)],
            asks=[BookLevel(price=0.40, size=100.0)],
            timestamp="2026-06-15T18:00:00+00:00",
        ),
        as_of_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
        min_quote_notional_usd=1.0,
    )
    store.insert_live_quote_intent(quote)

    counts = engine._reconcile_shadow_quotes(
        {"no-token": BookSnapshot(token_id="no-token", bids=[BookLevel(price=0.30, size=100.0)], asks=[BookLevel(price=0.40, size=100.0)], timestamp="2026-06-15T18:03:00+00:00")},
        datetime(2026, 6, 15, 18, 3, tzinfo=timezone.utc),
    )

    assert counts == {"fair_valid_until": 1}
    row = store.connection.execute("select state, cancel_reason, raw_json from live_quote_intents where quote_id = ?", (quote.quote_id,)).fetchone()
    assert row["state"] == "SHADOW_EXPIRED"
    assert row["cancel_reason"] == "fair_valid_until"
    assert json.loads(row["raw_json"])["state"] == "SHADOW_EXPIRED"


def _candidate_market(
    market_id: str,
    lower_f: int,
    upper_f: int,
    yes_token_id: str,
    no_token_id: str,
    *,
    station: str = "KATL",
    city: str = "Atlanta",
    market_family: MarketFamily = MarketFamily.HIGH_TEMP,
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id=None,
        question="q",
        slug=market_id,
        city=city,
        station=station,
        market_date=date(2026, 5, 20),
        lower_f=lower_f,
        upper_f=upper_f,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        end_date="2026-05-21T00:00:00Z",
        resolution_source="test",
        discovered_at="2026-05-20T16:00:00+00:00",
        market_family=market_family,
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
    station: str = "KATL",
    market_family: str = "HIGH_TEMP",
) -> dict:
    return {
        "id": id,
        "timestamp": f"2026-05-20T16:30:{id:02d}+00:00",
        "station": station,
        "market_date": "2026-05-20",
        "market_family": market_family,
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
    plan = live_execution.LiveStrategyPlan(
        live_execution.edge_core_live_strategy(EDGE_CORE_NOTIONAL_USD),
        (edge_core_policy_spec(LiveExecutionConfig()),),
        EDGE_CORE_NOTIONAL_USD,
        None,
        0.0,
    )
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


def test_insufficient_sweep_depth_sizes_for_resting_ladder(tmp_path: Path) -> None:
    from weather_trader.execution.contracts import ResearchPolicyPosition

    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live"))
    plan = live_execution.LiveStrategyPlan(
        live_execution.edge_core_live_strategy(EDGE_CORE_NOTIONAL_USD),
        (edge_core_policy_spec(LiveExecutionConfig()),),
        EDGE_CORE_NOTIONAL_USD,
        None,
        0.0,
    )
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
        selected_sweep_depth_to_cap=0.0,
        selected_depth_ask_plus_0_01=0.0,
        selected_book_age_seconds=1.0,
    )

    sizing = engine._size_candidate(live_execution.LiveCandidate(plan, source), as_of_utc=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc))

    assert sizing.blocked_reason is None
    assert sizing.target_notional_usd == pytest.approx(EDGE_CORE_NOTIONAL_USD)
    assert sizing.raw_json["resting_ladder_on_insufficient_depth"] is True
    assert sizing.raw_json["resting_ladder_source_reason"] == "INSUFFICIENT_DEPTH"
    assert sizing.raw_json["depth_limited_sweep_sizing"]["blocked_reason"] == "INSUFFICIENT_DEPTH"


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
        self.fak_calls: list[tuple[str, str, float, float]] = []
        self.gtc_calls: list[tuple[str, str, float, float]] = []
        self.gtc_post_only: list[bool] = []
        self.get_order_calls: list[str] = []
        self.cancel_calls: list[str] = []

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.fak_calls.append((token_id, side, price, amount))
        return OrderSubmission(False, None, "rejected", "insufficient depth", {"success": False, "status": "rejected", "errorMsg": "insufficient depth"})

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None, post_only: bool = False) -> OrderSubmission:
        self.gtc_post_only.append(post_only)
        self.gtc_calls.append((token_id, side, price, amount))
        order_id = f"gtc-{len(self.gtc_calls)}"
        return OrderSubmission(True, order_id, "live", None, {"success": True, "status": "live", "orderId": order_id})

    def get_order(self, order_id: str) -> dict[str, object]:
        self.get_order_calls.append(order_id)
        return self.after_ttl

    def cancel_order(self, order_id: str) -> CancelSubmission:
        self.cancel_calls.append(order_id)
        return CancelSubmission(True, [order_id], None, None, {"canceled": [order_id]})


class DepthLimitedFakThenRestingSubmitter(RestingFallbackSubmitter):
    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        self.fak_calls.append((token_id, side, price, amount))
        order_id = f"fak-{len(self.fak_calls)}"
        shares = amount / price
        return OrderSubmission(
            True,
            order_id,
            "matched",
            None,
            {"success": True, "status": "matched", "orderID": order_id, "makingAmount": str(amount), "takingAmount": str(shares)},
        )


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
        assert amount == pytest.approx(1.04)
        return OrderSubmission(
            True,
            "order-2",
            "matched",
            None,
            {"success": True, "status": "matched", "makingAmount": "1.04", "takingAmount": "2.6"},
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
        self.gtc_post_only: list[bool] = []
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

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None, post_only: bool = False) -> OrderSubmission:
        self.gtc_post_only.append(post_only)
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
    book_client = StaticBookClient(_retry_book(price=0.40, size=2.6))
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0, enable_resting_fallback=False),
        book_client=book_client,
        submitter=submitter,
    )
    position = _live_position().__class__(**{**_live_position().__dict__, "raw_json": {"limit_price": 0.55}})
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.PARTIAL
    assert sleeps == [5.0]
    assert submitter.place_calls == pytest.approx([3.0, 1.04])
    assert submitter.get_order_calls == ["order-1"]
    assert book_client.calls == [["no-token"]]
    attempts = store.connection.execute("select attempt_seq, external_order_id, target_notional_usd, final_state, raw_payload from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["external_order_id"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "order-1", 3.0, "SUBMITTED"),
        (2, "order-2", 1.04, "FILLED"),
    ]
    first_payload = json.loads(attempts[0]["raw_payload"])["raw_payload"]
    retry_payload = json.loads(attempts[1]["raw_payload"])["raw_payload"]
    assert first_payload["execution"]["attempt_label"] == "initial"
    assert retry_payload["execution"]["attempt_label"] == "retry"
    assert retry_payload["execution"]["retry"]["wait_seconds"] == 5.0
    assert retry_payload["execution"]["retry"]["first_attempt_order_id"] == "order-1"
    assert retry_payload["execution"]["retry"]["retry_book_timestamp"] == "2026-05-20T18:00:05+00:00"
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["cost_usd"] == pytest.approx(1.04)
    assert row["filled_shares"] == pytest.approx(2.6)

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
    assert sleeps == [5.0, 420.0]
    assert len(submitter.place_calls) == 2
    assert submitter.place_calls[0] == pytest.approx(3.0)
    assert submitter.place_calls[1] > 0
    assert submitter.get_order_calls == ["order-1", "gtc-1", "gtc-1", "gtc-1", "gtc-1"]
    assert submitter.cancel_calls == ["gtc-1", "gtc-1", "gtc-1", "gtc-1"]
    assert len(submitter.gtc_calls) == 4
    assert submitter.gtc_calls[0][0] == "no-token"
    assert submitter.gtc_calls[0][1] == "BUY"
    assert submitter.gtc_calls[0][3] == pytest.approx(0.84)
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
        (4, "GTC", "PARTIAL"),
        (5, "GTC", "PARTIAL"),
        (6, "GTC", "PARTIAL"),
    ]
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["cost_usd"] == pytest.approx(0.76)




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
    assert sleeps == [5.0, 420.0]
    assert len(submitter.place_calls) == 2
    assert submitter.get_order_calls == ["order-1", "gtc-1", "gtc-1", "gtc-1", "gtc-1"]
    assert submitter.cancel_calls == ["gtc-1", "gtc-1", "gtc-1", "gtc-1"]
    assert len(submitter.gtc_calls) == 4
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
        (4, "GTC", "PARTIAL"),
        (5, "GTC", "PARTIAL"),
        (6, "GTC", "PARTIAL"),
    ]
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["cost_usd"] == pytest.approx(1.56)



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
    assert sleeps == [5.0, 420.0]
    assert submitter.get_order_calls == ["order-1", "gtc-1", "gtc-1", "gtc-1", "gtc-1"]
    assert len(submitter.gtc_calls) == 4
    attempts = store.connection.execute("select attempt_seq, order_mode, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["final_state"]) for row in attempts] == [
        (1, "FAK", "PARTIAL"),
        (2, "FAK", "PARTIAL"),
        (3, "GTC", "PARTIAL"),
        (4, "GTC", "PARTIAL"),
        (5, "GTC", "PARTIAL"),
        (6, "GTC", "PARTIAL"),
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


def test_depth_limited_initial_fak_continues_into_retry_and_resting_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = DepthLimitedFakThenRestingSubmitter(after_ttl={"success": True, "status": "live"})
    retry_book = BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.33, size=100.0)],
        asks=[BookLevel(price=0.42, size=12.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    )
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setenv("LIVE_MIN_ORDER_NOTIONAL", "0.01")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", retry_wait_seconds=5.0, resting_fallback_ttl_seconds=60.0),
        book_client=StaticBookClient(retry_book),
        submitter=submitter,
    )
    base_position = _live_position()
    position = base_position.__class__(
        **{
            **base_position.__dict__,
            "target_notional_usd": 100.0,
            "target_shares": 250.0,
            "raw_json": {
                "limit_price": 0.5,
                "sizing": {
                    "initial_fak_notional_usd": 13.5,
                    "resting_ladder_after_depth_limited_fak": True,
                    "resting_ladder_source_reason": "INSUFFICIENT_DEPTH",
                },
            },
        }
    )
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=retry_book)

    assert state == LivePositionState.PARTIAL
    assert sleeps == [5.0, 60.0]
    assert submitter.fak_calls == pytest.approx([
        ("no-token", "BUY", 0.41, 13.5),
    ])
    assert submitter.gtc_calls == pytest.approx([
        ("no-token", "BUY", 0.41, 25.95),
        ("no-token", "BUY", 0.4, 34.6),
        ("no-token", "BUY", 0.39, 17.3),
        ("no-token", "BUY", 0.38, 8.65),
    ])
    assert submitter.gtc_post_only == [False, True, True, True]
    attempts = store.connection.execute("select attempt_seq, order_mode, target_notional_usd, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "FAK", 13.5, "FILLED"),
        (2, "GTC", 25.95, "CANCELLED"),
        (3, "GTC", 34.6, "CANCELLED"),
        (4, "GTC", 17.3, "CANCELLED"),
        (5, "GTC", 8.65, "CANCELLED"),
    ]
    row = store.live_open_positions()[0]
    assert row["state"] == "PARTIAL"
    assert row["target_notional_usd"] == pytest.approx(100.0)
    assert row["cost_usd"] == pytest.approx(13.5)


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
    assert submitter.gtc_calls == [("no-token", "BUY", 0.4, 1.2)]
    assert submitter.gtc_post_only == [True]
    assert submitter.get_order_calls == ["gtc-1"]
    assert submitter.cancel_calls == ["gtc-1"]
    attempts = store.connection.execute("select attempt_seq, order_mode, external_order_id, limit_price, target_notional_usd, final_state, raw_payload from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["external_order_id"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "FAK", None, 3.0, "REJECTED"),
        (2, "GTC", "gtc-1", 1.2, "CANCELLED"),
    ]
    resting_payload = json.loads(attempts[1]["raw_payload"])["raw_payload"]["execution"]["resting_fallback"]
    assert attempts[1]["limit_price"] == pytest.approx(0.4)
    assert resting_payload["ttl_seconds"] == pytest.approx(60.0)
    assert resting_payload["notional_fraction"] == pytest.approx(1.0)
    assert resting_payload["best_bid"] == pytest.approx(0.33)
    assert resting_payload["best_ask"] == pytest.approx(0.42)
    assert resting_payload["cancel_response"] == {"canceled": ["gtc-1"]}


def test_resting_fallback_ladders_large_remainder_into_twenty_five_dollar_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    base_position = _live_position()
    position = base_position.__class__(**{**base_position.__dict__, "target_notional_usd": 70.0, "target_shares": 175.0})
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.REJECTED
    assert sleeps == [60.0]
    assert submitter.gtc_calls == [
        ("no-token", "BUY", 0.41, 21.0),
        ("no-token", "BUY", 0.4, 28.0),
        ("no-token", "BUY", 0.39, 14.0),
        ("no-token", "BUY", 0.38, 7.0),
    ]
    assert submitter.gtc_post_only == [False, True, True, True]
    assert submitter.get_order_calls == ["gtc-1", "gtc-2", "gtc-3", "gtc-4"]
    assert submitter.cancel_calls == ["gtc-1", "gtc-2", "gtc-3", "gtc-4"]
    attempts = store.connection.execute("select attempt_seq, order_mode, external_order_id, limit_price, target_notional_usd, final_state, raw_payload from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["external_order_id"], row["limit_price"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "FAK", None, 0.41, 70.0, "REJECTED"),
        (2, "GTC", "gtc-1", 0.41, 21.0, "CANCELLED"),
        (3, "GTC", "gtc-2", 0.4, 28.0, "CANCELLED"),
        (4, "GTC", "gtc-3", 0.39, 14.0, "CANCELLED"),
        (5, "GTC", "gtc-4", 0.38, 7.0, "CANCELLED"),
    ]
    payload = json.loads(attempts[1]["raw_payload"])["raw_payload"]["execution"]["resting_fallback"]
    assert payload["chunk_usd"] == pytest.approx(25.0)
    assert payload["price_step"] == pytest.approx(0.01)
    assert payload["orders"] == [
        {"child_index": 1.0, "limit_price": 0.41, "target_notional_usd": 21.0, "ladder_offset_cents": 1.0, "post_only": False},
        {"child_index": 2.0, "limit_price": 0.4, "target_notional_usd": 28.0, "ladder_offset_cents": 0.0, "post_only": True},
        {"child_index": 3.0, "limit_price": 0.39, "target_notional_usd": 14.0, "ladder_offset_cents": -1.0, "post_only": True},
        {"child_index": 4.0, "limit_price": 0.38, "target_notional_usd": 7.0, "ladder_offset_cents": -2.0, "post_only": True},
    ]


def test_resting_ladder_splits_sixty_dollar_remainder(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(model_paths=(), mode="live"))
    base_position = _live_position()
    position = base_position.__class__(**{**base_position.__dict__, "target_notional_usd": 60.0, "target_shares": 150.0})
    book = BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.33, size=100.0)],
        asks=[BookLevel(price=0.42, size=100.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    )

    orders, reason = engine._resting_ladder_orders(position, book, current_cost_usd=0.0)

    assert reason is None
    assert orders == [
        {"child_index": 1.0, "limit_price": 0.41, "target_notional_usd": 18.0, "ladder_offset_cents": 1.0, "post_only": False},
        {"child_index": 2.0, "limit_price": 0.4, "target_notional_usd": 24.0, "ladder_offset_cents": 0.0, "post_only": True},
        {"child_index": 3.0, "limit_price": 0.39, "target_notional_usd": 12.0, "ladder_offset_cents": -1.0, "post_only": True},
        {"child_index": 4.0, "limit_price": 0.38, "target_notional_usd": 6.0, "ladder_offset_cents": -2.0, "post_only": True},
    ]


def test_insufficient_depth_position_skips_fak_and_posts_resting_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = RestingFallbackSubmitter(after_ttl={"success": True, "status": "live"})
    book = BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.33, size=100.0)],
        asks=[BookLevel(price=0.42, size=100.0)],
        timestamp="2026-05-20T18:00:06+00:00",
    )
    book_client = StaticBookClient(book)
    sleeps: list[float] = []
    monkeypatch.setattr(live_execution.time, "sleep", lambda seconds: sleeps.append(seconds))
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(live_db_path=tmp_path / "live.sqlite", model_paths=(), mode="live", resting_fallback_ttl_seconds=60.0),
        book_client=book_client,
        submitter=submitter,
    )
    base_position = _live_position()
    position = base_position.__class__(
        **{
            **base_position.__dict__,
            "target_notional_usd": 60.0,
            "target_shares": 150.0,
            "raw_json": {
                "limit_price": 0.4,
                "sizing": {
                    "resting_ladder_on_insufficient_depth": True,
                    "resting_ladder_source_reason": "INSUFFICIENT_DEPTH",
                },
            },
        }
    )
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=book)

    assert state == LivePositionState.REJECTED
    assert sleeps == [60.0]
    assert book_client.calls == []
    assert submitter.fak_calls == []
    assert submitter.gtc_calls == [
        ("no-token", "BUY", 0.41, 18.0),
        ("no-token", "BUY", 0.4, 24.0),
        ("no-token", "BUY", 0.39, 12.0),
        ("no-token", "BUY", 0.38, 6.0),
    ]
    assert submitter.gtc_post_only == [False, True, True, True]
    assert submitter.get_order_calls == ["gtc-1", "gtc-2", "gtc-3", "gtc-4"]
    assert submitter.cancel_calls == ["gtc-1", "gtc-2", "gtc-3", "gtc-4"]
    attempts = store.connection.execute("select attempt_seq, order_mode, external_order_id, limit_price, target_notional_usd, final_state from live_order_attempts order by attempt_seq").fetchall()
    assert [(row["attempt_seq"], row["order_mode"], row["external_order_id"], row["limit_price"], row["target_notional_usd"], row["final_state"]) for row in attempts] == [
        (1, "GTC", "gtc-1", 0.41, 18.0, "CANCELLED"),
        (2, "GTC", "gtc-2", 0.4, 24.0, "CANCELLED"),
        (3, "GTC", "gtc-3", 0.39, 12.0, "CANCELLED"),
        (4, "GTC", "gtc-4", 0.38, 6.0, "CANCELLED"),
    ]
    row = store.connection.execute("select state, raw_json from live_policy_positions where id = ?", (position_id,)).fetchone()
    assert row["state"] == "REJECTED"
    assert "RESTING_TTL_EXPIRED" in row["raw_json"]
    assert "RESTING_LADDER_SKIPPED_AFTER_INSUFFICIENT_DEPTH" not in row["raw_json"]


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
    base_position = _live_position()
    position = base_position.__class__(**{**base_position.__dict__, "target_notional_usd": 10.0, "target_shares": 25.0})
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None

    state = engine._submit(position_id, position, market=_retry_market(), initial_book=_retry_book())

    assert state == LivePositionState.FILLED
    assert sleeps == [60.0]
    row = store.live_open_positions()[0]
    assert row["state"] == "FILLED"
    assert row["cost_usd"] >= 10.0
    attempt = store.connection.execute("select order_mode, final_state from live_order_attempts order by attempt_seq desc limit 1").fetchone()
    assert attempt["order_mode"] == "GTC"


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
    assert limit_price == pytest.approx(0.4)
    assert target_notional == pytest.approx(3.0)


def test_retry_order_parameters_respect_entry_anchored_cap(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), mode="live"))
    position = _live_position().__class__(
        **{
            **_live_position().__dict__,
            "entry_price": 0.49,
            "entry_fair": 0.8771,
            "target_notional_usd": 50.0,
            "raw_json": {"limit_price": 0.54},
        }
    )
    book = BookSnapshot(
        token_id="no-token",
        bids=[BookLevel(price=0.45, size=100.0)],
        asks=[
            BookLevel(price=0.67, size=40.0),
            BookLevel(price=0.72, size=40.0),
        ],
        timestamp="2026-06-14T17:49:05+00:00",
    )

    limit_price, target_notional, reason = engine._retry_order_parameters(position, book, current_cost_usd=10.2)

    assert limit_price == pytest.approx(0.5)
    assert target_notional == pytest.approx(0.0)
    assert reason == "ASK_ABOVE_ENTRY_CAP"


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


def _write_calibration_file(
    tmp_path: Path,
    *,
    decision: str = "BLOCK",
    station: str = "KATL",
    side: str = "BUY_NO",
    band: str = "0.35-0.45",
    family: str = "obs",
) -> Path:
    path = tmp_path / f"calibration-{family}-{decision}-{station}.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-06-15T12:00:00Z",
                "families": {
                    family: {
                        "label": "Obs",
                        "market_dates": 2,
                        "buckets": {
                            station: {
                                side: {
                                    band: {
                                        "decision": decision,
                                        "n": 6,
                                        "avg_rr": -0.2 if decision == "BLOCK" else 0.05,
                                        "win_pct": 50.0,
                                        "market_dates": 2,
                                    }
                                }
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _calibration_candidate(*, entry_price: float = 0.40, station: str = "KATL", strategy_name: str = LIVE_POLICY_NAME, target: float = 50.0):
    from weather_trader.execution.contracts import ResearchPolicyPosition

    strategy = default_live_strategy(target) if strategy_name == LIVE_POLICY_NAME else LiveStrategy(
        name=strategy_name,
        active=True,
        source="model",
        model_group="test",
        model_names=["test"],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.0,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=target,
    )
    plan = live_execution.LiveStrategyPlan(
        strategy,
        (live_policy_spec(LiveExecutionConfig()),),
        target,
        None,
        0.0,
    )
    source = ResearchPolicyPosition(
        timestamp="2026-06-15T18:00:00+00:00",
        policy_name=strategy_name,
        station=station,
        market_date=date(2026, 6, 15),
        scope_key="station_date_bucket_side_obs_delay:72-73F:BUY_NO:15m",
        model_group=LIVE_MODEL_GROUP,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        obs_delay_bucket="15m",
        selected_market_id="market-1",
        selected_side=TradeAction.BUY_NO,
        selected_bucket="72-73F",
        entry_price=entry_price,
        entry_edge=0.5,
        entry_fair=0.9,
        source_prediction_snapshot_ids=[1, 2],
        raw_policy={},
        selected_sweep_price_cap=entry_price,
        selected_sweep_depth_to_cap=100.0,
        selected_depth_ask_plus_0_01=100.0,
        selected_book_age_seconds=1.0,
    )
    return live_execution.LiveCandidate(plan, source)


def test_bad_legacy_calibration_json_is_ignored_when_bucket_calibration_applies(tmp_path: Path) -> None:
    path = tmp_path / "bad-calibration.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    store = ExecutionStore(tmp_path / "live.sqlite")

    LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=path, bucket_calibration_mode="apply"))


def test_bad_legacy_calibration_json_fails_engine_startup_when_bucket_calibration_off(tmp_path: Path) -> None:
    path = tmp_path / "bad-calibration.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    store = ExecutionStore(tmp_path / "live.sqlite")

    with pytest.raises(ValueError):
        LiveExecutionEngine(
            store,
            LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=path, bucket_calibration_mode="off"),
        )


def test_no_calibration_path_leaves_candidate_unchanged(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=()))
    candidate = _calibration_candidate(target=50.0)

    decision = engine._apply_calibration(candidate)

    assert decision.reject_reason is None
    assert decision.candidate.plan.target_notional_usd == pytest.approx(50.0)
    assert decision.metadata["enabled"] is False
    assert decision.metadata["reason"] == "disabled_by_bucket_calibration"
    assert decision.metadata["decision"] == "DISABLED"
    assert decision.metadata["calibration_effect"] == "DISABLED_BY_BUCKET_CALIBRATION"


def test_bucket_calibration_apply_disables_legacy_blocker(tmp_path: Path) -> None:
    path = _write_calibration_file(tmp_path, decision="BLOCK")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=path))

    decision = engine._apply_calibration(_calibration_candidate())

    assert decision.reject_reason is None
    assert decision.metadata["reason"] == "disabled_by_bucket_calibration"
    assert decision.metadata["legacy_calibration_path_ignored"] == str(path)


def test_live_position_payload_preserves_candidate_bucket_calibration_metadata(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=()))
    candidate = _calibration_candidate()
    source = candidate.position.__class__(
        **{
            **candidate.position.__dict__,
            "raw_policy": {
                "model_bucket_calibration": {
                    DYNAMIC_TUNED_MODEL: {"fit_scope": "model_station", "fit_n": 500},
                    CATBOOST_MODEL: {"fit_scope": "model_global", "fit_n": 1000},
                }
            },
        }
    )
    candidate = live_execution.LiveCandidate(candidate.plan, source)
    sizing = engine._size_candidate(candidate, datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc))

    position = engine._live_position(
        candidate,
        _candidate_market("market-1", 72, 73, "yes-token", "no-token"),
        reject_reason=None,
        sizing=sizing,
        calibration={"enabled": False, "reason": "disabled_by_bucket_calibration"},
    )

    raw_policy = position.raw_json["candidate"]["raw_policy"]
    assert raw_policy["model_bucket_calibration"][DYNAMIC_TUNED_MODEL]["fit_scope"] == "model_station"


def test_calibration_block_records_rejected_position_and_attempt_metadata(tmp_path: Path) -> None:
    path = _write_calibration_file(tmp_path, decision="BLOCK")
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=path, bucket_calibration_mode="off"))
    candidate = _calibration_candidate()

    decision = engine._apply_calibration(candidate)
    sizing = engine._size_candidate(decision.candidate, datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc))
    position = engine._live_position(
        decision.candidate,
        _candidate_market("market-1", 72, 73, "yes-token", "no-token"),
        reject_reason=decision.reject_reason,
        sizing=sizing,
        calibration=decision.metadata,
    )
    position_id = store.insert_live_policy_position(position)
    assert position_id is not None
    engine._record_rejected(position_id, position, decision.reject_reason or "")

    row = store.connection.execute("select raw_json from live_policy_positions where id = ?", (position_id,)).fetchone()
    raw = json.loads(row["raw_json"])
    assert raw["calibration"]["decision"] == "BLOCK"
    assert raw["calibration"]["reason"] == "bucket_match"
    assert raw["calibration"]["calibration_mode"] == "trade_blocker"
    assert raw["calibration"]["calibration_effect"] == "BLOCK"
    attempt = store.connection.execute("select final_reason, raw_payload from live_order_attempts").fetchone()
    assert attempt["final_reason"] == "CALIBRATION_BLOCK"
    assert json.loads(attempt["raw_payload"])["raw_payload"]["calibration"]["decision"] == "BLOCK"


@pytest.mark.parametrize("decision", ["CANARY", "WATCH", "INSUFFICIENT_DATA"])
def test_trade_blocker_rejects_non_trade_decisions_without_resizing(tmp_path: Path, decision: str) -> None:
    path = _write_calibration_file(tmp_path, decision=decision)
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(
            live_db_path=store.path,
            model_paths=(),
            calibration_path=path,
            calibration_canary_notional_usd=4.0,
            bucket_calibration_mode="off",
        ),
    )
    candidate = _calibration_candidate(target=50.0)

    decision = engine._apply_calibration(candidate)

    assert decision.reject_reason == "CALIBRATION_BLOCK"
    assert decision.candidate.plan.target_notional_usd == pytest.approx(50.0)
    assert "calibration_target_notional_before" not in decision.metadata
    assert "calibration_target_notional_after" not in decision.metadata
    assert decision.metadata["calibration_mode"] == "trade_blocker"
    assert decision.metadata["calibration_effect"] == "BLOCK"


def test_trade_blocker_allows_trade_decision(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    trade_path = _write_calibration_file(tmp_path, decision="TRADE", station="KLAX")
    trade_engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=trade_path, bucket_calibration_mode="off"))
    trade = trade_engine._apply_calibration(_calibration_candidate(station="KLAX"))

    assert trade.reject_reason is None
    assert trade.metadata["decision"] == "TRADE"
    assert trade.metadata["calibration_mode"] == "trade_blocker"
    assert trade.metadata["calibration_effect"] == "TRADE_ALLOW"


def test_trade_blocker_rejects_missing_bucket_and_unmapped_policy(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    trade_path = _write_calibration_file(tmp_path, decision="TRADE", station="KLAX")
    trade_engine = LiveExecutionEngine(store, LiveExecutionConfig(live_db_path=store.path, model_paths=(), calibration_path=trade_path, bucket_calibration_mode="off"))
    missing = trade_engine._apply_calibration(_calibration_candidate(station="KSEA"))
    assert missing.reject_reason == "CALIBRATION_BLOCK"
    assert missing.metadata["reason"] == "bucket_missing"
    assert missing.metadata["decision"] == "INSUFFICIENT_DATA"
    assert missing.metadata["calibration_mode"] == "trade_blocker"
    assert missing.metadata["calibration_effect"] == "BLOCK"

    unmapped = trade_engine._apply_calibration(_calibration_candidate(strategy_name="unmapped_strategy"))
    assert unmapped.reject_reason == "CALIBRATION_BLOCK"
    assert unmapped.metadata["reason"] == "family_unmapped"
    assert unmapped.metadata["decision"] == "UNMAPPED"
    assert unmapped.metadata["calibration_mode"] == "trade_blocker"
    assert unmapped.metadata["calibration_effect"] == "BLOCK"


@pytest.mark.parametrize(
    ("strategy_name", "family"),
    [
        (live_execution.HRRR_INLAND_DISAGREEMENT_POLICY_NAME, "hrrr_rich"),
        (live_execution.METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME, "metar_hrrr_rich"),
    ],
)
def test_hrrr_execution_experiment_rejects_only_block(tmp_path: Path, strategy_name: str, family: str) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(
            live_db_path=store.path,
            model_paths=(),
            calibration_path=_write_calibration_file(tmp_path, decision="BLOCK", family=family),
            bucket_calibration_mode="off",
        ),
    )

    decision = engine._apply_calibration(_calibration_candidate(strategy_name=strategy_name))

    assert decision.reject_reason == "CALIBRATION_BLOCK"
    assert decision.metadata["decision"] == "BLOCK"
    assert decision.metadata["calibration_mode"] == "hrrr_execution_experiment"
    assert decision.metadata["calibration_effect"] == "BLOCK"


@pytest.mark.parametrize("decision_label", ["CANARY", "WATCH", "TRADE", "INSUFFICIENT_DATA"])
def test_hrrr_execution_experiment_allows_non_block_decisions(tmp_path: Path, decision_label: str) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(
            live_db_path=store.path,
            model_paths=(),
            calibration_path=_write_calibration_file(tmp_path, decision=decision_label, family="hrrr_rich"),
            calibration_canary_notional_usd=4.0,
            bucket_calibration_mode="off",
        ),
    )

    decision = engine._apply_calibration(
        _calibration_candidate(
            strategy_name=live_execution.HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
            target=50.0,
        )
    )

    assert decision.reject_reason is None
    assert decision.candidate.plan.target_notional_usd == pytest.approx(50.0)
    assert decision.metadata["decision"] == decision_label
    assert decision.metadata["calibration_mode"] == "hrrr_execution_experiment"
    assert decision.metadata["calibration_effect"] == "EXPERIMENT_ALLOW"


def test_hrrr_execution_experiment_allows_missing_bucket(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(
            live_db_path=store.path,
            model_paths=(),
            calibration_path=_write_calibration_file(tmp_path, decision="BLOCK", family="hrrr_rich", station="KLAX"),
            bucket_calibration_mode="off",
        ),
    )

    decision = engine._apply_calibration(_calibration_candidate(strategy_name=live_execution.HRRR_INLAND_DISAGREEMENT_POLICY_NAME))

    assert decision.reject_reason is None
    assert decision.metadata["reason"] == "bucket_missing"
    assert decision.metadata["decision"] == "INSUFFICIENT_DATA"
    assert decision.metadata["calibration_mode"] == "hrrr_execution_experiment"
    assert decision.metadata["calibration_effect"] == "EXPERIMENT_ALLOW"


def test_calibration_debug_counts_effects_and_decisions(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "live.sqlite")
    engine = LiveExecutionEngine(
        store,
        LiveExecutionConfig(
            live_db_path=store.path,
            model_paths=(),
            calibration_path=_write_calibration_file(tmp_path, decision="CANARY", family="hrrr_rich"),
            bucket_calibration_mode="off",
        ),
    )
    debug = {"calibration": engine._empty_calibration_counts()}
    decision = engine._apply_calibration(_calibration_candidate(strategy_name=live_execution.HRRR_INLAND_DISAGREEMENT_POLICY_NAME))

    engine._increment_calibration_debug(debug, decision.metadata)

    assert debug["calibration"]["CANARY"] == 1
    assert debug["calibration"]["EXPERIMENT_ALLOW"] == 1
