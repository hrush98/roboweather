from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather_trader.execution.contracts import TradeAction
from weather_trader.live.execution import EDGE_CORE_POLICY_NAME, LIVE_POLICY_NAME, MOONSHOT_POLICY_NAME
from weather_trader.live.settings import LiveSettings
from weather_trader.live.sizing import (
    INSUFFICIENT_DEPTH,
    RISK_DAILY_NEW_CAP,
    RISK_EXACT_BUCKET_SIDE_CAP,
    RISK_STATION_DATE_CAP,
    RISK_STATION_DATE_SIDE_CAP,
    RISK_TOTAL_OPEN_CAP,
    LiveSizingModel,
)


def _decision(**overrides):
    params = {
        "strategy_name": EDGE_CORE_POLICY_NAME,
        "entry_price": 0.40,
        "station": "KATL",
        "market_date": "2026-05-25",
        "selected_side": TradeAction.BUY_NO,
        "selected_bucket": "72-73F",
        "sweep_depth_to_cap": 100.0,
        "exposure": {},
        "as_of_utc": datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc),
    }
    params.update(overrides)
    return LiveSizingModel(LiveSettings()).size_candidate(**params)


def test_core_consensus_and_moonshot_sizes() -> None:
    assert _decision(strategy_name=EDGE_CORE_POLICY_NAME).target_notional_usd == pytest.approx(10.0)
    assert _decision(strategy_name=LIVE_POLICY_NAME).target_notional_usd == pytest.approx(6.0)
    assert _decision(strategy_name=MOONSHOT_POLICY_NAME, entry_price=0.05).target_notional_usd == pytest.approx(2.0)


def test_explicit_strategy_target_notional_overrides_multiplier() -> None:
    decision = _decision(strategy_name=LIVE_POLICY_NAME, target_notional_usd=25.0)

    assert decision.target_notional_usd == pytest.approx(25.0)
    assert decision.pre_cap_target_usd == pytest.approx(25.0)
    assert decision.policy_multiplier == pytest.approx(1.0)
    assert decision.price_multiplier == pytest.approx(1.0)


def test_consensus_buy_yes_explicit_target_is_halved() -> None:
    decision = _decision(
        strategy_name=LIVE_POLICY_NAME,
        selected_side=TradeAction.BUY_YES,
        target_notional_usd=30.0,
    )

    assert decision.target_notional_usd == pytest.approx(15.0)
    assert decision.pre_cap_target_usd == pytest.approx(15.0)
    assert decision.raw_json["side_multiplier"] == pytest.approx(0.5)


def test_price_bands_and_rounding_floor_to_half_dollar() -> None:
    assert _decision(entry_price=0.09).target_notional_usd == pytest.approx(2.5)
    assert _decision(entry_price=0.10).target_notional_usd == pytest.approx(6.0)
    assert _decision(entry_price=0.25).target_notional_usd == pytest.approx(10.0)
    assert _decision(entry_price=0.76).target_notional_usd == pytest.approx(6.0)
    settings = LiveSettings(live_bankroll_usd=1999.0, live_fixed_fraction=0.005, live_max_usd_per_order=20.0)
    decision = LiveSizingModel(settings).size_candidate(
        strategy_name=EDGE_CORE_POLICY_NAME,
        entry_price=0.40,
        station="KATL",
        market_date="2026-05-25",
        selected_side=TradeAction.BUY_NO,
        selected_bucket="72-73F",
        sweep_depth_to_cap=100.0,
        exposure={},
        as_of_utc=datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc),
    )
    assert decision.pre_cap_target_usd == pytest.approx(9.995)
    assert decision.target_notional_usd == pytest.approx(9.5)


def test_risk_caps_clip_or_block() -> None:
    assert _decision(exposure={"open_risk_usd": 445.25}).target_notional_usd == pytest.approx(4.5)
    assert _decision(exposure={"open_risk_usd": 450.0}).blocked_reason == RISK_TOTAL_OPEN_CAP
    assert _decision(exposure={"daily_new_risk_usd": {"2026-05-25": 295.25}}).target_notional_usd == pytest.approx(4.5)
    assert _decision(exposure={"daily_new_risk_usd": {"2026-05-25": 300.0}}).blocked_reason == RISK_DAILY_NEW_CAP
    assert _decision(exposure={"station_date_exposure_usd": {"KATL:2026-05-25": 120.25}}).target_notional_usd == pytest.approx(4.5)
    assert _decision(exposure={"station_date_exposure_usd": {"KATL:2026-05-25": 125.0}}).blocked_reason == RISK_STATION_DATE_CAP
    assert _decision(exposure={"station_date_side_exposure_usd": {"KATL:2026-05-25:BUY_NO": 80.25}}).target_notional_usd == pytest.approx(4.5)
    assert _decision(exposure={"station_date_side_exposure_usd": {"KATL:2026-05-25:BUY_NO": 85.0}}).blocked_reason == RISK_STATION_DATE_SIDE_CAP
    assert _decision(exposure={"exact_bucket_side_exposure_usd": {"KATL:2026-05-25:BUY_NO:72-73F": 45.25}}).target_notional_usd == pytest.approx(4.5)
    assert _decision(exposure={"exact_bucket_side_exposure_usd": {"KATL:2026-05-25:BUY_NO:72-73F": 50.0}}).blocked_reason == RISK_EXACT_BUCKET_SIDE_CAP


def test_liquidity_depth_caps_or_blocks() -> None:
    assert _decision(sweep_depth_to_cap=8.25).target_notional_usd == pytest.approx(8.0)
    decision = _decision(sweep_depth_to_cap=0.75)
    assert decision.blocked_reason == INSUFFICIENT_DEPTH
    assert decision.raw_json["caps"][INSUFFICIENT_DEPTH]["remaining_usd"] == pytest.approx(0.75)
