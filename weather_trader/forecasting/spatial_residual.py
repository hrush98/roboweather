"""Frozen causal ASOS-neighbor residual features for F4 forecast research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from weather_trader.forecasting.evaluation import FixedSupport, normalize_probability_matrix
from weather_trader.forecasting.remaining_heating import enforce_high_so_far_lower_bound
from weather_trader.stations.metadata import get_station


SPATIAL_RESIDUAL_VERSION = "asos_upwind_residual_exact_cutoff_v2"


@dataclass(frozen=True)
class NeighborSite:
    station: str
    latitude: float
    longitude: float
    elevation_m: float


# Outcome-blind selection frozen from the IEM AZOS metadata endpoint on
# 2026-08-13: five nearest online US ASOS-class stations within 150 km whose
# archive began no later than 2022-01-01. Target stations are excluded.
FROZEN_NEIGHBORS: Mapping[str, tuple[NeighborSite, ...]] = {
    "KATL": (NeighborSite("FTY", 33.7800, -84.5200, 256.0), NeighborSite("HMP", 33.3899, -84.3310, 256.7249), NeighborSite("PDK", 33.8756, -84.3020, 305.0), NeighborSite("MGE", 33.9153, -84.5163, 326.0), NeighborSite("FFC", 33.3553, -84.5669, 264.0)),
    "KBKF": (NeighborSite("DEN", 39.8328, -104.6575, 1656.0), NeighborSite("APA", 39.5700, -104.8500, 1793.0), NeighborSite("CFO", 39.7841, -104.5376, 1674.3636), NeighborSite("BJC", 39.9088, -105.1172, 1724.0), NeighborSite("EIK", 40.0102, -105.0480, 1563.6)),
    "KBOS": (NeighborSite("OWD", 42.1912, -71.1733, 15.0), NeighborSite("BVY", 42.5841, -70.9161, 33.0), NeighborSite("BED", 42.4700, -71.2890, 41.0), NeighborSite("GHG", 42.0982, -70.6721, 3.4), NeighborSite("LWM", 42.7172, -71.1234, 45.0)),
    "KDAL": (NeighborSite("ADS", 32.9686, -96.8364, 196.0), NeighborSite("DFW", 32.8968, -97.0380, 182.0), NeighborSite("RBD", 32.6800, -96.8700, 201.0), NeighborSite("GPM", 32.6988, -97.0469, 180.0), NeighborSite("GKY", 32.6639, -97.0943, 192.0)),
    "KDCA": (NeighborSite("ADW", 38.8108, -76.8670, 86.0), NeighborSite("CGS", 38.9806, -76.9223, 14.6), NeighborSite("DAA", 38.7150, -77.1810, 21.0), NeighborSite("FME", 39.0854, -76.7594, 46.0), NeighborSite("IAD", 38.9348, -77.4473, 98.0)),
    "KDEN": (NeighborSite("CFO", 39.7841, -104.5376, 1674.3636), NeighborSite("BKF", 39.7017, -104.7517, 1726.0), NeighborSite("APA", 39.5700, -104.8500, 1793.0), NeighborSite("EIK", 40.0102, -105.0480, 1563.6), NeighborSite("BJC", 39.9088, -105.1172, 1724.0)),
    "KDFW": (NeighborSite("DAL", 32.8471, -96.8518, 148.0), NeighborSite("ADS", 32.9686, -96.8364, 196.0), NeighborSite("GPM", 32.6988, -97.0469, 180.0), NeighborSite("GKY", 32.6639, -97.0943, 192.0), NeighborSite("AFW", 32.9716, -97.3179, 204.81914)),
    "KHOU": (NeighborSite("EFD", 29.6073, -95.1588, 10.0), NeighborSite("MCJ", 29.7140, -95.3950, 22.525835), NeighborSite("LVJ", 29.5189, -95.2417, 13.164371), NeighborSite("T41", 29.6692, -95.0641, 7.0), NeighborSite("AXH", 29.5061, -95.4769, 20.7)),
    "KLAX": (NeighborSite("HHR", 33.9228, -118.3352, 19.0), NeighborSite("SMO", 34.0210, -118.4471, 53.0), NeighborSite("TOA", 33.8034, -118.3396, 31.0), NeighborSite("LGB", 33.8118, -118.1472, 12.0), NeighborSite("BUR", 34.2007, -118.3587, 236.0)),
    "KLGA": (NeighborSite("NYC", 40.7790, -73.9692, 27.0), NeighborSite("JRB", 40.7012, -74.0090, 0.0), NeighborSite("TEB", 40.8590, -74.0562, 3.0), NeighborSite("JFK", 40.6386, -73.7622, 7.0), NeighborSite("EWR", 40.6827, -74.1693, 2.0)),
    "KMIA": (NeighborSite("OPF", 25.9102, -80.2828, 3.0), NeighborSite("TMB", 25.6423, -80.4347, 3.0), NeighborSite("HWO", 25.9996, -80.2412, 3.0), NeighborSite("FLL", 26.0787, -80.1622, 1.0), NeighborSite("HST", 25.4884, -80.3837, 2.0)),
    "KORD": (NeighborSite("PWK", 42.1208, -87.9047, 203.0), NeighborSite("06C", 41.9913, -88.1050, 243.0), NeighborSite("MDW", 41.7860, -87.7524, 188.0), NeighborSite("DPA", 41.9078, -88.2486, 231.0), NeighborSite("LOT", 41.6081, -88.0962, 205.0)),
    "KSEA": (NeighborSite("RNT", 47.4931, -122.2158, 9.0), NeighborSite("BFI", 47.5300, -122.3000, 5.0), NeighborSite("TIW", 47.2675, -122.5761, 89.0), NeighborSite("PWT", 47.4902, -122.7648, 147.0), NeighborSite("TCM", 47.1377, -122.4765, 98.0)),
    "KSFO": (NeighborSite("HAF", 37.5136, -122.4996, 9.366775), NeighborSite("OAK", 37.7178, -122.2330, 2.0), NeighborSite("SQL", 37.5119, -122.2483, 1.0), NeighborSite("HWD", 37.6588, -122.1212, 8.88734), NeighborSite("PAO", 37.4611, -122.1151, 2.0)),
}

TARGET_ELEVATION_M: Mapping[str, float] = {
    "KATL": 315.0,
    "KBKF": 1726.0,
    "KBOS": 9.0,
    "KDAL": 148.0,
    "KDCA": 20.0,
    "KDEN": 1656.0,
    "KDFW": 182.0,
    "KHOU": 14.0,
    "KLAX": 32.0,
    "KLGA": 9.0,
    "KMIA": 4.0,
    "KORD": 205.0,
    "KSEA": 137.0,
    "KSFO": 5.0,
}


SPATIAL_FEATURES = (
    "spatial_upwind_temp_residual_f",
    "spatial_weighted_temp_residual_f",
    "spatial_warming_residual_f_per_hour",
    "spatial_temp_gradient_f",
    "spatial_dewpoint_gradient_f",
    "spatial_boundary_score",
    "spatial_min_travel_time_hours",
    "spatial_neighbor_count",
)


@dataclass(frozen=True)
class SpatialResidualContract:
    version: str = SPATIAL_RESIDUAL_VERSION
    observation_availability_lag_minutes: int = 10
    maximum_observation_age_minutes: int = 90
    distance_scale_km: float = 75.0
    upwind_sigma_degrees: float = 60.0
    elevation_scale_m: float = 500.0
    minimum_neighbors: int = 2
    ridge_alpha: float = 10.0
    maximum_correction_f: float = 5.0
    feature_columns: tuple[str, ...] = SPATIAL_FEATURES

    @property
    def fingerprint(self) -> str:
        payload = {
            **asdict(self),
            "neighbors": {
                key: [asdict(site) for site in value]
                for key, value in sorted(FROZEN_NEIGHBORS.items())
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class SpatialResidualCalibrator:
    """Fixed-complexity correction to a predecessor distribution's mean."""

    def __init__(self, contract: SpatialResidualContract | None = None) -> None:
        self.contract = contract or SpatialResidualContract()
        self.pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="constant", fill_value=0.0)),
                ("scale", StandardScaler()),
                ("ridge", Ridge(alpha=self.contract.ridge_alpha, fit_intercept=False)),
            ]
        )
        self.fitted = False
        self.training_summary: dict[str, Any] = {}

    def fit(
        self,
        features: pd.DataFrame,
        predecessor: np.ndarray,
        targets: Sequence[int],
        support: FixedSupport,
    ) -> "SpatialResidualCalibrator":
        matrix = normalize_probability_matrix(predecessor)
        if len(features) != len(matrix) or len(features) != len(targets):
            raise ValueError("features, predecessor, and targets do not align")
        expected = matrix @ support.values.astype(float)
        errors = np.asarray(targets, dtype=float) - expected
        eligible = _eligible_mask(features, self.contract)
        if int(eligible.sum()) < 10:
            raise ValueError("spatial calibrator requires at least 10 eligible rows")
        self.pipeline.fit(features.loc[eligible, self.contract.feature_columns], errors[eligible])
        self.fitted = True
        self.training_summary = {
            "rows": int(len(features)),
            "eligible_rows": int(eligible.sum()),
            "mean_target_error_f": float(errors[eligible].mean()),
            "feature_columns": list(self.contract.feature_columns),
        }
        return self

    def predict_correction(self, features: pd.DataFrame) -> np.ndarray:
        if not self.fitted:
            raise ValueError("spatial calibrator has not been fitted")
        corrections = np.zeros(len(features), dtype=float)
        eligible = _eligible_mask(features, self.contract)
        if eligible.any():
            values = self.pipeline.predict(
                features.loc[eligible, self.contract.feature_columns]
            )
            corrections[eligible] = np.clip(
                values,
                -self.contract.maximum_correction_f,
                self.contract.maximum_correction_f,
            )
        return corrections

    def predict_proba(
        self,
        features: pd.DataFrame,
        predecessor: np.ndarray,
        high_so_far: Sequence[float],
        support: FixedSupport,
    ) -> np.ndarray:
        corrections = self.predict_correction(features)
        shifted = shift_probability_matrix(predecessor, corrections, support)
        return enforce_high_so_far_lower_bound(shifted, high_so_far, support)


def frozen_neighbor_stations(targets: Sequence[str] | None = None) -> list[NeighborSite]:
    selected = sorted(set(FROZEN_NEIGHBORS if targets is None else targets))
    sites: dict[str, NeighborSite] = {}
    for target in selected:
        for site in FROZEN_NEIGHBORS.get(str(target).upper(), ()):
            sites[site.station] = site
    return [sites[key] for key in sorted(sites)]


def materialize_spatial_features(
    decisions: pd.DataFrame,
    observations: pd.DataFrame,
    model_points: pd.DataFrame,
    contract: SpatialResidualContract | None = None,
) -> pd.DataFrame:
    """Build one causal feature row per decision from frozen neighbor inputs.

    ``model_points`` is keyed by ``decision_id`` and neighbor ``station`` and
    carries HRRR temperature at the decision-valid hour plus the prior hour.
    """

    contract = contract or SpatialResidualContract()
    required_decisions = {"decision_id", "station", "decision_time_utc"}
    required_observations = {"station", "valid", "tmpf", "dwpf", "drct", "sknt"}
    required_model = {"decision_id", "station", "hrrr_tmpf", "hrrr_previous_tmpf"}
    _require_columns(decisions, required_decisions, "decisions")
    _require_columns(observations, required_observations, "observations")
    _require_columns(model_points, required_model, "model_points")
    obs = _prepare_observations(observations, contract)
    obs_groups = {
        str(station).upper(): group.sort_values("available_at_utc").reset_index(drop=True)
        for station, group in obs.groupby("station", observed=True)
    }
    model_lookup = {
        (str(row.decision_id), str(row.station).upper()): row
        for row in model_points.itertuples(index=False)
    }
    rows = []
    for decision in decisions.itertuples(index=False):
        decision_id = str(decision.decision_id)
        target_id = str(decision.station).upper()
        decision_time = pd.Timestamp(decision.decision_time_utc)
        if decision_time.tzinfo is None:
            raise ValueError("decision_time_utc must be timezone-aware")
        target = get_station(target_id)
        target_obs = _latest_observation(
            obs_groups.get(target_id.removeprefix("K")), decision_time, contract
        )
        target_wind = _finite(target_obs.get("drct")) if target_obs is not None else None
        target_temp = _finite(target_obs.get("tmpf")) if target_obs is not None else None
        target_dewpoint = _finite(target_obs.get("dwpf")) if target_obs is not None else None
        candidates: list[dict[str, float]] = []
        for site in FROZEN_NEIGHBORS.get(target_id, ()):
            latest = _latest_observation(obs_groups.get(site.station), decision_time, contract)
            model = model_lookup.get((decision_id, site.station))
            if latest is None or model is None:
                continue
            observed_temp = _finite(latest.get("tmpf"))
            modeled_temp = _finite(getattr(model, "hrrr_tmpf"))
            if observed_temp is None or modeled_temp is None:
                continue
            previous_obs = _previous_observation(
                obs_groups.get(site.station), pd.Timestamp(latest["valid"]), decision_time, contract
            )
            observed_warming = None
            if previous_obs is not None:
                hours = (pd.Timestamp(latest["valid"]) - pd.Timestamp(previous_obs["valid"])).total_seconds() / 3600.0
                previous_temp = _finite(previous_obs.get("tmpf"))
                if previous_temp is not None and hours > 0:
                    observed_warming = (observed_temp - previous_temp) / hours
            previous_model = _finite(getattr(model, "hrrr_previous_tmpf"))
            model_warming = modeled_temp - previous_model if previous_model is not None else None
            distance = haversine_km(target.latitude, target.longitude, site.latitude, site.longitude)
            bearing = initial_bearing_degrees(target.latitude, target.longitude, site.latitude, site.longitude)
            angular = 1.0 if target_wind is None else math.exp(
                -0.5 * (angular_difference_degrees(bearing, target_wind) / contract.upwind_sigma_degrees) ** 2
            )
            distance_weight = math.exp(-distance / contract.distance_scale_km)
            elevation_weight = math.exp(-abs(site.elevation_m - _target_elevation(target_id)) / contract.elevation_scale_m)
            base_weight = distance_weight * elevation_weight
            wind_speed_mph = (_finite(target_obs.get("sknt")) or 0.0) * 1.15078 if target_obs is not None else 0.0
            candidates.append(
                {
                    "residual": observed_temp - modeled_temp,
                    "warming_residual": (observed_warming - model_warming) if observed_warming is not None and model_warming is not None else np.nan,
                    "temp_gradient": observed_temp - target_temp if target_temp is not None else np.nan,
                    "dewpoint_gradient": (_finite(latest.get("dwpf")) - target_dewpoint) if _finite(latest.get("dwpf")) is not None and target_dewpoint is not None else np.nan,
                    "base_weight": base_weight,
                    "upwind_weight": base_weight * angular,
                    "travel_time": distance / max(wind_speed_mph * 1.60934, 8.0),
                }
            )
        rows.append(_aggregate_candidates(decision_id, target_id, candidates, contract))
    return pd.DataFrame(rows)


def shift_probability_matrix(
    probabilities: np.ndarray,
    corrections_f: Sequence[float],
    support: FixedSupport,
) -> np.ndarray:
    matrix = normalize_probability_matrix(probabilities)
    corrections = np.asarray(corrections_f, dtype=float)
    if len(matrix) != len(corrections) or not np.isfinite(corrections).all():
        raise ValueError("probabilities and finite corrections must align")
    output = np.zeros_like(matrix)
    for row_index, correction in enumerate(corrections):
        destinations = support.values.astype(float) + correction
        lower = np.floor(destinations).astype(int)
        upper = np.ceil(destinations).astype(int)
        upper_weight = destinations - lower
        lower_weight = 1.0 - upper_weight
        lower_index = np.clip(lower - support.minimum, 0, len(support.values) - 1)
        upper_index = np.clip(upper - support.minimum, 0, len(support.values) - 1)
        np.add.at(output[row_index], lower_index, matrix[row_index] * lower_weight)
        np.add.at(output[row_index], upper_index, matrix[row_index] * upper_weight)
    return normalize_probability_matrix(output)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 12742.0 * math.asin(math.sqrt(value))


def initial_bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference_degrees(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _prepare_observations(frame: pd.DataFrame, contract: SpatialResidualContract) -> pd.DataFrame:
    output = frame.copy()
    output["station"] = output["station"].astype(str).str.upper().str.removeprefix("K")
    output["valid"] = pd.to_datetime(output["valid"], utc=True, errors="raise")
    output["available_at_utc"] = output["valid"] + pd.to_timedelta(contract.observation_availability_lag_minutes, unit="m")
    for column in ("tmpf", "dwpf", "drct", "sknt"):
        output[column] = pd.to_numeric(output[column], errors="coerce")
    valid = output["tmpf"].between(-80.0, 140.0)
    valid &= output["dwpf"].isna() | (output["dwpf"].between(-100.0, 100.0) & (output["dwpf"] <= output["tmpf"] + 2.0))
    valid &= output["drct"].isna() | output["drct"].between(0.0, 360.0)
    valid &= output["sknt"].isna() | output["sknt"].between(0.0, 100.0)
    return output.loc[valid].sort_values(["station", "available_at_utc"]).reset_index(drop=True)


def _latest_observation(group: pd.DataFrame | None, decision_time: pd.Timestamp, contract: SpatialResidualContract) -> pd.Series | None:
    if group is None or group.empty:
        return None
    eligible = group.loc[group["available_at_utc"] <= decision_time]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    age = (decision_time - pd.Timestamp(row["valid"])).total_seconds() / 60.0
    return None if age > contract.maximum_observation_age_minutes else row


def _previous_observation(group: pd.DataFrame | None, latest_valid: pd.Timestamp, decision_time: pd.Timestamp, contract: SpatialResidualContract) -> pd.Series | None:
    if group is None or group.empty:
        return None
    lower = latest_valid - timedelta(minutes=120)
    upper = latest_valid - timedelta(minutes=30)
    eligible = group.loc[(group["valid"] >= lower) & (group["valid"] <= upper) & (group["available_at_utc"] <= decision_time)]
    return None if eligible.empty else eligible.iloc[-1]


def _aggregate_candidates(decision_id: str, station: str, candidates: list[dict[str, float]], contract: SpatialResidualContract) -> dict[str, Any]:
    output: dict[str, Any] = {"decision_id": decision_id, "station": station, "spatial_neighbor_count": len(candidates)}
    for feature in SPATIAL_FEATURES:
        output.setdefault(feature, np.nan)
    if not candidates:
        return output
    frame = pd.DataFrame(candidates)
    output["spatial_weighted_temp_residual_f"] = _weighted_mean(frame["residual"], frame["base_weight"])
    output["spatial_upwind_temp_residual_f"] = _weighted_mean(frame["residual"], frame["upwind_weight"])
    output["spatial_warming_residual_f_per_hour"] = _weighted_mean(frame["warming_residual"], frame["upwind_weight"])
    output["spatial_temp_gradient_f"] = _weighted_mean(frame["temp_gradient"], frame["upwind_weight"])
    output["spatial_dewpoint_gradient_f"] = _weighted_mean(frame["dewpoint_gradient"], frame["upwind_weight"])
    residual = frame["residual"].to_numpy(float)
    output["spatial_boundary_score"] = float(np.nanstd(residual)) if np.isfinite(residual).any() else np.nan
    output["spatial_min_travel_time_hours"] = float(frame["travel_time"].min())
    return output


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    value = pd.to_numeric(values, errors="coerce").to_numpy(float)
    weight = pd.to_numeric(weights, errors="coerce").to_numpy(float)
    valid = np.isfinite(value) & np.isfinite(weight) & (weight > 0)
    return float(np.average(value[valid], weights=weight[valid])) if valid.any() else np.nan


def _eligible_mask(features: pd.DataFrame, contract: SpatialResidualContract) -> np.ndarray:
    count = pd.to_numeric(features.get("spatial_neighbor_count"), errors="coerce").fillna(0).to_numpy()
    residual = pd.to_numeric(features.get("spatial_upwind_temp_residual_f"), errors="coerce").to_numpy()
    return (count >= contract.minimum_neighbors) & np.isfinite(residual)


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _target_elevation(station: str) -> float:
    try:
        return TARGET_ELEVATION_M[station]
    except KeyError as exc:
        raise ValueError(f"target elevation is not frozen for {station}") from exc
