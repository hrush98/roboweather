from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FixedSupport:
    """Outcome-independent integer support; endpoint cells absorb tail mass."""

    minimum: int = -20
    maximum: int = 130
    unit: str = "F"

    def __post_init__(self) -> None:
        if self.minimum >= self.maximum:
            raise ValueError("fixed support minimum must be below maximum")
        if self.unit != "F":
            raise ValueError("F0B currently freezes Fahrenheit support only")

    @property
    def values(self) -> np.ndarray:
        return np.arange(self.minimum, self.maximum + 1, dtype=int)


@dataclass(frozen=True)
class EvaluationContract:
    """Frozen F0B choices; any change creates a different fingerprint."""

    version: str = "forecast_fixed_support_weather_date_v1"
    support: FixedSupport = FixedSupport()
    validation_start: str = "2025-01-01"
    validation_end_exclusive: str = "2026-01-01"
    horizon_hour_local: int = 14
    snapshot_selector: str = "latest_at_or_before_local_hour"
    uncertainty_cluster: str = "local_date"
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20260812
    duplicate_probability_correlation: float = 0.999
    duplicate_mean_total_variation: float = 0.005

    def __post_init__(self) -> None:
        if not 0 <= self.horizon_hour_local <= 23:
            raise ValueError("horizon_hour_local must be between 0 and 23")
        if self.bootstrap_samples < 1:
            raise ValueError("bootstrap_samples must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def select_horizon_snapshots(dataset: pd.DataFrame, contract: EvaluationContract) -> pd.DataFrame:
    """Deduplicate synthetic thresholds, then select one row per station/date."""

    required = {"station", "local_date", "snapshot_time_local", "hour_local", "final_high_tmpf"}
    missing = sorted(required - set(dataset.columns))
    if missing:
        raise ValueError(f"dataset missing required columns: {', '.join(missing)}")
    frame = dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"], errors="raise").dt.date
    frame["snapshot_time_local"] = pd.to_datetime(frame["snapshot_time_local"], errors="raise", utc=True)
    frame["hour_local"] = pd.to_numeric(frame["hour_local"], errors="coerce")
    start = pd.Timestamp(contract.validation_start).date()
    end = pd.Timestamp(contract.validation_end_exclusive).date()
    frame = frame.loc[
        (frame["local_date"] >= start)
        & (frame["local_date"] < end)
        & (frame["hour_local"] <= contract.horizon_hour_local)
        & frame["final_high_tmpf"].notna()
    ].copy()
    identity = ["station", "local_date", "snapshot_time_local"]
    conflicts = frame.groupby(identity, observed=True)["final_high_tmpf"].nunique()
    if (conflicts > 1).any():
        raise ValueError("a station/date/snapshot maps to multiple outcomes")
    snapshots = frame.sort_values(identity).drop_duplicates(identity, keep="first")
    snapshots = snapshots.groupby(["station", "local_date"], observed=True, as_index=False).tail(1).copy()
    snapshots["horizon"] = f"latest_le_{contract.horizon_hour_local:02d}_local"
    snapshots["target_value"] = pd.to_numeric(snapshots["final_high_tmpf"], errors="raise").round().astype(int)
    outside = ~snapshots["target_value"].between(contract.support.minimum, contract.support.maximum)
    if outside.any():
        raise ValueError(f"targets outside frozen support: {sorted(snapshots.loc[outside, 'target_value'].unique())}")
    if snapshots.duplicated(["station", "local_date", "horizon"]).any():
        raise ValueError("snapshot selector did not produce unique evaluation rows")
    return snapshots.sort_values(["local_date", "station"]).reset_index(drop=True)


def normalize_probability_matrix(probabilities: np.ndarray) -> np.ndarray:
    matrix = np.asarray(probabilities, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("probabilities must be a two-dimensional row-by-support matrix")
    if not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("probabilities must be finite and nonnegative")
    totals = matrix.sum(axis=1)
    if (totals <= 0).any():
        raise ValueError("every forecast row must have positive total mass")
    return matrix / totals[:, None]


def evaluate_probability_matrix(probabilities: np.ndarray, targets: Sequence[int], local_dates: Sequence[Any], contract: EvaluationContract) -> dict[str, Any]:
    """Score full distributions after averaging station rows within weather date."""

    matrix = normalize_probability_matrix(probabilities)
    target = np.asarray(targets, dtype=int)
    dates = np.asarray([str(value) for value in local_dates], dtype=object)
    support = contract.support.values
    if len(matrix) != len(target) or len(matrix) != len(dates) or matrix.shape[1] != len(support):
        raise ValueError("probabilities, targets, dates, and frozen support do not align")
    target_index = target - contract.support.minimum
    if (target_index < 0).any() or (target_index >= len(support)).any():
        raise ValueError("targets fall outside frozen support")
    chosen = np.clip(matrix[np.arange(len(matrix)), target_index], 1e-12, 1.0)
    forecast_cdf = np.cumsum(matrix, axis=1)
    observed_cdf = (support[None, :] >= target[:, None]).astype(float)
    rps = np.sum((forecast_cdf - observed_cdf) ** 2, axis=1)
    per_row = pd.DataFrame(
        {
            "local_date": dates,
            "log_loss": -np.log(chosen),
            "rps": rps,
            "threshold_brier": rps / max(len(support) - 1, 1),
            "top_bucket_accuracy": (support[np.argmax(matrix, axis=1)] == target).astype(float),
            "entropy": -np.sum(matrix * np.log(np.clip(matrix, 1e-12, 1.0)), axis=1),
        }
    )
    per_date = per_row.groupby("local_date", observed=True).mean(numeric_only=True)
    return {
        "rows": int(len(matrix)),
        "weather_dates": int(len(per_date)),
        "metrics": {key: float(value) for key, value in per_date.mean().items()},
        "weather_date_clustered_95pct_ci": _cluster_bootstrap_intervals(per_date, contract),
    }


def pairwise_prediction_diagnostics(model_probabilities: Mapping[str, np.ndarray], support: FixedSupport, artifact_hashes: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    names = list(model_probabilities)
    matrices = {name: normalize_probability_matrix(model_probabilities[name]) for name in names}
    if len({matrix.shape for matrix in matrices.values()}) > 1:
        raise ValueError("pairwise diagnostics require identical row and support coverage")
    values = support.values.astype(float)
    hashes = artifact_hashes or {}
    rows = []
    for offset, left_name in enumerate(names):
        for right_name in names[offset + 1 :]:
            left, right = matrices[left_name], matrices[right_name]
            expected_left, expected_right = left @ values, right @ values
            rows.append(
                {
                    "model_a": left_name,
                    "model_b": right_name,
                    "probability_correlation": _safe_correlation(left.ravel(), right.ravel()),
                    "expected_high_correlation": _safe_correlation(expected_left, expected_right),
                    "mean_total_variation": float(np.mean(0.5 * np.abs(left - right).sum(axis=1))),
                    "mean_absolute_expected_high_disagreement_f": float(np.mean(np.abs(expected_left - expected_right))),
                    "exact_artifact_hash_match": bool(hashes.get(left_name) and hashes.get(left_name) == hashes.get(right_name)),
                }
            )
    return rows


def prune_behavioral_duplicates(model_names: Sequence[str], pairwise_rows: Sequence[Mapping[str, Any]], contract: EvaluationContract) -> list[dict[str, Any]]:
    """Outcome-blind connected-component pruning; input order is precedence."""

    ordered = list(model_names)
    parent = {name: name for name in ordered}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            representative = min((left_root, right_root), key=ordered.index)
            parent[right_root if representative == left_root else left_root] = representative

    for row in pairwise_rows:
        duplicate = bool(row.get("exact_artifact_hash_match")) or (
            float(row["probability_correlation"]) >= contract.duplicate_probability_correlation
            and float(row["mean_total_variation"]) <= contract.duplicate_mean_total_variation
        )
        if duplicate:
            union(str(row["model_a"]), str(row["model_b"]))
    return [
        {"model_id": name, "representative": find(name), "decision": "RETAIN" if name == find(name) else "COLLAPSE_DUPLICATE"}
        for name in ordered
    ]


def normalize_observed_market_ladder(ladder: pd.DataFrame, support: FixedSupport, price_column: str = "price") -> np.ndarray:
    """Normalize one actual complete ladder; fail on gaps, overlap, or fallback."""

    required = {"bucket_lower", "bucket_upper", price_column}
    missing = sorted(required - set(ladder.columns))
    if missing:
        raise ValueError(f"market ladder missing columns: {', '.join(missing)}")
    prices = pd.to_numeric(ladder[price_column], errors="raise").to_numpy(float)
    if not np.isfinite(prices).all() or (prices < 0).any() or prices.sum() <= 0:
        raise ValueError("market ladder prices must be finite, nonnegative, and nonzero")
    prices /= prices.sum()
    values = support.values
    projected, membership = np.zeros(len(values)), np.zeros(len(values), dtype=int)
    for offset, row in enumerate(ladder.itertuples(index=False)):
        active = np.ones(len(values), dtype=bool)
        if pd.notna(row.bucket_lower):
            active &= values >= int(row.bucket_lower)
        if pd.notna(row.bucket_upper):
            active &= values < int(row.bucket_upper)
        if not active.any():
            raise ValueError("market bucket covers no values on frozen support")
        membership[active] += 1
        projected[active] += prices[offset] / int(active.sum())
    if not np.all(membership == 1):
        raise ValueError("observed market ladder must partition the frozen support exactly once")
    return normalize_probability_matrix(projected[None, :])[0]


def _cluster_bootstrap_intervals(per_date: pd.DataFrame, contract: EvaluationContract) -> dict[str, list[float]]:
    rng = np.random.default_rng(contract.bootstrap_seed)
    values = per_date.to_numpy(float)
    draws = np.empty((contract.bootstrap_samples, values.shape[1]))
    for index in range(contract.bootstrap_samples):
        draws[index] = values[rng.integers(0, len(values), size=len(values))].mean(axis=0)
    return {column: [float(value) for value in np.quantile(draws[:, offset], [0.025, 0.975])] for offset, column in enumerate(per_date.columns)}


def _safe_correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2 or math.isclose(float(np.std(left)), 0.0) or math.isclose(float(np.std(right)), 0.0):
        return 1.0 if np.allclose(left, right) else 0.0
    return float(np.corrcoef(left, right)[0, 1])
