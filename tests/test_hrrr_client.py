from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from weather_trader.forecasts.hrrr_client import HRRRClient
from weather_trader.stations.metadata import get_station


class _FakeMessage:
    def __init__(self, short_name: str, level: int, value: float, *, fail: bool = False) -> None:
        self.shortName = short_name
        self.level = level
        self.value = value
        self.fail = fail

    def data(self):  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("decode failed")
        return (
            np.array([[self.value]]),
            np.array([[47.4502]]),
            np.array([[-122.3088]]),
        )


class _FakeGribFile:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self.messages = messages
        self.closed = False

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.messages)

    def close(self) -> None:
        self.closed = True


def test_point_forecast_closes_grib_handle_and_reuses_cache(monkeypatch) -> None:
    handles: list[_FakeGribFile] = []
    downloads: list[int] = []

    def fake_open(_path: str) -> _FakeGribFile:
        handle = _FakeGribFile(
            [
                _FakeMessage("2t", 2, 293.15),
                _FakeMessage("10u", 10, 3.0),
                _FakeMessage("10v", 10, 4.0),
            ]
        )
        handles.append(handle)
        return handle

    monkeypatch.setitem(sys.modules, "pygrib", SimpleNamespace(open=fake_open))
    client = HRRRClient(point_cache_max_entries=2)
    monkeypatch.setattr(
        client,
        "_download_subset",
        lambda station, cycle_utc, forecast_hour: downloads.append(forecast_hour) or b"grib",
    )
    station = get_station("KSEA")
    cycle = datetime(2026, 7, 16, 17, tzinfo=timezone.utc)

    first = client.fetch_point_forecast(station, cycle, 1)
    cached = client.fetch_point_forecast(station, cycle, 1)
    client.fetch_point_forecast(station, cycle, 2)
    client.fetch_point_forecast(station, cycle, 3)
    client.fetch_point_forecast(station, cycle, 1)

    assert first == cached
    assert first["tmpf"] == pytest.approx(68.0)
    assert first["wind_speed_mph"] == pytest.approx(11.1847)
    assert downloads == [1, 2, 3, 1]
    assert all(handle.closed for handle in handles)
    assert client.cache_stats() == {
        "entries": 2,
        "max_entries": 2,
        "hits": 1,
        "misses": 4,
    }


def test_point_forecast_closes_grib_handle_when_decode_fails(monkeypatch) -> None:
    handle = _FakeGribFile([_FakeMessage("2t", 2, 293.15, fail=True)])
    monkeypatch.setitem(sys.modules, "pygrib", SimpleNamespace(open=lambda _path: handle))
    client = HRRRClient()
    monkeypatch.setattr(client, "_download_subset", lambda **_kwargs: b"grib")

    with pytest.raises(RuntimeError, match="decode failed"):
        client.fetch_point_forecast(
            get_station("KSEA"),
            datetime(2026, 7, 16, 17, tzinfo=timezone.utc),
            1,
        )

    assert handle.closed is True
    assert client.cache_stats()["entries"] == 0
