from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from weather_trader.execution.contracts import MarketSnapshot
from weather_trader.execution.weather import StationWeatherState
from weather_trader.models.train_classifier import load_artifacts


@dataclass(frozen=True)
class FairValueResult:
    fair_yes: float
    fair_no: float
    reason_codes: list[str]
    model_name: str
    model_features_hash: str


class FairValueEngine:
    def __init__(self, model_path: Path) -> None:
        bundle = load_artifacts(model_path)
        self.model = bundle["model"]
        self.feature_columns = list(bundle["feature_columns"])
        self.model_type = str(bundle.get("model_type") or "threshold")
        self.model_name = model_path.stem
        self.model_features_hash = hashlib.sha256(json.dumps(self.feature_columns, sort_keys=True).encode()).hexdigest()[:16]

    def price_market(self, market: MarketSnapshot, weather: StationWeatherState) -> FairValueResult:
        reason_codes = ["MODEL_PROBABILITY"]
        if weather.stale:
            reason_codes.append("STALE_OBS_BLOCKED")
        if weather.hrrr_remaining_max is None:
            reason_codes.append("HRRR_MISSING_LOG")
        else:
            reason_codes.append("HRRR_AVAILABLE_LOG")
            reason_codes.extend(_hrrr_market_context_codes(market, weather))

        fair_yes = self._bucket_probability(market, weather, reason_codes)
        fair_yes = float(np.clip(fair_yes, 0.0, 1.0))
        return FairValueResult(
            fair_yes=fair_yes,
            fair_no=1.0 - fair_yes,
            reason_codes=reason_codes,
            model_name=self.model_name,
            model_features_hash=self.model_features_hash,
        )

    def price_markets(self, markets: list[MarketSnapshot], weather: StationWeatherState) -> dict[str, FairValueResult]:
        if self.model_type != "dynamic_bucket":
            return {market.market_id: self.price_market(market, weather) for market in markets}

        reason_codes_by_market = {market.market_id: self._base_reason_codes(market, weather) for market in markets}
        rows = [self._bucket_feature_row(market, weather) for market in markets]
        frame = pd.DataFrame([{column: row.get(column, np.nan) for column in self.feature_columns} for row in rows])
        raw_probabilities = self.model.predict_proba(frame)[:, 1] if len(frame) else np.array([])
        adjusted = np.array(
            [
                self._dynamic_bucket_probability_override(market, weather, float(probability), reason_codes_by_market[market.market_id])
                for market, probability in zip(markets, raw_probabilities)
            ],
            dtype=float,
        )
        adjusted = np.clip(adjusted, 0.0, 1.0)
        total = float(adjusted.sum())
        if np.isfinite(total) and total > 0.0:
            probabilities = adjusted / total
        else:
            probabilities = np.full(len(markets), 1.0 / len(markets)) if markets else np.array([])

        return {
            market.market_id: FairValueResult(
                fair_yes=float(probability),
                fair_no=1.0 - float(probability),
                reason_codes=reason_codes_by_market[market.market_id],
                model_name=self.model_name,
                model_features_hash=self.model_features_hash,
            )
            for market, probability in zip(markets, probabilities)
        }

    def _base_reason_codes(self, market: MarketSnapshot, weather: StationWeatherState) -> list[str]:
        reason_codes = ["MODEL_PROBABILITY"]
        if weather.stale:
            reason_codes.append("STALE_OBS_BLOCKED")
        if weather.hrrr_remaining_max is None:
            reason_codes.append("HRRR_MISSING_LOG")
        else:
            reason_codes.append("HRRR_AVAILABLE_LOG")
            reason_codes.extend(_hrrr_market_context_codes(market, weather))
        return reason_codes

    def _bucket_probability(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        reason_codes: list[str],
    ) -> float:
        high_so_far = weather.high_so_far
        lower = market.lower_f
        upper = market.upper_f

        if lower is not None and upper is not None:
            if high_so_far > upper:
                reason_codes.append("HIGH_SO_FAR_ABOVE_BUCKET")
                return 0.0
            if high_so_far >= lower:
                reason_codes.append("HIGH_SO_FAR_INSIDE_BUCKET")
                return 1.0 - self._probability_at_threshold(market, weather, upper + 1.0)
            return max(
                0.0,
                self._probability_at_threshold(market, weather, lower)
                - self._probability_at_threshold(market, weather, upper + 1.0),
            )

        if lower is not None:
            if high_so_far >= lower:
                reason_codes.append("OR_HIGHER_ALREADY_CROSSED")
                return 1.0
            return self._probability_at_threshold(market, weather, lower)

        if upper is not None:
            if high_so_far > upper:
                reason_codes.append("HIGH_SO_FAR_ABOVE_BUCKET")
                return 0.0
            return 1.0 - self._probability_at_threshold(market, weather, upper + 1.0)

        reason_codes.append("UNPARSEABLE_BUCKET_BLOCKED")
        return 0.0

    def _probability_at_threshold(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        threshold: float,
    ) -> float:
        hrrr_remaining = weather.hrrr_remaining_max if weather.hrrr_remaining_max is not None else np.nan
        hrrr_current = weather.hrrr_current_temp if weather.hrrr_current_temp is not None else np.nan
        features = {
            "station": weather.station,
            "hour_local": weather.hour_local,
            "day_of_year": weather.day_of_year,
            "current_temp": weather.current_temp,
            "max_temp_so_far": weather.high_so_far,
            "threshold": threshold,
            "threshold_minus_current_temp": threshold - weather.current_temp,
            "threshold_minus_max_so_far": threshold - weather.high_so_far,
            "temp_change_1h": weather.temp_change_1h,
            "temp_change_3h": weather.temp_change_3h,
            "dewpoint": weather.dewpoint,
            "wind_speed": weather.wind_speed,
            "wind_dir_sin": weather.wind_dir_sin,
            "wind_dir_cos": weather.wind_dir_cos,
            "cloud_cover_code": weather.cloud_cover_code,
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining,
            "hrrr_remaining_max_minus_threshold": hrrr_remaining - threshold,
            "hrrr_current_temp_minus_current_temp": hrrr_current - weather.current_temp,
        }
        frame = pd.DataFrame([{column: features.get(column, np.nan) for column in self.feature_columns}])
        return float(self.model.predict_proba(frame)[:, 1][0])

    def _bucket_feature_row(self, market: MarketSnapshot, weather: StationWeatherState) -> dict[str, object]:
        lower = market.lower_f
        upper = _exclusive_upper(market.upper_f)
        hrrr_remaining = weather.hrrr_remaining_max if weather.hrrr_remaining_max is not None else np.nan
        hrrr_current = weather.hrrr_current_temp if weather.hrrr_current_temp is not None else np.nan
        return {
            "station": weather.station,
            "hour_local": weather.hour_local,
            "day_of_year": weather.day_of_year,
            "current_temp": weather.current_temp,
            "max_temp_so_far": weather.high_so_far,
            "temp_change_1h": weather.temp_change_1h,
            "temp_change_3h": weather.temp_change_3h,
            "dewpoint": weather.dewpoint,
            "wind_speed": weather.wind_speed,
            "wind_dir_sin": weather.wind_dir_sin,
            "wind_dir_cos": weather.wind_dir_cos,
            "cloud_cover_code": weather.cloud_cover_code,
            "bucket_lower": lower,
            "bucket_upper": upper,
            "bucket_span": (upper - lower) if lower is not None and upper is not None else np.nan,
            "lower_minus_current_temp": lower - weather.current_temp if lower is not None else np.nan,
            "upper_minus_current_temp": upper - weather.current_temp if upper is not None else np.nan,
            "lower_minus_max_so_far": lower - weather.high_so_far if lower is not None else np.nan,
            "upper_minus_max_so_far": upper - weather.high_so_far if upper is not None else np.nan,
            "is_left_tail": int(lower is None),
            "is_right_tail": int(upper is None),
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining,
            "hrrr_current_temp_minus_current_temp": hrrr_current - weather.current_temp,
            "hrrr_lower_minus_current_temp": lower - hrrr_current if lower is not None else np.nan,
            "hrrr_upper_minus_current_temp": upper - hrrr_current if upper is not None else np.nan,
            "hrrr_remaining_max_minus_lower": hrrr_remaining - lower if lower is not None else np.nan,
            "hrrr_remaining_max_minus_upper": hrrr_remaining - upper if upper is not None else np.nan,
        }

    def _dynamic_bucket_probability_override(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        probability: float,
        reason_codes: list[str],
    ) -> float:
        if market.upper_f is not None and weather.high_so_far > market.upper_f:
            reason_codes.append("HIGH_SO_FAR_ABOVE_BUCKET")
            return 0.0
        return probability


def _hrrr_market_context_codes(market: MarketSnapshot, weather: StationWeatherState) -> list[str]:
    assert weather.hrrr_remaining_max is not None
    hrrr_max = weather.hrrr_remaining_max
    high_so_far = weather.high_so_far
    codes: list[str] = []
    if market.lower_f is not None and high_so_far < market.lower_f and hrrr_max < market.lower_f:
        codes.append("HRRR_REMAINING_BELOW_LOWER_LOG")
    if market.upper_f is not None and high_so_far <= market.upper_f and hrrr_max > market.upper_f:
        codes.append("HRRR_REMAINING_ABOVE_UPPER_LOG")
    if market.lower_f is not None and market.upper_f is not None and market.lower_f <= high_so_far <= market.upper_f and hrrr_max <= market.upper_f:
        codes.append("HRRR_SUPPORTS_CURRENT_BUCKET_LOG")
    if market.lower_f is not None and market.upper_f is None and (high_so_far >= market.lower_f or hrrr_max >= market.lower_f):
        codes.append("HRRR_SUPPORTS_OR_HIGHER_LOG")
    if market.upper_f is not None and market.lower_f is None and hrrr_max <= market.upper_f:
        codes.append("HRRR_SUPPORTS_OR_BELOW_LOG")
    return codes


def _exclusive_upper(upper_f: float | None) -> float | None:
    return upper_f + 1.0 if upper_f is not None else None
