from __future__ import annotations

from datetime import date

import pandas as pd

from weather_trader.features import dataset_builder


def test_build_default_dataset_uses_explicit_station_selection(monkeypatch) -> None:
    requested = []

    def fake_build_for_station(self, station, start, end):
        requested.append(station.station)
        return pd.DataFrame(
            {
                "station": [station.station],
                "start": [start.isoformat()],
                "end": [end.isoformat()],
            }
        )

    monkeypatch.setattr(dataset_builder, "IEMASOSClient", lambda: object())
    monkeypatch.setattr(dataset_builder.DatasetBuilder, "build_for_station", fake_build_for_station)

    frame = dataset_builder.build_default_dataset(
        start=date(2022, 1, 1),
        end=date(2025, 12, 31),
        station_ids=["KATL", "KSEA", "KHOU"],
    )

    assert requested == ["KATL", "KSEA", "KHOU"]
    assert set(frame["station"]) == {"KATL", "KSEA", "KHOU"}
    assert "KDFW" not in set(frame["station"])
    assert "KDEN" not in set(frame["station"])
