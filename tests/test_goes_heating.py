from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import pytest

from weather_trader.forecasting.goes_heating import (
    GOES_DSR_SOURCE_ID,
    object_to_request,
    parse_dsr_object,
    parse_goes_time,
    sample_station_dsr,
    satellite_for_longitude,
)
from weather_trader.forecasting.source_catalog import contract_for, validate_payload
from weather_trader.stations.metadata import Station

UTC = timezone.utc
KEY = (
    "ABI-L2-DSRF/2026/225/18/"
    "OR_ABI-L2-DSRF-M6_G19_s20262251800212_e20262251809521_c20262251817186.nc"
)


def test_goes_contract_is_forward_observed_and_hdf5_validated() -> None:
    contract = contract_for(GOES_DSR_SOURCE_ID)
    assert contract.availability_rule == "FIRST_SUCCESSFUL_OBSERVATION"
    assert contract.operational_version == "ABI-L2-DSRF-v02r00"
    validate_payload(GOES_DSR_SOURCE_ID, b"\x89HDF\r\n\x1a\nmore")
    with pytest.raises(ValueError, match="NetCDF4"):
        validate_payload(GOES_DSR_SOURCE_ID, b"not hdf")


def test_goes_key_times_and_request_keep_archive_clocks_as_provenance() -> None:
    assert parse_goes_time("20262251800212") == datetime(
        2026, 8, 13, 18, 0, 21, 200000, tzinfo=UTC
    )
    item = parse_dsr_object(
        "19", KEY,
        last_modified_at_utc="2026-08-13T18:17:25Z",
        byte_count=14_000_000,
    )
    request = object_to_request(item)
    assert request.provider_available_at_utc is None
    assert request.valid_start_at_utc == "2026-08-13T18:00:21.200000+00:00"
    assert request.metadata["causal_clock"] == "FIRST_SUCCESSFUL_LOCAL_OBSERVATION"
    assert request.metadata["s3_last_modified_at_utc"] == "2026-08-13T18:17:25+00:00"


def test_satellite_selection_is_predeclared_by_longitude() -> None:
    assert satellite_for_longitude(-118.4) == "18"
    assert satellite_for_longitude(-122.3) == "18"
    assert satellite_for_longitude(-104.75) == "19"
    assert satellite_for_longitude(-84.4) == "19"


def test_station_sampler_applies_projection_scale_and_dqf(tmp_path: Path) -> None:
    path = tmp_path / "sample.nc"
    _write_synthetic_dsr(path)
    station = Station(
        city="Origin", station="KORG", display_name="Origin",
        timezone="UTC", latitude=0.0, longitude=-75.0,
    )
    sample = sample_station_dsr(str(path), station)
    assert sample.station == "KORG"
    assert sample.satellite == "G19"
    assert sample.good_pixels == 8
    assert sample.requested_pixels == 9
    assert sample.dsr_median_w_m2 == pytest.approx(100.0)


def _write_synthetic_dsr(path: Path) -> None:
    with h5py.File(path, "w") as dataset:
        dataset.attrs["title"] = np.bytes_(
            "Advanced Baseline Imager (ABI) Level 2+ Enterprise Downward Shortwave Radiation"
        )
        dataset.attrs["platform_ID"] = np.bytes_("G19")
        dataset.attrs["time_coverage_start"] = np.bytes_("2026-08-13T18:00:21.2Z")
        dataset.attrs["time_coverage_end"] = np.bytes_("2026-08-13T18:09:52.1Z")
        projection = dataset.create_dataset("goes_imager_projection", data=np.int32(-1))
        projection.attrs["perspective_point_height"] = np.array([35786023.0])
        projection.attrs["longitude_of_projection_origin"] = np.array([-75.0])
        projection.attrs["sweep_angle_axis"] = np.bytes_("x")
        projection.attrs["semi_major_axis"] = np.array([6378137.0])
        projection.attrs["semi_minor_axis"] = np.array([6356752.31414])
        x = dataset.create_dataset("x", data=np.array([-1, 0, 1], dtype=np.int16))
        y = dataset.create_dataset("y", data=np.array([1, 0, -1], dtype=np.int16))
        for coordinate in (x, y):
            coordinate.attrs["scale_factor"] = np.array([0.000056])
            coordinate.attrs["add_offset"] = np.array([0.0])
        dsr = dataset.create_dataset(
            "DSR",
            data=np.array([[10, 20, 30], [40, 50, 60], [70, 80, 90]], dtype=np.uint16),
        )
        dsr.attrs["scale_factor"] = np.array([2.0])
        dsr.attrs["add_offset"] = np.array([0.0])
        dsr.attrs["_FillValue"] = np.array([65535], dtype=np.uint16)
        dataset.create_dataset(
            "DQF",
            data=np.array([[0, 0, 0], [0, 1, 0], [0, 0, 0]], dtype=np.uint8),
        )


def test_discovery_excludes_known_and_not_yet_created_objects() -> None:
    from weather_trader.forecasting.goes_heating import discover_dsr_requests

    first = KEY
    future = (
        "ABI-L2-DSRF/2026/225/18/"
        "OR_ABI-L2-DSRF-M6_G19_s20262251810212_e20262251819520_c20262251827112.nc"
    )
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
    <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
      <Contents><Key>{first}</Key><LastModified>2026-08-13T18:17:25Z</LastModified><Size>10</Size></Contents>
      <Contents><Key>{future}</Key><LastModified>2026-08-13T18:27:20Z</LastModified><Size>11</Size></Contents>
    </ListBucketResult>'''.encode()

    class Response:
        content = xml
        def raise_for_status(self) -> None:
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    requests = discover_dsr_requests(
        {"19"},
        as_of_utc=datetime(2026, 8, 13, 18, 20, tzinfo=UTC),
        earliest_scan_at_utc=datetime(2026, 8, 13, 18, 0, tzinfo=UTC),
        session=Session(),
    )
    assert [item.source_key for item in requests] == [first]
    assert discover_dsr_requests(
        {"19"},
        as_of_utc=datetime(2026, 8, 13, 18, 20, tzinfo=UTC),
        earliest_scan_at_utc=datetime(2026, 8, 13, 18, 0, tzinfo=UTC),
        existing_source_keys={first},
        session=Session(),
    ) == []


def test_causal_station_window_rejects_artifacts_observed_after_decision(tmp_path: Path) -> None:
    from weather_trader.forecasting.goes_heating import causal_station_window

    path = tmp_path / "sample.nc"
    _write_synthetic_dsr(path)
    station = Station(
        city="Origin", station="KORG", display_name="Origin",
        timezone="UTC", latitude=0.0, longitude=-75.0,
    )
    artifacts = []
    for index, minute in enumerate((10, 20, 30, 40)):
        artifacts.append({
            "artifact_id": f"a{index}",
            "source_key": KEY,
            "valid_end_at_utc": f"2026-08-13T18:{minute:02d}:00+00:00",
            "causal_available_at_utc": (
                "2026-08-13T19:01:00+00:00" if minute == 40
                else f"2026-08-13T18:{minute + 1:02d}:00+00:00"
            ),
            "raw_path": str(path),
        })
    result = causal_station_window(
        artifacts,
        station,
        decision_time_utc=datetime(2026, 8, 13, 19, 0, tzinfo=UTC),
    )
    assert result is not None
    assert result["scan_count"] == 3
    assert result["latest_scan_end_at_utc"] == "2026-08-13T18:30:00+00:00"
    assert result["artifact_ids"] == ["a0", "a1", "a2"]


def test_normalized_radiation_surprise_uses_solar_geometry() -> None:
    from weather_trader.forecasting.goes_heating import (
        extraterrestrial_horizontal_w_m2,
        normalized_radiation_surprise,
    )

    station = Station(
        city="Atlanta", station="KATL", display_name="Atlanta",
        timezone="America/New_York", latitude=33.6367, longitude=-84.4281,
    )
    decision = datetime(2026, 8, 13, 18, 0, tzinfo=UTC)
    noonish = extraterrestrial_horizontal_w_m2(
        station.latitude, station.longitude, decision
    )
    midnight = extraterrestrial_horizontal_w_m2(
        station.latitude, station.longitude, datetime(2026, 8, 13, 5, 0, tzinfo=UTC)
    )
    assert noonish > 1000.0
    assert midnight == 0.0
    result = normalized_radiation_surprise(
        {
            "decision_time_utc": decision.isoformat(),
            "trailing_minutes": 60,
            "dsr_mean_w_m2": 900.0,
        },
        station,
        hrrr_shortwave_next_3h_mean=500.0,
    )
    assert result["observed_transmission_proxy"] > result["hrrr_transmission_proxy"]
    assert result["radiation_surprise"] > 0
