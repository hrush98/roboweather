from __future__ import annotations

from datetime import datetime, timezone

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
    assert rows[0]["f3_selected_token_probability"] == pytest.approx(0.1)
    assert rows[0]["market_selected_token_probability"] == pytest.approx(0.2)
    assert rows[0]["outcome_label"] == 0
    assert rows[0]["cloud_regime"] == "CLOUDY"
    assert rows[0]["radiation_surprise"] == 0.2
    assert captured == {
        "decision": datetime(2026, 8, 14, 18, 0, tzinfo=UTC),
        "trailing": 60,
        "minimum": 3,
    }
