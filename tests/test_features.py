from __future__ import annotations

import pandas as pd

from weather_trader.features.build_same_day_features import build_synthetic_threshold_examples, prepare_station_observations
from weather_trader.stations.metadata import get_station


def test_prepare_station_observations_adds_local_fields() -> None:
    frame = pd.DataFrame(
        {
            "valid": pd.to_datetime(["2025-05-01T15:00:00Z", "2025-05-01T16:00:00Z"], utc=True),
            "tmpf": [70.0, 72.0],
            "dwpf": [55.0, 56.0],
            "sknt": [5.0, 7.0],
            "drct": [180.0, 190.0],
            "relh": [60.0, 58.0],
            "mslp": [1012.0, 1011.0],
            "skyc1": ["CLR", "FEW"],
            "skyc2": [None, None],
            "skyc3": [None, None],
        }
    )
    prepared = prepare_station_observations(frame, get_station("KATL"))
    assert "valid_local" in prepared.columns
    assert "local_date" in prepared.columns
    assert prepared["station"].iloc[0] == "KATL"


def test_prepare_station_observations_uses_timestamp_based_temperature_changes() -> None:
    frame = pd.DataFrame(
        {
            "valid": pd.to_datetime(
                [
                    "2025-05-01T13:51:00Z",
                    "2025-05-01T14:51:00Z",
                    "2025-05-01T15:52:00Z",
                    "2025-05-01T16:51:00Z",
                ],
                utc=True,
            ),
            "tmpf": [65.0, 68.0, 70.0, 74.0],
            "dwpf": [55.0] * 4,
            "sknt": [5.0] * 4,
            "drct": [180.0] * 4,
            "relh": [60.0] * 4,
            "mslp": [1012.0] * 4,
            "skyc1": ["CLR"] * 4,
            "skyc2": [None] * 4,
            "skyc3": [None] * 4,
        }
    )

    prepared = prepare_station_observations(frame, get_station("KATL"))

    assert pd.isna(prepared["temp_change_1h"].iloc[0])
    assert prepared["temp_change_1h"].iloc[1] == 3.0
    assert prepared["temp_change_1h"].iloc[2] == 2.0
    assert prepared["temp_change_3h"].iloc[3] == 9.0


def test_prepare_station_observations_leaves_change_null_without_nearby_lookback() -> None:
    frame = pd.DataFrame(
        {
            "valid": pd.to_datetime(["2025-05-01T13:51:00Z", "2025-05-01T16:00:00Z"], utc=True),
            "tmpf": [65.0, 74.0],
            "dwpf": [55.0, 56.0],
            "sknt": [5.0, 7.0],
            "drct": [180.0, 190.0],
            "relh": [60.0, 58.0],
            "mslp": [1012.0, 1011.0],
            "skyc1": ["CLR", "FEW"],
            "skyc2": [None, None],
            "skyc3": [None, None],
        }
    )

    prepared = prepare_station_observations(frame, get_station("KATL"))

    assert pd.isna(prepared["temp_change_1h"].iloc[1])


def test_build_synthetic_threshold_examples_produces_rows() -> None:
    valid = pd.date_range("2025-05-01T13:00:00Z", periods=40, freq="15min", tz="UTC")
    temps = [65.0 + i * 0.3 for i in range(40)]
    frame = pd.DataFrame(
        {
            "valid": valid,
            "tmpf": temps,
            "dwpf": [55.0] * 40,
            "sknt": [5.0] * 40,
            "drct": [180.0] * 40,
            "relh": [60.0] * 40,
            "mslp": [1012.0] * 40,
            "skyc1": ["CLR"] * 40,
            "skyc2": [None] * 40,
            "skyc3": [None] * 40,
        }
    )
    features = build_synthetic_threshold_examples(frame, get_station("KATL"))
    assert not features.empty
    assert {"threshold", "target", "max_temp_so_far", "threshold_minus_current_temp"} <= set(features.columns)
