from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from weather_trader.forecasts.hrrr_v2 import (
    EXTRACTOR_VERSION,
    GribRangeHRRRSource,
    HRRRExtractionTask,
    HRRRV2Store,
    build_extraction_tasks,
    build_hrrr_v2_cache,
    materialize_hrrr_v2_features,
)
from weather_trader.stations.metadata import get_station


def _dataset() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "station": "KDFW",
                "city": "Dallas",
                "timezone": "America/Chicago",
                "local_date": "2025-05-01",
                "snapshot_time_local": "2025-05-01T15:05:00+00:00",
                "hour_local": 10,
                "day_of_year": 121,
                "current_temp": 70.0,
                "max_temp_so_far": 72.0,
                "threshold": 80.0,
                "final_high_tmpf": 84.0,
                "target": 1,
            },
            {
                "station": "KDAL",
                "city": "Dallas",
                "timezone": "America/Chicago",
                "local_date": "2025-05-01",
                "snapshot_time_local": "2025-05-01T15:05:00+00:00",
                "hour_local": 10,
                "day_of_year": 121,
                "current_temp": 71.0,
                "max_temp_so_far": 73.0,
                "threshold": 80.0,
                "final_high_tmpf": 85.0,
                "target": 1,
            },
        ]
    )


class FakeHRRRSource(GribRangeHRRRSource):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, int]] = []

    def fetch_point_rows(self, task: HRRRExtractionTask, stations):  # type: ignore[no-untyped-def]
        self.calls.append(task.key)
        rows = []
        for index, station in enumerate(stations):
            tmpf = 70.0 + task.forecast_hour + index
            rows.append(
                {
                    "station": station.station,
                    "cycle_utc": task.cycle_utc.isoformat(),
                    "forecast_hour": task.forecast_hour,
                    "valid_utc": (task.cycle_utc + pd.Timedelta(hours=task.forecast_hour)).isoformat(),
                    "tmpf": tmpf,
                    "dwpf": tmpf - 10.0,
                    "rh": 50.0,
                    "u10": 3.0,
                    "v10": 4.0,
                    "wind_speed_mph": 11.1847,
                    "gust_mph": None,
                    "tcdc": 25.0,
                    "dswrf": 500.0,
                    "source_model": "hrrr",
                    "product": "wrfsfc",
                    "extractor_version": EXTRACTOR_VERSION,
                }
            )
        return rows


def test_extraction_tasks_are_deduped_by_cycle_and_forecast_hour() -> None:
    tasks = build_extraction_tasks(_dataset(), forecast_stride_hours=3, max_forecast_hour=9)

    assert [task.key for task in tasks] == [
        ("2025-05-01T14:00:00+00:00", 2),
        ("2025-05-01T14:00:00+00:00", 5),
        ("2025-05-01T14:00:00+00:00", 8),
    ]


def test_v2_cache_extracts_each_hrrr_file_once_for_multiple_stations(tmp_path: Path) -> None:
    cache_path = tmp_path / "hrrr_v2.sqlite"
    source = FakeHRRRSource()
    stations = [get_station("KDFW"), get_station("KDAL")]

    summary = build_hrrr_v2_cache(
        dataset=_dataset(),
        cache_path=cache_path,
        stations=stations,
        forecast_stride_hours=3,
        max_forecast_hour=9,
        workers=1,
        source=source,
        progress_every=0,
    )

    assert source.calls == [
        ("2025-05-01T14:00:00+00:00", 2),
        ("2025-05-01T14:00:00+00:00", 5),
        ("2025-05-01T14:00:00+00:00", 8),
    ]
    assert summary["tasks_fetched_this_run"] == 3
    assert summary["rows_by_station"] == {"KDAL": 3, "KDFW": 3}

    second_source = FakeHRRRSource()
    second = build_hrrr_v2_cache(
        dataset=_dataset(),
        cache_path=cache_path,
        stations=stations,
        forecast_stride_hours=3,
        max_forecast_hour=9,
        workers=1,
        source=second_source,
        progress_every=0,
    )
    assert second_source.calls == []
    assert second["tasks_requested"] == 0


def test_materialize_hrrr_v2_features_from_local_point_rows(tmp_path: Path) -> None:
    cache_path = tmp_path / "hrrr_v2.sqlite"
    store = HRRRV2Store(cache_path)
    task = HRRRExtractionTask(datetime(2025, 5, 1, 14, tzinfo=ZoneInfo("UTC")), 2)
    rows = FakeHRRRSource().fetch_point_rows(task, [get_station("KDFW")])
    store.write_task_rows(task, rows, elapsed_seconds=0.1)
    store.close()

    enriched = materialize_hrrr_v2_features(
        _dataset().query("station == 'KDFW'").copy(),
        cache_path,
        forecast_stride_hours=3,
        max_forecast_hour=2,
        progress_every=0,
    )

    assert len(enriched) == 1
    assert enriched["hrrr_current_temp"].iloc[0] == 72.0
    assert enriched["hrrr_remaining_max"].iloc[0] == 72.0
    assert enriched["hrrr_remaining_max_minus_threshold"].iloc[0] == -8.0
    assert enriched["hrrr_current_temp_minus_current_temp"].iloc[0] == 2.0

