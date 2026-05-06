from __future__ import annotations

import pandas as pd

from weather_trader.models.diagnostics import validate_next_day_dataset, validate_same_day_dataset
from weather_trader.models.train_classifier import FEATURE_COLUMNS


def test_same_day_diagnostics_accept_clean_dataset_and_score_policies() -> None:
    frame = pd.DataFrame(
        [
            _same_day_row("KAAA", "2024-01-01", 70, 71, 0),
            _same_day_row("KAAA", "2024-01-01", 70, 70, 1),
            _same_day_row("KAAA", "2025-01-01", 72, 73, 0),
            _same_day_row("KAAA", "2025-01-01", 72, 72, 1),
        ]
    )

    diagnostics = validate_same_day_dataset(frame, validation_year=2025)

    assert not diagnostics.has_errors
    assert diagnostics.summary["rows"] == 4
    assert "already_hit_threshold" in set(diagnostics.policy_report["policy"])


def test_same_day_diagnostics_reject_feature_target_conflicts() -> None:
    base = _same_day_row("KAAA", "2024-01-01", 70, 70, 1)
    conflict = dict(base)
    conflict["target"] = 0
    frame = pd.DataFrame([base, conflict, _same_day_row("KAAA", "2025-01-01", 72, 72, 1), _same_day_row("KAAA", "2025-01-01", 72, 73, 0)])

    diagnostics = validate_same_day_dataset(frame, validation_year=2025)

    assert diagnostics.has_errors
    assert "duplicate_feature_conflicts" in set(diagnostics.issue_frame()["check"])


def test_configured_features_do_not_include_future_outcomes() -> None:
    forbidden = {"target", "final_high_tmpf", "target_final_high_tmpf"}
    assert forbidden.isdisjoint(FEATURE_COLUMNS)


def test_next_day_diagnostics_reject_non_next_day_rows() -> None:
    frame = pd.DataFrame(
        [
            _next_day_row("KAAA", "2025-01-03", "2025-01-01", 70, 70, 1),
            _next_day_row("KAAA", "2024-01-02", "2024-01-01", 70, 70, 1),
            _next_day_row("KAAA", "2024-01-02", "2024-01-01", 71, 70, 0),
        ]
    )

    diagnostics = validate_next_day_dataset(frame, validation_year=2025)

    assert diagnostics.has_errors
    assert "next_day_time_order" in set(diagnostics.issue_frame()["check"])


def _same_day_row(station: str, local_date: str, final_high: float, threshold: float, target: int) -> dict[str, object]:
    return {
        "station": station,
        "city": station,
        "timezone": "America/New_York",
        "local_date": local_date,
        "snapshot_time_local": f"{local_date} 12:00:00",
        "hour_local": 12,
        "day_of_year": pd.Timestamp(local_date).dayofyear,
        "current_temp": final_high - 2,
        "max_temp_so_far": final_high - 1,
        "threshold": threshold,
        "threshold_minus_current_temp": threshold - (final_high - 2),
        "threshold_minus_max_so_far": threshold - (final_high - 1),
        "temp_change_1h": 1.0,
        "temp_change_3h": 2.0,
        "dewpoint": final_high - 10,
        "wind_speed": 5.0,
        "wind_dir_sin": 0.0,
        "wind_dir_cos": 1.0,
        "cloud_cover_code": 0,
        "final_high_tmpf": final_high,
        "target": target,
    }


def _next_day_row(
    station: str,
    local_date: str,
    prediction_date: str,
    target_high: float,
    threshold: float,
    target: int,
) -> dict[str, object]:
    return {
        "station": station,
        "city": station,
        "timezone": "America/New_York",
        "local_date": local_date,
        "prediction_date": prediction_date,
        "prediction_snapshot_time_local": f"{prediction_date} 12:00:00",
        "prediction_hour_local": 12,
        "prediction_day_of_year": pd.Timestamp(prediction_date).dayofyear,
        "target_day_of_year": pd.Timestamp(local_date).dayofyear,
        "current_temp": 65.0,
        "today_high_so_far": 66.0,
        "threshold": threshold,
        "threshold_minus_current_temp": threshold - 65,
        "threshold_minus_today_high_so_far": threshold - 66,
        "temp_change_1h": 1.0,
        "temp_change_3h": 2.0,
        "dewpoint": 55.0,
        "wind_speed": 5.0,
        "wind_dir_sin": 0.0,
        "wind_dir_cos": 1.0,
        "cloud_cover_code": 0,
        "prior_day_high": 68.0,
        "prior_3day_high_mean": 67.0,
        "prior_7day_high_mean": 66.0,
        "prior_day_high_minus_threshold": 68 - threshold,
        "prior_7day_high_mean_minus_threshold": 66 - threshold,
        "target_final_high_tmpf": target_high,
        "target": target,
    }
