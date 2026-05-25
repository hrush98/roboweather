from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from weather_trader.execution.contracts import TradeAction
from weather_trader.live.settings import LiveSettings

CORE_POLICY_MULTIPLIER = 1.0
CONSENSUS_POLICY_MULTIPLIER = 0.6
MOONSHOT_FIXED_NOTIONAL_USD = 1.0
ROUNDING_STEP_USD = 0.5

RISK_TOTAL_OPEN_CAP = "RISK_TOTAL_OPEN_CAP"
RISK_DAILY_NEW_CAP = "RISK_DAILY_NEW_CAP"
RISK_STATION_DATE_CAP = "RISK_STATION_DATE_CAP"
RISK_STATION_DATE_SIDE_CAP = "RISK_STATION_DATE_SIDE_CAP"
RISK_EXACT_BUCKET_SIDE_CAP = "RISK_EXACT_BUCKET_SIDE_CAP"
RISK_MIN_ORDER_NOTIONAL = "RISK_MIN_ORDER_NOTIONAL"
INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"


@dataclass(frozen=True)
class LiveSizingDecision:
    target_notional_usd: float
    base_notional_usd: float
    policy_multiplier: float
    price_multiplier: float
    pre_cap_target_usd: float
    caps: dict[str, dict[str, float]]
    blocked_reason: str | None
    raw_json: dict[str, Any]

    @property
    def cap_reason(self) -> str:
        if self.blocked_reason:
            return self.blocked_reason
        clipped = [
            name
            for name, cap in self.caps.items()
            if cap.get("applied_usd", cap.get("remaining_usd", 0.0)) < self.pre_cap_target_usd
        ]
        return clipped[0] if clipped else "NONE"


class LiveSizingModel:
    def __init__(self, settings: LiveSettings) -> None:
        self.settings = settings

    def size_candidate(
        self,
        *,
        strategy_name: str,
        entry_price: float,
        station: str,
        market_date: object,
        selected_side: TradeAction | str,
        selected_bucket: str | None,
        sweep_depth_to_cap: float | None,
        exposure: dict[str, Any],
        as_of_utc: datetime | None = None,
    ) -> LiveSizingDecision:
        as_of_utc = as_of_utc or datetime.now(timezone.utc)
        side = str(selected_side)
        market_date_text = str(market_date)
        base = float(self.settings.live_base_notional_usd)
        is_moonshot = "moonshot" in strategy_name.lower() or "entry_05_10" in strategy_name.lower()
        if is_moonshot:
            policy_multiplier = 0.0
            price_multiplier = 1.0
            pre_cap = MOONSHOT_FIXED_NOTIONAL_USD
        else:
            policy_multiplier = CONSENSUS_POLICY_MULTIPLIER if "consensus" in strategy_name else CORE_POLICY_MULTIPLIER
            price_multiplier = _price_multiplier(entry_price)
            pre_cap = base * policy_multiplier * price_multiplier

        station_date_key = f"{station}:{market_date_text}"
        station_date_side_key = f"{station}:{market_date_text}:{side}"
        exact_key = f"{station}:{market_date_text}:{side}:{selected_bucket or ''}"
        daily_key = as_of_utc.astimezone(timezone.utc).date().isoformat()

        open_risk = float(exposure.get("open_risk_usd") or 0.0)
        daily_new = float((exposure.get("daily_new_risk_usd") or {}).get(daily_key, 0.0))
        station_date = float((exposure.get("station_date_exposure_usd") or {}).get(station_date_key, 0.0))
        station_date_side = float((exposure.get("station_date_side_exposure_usd") or {}).get(station_date_side_key, 0.0))
        exact_bucket_side = float((exposure.get("exact_bucket_side_exposure_usd") or {}).get(exact_key, 0.0))

        cap_specs = [
            ("per_order", "LIVE_MAX_USD_PER_ORDER", float(self.settings.live_max_usd_per_order), 0.0),
            (RISK_TOTAL_OPEN_CAP, "LIVE_MAX_TOTAL_OPEN_RISK", float(self.settings.live_max_total_open_risk), open_risk),
            (RISK_DAILY_NEW_CAP, "LIVE_MAX_DAILY_NEW_RISK", float(self.settings.live_max_daily_new_risk), daily_new),
            (RISK_STATION_DATE_CAP, "LIVE_MAX_EXPOSURE_PER_STATION_DATE", float(self.settings.live_max_exposure_per_station_date), station_date),
            (
                RISK_STATION_DATE_SIDE_CAP,
                "LIVE_MAX_EXPOSURE_PER_STATION_DATE_SIDE",
                float(self.settings.live_max_exposure_per_station_date_side),
                station_date_side,
            ),
            (
                RISK_EXACT_BUCKET_SIDE_CAP,
                "LIVE_MAX_EXPOSURE_PER_EXACT_BUCKET_SIDE",
                float(self.settings.live_max_exposure_per_exact_bucket_side),
                exact_bucket_side,
            ),
        ]
        if sweep_depth_to_cap is not None:
            cap_specs.append((INSUFFICIENT_DEPTH, "selected_sweep_depth_to_cap", max(0.0, float(sweep_depth_to_cap)), 0.0))

        target = pre_cap
        caps: dict[str, dict[str, float]] = {}
        blocked_reason: str | None = None
        for reason, label, limit, used in cap_specs:
            remaining = max(0.0, limit - used)
            applied = min(target, remaining)
            caps[reason] = {"limit_usd": limit, "used_usd": used, "remaining_usd": remaining, "applied_usd": applied}
            if remaining <= 0.0 and blocked_reason is None:
                blocked_reason = reason
            target = applied

        rounded = _floor_to_step(target, ROUNDING_STEP_USD)
        if blocked_reason is None and rounded < float(self.settings.live_min_order_notional):
            blocked_reason = (
                INSUFFICIENT_DEPTH
                if sweep_depth_to_cap is not None and float(sweep_depth_to_cap) < float(self.settings.live_min_order_notional)
                else RISK_MIN_ORDER_NOTIONAL
            )

        raw = {
            "base_notional_usd": base,
            "policy_multiplier": policy_multiplier,
            "price_multiplier": price_multiplier,
            "pre_cap_target_usd": pre_cap,
            "caps": caps,
            "rounding_step_usd": ROUNDING_STEP_USD,
            "min_order_notional_usd": float(self.settings.live_min_order_notional),
            "final_target_notional_usd": rounded,
            "blocked_reason": blocked_reason,
            "daily_new_risk_date_utc": daily_key,
            "strategy_name": strategy_name,
        }
        return LiveSizingDecision(rounded, base, policy_multiplier, price_multiplier, pre_cap, caps, blocked_reason, raw)


def _price_multiplier(price: float) -> float:
    if price < 0.10:
        return 0.25
    if price < 0.25:
        return 0.60
    if price <= 0.75:
        return 1.00
    return 0.60


def _floor_to_step(value: float, step: float) -> float:
    if value <= 0.0:
        return 0.0
    return math.floor((value + 1e-9) / step) * step
