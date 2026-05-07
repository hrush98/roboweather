from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from weather_trader.forecasts.hrrr_archive import HRRRArchiveClient
from weather_trader.stations.metadata import get_station


class CountingHRRRArchiveClient(HRRRArchiveClient):
    def __init__(self, cache_path: Path) -> None:
        super().__init__(cache_path=cache_path, forecast_stride_hours=3, max_forecast_hour=9)
        self.fetches: list[tuple[str, str, int]] = []

    def _fetch_point_feature_row_uncached(self, station, cycle_utc, forecast_hour):  # type: ignore[no-untyped-def]
        self.fetches.append((station.station, cycle_utc.isoformat(), forecast_hour))
        tmpf = 70.0 + forecast_hour
        return {
            "forecast_hour": forecast_hour,
            "tmpf": tmpf,
            "dwpf": tmpf - 10.0,
            "rh": 50.0,
            "u10": 3.0,
            "v10": 4.0,
            "gust_mph": np.nan,
            "tcdc": np.nan,
            "dswrf": np.nan,
            "wind_speed_mph": 11.1847,
        }


def test_point_feature_rows_are_persistently_cached(tmp_path: Path) -> None:
    station = get_station("KATL")
    cycle = datetime(2025, 5, 1, 14, tzinfo=ZoneInfo("UTC"))
    cache_path = tmp_path / "hrrr.sqlite"
    client = CountingHRRRArchiveClient(cache_path)

    first = client.fetch_point_feature_row(station=station, cycle_utc=cycle, forecast_hour=2)
    second = client.fetch_point_feature_row(station=station, cycle_utc=cycle, forecast_hour=2)

    assert first["tmpf"] == second["tmpf"]
    assert first["forecast_hour"] == second["forecast_hour"]
    assert client.fetches == [("KATL", "2025-05-01T14:00:00+00:00", 2)]

    fresh_client = CountingHRRRArchiveClient(cache_path)
    cached = fresh_client.fetch_point_feature_row(station=station, cycle_utc=cycle, forecast_hour=2)

    assert cached["tmpf"] == first["tmpf"]
    assert cached["forecast_hour"] == first["forecast_hour"]
    assert fresh_client.fetches == []


def test_snapshot_features_reuse_overlapping_point_rows(tmp_path: Path) -> None:
    station = get_station("KATL")
    cache_path = tmp_path / "hrrr.sqlite"
    client = CountingHRRRArchiveClient(cache_path)
    first_as_of = datetime(2025, 5, 1, 15, 5, tzinfo=ZoneInfo("UTC"))
    second_as_of = datetime(2025, 5, 1, 15, 30, tzinfo=ZoneInfo("UTC"))

    first = client.fetch_remaining_day_features(station=station, as_of_utc=first_as_of)
    second = client.fetch_remaining_day_features(station=station, as_of_utc=second_as_of)

    assert first["hrrr_remaining_max"] == second["hrrr_remaining_max"]
    assert client.forecast_plan(station=station, as_of_utc=first_as_of) == client.forecast_plan(
        station=station,
        as_of_utc=second_as_of,
    )
    assert client.fetches == [
        ("KATL", "2025-05-01T14:00:00+00:00", 2),
        ("KATL", "2025-05-01T14:00:00+00:00", 5),
        ("KATL", "2025-05-01T14:00:00+00:00", 8),
    ]
