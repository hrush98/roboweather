from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from weather_trader.calibration.bucket_probability import (
    DEFAULT_BUCKET_CALIBRATION_PATH,
    disabled_metadata,
    load_bucket_probability_calibrator,
)
from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.execution.weather import StationWeatherState
from weather_trader.models.train_classifier import load_artifacts


@dataclass(frozen=True)
class FairValueResult:
    fair_yes: float
    fair_no: float
    reason_codes: list[str]
    model_name: str
    model_features_hash: str
    raw_fair_yes: float | None = None
    raw_fair_no: float | None = None
    bucket_calibration: dict[str, Any] = field(default_factory=dict)


class FairValueEngine:
    def __init__(
        self,
        model_path: Path,
        *,
        bucket_calibration_path: Path | None = DEFAULT_BUCKET_CALIBRATION_PATH,
        bucket_calibration_mode: str = "off",
    ) -> None:
        bundle = load_artifacts(model_path)
        self.model = bundle["model"]
        self.feature_columns = list(bundle["feature_columns"])
        self.model_type = str(bundle.get("model_type") or "threshold")
        self.residuals = bundle.get("residuals")
        self.residual_scope = str(bundle.get("residual_scope") or "window")
        self.model_name = model_path.stem
        self.market_family = _artifact_market_family(bundle, self.model_name)
        self.model_features_hash = hashlib.sha256(json.dumps(self.feature_columns, sort_keys=True).encode()).hexdigest()[:16]
        self.bucket_calibration_mode = bucket_calibration_mode
        self.bucket_calibration_path = bucket_calibration_path
        self.bucket_calibrator = load_bucket_probability_calibrator(path=bucket_calibration_path, mode=bucket_calibration_mode)

    def supports_market_family(self, market_family: str | MarketFamily) -> bool:
        return str(self.market_family) == str(market_family)

    @property
    def bucket_calibration_active(self) -> bool:
        return self.bucket_calibration_mode == "apply" and self.bucket_calibrator is not None

    def price_market(self, market: MarketSnapshot, weather: StationWeatherState) -> FairValueResult:
        reason_codes = ["MODEL_PROBABILITY"]
        if weather.stale:
            reason_codes.append("STALE_OBS_BLOCKED")
        hrrr_remaining = _hrrr_remaining_for_market(market, weather)
        if hrrr_remaining is None:
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
        if self.model_type in {"high_regression_empirical_residual", "ngboost_normal_crps"}:
            return self._price_distribution_markets(markets, weather)
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

        results: dict[str, FairValueResult] = {}
        for market, probability in zip(markets, probabilities):
            raw_yes = float(probability)
            fair_yes, calibration = self._calibrated_bucket_probability(
                market=market,
                weather=weather,
                raw_fair_yes=raw_yes,
                reason_codes=reason_codes_by_market[market.market_id],
            )
            results[market.market_id] = FairValueResult(
                fair_yes=fair_yes,
                fair_no=1.0 - fair_yes,
                reason_codes=reason_codes_by_market[market.market_id],
                model_name=self.model_name,
                model_features_hash=self.model_features_hash,
                raw_fair_yes=raw_yes,
                raw_fair_no=1.0 - raw_yes,
                bucket_calibration=calibration,
            )
        return results

    def _base_reason_codes(self, market: MarketSnapshot, weather: StationWeatherState) -> list[str]:
        reason_codes = ["MODEL_PROBABILITY"]
        if weather.stale:
            reason_codes.append("STALE_OBS_BLOCKED")
        hrrr_remaining = _hrrr_remaining_for_market(market, weather)
        if hrrr_remaining is None:
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
        if market.market_family == MarketFamily.LOW_TEMP:
            return self._low_bucket_probability(market, weather, reason_codes)
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

    def _low_bucket_probability(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        reason_codes: list[str],
    ) -> float:
        low_so_far = weather.low_so_far
        lower = market.lower_f
        upper = market.upper_f

        if lower is not None and upper is not None:
            if low_so_far < lower:
                reason_codes.append("LOW_SO_FAR_BELOW_BUCKET")
                return 0.0
            return max(
                0.0,
                self._probability_at_threshold(market, weather, upper)
                - self._probability_at_threshold(market, weather, lower - 1.0),
            )

        if lower is not None:
            if low_so_far < lower:
                reason_codes.append("LOW_SO_FAR_BELOW_BUCKET")
                return 0.0
            return 1.0 - self._probability_at_threshold(market, weather, lower - 1.0)

        if upper is not None:
            if low_so_far <= upper:
                reason_codes.append("OR_LOWER_ALREADY_CROSSED")
                return 1.0
            return self._probability_at_threshold(market, weather, upper)

        reason_codes.append("UNPARSEABLE_BUCKET_BLOCKED")
        return 0.0

    def _probability_at_threshold(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        threshold: float,
    ) -> float:
        is_low = market.market_family == MarketFamily.LOW_TEMP
        hrrr_remaining = _hrrr_remaining_for_market(market, weather)
        hrrr_remaining = hrrr_remaining if hrrr_remaining is not None else np.nan
        hrrr_remaining_max = weather.hrrr_remaining_max if weather.hrrr_remaining_max is not None else np.nan
        hrrr_remaining_min = weather.hrrr_remaining_min if weather.hrrr_remaining_min is not None else np.nan
        hrrr_current = weather.hrrr_current_temp if weather.hrrr_current_temp is not None else np.nan
        temp_so_far_key = "min_temp_so_far" if is_low else "max_temp_so_far"
        temp_so_far = weather.low_so_far if is_low else weather.high_so_far
        threshold_gap_key = "threshold_minus_min_so_far" if is_low else "threshold_minus_max_so_far"
        hrrr_gap_key = "hrrr_remaining_min_minus_threshold" if is_low else "hrrr_remaining_max_minus_threshold"
        features = {
            "station": weather.station,
            "hour_local": weather.hour_local,
            "day_of_year": weather.day_of_year,
            "current_temp": weather.current_temp,
            "max_temp_so_far": weather.high_so_far,
            "min_temp_so_far": weather.low_so_far,
            "threshold": threshold,
            "threshold_minus_current_temp": threshold - weather.current_temp,
            "threshold_minus_max_so_far": threshold - weather.high_so_far,
            "threshold_minus_min_so_far": threshold - weather.low_so_far,
            "temp_change_1h": weather.temp_change_1h,
            "temp_change_3h": weather.temp_change_3h,
            "dewpoint": weather.dewpoint,
            "wind_speed": weather.wind_speed,
            "wind_dir_sin": weather.wind_dir_sin,
            "wind_dir_cos": weather.wind_dir_cos,
            "cloud_cover_code": weather.cloud_cover_code,
            "temp_range_so_far": weather.temp_range_so_far,
            "relative_humidity": weather.relative_humidity,
            "wet_bulb_approx": weather.wet_bulb_approx,
            "pressure_mslp": weather.pressure_mslp,
            "pressure_tendency_3h": weather.pressure_tendency_3h,
            "visibility_miles": weather.visibility_miles,
            "precip_1h_in": weather.precip_1h_in,
            "altimeter_inhg": weather.altimeter_inhg,
            "feels_like": weather.feels_like,
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining_max,
            "hrrr_remaining_min": hrrr_remaining_min,
            "hrrr_remaining_max_minus_threshold": hrrr_remaining_max - threshold,
            "hrrr_remaining_min_minus_threshold": hrrr_remaining_min - threshold,
            "hrrr_current_temp_minus_current_temp": hrrr_current - weather.current_temp,
            "hrrr_temp_next_3h_max_minus_threshold": weather.hrrr_temp_next_3h_max - threshold,
            **_hrrr_rich_features(weather),
            temp_so_far_key: temp_so_far,
            threshold_gap_key: threshold - temp_so_far,
            hrrr_gap_key: hrrr_remaining - threshold,
        }
        frame = pd.DataFrame([{column: features.get(column, np.nan) for column in self.feature_columns}])
        return float(self.model.predict_proba(frame)[:, 1][0])

    def _bucket_feature_row(self, market: MarketSnapshot, weather: StationWeatherState) -> dict[str, object]:
        lower = market.lower_f
        upper = _exclusive_upper(market.upper_f)
        is_low = market.market_family == MarketFamily.LOW_TEMP
        hrrr_remaining = _hrrr_remaining_for_market(market, weather)
        hrrr_remaining = hrrr_remaining if hrrr_remaining is not None else np.nan
        hrrr_remaining_max = weather.hrrr_remaining_max if weather.hrrr_remaining_max is not None else np.nan
        hrrr_remaining_min = weather.hrrr_remaining_min if weather.hrrr_remaining_min is not None else np.nan
        hrrr_current = weather.hrrr_current_temp if weather.hrrr_current_temp is not None else np.nan
        return {
            "station": weather.station,
            "hour_local": weather.hour_local,
            "day_of_year": weather.day_of_year,
            "current_temp": weather.current_temp,
            "max_temp_so_far": weather.high_so_far,
            "min_temp_so_far": weather.low_so_far,
            "temp_change_1h": weather.temp_change_1h,
            "temp_change_3h": weather.temp_change_3h,
            "dewpoint": weather.dewpoint,
            "wind_speed": weather.wind_speed,
            "wind_dir_sin": weather.wind_dir_sin,
            "wind_dir_cos": weather.wind_dir_cos,
            "cloud_cover_code": weather.cloud_cover_code,
            "temp_range_so_far": weather.temp_range_so_far,
            "relative_humidity": weather.relative_humidity,
            "wet_bulb_approx": weather.wet_bulb_approx,
            "pressure_mslp": weather.pressure_mslp,
            "pressure_tendency_3h": weather.pressure_tendency_3h,
            "visibility_miles": weather.visibility_miles,
            "precip_1h_in": weather.precip_1h_in,
            "altimeter_inhg": weather.altimeter_inhg,
            "feels_like": weather.feels_like,
            "bucket_lower": lower,
            "bucket_upper": upper,
            "bucket_span": (upper - lower) if lower is not None and upper is not None else np.nan,
            "lower_minus_current_temp": lower - weather.current_temp if lower is not None else np.nan,
            "upper_minus_current_temp": upper - weather.current_temp if upper is not None else np.nan,
            "lower_minus_max_so_far": lower - weather.high_so_far if lower is not None else np.nan,
            "upper_minus_max_so_far": upper - weather.high_so_far if upper is not None else np.nan,
            "lower_minus_min_so_far": lower - weather.low_so_far if lower is not None else np.nan,
            "upper_minus_min_so_far": upper - weather.low_so_far if upper is not None else np.nan,
            "is_left_tail": int(lower is None),
            "is_right_tail": int(upper is None),
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining_max,
            "hrrr_remaining_min": hrrr_remaining_min,
            "hrrr_current_temp_minus_current_temp": hrrr_current - weather.current_temp,
            **_hrrr_rich_features(weather),
            "hrrr_lower_minus_current_temp": lower - hrrr_current if lower is not None else np.nan,
            "hrrr_upper_minus_current_temp": upper - hrrr_current if upper is not None else np.nan,
            "hrrr_remaining_max_minus_lower": hrrr_remaining_max - lower if lower is not None else np.nan,
            "hrrr_remaining_max_minus_upper": hrrr_remaining_max - upper if upper is not None else np.nan,
            "hrrr_remaining_min_minus_lower": hrrr_remaining_min - lower if lower is not None else np.nan,
            "hrrr_remaining_min_minus_upper": hrrr_remaining_min - upper if upper is not None else np.nan,
        }

    def _dynamic_bucket_probability_override(
        self,
        market: MarketSnapshot,
        weather: StationWeatherState,
        probability: float,
        reason_codes: list[str],
    ) -> float:
        if market.market_family == MarketFamily.LOW_TEMP:
            if market.lower_f is not None and weather.low_so_far < market.lower_f:
                reason_codes.append("LOW_SO_FAR_BELOW_BUCKET")
                return 0.0
        elif market.upper_f is not None and weather.high_so_far > market.upper_f:
            reason_codes.append("HIGH_SO_FAR_ABOVE_BUCKET")
            return 0.0
        return probability

    def _calibrated_bucket_probability(
        self,
        *,
        market: MarketSnapshot,
        weather: StationWeatherState,
        raw_fair_yes: float,
        reason_codes: list[str],
    ) -> tuple[float, dict[str, Any]]:
        mode = getattr(self, "bucket_calibration_mode", "off")
        path = getattr(self, "bucket_calibration_path", None)
        calibrator = getattr(self, "bucket_calibrator", None)
        if mode == "off":
            return raw_fair_yes, disabled_metadata(path=path, mode="off", reason="mode_off")
        if market.market_family != MarketFamily.HIGH_TEMP:
            return raw_fair_yes, disabled_metadata(
                path=path,
                mode=mode,
                reason="market_family_not_supported",
            )
        if calibrator is None:
            return raw_fair_yes, disabled_metadata(
                path=path,
                mode=mode,
                reason="artifact_missing",
            )
        result = calibrator.calibrate(model_name=self.model_name, station=weather.station, raw_fair_yes=raw_fair_yes)
        reason_codes.append("BUCKET_CALIBRATION_APPLIED" if result.applied else "BUCKET_CALIBRATION_MISSING_FIT")
        if result.fit_scope == "model_station":
            reason_codes.append("BUCKET_CALIBRATION_MODEL_STATION")
        elif result.fit_scope == "model_global":
            reason_codes.append("BUCKET_CALIBRATION_MODEL_GLOBAL")
        return result.calibrated_fair_yes, result.metadata

    def _price_distribution_markets(self, markets: list[MarketSnapshot], weather: StationWeatherState) -> dict[str, FairValueResult]:
        if not markets:
            return {}

        reason_codes_by_market = {market.market_id: self._base_reason_codes(market, weather) for market in markets}
        rows = [self._distribution_feature_row(weather) for _market in markets]
        frame = pd.DataFrame([{column: row.get(column, np.nan) for column in self.feature_columns} for row in rows])

        if self.model_type == "high_regression_empirical_residual":
            raw_probabilities = self._empirical_distribution_probabilities(markets, weather, frame)
        else:
            raw_probabilities = self._normal_distribution_probabilities(markets, weather, frame)

        adjusted = np.array(
            [
                self._dynamic_bucket_probability_override(market, weather, float(probability), reason_codes_by_market[market.market_id])
                for market, probability in zip(markets, raw_probabilities)
            ],
            dtype=float,
        )
        adjusted = np.nan_to_num(adjusted, nan=0.0, posinf=0.0, neginf=0.0)
        adjusted = np.clip(adjusted, 0.0, 1.0)
        total = float(adjusted.sum())
        if np.isfinite(total) and total > 0.0:
            probabilities = adjusted / total
        else:
            probabilities = np.full(len(markets), 1.0 / len(markets))

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

    def _distribution_feature_row(self, weather: StationWeatherState) -> dict[str, object]:
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
            "temp_range_so_far": weather.temp_range_so_far,
            "relative_humidity": weather.relative_humidity,
            "wet_bulb_approx": weather.wet_bulb_approx,
            "pressure_mslp": weather.pressure_mslp,
            "pressure_tendency_3h": weather.pressure_tendency_3h,
            "visibility_miles": weather.visibility_miles,
            "precip_1h_in": weather.precip_1h_in,
            "altimeter_inhg": weather.altimeter_inhg,
            "feels_like": weather.feels_like,
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining,
            "hrrr_current_temp_minus_current_temp": hrrr_current - weather.current_temp,
            **_hrrr_rich_features(weather),
        }

    def _empirical_distribution_probabilities(
        self,
        markets: list[MarketSnapshot],
        weather: StationWeatherState,
        frame: pd.DataFrame,
    ) -> np.ndarray:
        if self.residuals is None:
            return np.zeros(len(markets), dtype=float)
        residual_frame = pd.DataFrame(self.residuals).copy()
        if "window" not in residual_frame and "hour_local" in residual_frame:
            residual_frame["window"] = residual_frame["hour_local"].map(_entry_window)
        all_residuals = residual_frame["residual"].dropna().astype(float).to_numpy()
        by_window = {
            str(window): group["residual"].dropna().astype(float).to_numpy()
            for window, group in residual_frame.groupby("window", observed=True)
        } if "window" in residual_frame else {}
        residuals = by_window.get(_entry_window(weather.hour_local), all_residuals) if self.residual_scope == "window" else all_residuals
        if len(residuals) == 0:
            return np.zeros(len(markets), dtype=float)

        means = np.asarray(self.model.predict(frame), dtype=float)
        probabilities = []
        for offset, market in enumerate(markets):
            mean = float(means[offset])
            lower = -np.inf if market.lower_f is None else float(market.lower_f) - mean
            upper = np.inf if market.upper_f is None else float(market.upper_f + 1.0) - mean
            probabilities.append(float(((residuals >= lower) & (residuals < upper)).mean()))
        return np.asarray(probabilities, dtype=float)

    def _normal_distribution_probabilities(
        self,
        markets: list[MarketSnapshot],
        _weather: StationWeatherState,
        frame: pd.DataFrame,
    ) -> np.ndarray:
        preprocessor = self.model["preprocessor"]
        ngboost = self.model["ngboost"]
        dist = ngboost.pred_dist(preprocessor.transform(frame))
        means = _broadcast_array(_dist_attr(dist, "loc", "mean"), len(markets))
        scales = np.maximum(_broadcast_array(_dist_attr(dist, "scale", "std"), len(markets)), 1e-6)
        probabilities = []
        for offset, market in enumerate(markets):
            lower_cdf = 0.0 if market.lower_f is None else _normal_cdf(float(market.lower_f), means[offset], scales[offset])
            upper_cdf = 1.0 if market.upper_f is None else _normal_cdf(float(market.upper_f + 1.0), means[offset], scales[offset])
            probabilities.append(max(float(upper_cdf - lower_cdf), 0.0))
        return np.asarray(probabilities, dtype=float)


def _hrrr_rich_features(weather: StationWeatherState) -> dict[str, float]:
    return {
        "hrrr_temp_next_3h_max": weather.hrrr_temp_next_3h_max,
        "hrrr_temp_next_3h_mean": weather.hrrr_temp_next_3h_mean,
        "hrrr_temp_trend_next_3h": weather.hrrr_temp_trend_next_3h,
        "hrrr_dewpoint_current": weather.hrrr_dewpoint_current,
        "hrrr_dewpoint_next_3h_mean": weather.hrrr_dewpoint_next_3h_mean,
        "hrrr_dewpoint_remaining_mean": weather.hrrr_dewpoint_remaining_mean,
        "hrrr_rh_current": weather.hrrr_rh_current,
        "hrrr_rh_next_3h_mean": weather.hrrr_rh_next_3h_mean,
        "hrrr_rh_remaining_mean": weather.hrrr_rh_remaining_mean,
        "hrrr_wind_speed_current": weather.hrrr_wind_speed_current,
        "hrrr_wind_speed_next_3h_mean": weather.hrrr_wind_speed_next_3h_mean,
        "hrrr_wind_speed_remaining_max": weather.hrrr_wind_speed_remaining_max,
        "hrrr_gust_remaining_max": weather.hrrr_gust_remaining_max,
        "hrrr_cloud_cover_current": weather.hrrr_cloud_cover_current,
        "hrrr_cloud_cover_next_3h_mean": weather.hrrr_cloud_cover_next_3h_mean,
        "hrrr_cloud_cover_remaining_mean": weather.hrrr_cloud_cover_remaining_mean,
        "hrrr_cloud_cover_remaining_max": weather.hrrr_cloud_cover_remaining_max,
        "hrrr_shortwave_next_3h_mean": weather.hrrr_shortwave_next_3h_mean,
        "hrrr_shortwave_remaining_max": weather.hrrr_shortwave_remaining_max,
        "hrrr_forecast_hours_count": weather.hrrr_forecast_hours_count,
    }


def _hrrr_market_context_codes(market: MarketSnapshot, weather: StationWeatherState) -> list[str]:
    if market.market_family == MarketFamily.LOW_TEMP:
        return _low_hrrr_market_context_codes(market, weather)
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


def _low_hrrr_market_context_codes(market: MarketSnapshot, weather: StationWeatherState) -> list[str]:
    assert weather.hrrr_remaining_min is not None
    hrrr_min = weather.hrrr_remaining_min
    low_so_far = weather.low_so_far
    codes: list[str] = []
    if market.lower_f is not None and low_so_far >= market.lower_f and hrrr_min < market.lower_f:
        codes.append("HRRR_REMAINING_BELOW_LOWER_LOG")
    if market.upper_f is not None and low_so_far > market.upper_f and hrrr_min > market.upper_f:
        codes.append("HRRR_REMAINING_ABOVE_UPPER_LOG")
    if market.lower_f is not None and market.upper_f is not None and market.lower_f <= low_so_far <= market.upper_f and hrrr_min >= market.lower_f:
        codes.append("HRRR_SUPPORTS_CURRENT_BUCKET_LOG")
    if market.lower_f is not None and market.upper_f is None and hrrr_min >= market.lower_f:
        codes.append("HRRR_SUPPORTS_OR_HIGHER_LOG")
    if market.upper_f is not None and market.lower_f is None and (low_so_far <= market.upper_f or hrrr_min <= market.upper_f):
        codes.append("HRRR_SUPPORTS_OR_BELOW_LOG")
    return codes


def _hrrr_remaining_for_market(market: MarketSnapshot, weather: StationWeatherState) -> float | None:
    return weather.hrrr_remaining_min if market.market_family == MarketFamily.LOW_TEMP else weather.hrrr_remaining_max


def _artifact_market_family(bundle: dict[str, object], model_name: str) -> MarketFamily:
    value = bundle.get("market_family") or bundle.get("temperature_metric")
    if value is not None:
        text = str(value).upper()
        if text in {"LOW", "LOW_TEMP"}:
            return MarketFamily.LOW_TEMP
        if text in {"HIGH", "HIGH_TEMP"}:
            return MarketFamily.HIGH_TEMP
    return MarketFamily.LOW_TEMP if model_name.startswith("low_") else MarketFamily.HIGH_TEMP


def _exclusive_upper(upper_f: float | None) -> float | None:
    return upper_f + 1.0 if upper_f is not None else None


def _entry_window(hour_local: float | int) -> str:
    if pd.isna(hour_local):
        return "unknown"
    hour = int(hour_local)
    if hour < 11:
        return "early_09_10"
    if hour < 13:
        return "midday_11_12"
    return "late_13_plus"


def _dist_attr(dist: object, primary: str, fallback: str) -> object:
    if hasattr(dist, primary):
        return getattr(dist, primary)
    attr = getattr(dist, fallback)
    return attr() if callable(attr) else attr


def _broadcast_array(values: object, length: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.size == length:
        return array.reshape(length)
    if array.size == 1:
        return np.full(length, float(array.reshape(-1)[0]))
    return np.resize(array.reshape(-1), length)


def _normal_cdf(value: float, mean: float, scale: float) -> float:
    z = (value - mean) / (scale * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))
