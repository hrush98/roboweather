import numpy as np
import pandas as pd
import pytest

from scripts.forecast_spatial_residual_report import build_acceptance_checks
from weather_trader.forecasting.evaluation import FixedSupport
from weather_trader.forecasting.spatial_residual import (
    SpatialResidualCalibrator, SpatialResidualContract,
    angular_difference_degrees, materialize_spatial_features, shift_probability_matrix,
)

def _decision() -> pd.DataFrame:
    return pd.DataFrame([{"decision_id": "one", "station": "KATL", "decision_time_utc": "2026-07-01T18:00:00+00:00"}])

def _observations() -> pd.DataFrame:
    return pd.DataFrame([
        {"station": "ATL", "valid": "2026-07-01T17:45:00Z", "tmpf": 90, "dwpf": 65, "drct": 270, "sknt": 10},
        {"station": "FTY", "valid": "2026-07-01T16:45:00Z", "tmpf": 84, "dwpf": 63, "drct": 260, "sknt": 9},
        {"station": "FTY", "valid": "2026-07-01T17:45:00Z", "tmpf": 88, "dwpf": 64, "drct": 270, "sknt": 10},
        {"station": "PDK", "valid": "2026-07-01T16:45:00Z", "tmpf": 91, "dwpf": 66, "drct": 90, "sknt": 8},
        {"station": "PDK", "valid": "2026-07-01T17:45:00Z", "tmpf": 92, "dwpf": 67, "drct": 90, "sknt": 8},
        {"station": "FTY", "valid": "2026-07-01T17:55:00Z", "tmpf": 110, "dwpf": 70, "drct": 270, "sknt": 10},
    ])

def _model_points() -> pd.DataFrame:
    return pd.DataFrame([
        {"decision_id": "one", "station": "FTY", "hrrr_tmpf": 86, "hrrr_previous_tmpf": 83},
        {"decision_id": "one", "station": "PDK", "hrrr_tmpf": 91, "hrrr_previous_tmpf": 90},
    ])

def test_materialization_is_causal_and_uses_frozen_upwind_geometry() -> None:
    features = materialize_spatial_features(_decision(), _observations(), _model_points()).iloc[0]
    assert features["spatial_neighbor_count"] == 2
    assert 1.0 < features["spatial_upwind_temp_residual_f"] < 2.1
    assert features["spatial_weighted_temp_residual_f"] < 2.0
    assert features["spatial_warming_residual_f_per_hour"] > 0

def test_qc_and_staleness_fail_closed() -> None:
    observations = _observations()
    observations.loc[observations["station"].eq("FTY"), "tmpf"] = 200
    observations.loc[observations["station"].eq("PDK"), "valid"] = "2026-07-01T15:00:00Z"
    features = materialize_spatial_features(_decision(), observations, _model_points()).iloc[0]
    assert features["spatial_neighbor_count"] == 0
    assert np.isnan(features["spatial_upwind_temp_residual_f"])

def test_probability_shift_is_fractional_normalized_and_endpoint_safe() -> None:
    support = FixedSupport(68, 72)
    shifted = shift_probability_matrix(np.array([[0, 0, 1, 0, 0], [1, 0, 0, 0, 0]], dtype=float), [1.5, -3.0], support)
    assert shifted[0].tolist() == pytest.approx([0, 0, 0, 0.5, 0.5])
    assert shifted[1].tolist() == pytest.approx([1, 0, 0, 0, 0])

def test_calibrator_leaves_missing_neighbor_rows_unchanged_and_coherent() -> None:
    support = FixedSupport(68, 75)
    predecessor = np.tile(np.array([[0, 0, 0.6, 0.4, 0, 0, 0, 0]]), (20, 1))
    features = pd.DataFrame({
        "spatial_upwind_temp_residual_f": np.linspace(-2, 2, 20), "spatial_weighted_temp_residual_f": np.linspace(-1, 1, 20),
        "spatial_warming_residual_f_per_hour": 0.2, "spatial_temp_gradient_f": 1.0,
        "spatial_dewpoint_gradient_f": 0.5, "spatial_boundary_score": 0.4,
        "spatial_min_travel_time_hours": 1.0, "spatial_neighbor_count": 3,
    })
    targets = np.where(np.arange(20) < 10, 70, 72)
    model = SpatialResidualCalibrator(SpatialResidualContract(minimum_neighbors=2, ridge_alpha=1.0)).fit(features, predecessor, targets, support)
    evaluation_features = features.iloc[:2].copy()
    evaluation_features.loc[evaluation_features.index[1], "spatial_neighbor_count"] = 0
    corrected = model.predict_proba(evaluation_features, predecessor[:2], high_so_far=[70, 71], support=support)
    assert corrected.sum(axis=1).tolist() == pytest.approx([1, 1])
    assert corrected[1].tolist() == pytest.approx([0, 0, 0, 1, 0, 0, 0, 0])
    assert corrected[0, : 70 - support.minimum].sum() == 0

def test_angular_difference_wraps_at_north() -> None:
    assert angular_difference_degrees(350, 10) == pytest.approx(20)


def test_acceptance_fails_closed_when_spatial_model_is_worse() -> None:
    def comparison(log_loss: float, rps: float, upper: float = -0.01) -> dict:
        return {
            "candidate_minus_reference": {"log_loss": log_loss, "rps": rps},
            "weather_date_clustered_95pct_ci": {
                "log_loss": [-0.2, upper], "rps": [-0.2, upper],
            },
        }

    checks = build_acceptance_checks(
        {
            "spatial_minus_f3_holdout": comparison(0.14, 0.01, 0.4),
            "spatial_minus_f3_recent": comparison(0.21, 0.02),
            "market_stack_minus_market_holdout": comparison(-0.01, -0.01),
            "market_stack_minus_market_recent": comparison(-0.01, -0.01),
        },
        eligible_rate=1.0, fit_rows=323, holdout_dates=22, recent_dates=14,
        market_weight=0.88,
    )

    assert checks["spatial_coverage_at_least_80pct"]
    assert not checks["holdout_improves_log_loss"]
    assert not checks["holdout_improves_rps"]
    assert not all(checks.values())
