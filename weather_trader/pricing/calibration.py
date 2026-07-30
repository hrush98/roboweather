from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from weather_trader.pricing.contracts import (
    V2A_CALIBRATION_VERSION,
    DatasetRole,
    MarketReferenceKind,
    OutcomeLabelSource,
    stable_hash,
)
from weather_trader.pricing.dataset import V2ADatasetArtifact, V2ADatasetRow


class CalibrationBaseline(str, Enum):
    RAW_MODEL = "raw_model"
    MARKET = "market"
    POOLED_PLATT = "pooled_platt"
    MARKET_AWARE = "market_aware"


@dataclass(frozen=True)
class WalkForwardCalibrationConfig:
    version: str = V2A_CALIBRATION_VERSION
    probability_clip: float = 1e-6
    regularization_c: float = 1.0
    min_training_market_dates: int = 5
    reliability_bin_width: float = 0.1

    def __post_init__(self) -> None:
        if not 0.0 < self.probability_clip < 0.5:
            raise ValueError("probability_clip must be between zero and 0.5")
        if self.regularization_c <= 0.0:
            raise ValueError("regularization_c must be positive")
        if self.min_training_market_dates < 1:
            raise ValueError("min_training_market_dates must be positive")
        if not 0.0 < self.reliability_bin_width <= 1.0:
            raise ValueError("reliability_bin_width must be in (0, 1]")

    @property
    def config_hash(self) -> str:
        return stable_hash(asdict(self))


@dataclass(frozen=True)
class FoldCalibrator:
    baseline: CalibrationBaseline
    evaluation_date: str
    training_cutoff_date_exclusive: str
    training_rows: int
    training_market_dates: int
    training_station_dates: int
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float | None
    fallback: str | None
    calibrator_hash: str

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["baseline"] = self.baseline.value
        payload["feature_names"] = list(self.feature_names)
        payload["coefficients"] = list(self.coefficients)
        if not include_hash:
            payload.pop("calibrator_hash", None)
        return payload


@dataclass(frozen=True)
class WalkForwardPrediction:
    decision_id: str
    source_row_hash: str
    market_date: str
    station: str
    outcome_label: int
    evaluation_weight: float
    raw_model_probability: float
    market_probability: float | None
    pooled_platt_probability: float
    market_aware_probability: float
    pooled_platt_calibrator_hash: str
    market_aware_calibrator_hash: str
    training_cutoff_date_exclusive: str
    quality_flags: tuple[str, ...]

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality_flags"] = list(self.quality_flags)
        return payload


@dataclass(frozen=True)
class WalkForwardCalibrationArtifact:
    calibration_version: str
    config_hash: str
    dataset_version: str
    signal_spec_id: str
    signal_spec_hash: str
    predictions: tuple[WalkForwardPrediction, ...]
    calibrators: tuple[FoldCalibrator, ...]
    report: dict[str, Any]

    def manifest(self) -> dict[str, Any]:
        return {
            "calibration_version": self.calibration_version,
            "config_hash": self.config_hash,
            "dataset_version": self.dataset_version,
            "signal_spec_id": self.signal_spec_id,
            "signal_spec_hash": self.signal_spec_hash,
            "prediction_rows": len(self.predictions),
            "evaluation_market_dates": len({row.market_date for row in self.predictions}),
            "calibrator_folds": len(self.calibrators),
            "report": self.report,
        }


def walk_forward_calibration(
    dataset: V2ADatasetArtifact,
    *,
    config: WalkForwardCalibrationConfig | None = None,
) -> WalkForwardCalibrationArtifact:
    config = config or WalkForwardCalibrationConfig()
    evaluation_rows = sorted(
        dataset.evaluation_rows,
        key=lambda row: (row.market_date, row.decision_time_utc, row.decision_id),
    )
    predictions: list[WalkForwardPrediction] = []
    calibrators: list[FoldCalibrator] = []

    for evaluation_date in sorted({row.market_date for row in evaluation_rows}):
        fold_rows = [row for row in evaluation_rows if row.market_date == evaluation_date]
        first_fold_decision_time = min(_parse_utc(row.decision_time_utc) for row in fold_rows)
        training_rows = _dedupe_training_rows(
            [
                row
                for row in (*dataset.fit_rows, *evaluation_rows)
                if row.market_date < evaluation_date
                and _parse_utc(row.outcome_resolved_at_utc) < first_fold_decision_time
            ]
        )
        pooled = _fit_fold(
            CalibrationBaseline.POOLED_PLATT,
            evaluation_date,
            training_rows,
            config,
        )
        market_aware = _fit_fold(
            CalibrationBaseline.MARKET_AWARE,
            evaluation_date,
            training_rows,
            config,
        )
        calibrators.extend((pooled, market_aware))
        for row in fold_rows:
            pooled_probability = _predict_fold(pooled, row, config, fallback=row.raw_model_fair)
            quality_flags: list[str] = []
            if market_aware.fallback is not None:
                quality_flags.append(f"MARKET_AWARE_{market_aware.fallback}")
            if row.market_reference is None:
                quality_flags.append("MARKET_AWARE_EVALUATION_REFERENCE_MISSING")
                market_probability = None
                market_aware_probability = pooled_probability
            else:
                market_probability = _clip_probability(row.market_reference, config.probability_clip)
                market_aware_probability = _predict_fold(
                    market_aware,
                    row,
                    config,
                    fallback=pooled_probability,
                )
            predictions.append(
                WalkForwardPrediction(
                    decision_id=row.decision_id,
                    source_row_hash=row.row_hash,
                    market_date=row.market_date,
                    station=row.station,
                    outcome_label=row.outcome_label,
                    evaluation_weight=row.market_date_cluster_weight,
                    raw_model_probability=_clip_probability(row.raw_model_fair, config.probability_clip),
                    market_probability=market_probability,
                    pooled_platt_probability=pooled_probability,
                    market_aware_probability=market_aware_probability,
                    pooled_platt_calibrator_hash=pooled.calibrator_hash,
                    market_aware_calibrator_hash=market_aware.calibrator_hash,
                    training_cutoff_date_exclusive=evaluation_date,
                    quality_flags=tuple(sorted(set(quality_flags))),
                )
            )

    report = build_calibration_report(predictions, config=config)
    return WalkForwardCalibrationArtifact(
        calibration_version=config.version,
        config_hash=config.config_hash,
        dataset_version=dataset.dataset_version,
        signal_spec_id=dataset.signal_spec_id,
        signal_spec_hash=dataset.signal_spec_hash,
        predictions=tuple(predictions),
        calibrators=tuple(calibrators),
        report=report,
    )


def build_calibration_report(
    predictions: Sequence[WalkForwardPrediction],
    *,
    config: WalkForwardCalibrationConfig | None = None,
) -> dict[str, Any]:
    config = config or WalkForwardCalibrationConfig()
    baseline_fields = {
        CalibrationBaseline.RAW_MODEL: "raw_model_probability",
        CalibrationBaseline.MARKET: "market_probability",
        CalibrationBaseline.POOLED_PLATT: "pooled_platt_probability",
        CalibrationBaseline.MARKET_AWARE: "market_aware_probability",
    }
    metrics = {}
    reliability = {}
    for baseline, field in baseline_fields.items():
        available = [row for row in predictions if getattr(row, field) is not None]
        metrics[baseline.value] = _probability_metrics(available, field, config)
        reliability[baseline.value] = _reliability_rows(available, field, config)
    return {
        "rows": len(predictions),
        "market_dates": len({row.market_date for row in predictions}),
        "station_dates": len({(row.station, row.market_date) for row in predictions}),
        "metrics": metrics,
        "reliability": reliability,
        "weighting": "evaluation market_date_cluster_weight",
        "market_missing_rows": sum(row.market_probability is None for row in predictions),
    }


def write_calibration_artifact(artifact: WalkForwardCalibrationArtifact, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "manifest.json", artifact.manifest())
    _write_jsonl(
        output_dir / "walk_forward_predictions.jsonl",
        (row.canonical_payload() for row in artifact.predictions),
    )
    _write_jsonl(
        output_dir / "calibrators.jsonl",
        (row.canonical_payload() for row in artifact.calibrators),
    )


def load_v2a_dataset_artifact(dataset_dir: Path) -> V2ADatasetArtifact:
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    fit_rows = _read_dataset_rows(dataset_dir / "calibration_fit.jsonl")
    evaluation_rows = _read_dataset_rows(dataset_dir / "frozen_policy_evaluation.jsonl")
    return V2ADatasetArtifact(
        dataset_version=str(manifest["dataset_version"]),
        signal_spec_id=str(manifest["signal_spec_id"]),
        signal_spec_hash=str(manifest["signal_spec_hash"]),
        fit_cutoff_date_exclusive=manifest.get("fit_cutoff_date_exclusive"),
        evaluation_start_date=str(manifest["evaluation_start_date"]),
        evaluation_end_date=manifest.get("evaluation_end_date"),
        fit_rows=tuple(fit_rows),
        evaluation_rows=tuple(evaluation_rows),
        diagnostics=dict(manifest.get("diagnostics") or {}),
    )


def _fit_fold(
    baseline: CalibrationBaseline,
    evaluation_date: str,
    rows: Sequence[V2ADatasetRow],
    config: WalkForwardCalibrationConfig,
) -> FoldCalibrator:
    if baseline == CalibrationBaseline.POOLED_PLATT:
        eligible = list(rows)
        feature_names = ("logit_raw_model_fair",)
    elif baseline == CalibrationBaseline.MARKET_AWARE:
        eligible = [row for row in rows if row.market_reference is not None]
        feature_names = ("logit_raw_model_fair", "logit_market_reference")
    else:
        raise ValueError(f"unsupported fitted baseline: {baseline}")

    market_dates = {row.market_date for row in eligible}
    station_dates = {(row.station, row.market_date) for row in eligible}
    fallback = None
    coefficients: tuple[float, ...] = ()
    intercept: float | None = None
    if len(market_dates) < config.min_training_market_dates:
        fallback = "INSUFFICIENT_MARKET_DATES"
    elif len({row.outcome_label for row in eligible}) < 2:
        fallback = "SINGLE_CLASS"
    else:
        features = np.asarray([_features(row, baseline, config) for row in eligible], dtype=float)
        labels = np.asarray([row.outcome_label for row in eligible], dtype=int)
        weights = np.asarray([row.market_date_cluster_weight for row in eligible], dtype=float)
        try:
            model = LogisticRegression(
                C=config.regularization_c,
                solver="lbfgs",
                max_iter=1000,
                random_state=0,
            )
            model.fit(features, labels, sample_weight=weights)
            coefficients = tuple(float(value) for value in model.coef_[0])
            intercept = float(model.intercept_[0])
        except (ValueError, FloatingPointError):
            fallback = "FIT_FAILED"

    payload = {
        "calibration_version": config.version,
        "config_hash": config.config_hash,
        "baseline": baseline.value,
        "evaluation_date": evaluation_date,
        "training_cutoff_date_exclusive": evaluation_date,
        "training_row_hashes": sorted(row.row_hash for row in eligible),
        "training_market_dates": sorted(market_dates),
        "feature_names": feature_names,
        "coefficients": coefficients,
        "intercept": intercept,
        "fallback": fallback,
    }
    return FoldCalibrator(
        baseline=baseline,
        evaluation_date=evaluation_date,
        training_cutoff_date_exclusive=evaluation_date,
        training_rows=len(eligible),
        training_market_dates=len(market_dates),
        training_station_dates=len(station_dates),
        feature_names=feature_names,
        coefficients=coefficients,
        intercept=intercept,
        fallback=fallback,
        calibrator_hash=stable_hash(payload),
    )


def _predict_fold(
    calibrator: FoldCalibrator,
    row: V2ADatasetRow,
    config: WalkForwardCalibrationConfig,
    *,
    fallback: float,
) -> float:
    if calibrator.fallback is not None or calibrator.intercept is None:
        return _clip_probability(fallback, config.probability_clip)
    features = _features(row, calibrator.baseline, config)
    linear = calibrator.intercept + sum(
        coefficient * feature for coefficient, feature in zip(calibrator.coefficients, features)
    )
    if linear >= 0:
        probability = 1.0 / (1.0 + math.exp(-linear))
    else:
        exp_linear = math.exp(linear)
        probability = exp_linear / (1.0 + exp_linear)
    return _clip_probability(probability, config.probability_clip)


def _features(
    row: V2ADatasetRow,
    baseline: CalibrationBaseline,
    config: WalkForwardCalibrationConfig,
) -> tuple[float, ...]:
    raw = (_logit(row.raw_model_fair, config.probability_clip),)
    if baseline == CalibrationBaseline.POOLED_PLATT:
        return raw
    if baseline == CalibrationBaseline.MARKET_AWARE:
        if row.market_reference is None:
            raise ValueError("market-aware calibration requires a market reference")
        return (*raw, _logit(row.market_reference, config.probability_clip))
    raise ValueError(f"unsupported fitted baseline: {baseline}")


def _probability_metrics(
    rows: Sequence[WalkForwardPrediction],
    field: str,
    config: WalkForwardCalibrationConfig,
) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "market_dates": 0,
            "weight_sum": 0.0,
            "mean_probability": None,
            "observed_rate": None,
            "brier_score": None,
            "log_loss": None,
            "calibration_in_the_large": None,
            "calibration_intercept": None,
            "calibration_slope": None,
        }
    probabilities = np.asarray([float(getattr(row, field)) for row in rows], dtype=float)
    labels = np.asarray([row.outcome_label for row in rows], dtype=float)
    weights = np.asarray([row.evaluation_weight for row in rows], dtype=float)
    weight_sum = float(weights.sum())
    if weight_sum <= 0.0:
        weights = np.ones(len(rows), dtype=float)
        weight_sum = float(len(rows))
    clipped = np.clip(probabilities, config.probability_clip, 1.0 - config.probability_clip)
    observed = float(np.average(labels, weights=weights))
    mean_probability = float(np.average(clipped, weights=weights))
    brier = float(np.average((clipped - labels) ** 2, weights=weights))
    log_loss = float(np.average(-(labels * np.log(clipped) + (1.0 - labels) * np.log(1.0 - clipped)), weights=weights))
    calibration_intercept, calibration_slope = _calibration_line(clipped, labels, weights)
    return {
        "rows": len(rows),
        "market_dates": len({row.market_date for row in rows}),
        "weight_sum": round(weight_sum, 12),
        "mean_probability": round(mean_probability, 12),
        "observed_rate": round(observed, 12),
        "brier_score": round(brier, 12),
        "log_loss": round(log_loss, 12),
        "calibration_in_the_large": round(observed - mean_probability, 12),
        "calibration_intercept": calibration_intercept,
        "calibration_slope": calibration_slope,
    }


def _calibration_line(
    probabilities: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
) -> tuple[float | None, float | None]:
    if len(probabilities) < 2 or len(set(labels.tolist())) < 2:
        return None, None
    model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000, random_state=0)
    try:
        model.fit(np.asarray([_logit(value, 1e-12) for value in probabilities]).reshape(-1, 1), labels, sample_weight=weights)
    except (ValueError, FloatingPointError):
        return None, None
    return round(float(model.intercept_[0]), 12), round(float(model.coef_[0][0]), 12)


def _reliability_rows(
    rows: Sequence[WalkForwardPrediction],
    field: str,
    config: WalkForwardCalibrationConfig,
) -> list[dict[str, Any]]:
    bins: dict[int, list[WalkForwardPrediction]] = {}
    bin_count = max(1, int(math.ceil(1.0 / config.reliability_bin_width)))
    for row in rows:
        probability = float(getattr(row, field))
        index = min(int(probability / config.reliability_bin_width), bin_count - 1)
        bins.setdefault(index, []).append(row)
    output = []
    for index in sorted(bins):
        members = bins[index]
        weights = np.asarray([row.evaluation_weight for row in members], dtype=float)
        if float(weights.sum()) <= 0.0:
            weights = np.ones(len(members), dtype=float)
        probabilities = np.asarray([float(getattr(row, field)) for row in members], dtype=float)
        labels = np.asarray([row.outcome_label for row in members], dtype=float)
        output.append(
            {
                "lower": round(index * config.reliability_bin_width, 12),
                "upper": round(min(1.0, (index + 1) * config.reliability_bin_width), 12),
                "rows": len(members),
                "market_dates": len({row.market_date for row in members}),
                "weight_sum": round(float(weights.sum()), 12),
                "mean_probability": round(float(np.average(probabilities, weights=weights)), 12),
                "observed_rate": round(float(np.average(labels, weights=weights)), 12),
            }
        )
    return output


def _dedupe_training_rows(rows: Iterable[V2ADatasetRow]) -> list[V2ADatasetRow]:
    selected: dict[tuple[str, str], V2ADatasetRow] = {}
    for row in rows:
        key = (row.decision_id, row.market_date)
        existing = selected.get(key)
        if existing is None or (
            existing.dataset_role == DatasetRole.CALIBRATION_FIT
            and row.dataset_role == DatasetRole.FROZEN_POLICY_EVALUATION
        ):
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (row.market_date, row.decision_time_utc, row.decision_id))


def _read_dataset_rows(path: Path) -> list[V2ADatasetRow]:
    output = []
    if not path.exists():
        return output
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["dataset_role"] = DatasetRole(payload["dataset_role"])
        payload["market_reference_kind"] = MarketReferenceKind(payload["market_reference_kind"])
        payload["outcome_label_source"] = OutcomeLabelSource(payload["outcome_label_source"])
        payload["source_prediction_snapshot_ids"] = tuple(payload["source_prediction_snapshot_ids"])
        payload["quality_flags"] = tuple(payload["quality_flags"])
        output.append(V2ADatasetRow(**payload))
    return output


def _clip_probability(value: float, epsilon: float) -> float:
    if not math.isfinite(float(value)):
        raise ValueError("probability must be finite")
    return min(1.0 - epsilon, max(epsilon, float(value)))


def _logit(value: float, epsilon: float) -> float:
    probability = _clip_probability(value, epsilon)
    return math.log(probability / (1.0 - probability))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
