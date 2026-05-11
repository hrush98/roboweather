from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from weather_trader.models.bucket_classifier import (
    BucketModelConfig,
    LadderConfig,
    build_bucket_validation_predictions,
    build_grouped_metrics,
    build_threshold_bucket_validation_predictions,
    build_synthetic_bucket_dataset,
    normalize_grouped_probabilities,
    train_bucket_classifier,
    train_catboost_bucket_classifier,
    tune_bucket_model_configs,
)
from weather_trader.models.high_regressor import build_regression_bucket_validation_predictions


def test_synthetic_ladder_generation_has_tails_and_one_winner() -> None:
    dataset = pd.DataFrame([_source_row("KAAA", "2024-07-01", 79.5, 78.0, threshold=79.0, target=1)])

    candidates = build_synthetic_bucket_dataset(dataset, LadderConfig(bounded_buckets_each_side=3))

    assert candidates["bucket_label"].tolist() == [
        "<76F",
        "76-77F",
        "77-78F",
        "78-79F",
        "79-80F",
        "80-81F",
        "81-82F",
        "82-83F",
        ">=83F",
    ]
    assert int(candidates["target"].sum()) == 1
    winner = candidates.loc[candidates["target"] == 1].iloc[0]
    assert winner["bucket_label"] == "79-80F"


def test_half_open_bucket_semantics_put_boundary_in_upper_bucket() -> None:
    dataset = pd.DataFrame([_source_row("KAAA", "2024-07-01", 80.0, 78.0, threshold=80.0, target=1)])

    candidates = build_synthetic_bucket_dataset(dataset, LadderConfig(bounded_buckets_each_side=3))

    assert int(candidates["target"].sum()) == 1
    assert candidates.loc[candidates["target"] == 1, "bucket_label"].iloc[0] == "80-81F"


def test_bucket_feature_construction_handles_gaps_and_open_tails() -> None:
    dataset = pd.DataFrame([_source_row("KAAA", "2024-07-01", 79.5, 78.0, max_so_far=79.0, threshold=79.0, target=1)])

    candidates = build_synthetic_bucket_dataset(dataset, LadderConfig(bounded_buckets_each_side=3))
    bounded = candidates.loc[candidates["bucket_label"] == "79-80F"].iloc[0]
    left_tail = candidates.loc[candidates["bucket_label"] == "<76F"].iloc[0]
    right_tail = candidates.loc[candidates["bucket_label"] == ">=83F"].iloc[0]

    assert bounded["lower_minus_current_temp"] == 1.0
    assert bounded["upper_minus_current_temp"] == 2.0
    assert bounded["lower_minus_max_so_far"] == 0.0
    assert bounded["upper_minus_max_so_far"] == 1.0
    assert bounded["bucket_span"] == 1.0
    assert left_tail["is_left_tail"] == 1
    assert pd.isna(left_tail["bucket_lower"])
    assert right_tail["is_right_tail"] == 1
    assert pd.isna(right_tail["bucket_upper"])


def test_grouped_normalization_sums_to_one_and_falls_back_to_uniform() -> None:
    frame = pd.DataFrame(
        [
            _candidate("KAAA", "2025-01-01", "a", 0.2),
            _candidate("KAAA", "2025-01-01", "a", 0.3),
            _candidate("KAAA", "2025-01-01", "a", 0.5),
            _candidate("KBBB", "2025-01-02", "b", 0.0),
            _candidate("KBBB", "2025-01-02", "b", np.nan),
        ]
    )

    normalized = normalize_grouped_probabilities(frame, "raw_probability")

    assert normalized.iloc[:3].sum() == 1.0
    assert normalized.iloc[0] == 0.2
    assert normalized.iloc[3:].tolist() == [0.5, 0.5]


def test_threshold_bucket_derivation_uses_cumulative_differences() -> None:
    dataset = pd.DataFrame(
        [
            _source_row("KAAA", "2024-07-01", 79.5, 78.0, threshold=79.0, target=1),
            _source_row("KAAA", "2025-07-01", 79.5, 78.0, threshold=79.0, target=1),
        ]
    )
    model = _ThresholdLookupModel({76.0: 0.95, 77.0: 0.90, 78.0: 0.80, 79.0: 0.70, 80.0: 0.30, 81.0: 0.20, 82.0: 0.10, 83.0: 0.05})

    predictions = build_threshold_bucket_validation_predictions(
        dataset=dataset,
        threshold_model=model,
        threshold_feature_columns=["station", "threshold", "threshold_minus_current_temp", "threshold_minus_max_so_far"],
        validation_year=2025,
        ladder_config=LadderConfig(bounded_buckets_each_side=3),
    )

    left_tail = predictions.loc[predictions["bucket_label"] == "<76F"].iloc[0]
    bounded = predictions.loc[predictions["bucket_label"] == "79-80F"].iloc[0]
    right_tail = predictions.loc[predictions["bucket_label"] == ">=83F"].iloc[0]
    assert left_tail["raw_probability"] == pytest.approx(0.05)
    assert bounded["raw_probability"] == pytest.approx(0.40)
    assert right_tail["raw_probability"] == pytest.approx(0.05)
    assert predictions.groupby(["station", "local_date", "snapshot_time_local", "synthetic_ladder_id"])["normalized_probability"].sum().iloc[0] == pytest.approx(1.0)


def test_regression_bucket_derivation_uses_empirical_residuals() -> None:
    dataset = pd.DataFrame(
        [
            _source_row("KAAA", "2024-07-01", 79.5, 78.0, threshold=79.0, target=1),
            _source_row("KAAA", "2025-07-01", 79.5, 78.0, threshold=79.0, target=1),
        ]
    )
    residuals = pd.DataFrame(
        {
            "station": ["KAAA"] * 4,
            "hour_local": [14] * 4,
            "window": ["late_13_plus"] * 4,
            "residual": [-1.5, -0.5, 0.25, 1.25],
        }
    )

    predictions = build_regression_bucket_validation_predictions(
        dataset=dataset,
        model=_ConstantRegressionModel(79.5),
        feature_columns=["station", "current_temp"],
        residuals=residuals,
        validation_year=2025,
        ladder_config=LadderConfig(bounded_buckets_each_side=3),
    )

    bounded = predictions.loc[predictions["bucket_label"] == "79-80F"].iloc[0]
    assert bounded["raw_probability"] == pytest.approx(0.5)
    assert predictions.groupby(["station", "local_date", "snapshot_time_local", "synthetic_ladder_id"])["normalized_probability"].sum().iloc[0] == pytest.approx(1.0)


def test_train_bucket_model_cli_writes_artifact_and_reports(tmp_path) -> None:
    dataset_path = tmp_path / "sample.csv"
    output_path = tmp_path / "bucket_model.joblib"
    report_dir = tmp_path / "reports"
    pd.DataFrame(_sample_training_rows()).to_csv(dataset_path, index=False)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "weather_trader.cli",
            "train-bucket-model",
            "--dataset",
            str(dataset_path),
            "--output",
            str(output_path),
            "--validation-year",
            "2025",
            "--report-dir",
            str(report_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary["model_type"] == "dynamic_bucket"
    assert output_path.exists()
    assert (report_dir / "candidate_validation_predictions.csv").exists()
    assert (report_dir / "ladder_predictions.csv").exists()
    assert (report_dir / "bucket_calibration.csv").exists()
    assert (report_dir / "heuristic_comparison.csv").exists()
    assert (report_dir / "window_metrics.csv").exists()


def test_tune_bucket_model_configs_returns_ranked_sigmoid_and_isotonic_rows() -> None:
    dataset = pd.DataFrame(_sample_training_rows())

    results = tune_bucket_model_configs(
        dataset,
        validation_year=2025,
        configs=[
            BucketModelConfig(name="tiny_sigmoid", max_iter=20, calibration_method="sigmoid"),
            BucketModelConfig(name="tiny_isotonic", max_iter=20, calibration_method="isotonic"),
        ],
    )

    assert set(results["calibration_method"]) == {"sigmoid", "isotonic"}
    assert results["grouped_brier_score"].is_monotonic_increasing
    assert {"grouped_log_loss", "top_bucket_accuracy"}.issubset(results.columns)


def test_early_window_training_excludes_later_hours() -> None:
    rows = _sample_training_rows()
    rows.extend(_sample_training_rows(hour_local=10, day_offset=10))
    dataset = pd.DataFrame(rows)

    artifacts = train_bucket_classifier(dataset, validation_year=2025, hour_local_max=10)

    early_snapshots = (
        dataset.loc[dataset["hour_local"] <= 10]
        .assign(local_date=lambda frame: pd.to_datetime(frame["local_date"]))
        .loc[lambda frame: frame["local_date"].dt.year < 2025, ["station", "local_date", "snapshot_time_local"]]
        .drop_duplicates()
    )
    assert artifacts.train_rows == len(early_snapshots) * 9


def test_catboost_missing_dependency_has_clear_message(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "catboost":
            raise ModuleNotFoundError("No module named 'catboost'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError, match="CatBoost is not installed"):
        train_catboost_bucket_classifier(pd.DataFrame(_sample_training_rows()), validation_year=2025)


def test_catboost_bucket_predictions_normalize_and_match_metric_schema() -> None:
    pytest.importorskip("catboost")
    dataset = pd.DataFrame(_sample_training_rows())

    artifacts = train_catboost_bucket_classifier(dataset, validation_year=2025)
    predictions = build_bucket_validation_predictions(
        dataset=dataset,
        model=artifacts.model,
        feature_columns=artifacts.feature_columns,
        validation_year=2025,
        ladder_config=artifacts.ladder_config,
    )
    metrics = build_grouped_metrics(predictions)

    grouped_sum = predictions.groupby(["station", "local_date", "snapshot_time_local", "synthetic_ladder_id"])["normalized_probability"].sum()
    assert grouped_sum.tolist() == pytest.approx([1.0] * len(grouped_sum))
    assert {"grouped_log_loss", "grouped_brier_score", "top_bucket_accuracy"}.issubset(metrics)


class _ThresholdLookupModel:
    def __init__(self, probabilities: dict[float, float]) -> None:
        self.probabilities = probabilities

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        yes = frame["threshold"].astype(float).map(self.probabilities).fillna(0.5).to_numpy()
        return np.column_stack([1.0 - yes, yes])


class _ConstantRegressionModel:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value)


def _candidate(station: str, local_date: str, ladder_id: str, raw_probability: float) -> dict[str, object]:
    return {
        "station": station,
        "local_date": pd.Timestamp(local_date),
        "snapshot_time_local": pd.Timestamp(f"{local_date} 12:00:00Z"),
        "synthetic_ladder_id": ladder_id,
        "raw_probability": raw_probability,
    }


def _sample_training_rows(hour_local: int = 14, day_offset: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    specs = [
        ("KAAA", "2024-07-01", 79.5, 78.0),
        ("KAAA", "2024-07-02", 80.5, 79.0),
        ("KAAA", "2024-07-03", 81.5, 80.0),
        ("KBBB", "2024-07-01", 70.5, 69.0),
        ("KBBB", "2024-07-02", 71.5, 70.0),
        ("KBBB", "2024-07-03", 72.5, 71.0),
        ("KAAA", "2025-07-01", 82.5, 80.0),
        ("KBBB", "2025-07-01", 73.5, 71.0),
    ]
    for station, local_date, final_high, current_temp in specs:
        shifted_date = (pd.Timestamp(local_date) + pd.Timedelta(days=day_offset)).strftime("%Y-%m-%d")
        rows.append(_source_row(station, shifted_date, final_high, current_temp, threshold=final_high, target=1, hour_local=hour_local))
        rows.append(_source_row(station, shifted_date, final_high, current_temp, threshold=final_high + 2.0, target=0, hour_local=hour_local))
    return rows


def _source_row(
    station: str,
    local_date: str,
    final_high: float,
    current_temp: float,
    max_so_far: float | None = None,
    threshold: float | None = None,
    target: int = 1,
    hour_local: int = 14,
) -> dict[str, object]:
    max_so_far = current_temp if max_so_far is None else max_so_far
    threshold = final_high if threshold is None else threshold
    return {
        "station": station,
        "city": station,
        "timezone": "America/New_York",
        "local_date": local_date,
        "snapshot_time_local": f"{local_date} {hour_local:02d}:00:00+00:00",
        "hour_local": hour_local,
        "day_of_year": pd.Timestamp(local_date).dayofyear,
        "current_temp": current_temp,
        "max_temp_so_far": max_so_far,
        "threshold": threshold,
        "threshold_minus_current_temp": threshold - current_temp,
        "threshold_minus_max_so_far": threshold - max_so_far,
        "temp_change_1h": 1.0,
        "temp_change_3h": 2.0,
        "dewpoint": current_temp - 10.0,
        "wind_speed": 5.0,
        "wind_dir_sin": 0.0,
        "wind_dir_cos": 1.0,
        "cloud_cover_code": 0,
        "final_high_tmpf": final_high,
        "target": target,
    }
