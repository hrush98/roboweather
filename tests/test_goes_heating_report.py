from __future__ import annotations

from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import pytest

import scripts.forecast_goes_heating_report as report


UTC = timezone.utc


def test_cloud_regimes_are_predeclared() -> None:
    assert report.cloud_regime(None) == "MISSING"
    assert report.cloud_regime(25.0) == "CLEAR"
    assert report.cloud_regime(25.1) == "MIXED"
    assert report.cloud_regime(74.9) == "MIXED"
    assert report.cloud_regime(75.0) == "CLOUDY"


def test_materializer_keeps_exact_selected_side_and_causal_window(monkeypatch) -> None:
    frame = pd.DataFrame([{
        "station": "KATL",
        "local_date": "2026-08-14",
        "decision_time_utc": "2026-08-14T18:00:00+00:00",
        "selected_market_id": "m1",
        "selected_side": "BUY_NO",
        "target_value": 72,
        "source_prediction_snapshot_id": 7,
        "hrrr_shortwave_next_3h_mean": 500.0,
        "hrrr_cloud_cover_current": 80.0,
    }])
    captured = {}

    def fake_window(artifacts, station, *, decision_time_utc, trailing_minutes, minimum_scans):
        captured.update(
            decision=decision_time_utc,
            trailing=trailing_minutes,
            minimum=minimum_scans,
        )
        return {
            "decision_time_utc": decision_time_utc.isoformat(),
            "trailing_minutes": trailing_minutes,
            "dsr_mean_w_m2": 700.0,
            "scan_count": 4,
        }

    monkeypatch.setattr(report, "causal_station_window", fake_window)
    monkeypatch.setattr(
        report,
        "normalized_radiation_surprise",
        lambda *args, **kwargs: {
            "observed_transmission_proxy": 0.8,
            "hrrr_transmission_proxy": 0.6,
            "radiation_surprise": 0.2,
        },
    )
    rows = report.materialize_rows(
        frame,
        np.array([[0.1, 0.2, 0.7]]),
        np.array([[0.2, 0.3, 0.5]]),
        [],
        {"m1": (71.0, None)},
        np.array([70, 71, 72]),
        activation_date="2026-08-14",
    )
    assert len(rows) == 1
    assert rows[0]["selected_side"] == "BUY_NO"
    assert rows[0]["predecessor_selected_token_probability"] == pytest.approx(0.1)
    assert rows[0]["market_selected_token_probability"] == pytest.approx(0.2)
    assert rows[0]["outcome_label"] == 0
    assert rows[0]["cloud_regime"] == "CLOUDY"
    assert rows[0]["radiation_surprise"] == 0.2
    assert captured == {
        "decision": datetime(2026, 8, 14, 18, 0, tzinfo=UTC),
        "trailing": 60,
        "minimum": 3,
    }


def test_report_refuses_cross_horizon_predecessor(tmp_path) -> None:
    artifact = tmp_path / "mismatch.joblib"
    joblib.dump({
        "evaluation_contract": {
            "version": "forecast_fixed_support_exact_cutoff_weather_date_v2",
            "support": {"minimum": -20, "maximum": 130, "unit": "F"},
            "validation_start": "2025-01-01",
            "validation_end_exclusive": "2026-01-01",
            "horizon_hour_local": 12,
            "snapshot_selector": "latest_observation_at_or_before_exact_local_cutoff",
            "uncertainty_cluster": "local_date",
            "bootstrap_samples": 2000,
            "bootstrap_seed": 20260812,
            "duplicate_probability_correlation": 0.999,
            "duplicate_mean_total_variation": 0.005,
        },
        "forecast_version": "remaining_heating_hurdle_multinomial_exact_cutoff_v3",
    }, artifact)
    with pytest.raises(ValueError, match="requires predecessor"):
        report.run_report(
            tmp_path / "missing-research.sqlite",
            tmp_path / "missing-catalog.sqlite",
            artifact,
            tmp_path / "out",
        )
