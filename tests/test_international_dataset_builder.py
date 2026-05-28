from __future__ import annotations

from datetime import date

import pandas as pd

from weather_trader.features.international_dataset_builder import (
    InternationalDatasetBuilder,
    InternationalStation,
    _fahrenheit_observations_to_celsius,
)


def test_fahrenheit_observations_to_celsius() -> None:
    frame = pd.DataFrame({"tmpf": [32.0, 68.0], "dwpf": [50.0, None]})

    converted = _fahrenheit_observations_to_celsius(frame)

    assert converted["tmpf"].round(1).tolist() == [0.0, 20.0]
    assert round(float(converted["dwpf"].iloc[0]), 1) == 10.0


def test_hong_kong_target_uses_hko_daily_value() -> None:
    builder = InternationalDatasetBuilder(observations_client=_ObservationsClient(), hko_client=_HKOClient())
    station = InternationalStation(
        city="Hong Kong",
        station="VHHH",
        display_name="Hong Kong International Airport",
        timezone="Asia/Hong_Kong",
        latitude=22.3080,
        longitude=113.9185,
        resolution_station="HKO",
        resolution_source="Hong Kong Observatory Daily Extract",
    )

    dataset = builder.build_for_station(station=station, start=date(2025, 1, 1), end=date(2025, 1, 1), metric="high")

    assert set(dataset["final_high_tmpf"].round(1)) == {25.0}
    assert dataset.loc[dataset["threshold"] == 25.0, "target"].iloc[0] == 1
    assert dataset.loc[dataset["threshold"] == 26.0, "target"].iloc[0] == 0
    assert set(dataset["temperature_unit"]) == {"C"}
    assert set(dataset["target_source"]) == {"HKO"}


class _ObservationsClient:
    def fetch_observations(self, station, start, end):
        return pd.DataFrame(
            {
                "valid": pd.to_datetime(
                    [
                        "2024-12-31T18:00:00Z",
                        "2025-01-01T02:00:00Z",
                        "2025-01-01T04:00:00Z",
                        "2025-01-01T06:00:00Z",
                        "2025-01-01T08:00:00Z",
                    ],
                    utc=True,
                ),
                "tmpf": [68.0, 69.8, 71.6, 73.4, 75.2],
                "dwpf": [60.8, 60.8, 62.6, 62.6, 64.4],
                "sknt": [5.0, 5.0, 6.0, 7.0, 7.0],
                "drct": [90.0, 100.0, 100.0, 110.0, 120.0],
                "skyc1": ["FEW", "FEW", "SCT", "SCT", "BKN"],
                "skyc2": [None, None, None, None, None],
                "skyc3": [None, None, None, None, None],
            }
        )


class _HKOClient:
    def fetch_daily_temperature_series(self, metric, station="HKO"):
        column = "final_low_tmpf" if metric == "low" else "final_high_tmpf"
        return self.fetch_daily_temperatures(station=station)[["local_date", column]]

    def fetch_daily_temperatures(self, station="HKO"):
        return pd.DataFrame(
            {
                "local_date": [date(2025, 1, 1)],
                "final_high_tmpf": [25.0],
                "final_low_tmpf": [18.0],
            }
        )
