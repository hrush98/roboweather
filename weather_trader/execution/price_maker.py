from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from statistics import pstdev
from typing import Any

from weather_trader.execution.contracts import LivePriceSheet, MarketFamily, TradeAction, utc_now_iso


PHASE1_PRICE_SHEET_VERSION = "phase1_price_maker_v1"
PHASE1_CONSENSUS_NO_TINY_POLICY = "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first"
PHASE1_FAIR_CAP = 0.90
PHASE1_FAIR_FLOOR = 0.05
PHASE1_BASE_UNCERTAINTY_HAIRCUT = 0.04
PHASE1_BASE_ADVERSE_SELECTION_HAIRCUT = 0.06
PHASE1_MIN_REQUIRED_EDGE = 0.12
PHASE1_QUOTE_SIZE_CAP_USD = 5.0
PHASE1_VALID_SECONDS = 120


def build_phase1_price_sheet(
    *,
    live_candidate_id: str,
    strategy_name: str,
    policy_name: str | None,
    source: Any,
    selected_token_id: str | None,
    quote_features: dict[str, Any] | None,
    as_of_utc: datetime,
    target_notional_usd: float,
) -> LivePriceSheet:
    raw_policy = getattr(source, "raw_policy", None)
    raw_policy = raw_policy if isinstance(raw_policy, dict) else {}
    quote_features = quote_features if isinstance(quote_features, dict) else {}
    selected_side = TradeAction(str(getattr(source, "selected_side")))
    market_family = MarketFamily(str(getattr(source, "market_family", MarketFamily.HIGH_TEMP)))
    raw_model_fair = _raw_model_fair(source, raw_policy)
    calibrated_fair = _calibrated_quote_fair(raw_model_fair)
    market_reference = _market_reference(source, quote_features)
    uncertainty_haircut = _uncertainty_haircut(raw_model_fair, calibrated_fair, raw_policy)
    adverse_haircut = _adverse_selection_haircut(source, quote_features)
    min_required_edge = round(max(PHASE1_MIN_REQUIRED_EDGE, uncertainty_haircut + adverse_haircut), 4)
    max_quote_price = _floor_cent(calibrated_fair - min_required_edge) if calibrated_fair is not None else None
    reject_reason = _reject_reason(
        strategy_name=strategy_name,
        selected_side=selected_side,
        market_family=market_family,
        calibrated_fair=calibrated_fair,
        max_quote_price=max_quote_price,
    )
    eligible = reject_reason is None
    valid_until = as_of_utc.astimezone(timezone.utc) + timedelta(seconds=PHASE1_VALID_SECONDS)
    raw_json = {
        "version": PHASE1_PRICE_SHEET_VERSION,
        "scope": {
            "strategy_name": PHASE1_CONSENSUS_NO_TINY_POLICY,
            "market_family": str(MarketFamily.HIGH_TEMP),
            "selected_side": str(TradeAction.BUY_NO),
        },
        "formula": "max_quote_price=floor_cent(clamp(raw_model_fair,0.05,0.90)-max(0.12,uncertainty_haircut+adverse_selection_haircut))",
        "probability_caps": {"floor": PHASE1_FAIR_FLOOR, "cap": PHASE1_FAIR_CAP},
        "model_fairs": _model_fairs(raw_policy),
        "quote_features": quote_features,
    }
    return LivePriceSheet(
        timestamp=utc_now_iso(),
        version=PHASE1_PRICE_SHEET_VERSION,
        live_candidate_id=live_candidate_id,
        strategy_name=strategy_name,
        policy_name=policy_name,
        station=str(getattr(source, "station")),
        market_date=_date_value(getattr(source, "market_date")),
        market_family=market_family,
        selected_market_id=str(getattr(source, "selected_market_id")),
        selected_token_id=selected_token_id,
        selected_side=selected_side,
        selected_bucket=getattr(source, "selected_bucket", None),
        raw_model_fair=raw_model_fair,
        calibrated_fair=calibrated_fair,
        market_mid_or_reference=market_reference,
        uncertainty_haircut=uncertainty_haircut,
        adverse_selection_haircut=adverse_haircut,
        min_required_edge=min_required_edge,
        max_quote_price=max_quote_price if max_quote_price is not None and max_quote_price > 0.0 else None,
        quote_size_cap=round(min(float(target_notional_usd), PHASE1_QUOTE_SIZE_CAP_USD), 2),
        fair_valid_until=valid_until.isoformat(),
        cancel_triggers=[
            "fair_value_deteriorates",
            "weather_update_changes_candidate",
            "book_crosses_max_quote_price",
            "spread_or_depth_regime_deteriorates",
            "feed_stale_or_disconnected",
            "station_date_risk_state_changes",
            "fair_valid_until",
        ],
        eligible=eligible,
        reject_reason=reject_reason,
        raw_json=raw_json,
    )


def _reject_reason(
    *,
    strategy_name: str,
    selected_side: TradeAction,
    market_family: MarketFamily,
    calibrated_fair: float | None,
    max_quote_price: float | None,
) -> str | None:
    if strategy_name != PHASE1_CONSENSUS_NO_TINY_POLICY:
        return "PHASE1_STRATEGY_OUT_OF_SCOPE"
    if market_family != MarketFamily.HIGH_TEMP:
        return "PHASE1_MARKET_FAMILY_OUT_OF_SCOPE"
    if selected_side != TradeAction.BUY_NO:
        return "PHASE1_SIDE_OUT_OF_SCOPE"
    if calibrated_fair is None:
        return "MISSING_FAIR"
    if max_quote_price is None or max_quote_price <= 0.0:
        return "NO_POSITIVE_QUOTE_AFTER_HAIRCUTS"
    return None


def _raw_model_fair(source: Any, raw_policy: dict[str, Any]) -> float | None:
    values = _model_fairs(raw_policy)
    if values:
        return round(sum(values.values()) / len(values), 4)
    return _float_or_none(getattr(source, "entry_fair", None))


def _model_fairs(raw_policy: dict[str, Any]) -> dict[str, float]:
    raw = raw_policy.get("model_fairs")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        parsed = _float_or_none(value)
        if parsed is not None:
            result[str(key)] = parsed
    return result


def _calibrated_quote_fair(raw_model_fair: float | None) -> float | None:
    if raw_model_fair is None:
        return None
    return round(min(PHASE1_FAIR_CAP, max(PHASE1_FAIR_FLOOR, raw_model_fair)), 4)


def _uncertainty_haircut(raw_model_fair: float | None, calibrated_fair: float | None, raw_policy: dict[str, Any]) -> float:
    haircut = PHASE1_BASE_UNCERTAINTY_HAIRCUT
    fairs = list(_model_fairs(raw_policy).values())
    if len(fairs) >= 2:
        haircut += min(0.06, pstdev(fairs))
    else:
        haircut += 0.02
    if raw_model_fair is not None and calibrated_fair is not None and raw_model_fair > calibrated_fair:
        haircut += min(0.08, raw_model_fair - calibrated_fair)
    calibrations = raw_policy.get("model_bucket_calibration")
    if isinstance(calibrations, dict):
        decisions = [str(value.get("decision") or "") for value in calibrations.values() if isinstance(value, dict)]
        if decisions and any(decision not in {"TRADE", "CANARY", "WATCH"} for decision in decisions):
            haircut += 0.03
    else:
        haircut += 0.02
    return round(haircut, 4)


def _adverse_selection_haircut(source: Any, quote_features: dict[str, Any]) -> float:
    haircut = PHASE1_BASE_ADVERSE_SELECTION_HAIRCUT
    spread = _float_or_none(quote_features.get("spread"))
    if spread is None:
        spread = _float_or_none(getattr(source, "selected_spread", None))
    if spread is not None:
        haircut += min(0.04, max(0.0, spread) * 0.5)
    if quote_features.get("selected_ask_just_depleted"):
        haircut += 0.02
    cancel_count = _float_or_none(quote_features.get("top_level_cancel_count_5m")) or 0.0
    trade_count = _float_or_none(quote_features.get("recent_trade_count_5m")) or 0.0
    haircut += min(0.03, cancel_count * 0.005)
    haircut += min(0.02, trade_count * 0.002)
    return round(haircut, 4)


def _market_reference(source: Any, quote_features: dict[str, Any]) -> float | None:
    bid = _float_or_none(quote_features.get("best_bid"))
    ask = _float_or_none(quote_features.get("best_ask"))
    if bid is None:
        bid = _float_or_none(getattr(source, "selected_best_bid", None))
    if ask is None:
        ask = _float_or_none(getattr(source, "selected_best_ask", None))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return bid if bid is not None else ask


def _date_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _floor_cent(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return math.floor(max(0.0, value) * 100.0 + 1e-12) / 100.0


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result
