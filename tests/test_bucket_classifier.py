from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd

from weather_trader.models.bucket_classifier import (
    LadderConfig,
    build_synthetic_bucket_dataset,
    normalize_grouped_probabilities,
)


def test_synthetic_ladder_generation_has_tails_and_one_winner() -> None:
    dataset = pd.DataFrame([_source_row("KAAA", "2024-07-01", 79.5, 78.0, threshold=79.0, target=1)])

    candidates = build_synthetic_bucket_dataset(dataset, LadderConfig(bounded_buckets_each_side=3))

    assert candidates["bucket_label"].tolist() == [
        "<=75F",
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


def test_bucket_feature_construction_handles_gaps_and_open_tails() -> None:
    dataset = pd.DataFrame([_source_row("KAAA", "2024-07-01", 79.5, 78.0, max_so_far=79.0, threshold=79.0, target=1)])

    candidates = build_synthetic_bucket_dataset(dataset, LadderConfig(bounded_buckets_each_side=3))
    bounded = candidates.loc[candidates["bucket_label"] == "79-80F"].iloc[0]
    left_tail = candidates.loc[candidates["bucket_label"] == "<=75F"].iloc[0]
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


def _candidate(station: str, local_date: str, ladder_id: str, raw_probability: float) -> dict[str, object]:
    return {
        "station": station,
        "local_date": pd.Timestamp(local_date),
        "snapshot_time_local": pd.Timestamp(f"{local_date} 12:00:00Z"),
        "synthetic_ladder_id": ladder_id,
        "raw_probability": raw_probability,
    }


def _sample_training_rows() -> list[dict[str, object]]:
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
        rows.append(_source_row(station, local_date, final_high, current_temp, threshold=final_high, target=1))
        rows.append(_source_row(station, local_date, final_high, current_temp, threshold=final_high + 2.0, target=0))
    return rows


def _source_row(
    station: str,
    local_date: str,
    final_high: float,
    current_temp: float,
    max_so_far: float | None = None,
    threshold: float | None = None,
    target: int = 1,
) -> dict[str, object]:
    max_so_far = current_temp if max_so_far is None else max_so_far
    threshold = final_high if threshold is None else threshold
    return {
        "station": station,
        "city": station,
        "timezone": "America/New_York",
        "local_date": local_date,
        "snapshot_time_local": f"{local_date} 14:00:00+00:00",
        "hour_local": 14,
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
