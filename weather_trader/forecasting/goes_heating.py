"""Causal GOES ABI downward-shortwave collection and station sampling.

Archive clocks remain provenance. A downloaded NOAA artifact is replay-visible
only from the source catalog's first successful local observation time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Iterable, Mapping
from xml.etree import ElementTree

import h5py
import numpy as np
from pyproj import CRS, Transformer
import requests

from weather_trader.forecasting.source_catalog import ArtifactRequest
from weather_trader.stations.metadata import Station

UTC = timezone.utc
GOES_DSR_SOURCE_ID = "goes_abi_dsr"
GOES_DSR_PRODUCT = "ABI-L2-DSRF"
GOES_EAST = "19"
GOES_WEST = "18"
GOES_WEST_LONGITUDE_MAX = -105.0
S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
KEY_TIMES = re.compile(r"_s(\d{14})_e(\d{14})_c(\d{14})\.nc$")


@dataclass(frozen=True)
class GoesDsrObject:
    satellite: str
    key: str
    scan_start_at_utc: datetime
    scan_end_at_utc: datetime
    created_at_utc: datetime
    last_modified_at_utc: datetime
    byte_count: int

    @property
    def url(self) -> str:
        return f"https://noaa-goes{self.satellite}.s3.amazonaws.com/{self.key}"


@dataclass(frozen=True)
class GoesDsrSample:
    station: str
    satellite: str
    scan_start_at_utc: str
    scan_end_at_utc: str
    dsr_median_w_m2: float
    dsr_mean_w_m2: float
    dsr_std_w_m2: float
    good_pixels: int
    requested_pixels: int
    center_x_index: int
    center_y_index: int


def satellite_for_longitude(longitude: float) -> str:
    return GOES_WEST if longitude <= GOES_WEST_LONGITUDE_MAX else GOES_EAST


def parse_goes_time(value: str) -> datetime:
    if len(value) < 13:
        raise ValueError(f"invalid GOES timestamp: {value}")
    parsed = datetime.strptime(value[:13], "%Y%j%H%M%S").replace(tzinfo=UTC)
    fraction = value[13:]
    if fraction:
        parsed += timedelta(seconds=int(fraction) / (10 ** len(fraction)))
    return parsed


def parse_dsr_object(
    satellite: str,
    key: str,
    *,
    last_modified_at_utc: str,
    byte_count: int,
) -> GoesDsrObject:
    if satellite not in {GOES_EAST, GOES_WEST}:
        raise ValueError(f"unsupported GOES satellite: {satellite}")
    match = KEY_TIMES.search(key)
    if not match or f"_G{satellite}_" not in key or GOES_DSR_PRODUCT not in key:
        raise ValueError(f"not a GOES-{satellite} DSR full-disk key: {key}")
    return GoesDsrObject(
        satellite=satellite,
        key=key,
        scan_start_at_utc=parse_goes_time(match.group(1)),
        scan_end_at_utc=parse_goes_time(match.group(2)),
        created_at_utc=parse_goes_time(match.group(3)),
        last_modified_at_utc=datetime.fromisoformat(
            last_modified_at_utc.replace("Z", "+00:00")
        ).astimezone(UTC),
        byte_count=int(byte_count),
    )


def list_dsr_objects(
    satellite: str,
    hour: datetime,
    *,
    session: requests.Session | None = None,
    timeout_seconds: float = 20.0,
) -> list[GoesDsrObject]:
    if hour.tzinfo is None:
        raise ValueError("GOES listing hour must be timezone-aware")
    hour = hour.astimezone(UTC)
    prefix = f"{GOES_DSR_PRODUCT}/{hour:%Y}/{hour:%j}/{hour:%H}/"
    client = session or requests.Session()
    response = client.get(
        f"https://noaa-goes{satellite}.s3.amazonaws.com/",
        params={"list-type": "2", "prefix": prefix, "max-keys": "100"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    output: list[GoesDsrObject] = []
    for item in root.findall("s3:Contents", S3_NAMESPACE):
        key = item.findtext("s3:Key", namespaces=S3_NAMESPACE)
        modified = item.findtext("s3:LastModified", namespaces=S3_NAMESPACE)
        size = item.findtext("s3:Size", namespaces=S3_NAMESPACE)
        if key and modified and size:
            output.append(
                parse_dsr_object(
                    satellite,
                    key,
                    last_modified_at_utc=modified,
                    byte_count=int(size),
                )
            )
    return sorted(output, key=lambda item: (item.scan_start_at_utc, item.key))


def discover_dsr_requests(
    satellites: Iterable[str],
    *,
    as_of_utc: datetime,
    earliest_scan_at_utc: datetime,
    existing_source_keys: Iterable[str] = (),
    session: requests.Session | None = None,
) -> list[ArtifactRequest]:
    if as_of_utc.tzinfo is None or earliest_scan_at_utc.tzinfo is None:
        raise ValueError("GOES discovery bounds must be timezone-aware")
    as_of = as_of_utc.astimezone(UTC)
    earliest = earliest_scan_at_utc.astimezone(UTC)
    known = set(existing_source_keys)
    hours: list[datetime] = []
    cursor = earliest.replace(minute=0, second=0, microsecond=0)
    while cursor <= as_of:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    objects: dict[str, GoesDsrObject] = {}
    for satellite in sorted(set(satellites)):
        for hour in hours:
            for item in list_dsr_objects(satellite, hour, session=session):
                if (
                    item.key not in known
                    and item.scan_start_at_utc >= earliest
                    and item.scan_end_at_utc <= as_of
                    and item.created_at_utc <= as_of
                ):
                    objects[item.key] = item
    return [object_to_request(objects[key]) for key in sorted(objects)]


def object_to_request(item: GoesDsrObject) -> ArtifactRequest:
    return ArtifactRequest(
        source_id=GOES_DSR_SOURCE_ID,
        source_key=item.key,
        url=item.url,
        valid_start_at_utc=item.scan_start_at_utc.isoformat(),
        valid_end_at_utc=item.scan_end_at_utc.isoformat(),
        metadata={
            "satellite": f"GOES-{item.satellite}",
            "product": GOES_DSR_PRODUCT,
            "embedded_created_at_utc": item.created_at_utc.isoformat(),
            "s3_last_modified_at_utc": item.last_modified_at_utc.isoformat(),
            "listed_byte_count": item.byte_count,
            "causal_clock": "FIRST_SUCCESSFUL_LOCAL_OBSERVATION",
        },
    )


def sample_station_dsr(
    path: str,
    station: Station,
    *,
    radius_pixels: int = 1,
    minimum_good_pixels: int = 5,
) -> GoesDsrSample:
    if radius_pixels < 0:
        raise ValueError("radius_pixels must be nonnegative")
    requested = (2 * radius_pixels + 1) ** 2
    with h5py.File(path, "r") as dataset:
        title = _text(dataset.attrs.get("title"))
        if "Downward Shortwave Radiation" not in title:
            raise ValueError("artifact is not ABI L2 downward shortwave radiation")
        projection = dataset["goes_imager_projection"].attrs
        height = _scalar(projection["perspective_point_height"])
        geos = CRS.from_proj4(
            "+proj=geos +h={height} +lon_0={lon} +sweep={sweep} "
            "+a={major} +b={minor} +units=m +no_defs".format(
                height=height,
                lon=_scalar(projection["longitude_of_projection_origin"]),
                sweep=_text(projection["sweep_angle_axis"]),
                major=_scalar(projection["semi_major_axis"]),
                minor=_scalar(projection["semi_minor_axis"]),
            )
        )
        x_m, y_m = Transformer.from_crs(
            "EPSG:4326", geos, always_xy=True
        ).transform(station.longitude, station.latitude)
        if not np.isfinite(x_m) or not np.isfinite(y_m):
            raise ValueError(f"station {station.station} is outside the GOES view")
        x = _scaled_coordinate(dataset["x"])
        y = _scaled_coordinate(dataset["y"])
        x_index = int(np.argmin(np.abs(x - x_m / height)))
        y_index = int(np.argmin(np.abs(y - y_m / height)))
        y_slice = slice(max(0, y_index - radius_pixels), y_index + radius_pixels + 1)
        x_slice = slice(max(0, x_index - radius_pixels), x_index + radius_pixels + 1)
        raw = dataset["DSR"][y_slice, x_slice]
        quality = dataset["DQF"][y_slice, x_slice]
        dsr = raw.astype(float) * _scalar(dataset["DSR"].attrs["scale_factor"])
        dsr += _scalar(dataset["DSR"].attrs.get("add_offset", 0.0))
        fill = int(np.asarray(dataset["DSR"].attrs["_FillValue"]).flat[0])
        good = dsr[(quality == 0) & (raw != fill) & np.isfinite(dsr)]
        if len(good) < minimum_good_pixels:
            raise ValueError(
                f"station {station.station} has {len(good)}/{requested} good DSR pixels"
            )
        return GoesDsrSample(
            station=station.station,
            satellite=_text(dataset.attrs.get("platform_ID")),
            scan_start_at_utc=_text(dataset.attrs["time_coverage_start"]),
            scan_end_at_utc=_text(dataset.attrs["time_coverage_end"]),
            dsr_median_w_m2=float(np.median(good)),
            dsr_mean_w_m2=float(np.mean(good)),
            dsr_std_w_m2=float(np.std(good)),
            good_pixels=len(good),
            requested_pixels=requested,
            center_x_index=x_index,
            center_y_index=y_index,
        )


def _scaled_coordinate(dataset: h5py.Dataset) -> np.ndarray:
    values = dataset[:].astype(float)
    return values * _scalar(dataset.attrs["scale_factor"]) + _scalar(
        dataset.attrs.get("add_offset", 0.0)
    )


def _scalar(value: object) -> float:
    return float(np.asarray(value).flat[0])


def _text(value: object) -> str:
    raw = np.asarray(value).flat[0]
    return raw.decode() if isinstance(raw, (bytes, np.bytes_)) else str(raw)


def causal_station_window(
    artifacts: Iterable[Mapping[str, object]],
    station: Station,
    *,
    decision_time_utc: datetime,
    trailing_minutes: int = 60,
    minimum_scans: int = 3,
) -> dict[str, object] | None:
    """Aggregate only scans locally observed by the frozen decision time."""
    if decision_time_utc.tzinfo is None:
        raise ValueError("GOES decision time must be timezone-aware")
    decision = decision_time_utc.astimezone(UTC)
    start = decision - timedelta(minutes=trailing_minutes)
    satellite = satellite_for_longitude(station.longitude)
    eligible: list[Mapping[str, object]] = []
    for artifact in artifacts:
        key = str(artifact["source_key"])
        if f"_G{satellite}_" not in key:
            continue
        valid_end = _aware(str(artifact["valid_end_at_utc"]))
        observed = _aware(str(artifact["causal_available_at_utc"]))
        if start <= valid_end <= decision and observed <= decision:
            eligible.append(artifact)
    eligible.sort(key=lambda row: (str(row["valid_end_at_utc"]), str(row["artifact_id"])))
    sampled: list[tuple[Mapping[str, object], GoesDsrSample]] = []
    errors: list[str] = []
    for artifact in eligible:
        try:
            sampled.append((artifact, sample_station_dsr(str(artifact["raw_path"]), station)))
        except (KeyError, OSError, ValueError) as exc:
            errors.append(f"{artifact['artifact_id']}: {type(exc).__name__}: {exc}")
    if len(sampled) < minimum_scans:
        return None
    medians = np.asarray([item.dsr_median_w_m2 for _artifact, item in sampled], dtype=float)
    ends = [_aware(str(artifact["valid_end_at_utc"])) for artifact, _item in sampled]
    return {
        "station": station.station,
        "satellite": f"G{satellite}",
        "decision_time_utc": decision.isoformat(),
        "trailing_minutes": trailing_minutes,
        "scan_count": len(sampled),
        "first_scan_end_at_utc": min(ends).isoformat(),
        "latest_scan_end_at_utc": max(ends).isoformat(),
        "latest_scan_age_minutes": (decision - max(ends)).total_seconds() / 60.0,
        "dsr_median_w_m2": float(np.median(medians)),
        "dsr_mean_w_m2": float(np.mean(medians)),
        "dsr_std_w_m2": float(np.std(medians)),
        "dsr_latest_w_m2": float(medians[-1]),
        "dsr_change_w_m2": float(medians[-1] - medians[0]),
        "artifact_ids": [str(row["artifact_id"]) for row, _item in sampled],
        "sample_errors": errors,
    }


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(UTC)


def extraterrestrial_horizontal_w_m2(
    latitude: float,
    longitude: float,
    timestamp_utc: datetime,
) -> float:
    """Approximate top-of-atmosphere horizontal irradiance using NOAA geometry."""
    if timestamp_utc.tzinfo is None:
        raise ValueError("solar timestamp must be timezone-aware")
    value = timestamp_utc.astimezone(UTC)
    day = value.timetuple().tm_yday
    hour = value.hour + value.minute / 60.0 + value.second / 3600.0
    gamma = 2.0 * np.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    equation_minutes = 229.18 * (
        0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma)
    )
    declination = (
        0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma)
    )
    solar_minutes = (
        value.hour * 60.0 + value.minute + value.second / 60.0
        + equation_minutes + 4.0 * longitude
    ) % 1440.0
    hour_angle = np.deg2rad(solar_minutes / 4.0 - 180.0)
    latitude_rad = np.deg2rad(latitude)
    cosine_zenith = (
        np.sin(latitude_rad) * np.sin(declination)
        + np.cos(latitude_rad) * np.cos(declination) * np.cos(hour_angle)
    )
    distance_factor = 1.0 + 0.033 * np.cos(2.0 * np.pi * day / 365.0)
    return float(max(0.0, 1361.0 * distance_factor * cosine_zenith))


def normalized_radiation_surprise(
    window: Mapping[str, object],
    station: Station,
    *,
    hrrr_shortwave_next_3h_mean: float,
) -> dict[str, float]:
    """Observed trailing transmission minus HRRR forward transmission proxy."""
    decision = _aware(str(window["decision_time_utc"]))
    observed_time = decision - timedelta(minutes=float(window["trailing_minutes"]) / 2.0)
    observed_toa = extraterrestrial_horizontal_w_m2(
        station.latitude, station.longitude, observed_time
    )
    forecast_toa = np.mean([
        extraterrestrial_horizontal_w_m2(
            station.latitude, station.longitude, decision + timedelta(hours=offset)
        )
        for offset in (0.5, 1.5, 2.5)
    ])
    if observed_toa < 50.0 or forecast_toa < 50.0:
        raise ValueError("solar geometry is too dark for a stable radiation surprise")
    observed = float(window["dsr_mean_w_m2"]) / observed_toa
    forecast = float(hrrr_shortwave_next_3h_mean) / float(forecast_toa)
    return {
        "observed_transmission_proxy": observed,
        "hrrr_transmission_proxy": forecast,
        "radiation_surprise": observed - forecast,
    }
