from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

try:
    import pygrib
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal local envs
    pygrib = None

from weather_trader.forecasts.hrrr_client import _kelvin_to_f, _nearest_index
from weather_trader.stations.metadata import Station, get_station


S3_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
FEATURE_RECORDS = (
    ("tmpf", "TMP", "2 m above ground", _kelvin_to_f, False),
    ("dwpf", "DPT", "2 m above ground", _kelvin_to_f, True),
    ("rh", "RH", "2 m above ground", None, True),
    ("u10", "UGRD", "10 m above ground", None, True),
    ("v10", "VGRD", "10 m above ground", None, True),
    ("gust_mph", "GUST", "surface", lambda value: value * 2.23694, True),
    ("tcdc", "TCDC", "entire atmosphere", None, True),
    ("dswrf", "DSWRF", "surface", None, True),
)


@dataclass(frozen=True)
class IndexRecord:
    number: int
    offset: int
    date_token: str
    variable: str
    level: str
    forecast_label: str
    next_offset: int | None = None


@dataclass
class HRRRArchiveClient:
    timeout_seconds: int = 120
    cycle_lag_hours: int = 1
    max_forecast_hour: int = 18
    forecast_stride_hours: int = 3
    cache_path: Path | None = None
    _idx_cache: dict[tuple[datetime, int], list[IndexRecord]] = field(default_factory=dict)
    _grid_index_cache: dict[str, tuple[int, int]] = field(default_factory=dict)
    _feature_cache: HRRRFeatureCache | None = None

    def fetch_remaining_day_features(self, station: Station, as_of_utc: datetime) -> dict[str, float]:
        if as_of_utc.tzinfo is None:
            raise ValueError("as_of_utc must be timezone-aware")
        cached = self._get_cached_features(station=station, as_of_utc=as_of_utc)
        if cached is not None:
            return cached
        cycle, forecast_hours = self.forecast_plan(station=station, as_of_utc=as_of_utc)
        rows = []
        for forecast_hour in forecast_hours:
            try:
                rows.append(self.fetch_point_feature_row(station=station, cycle_utc=cycle, forecast_hour=forecast_hour))
            except (LookupError, requests.HTTPError):
                continue
        if not rows:
            features = {
                "hrrr_cycle_utc": cycle.isoformat(),
                "hrrr_current_temp": np.nan,
                "hrrr_remaining_max": np.nan,
            }
            self._set_cached_features(station=station, as_of_utc=as_of_utc, features=features)
            return features
        frame = pd.DataFrame(rows).sort_values("forecast_hour")
        features = summarize_hrrr_rows(frame)
        features["hrrr_cycle_utc"] = cycle.isoformat()
        self._set_cached_features(station=station, as_of_utc=as_of_utc, features=features)
        return features

    def forecast_plan(self, station: Station, as_of_utc: datetime) -> tuple[datetime, list[int]]:
        if as_of_utc.tzinfo is None:
            raise ValueError("as_of_utc must be timezone-aware")
        cycle = self._cycle_for_as_of(as_of_utc)
        current_forecast_hour = max(0, int(np.ceil((as_of_utc - cycle).total_seconds() / 3600)))
        local_now = as_of_utc.astimezone(ZoneInfo(station.timezone))
        local_midnight = datetime.combine(
            local_now.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=ZoneInfo(station.timezone),
        )
        remaining_hours = max(
            0,
            int(np.ceil((local_midnight.astimezone(ZoneInfo("UTC")) - as_of_utc).total_seconds() / 3600)),
        )
        final_forecast_hour = min(self.max_forecast_hour, current_forecast_hour + remaining_hours)
        forecast_hours = [current_forecast_hour]
        forecast_hours.extend(
            range(current_forecast_hour + self.forecast_stride_hours, final_forecast_hour + 1, self.forecast_stride_hours)
        )
        return cycle, sorted(set(forecast_hours))

    def fetch_point_tmpf(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> float:
        return self.fetch_point_value(
            station=station,
            cycle_utc=cycle_utc,
            forecast_hour=forecast_hour,
            variable="TMP",
            level="2 m above ground",
            transform=_kelvin_to_f,
        )

    def fetch_point_feature_row(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> dict[str, float]:
        cached = self._get_cached_point_row(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour)
        if cached is not None:
            return cached
        row = self._fetch_point_feature_row_uncached(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour)
        self._set_cached_point_row(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour, row=row)
        return row

    def has_cached_point_row(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> bool:
        return self._get_cached_point_row(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour) is not None

    def _fetch_point_feature_row_uncached(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> dict[str, float]:
        values = self._fetch_point_feature_values(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour)
        row = {
            "forecast_hour": forecast_hour,
            **values,
        }
        row["wind_speed_mph"] = float(np.hypot(row["u10"], row["v10"]) * 2.23694) if pd.notna(row["u10"]) and pd.notna(row["v10"]) else np.nan
        return row

    def _fetch_point_feature_values(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> dict[str, float]:
        records = self._feature_records(cycle_utc, forecast_hour)
        if "tmpf" not in records:
            raise LookupError(f"No HRRR record for {cycle_utc=} {forecast_hour=} variable='TMP' level='2 m above ground'")
        payloads = [(name, transform, self._download_byte_range(cycle_utc, forecast_hour, record)) for name, record, transform in records.values()]
        include_grid = station.station not in self._grid_index_cache
        values: dict[str, float] = {name: np.nan for name, *_ in FEATURE_RECORDS}
        with NamedTemporaryFile(suffix=".grib2") as handle:
            for _, _, payload in payloads:
                handle.write(payload)
            handle.flush()
            if pygrib is None:
                raise RuntimeError("pygrib is required for HRRR archive GRIB decoding")
            messages = pygrib.open(handle.name)
            try:
                for idx, message in enumerate(messages, start=0):
                    name, transform, _ = payloads[idx]
                    if include_grid and station.station not in self._grid_index_cache:
                        data, lats, lons = message.data()
                        self._grid_index_cache[station.station] = _nearest_index(lats, lons, station.latitude, station.longitude)
                    else:
                        data = message.values
                    lat_idx, lon_idx = self._grid_index_cache[station.station]
                    value = float(data[lat_idx, lon_idx])
                    values[name] = float(transform(value)) if transform is not None else value
            finally:
                messages.close()
        return values

    def _feature_records(self, cycle_utc: datetime, forecast_hour: int) -> dict[str, tuple[str, IndexRecord, object]]:
        index = self._load_index(cycle_utc, forecast_hour)
        records: dict[str, tuple[str, IndexRecord, object]] = {}
        for name, variable, level, transform, optional in FEATURE_RECORDS:
            record = next((item for item in index if item.variable == variable and item.level == level), None)
            if record is None:
                if optional:
                    continue
                raise LookupError(f"No HRRR record for {cycle_utc=} {forecast_hour=} {variable=} {level=}")
            records[name] = (name, record, transform)
        return records

    def fetch_point_value_optional(
        self,
        station: Station,
        cycle_utc: datetime,
        forecast_hour: int,
        variable: str,
        level: str,
        transform=None,
    ) -> float:
        try:
            return self.fetch_point_value(station, cycle_utc, forecast_hour, variable, level, transform)
        except (LookupError, requests.HTTPError):
            return np.nan

    def fetch_point_value(
        self,
        station: Station,
        cycle_utc: datetime,
        forecast_hour: int,
        variable: str,
        level: str,
        transform=None,
    ) -> float:
        include_grid = station.station not in self._grid_index_cache
        data, lats, lons = self._load_message_arrays(
            cycle_utc=cycle_utc,
            forecast_hour=forecast_hour,
            variable=variable,
            level=level,
            include_grid=include_grid,
        )
        if include_grid:
            assert lats is not None and lons is not None
            self._grid_index_cache[station.station] = _nearest_index(lats, lons, station.latitude, station.longitude)
        lat_idx, lon_idx = self._grid_index_cache[station.station]
        value = float(data[lat_idx, lon_idx])
        if transform is not None:
            return float(transform(value))
        return value

    def _cycle_for_as_of(self, as_of_utc: datetime) -> datetime:
        return (as_of_utc - timedelta(hours=self.cycle_lag_hours)).replace(minute=0, second=0, microsecond=0)

    def _get_cached_features(self, station: Station, as_of_utc: datetime) -> dict[str, float] | None:
        cache = self._cache()
        if cache is None:
            return None
        return cache.get(
            station=station.station,
            as_of_utc=as_of_utc,
            cycle_lag_hours=self.cycle_lag_hours,
            max_forecast_hour=self.max_forecast_hour,
            forecast_stride_hours=self.forecast_stride_hours,
        )

    def has_cached_features(self, station: Station, as_of_utc: datetime) -> bool:
        return self._get_cached_features(station=station, as_of_utc=as_of_utc) is not None

    def _get_cached_point_row(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> dict[str, float] | None:
        cache = self._cache()
        if cache is None:
            return None
        return cache.get_point_row(station=station.station, cycle_utc=cycle_utc, forecast_hour=forecast_hour)

    def _set_cached_point_row(self, station: Station, cycle_utc: datetime, forecast_hour: int, row: dict[str, float]) -> None:
        cache = self._cache()
        if cache is None:
            return
        cache.set_point_row(station=station.station, cycle_utc=cycle_utc, forecast_hour=forecast_hour, row=row)

    def _set_cached_features(self, station: Station, as_of_utc: datetime, features: dict[str, float]) -> None:
        cache = self._cache()
        if cache is None:
            return
        cache.set(
            station=station.station,
            as_of_utc=as_of_utc,
            cycle_lag_hours=self.cycle_lag_hours,
            max_forecast_hour=self.max_forecast_hour,
            forecast_stride_hours=self.forecast_stride_hours,
            features=features,
        )

    def _cache(self) -> HRRRFeatureCache | None:
        if self.cache_path is None:
            return None
        if self._feature_cache is None:
            self._feature_cache = HRRRFeatureCache(self.cache_path)
        return self._feature_cache

    def _load_message_arrays(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
        variable: str,
        level: str,
        include_grid: bool,
    ):
        record = self._find_record(cycle_utc, forecast_hour, variable, level)
        payload = self._download_byte_range(cycle_utc, forecast_hour, record)
        with NamedTemporaryFile(suffix=".grib2") as handle:
            handle.write(payload)
            handle.flush()
            if pygrib is None:
                raise RuntimeError("pygrib is required for HRRR archive GRIB decoding")
            message = pygrib.open(handle.name).message(1)
            if include_grid:
                data, lats, lons = message.data()
                return data, lats, lons
            return message.values, None, None

    def _find_record(self, cycle_utc: datetime, forecast_hour: int, variable: str, level: str) -> IndexRecord:
        records = self._load_index(cycle_utc, forecast_hour)
        for record in records:
            if record.variable == variable and record.level == level:
                return record
        raise LookupError(f"No HRRR record for {cycle_utc=} {forecast_hour=} {variable=} {level=}")

    def _load_index(self, cycle_utc: datetime, forecast_hour: int) -> list[IndexRecord]:
        key = (cycle_utc, forecast_hour)
        if key in self._idx_cache:
            return self._idx_cache[key]
        response = requests.get(self._grib_url(cycle_utc, forecast_hour) + ".idx", timeout=self.timeout_seconds)
        response.raise_for_status()
        records = _parse_index(response.text)
        self._idx_cache[key] = records
        return records

    def _download_byte_range(self, cycle_utc: datetime, forecast_hour: int, record: IndexRecord) -> bytes:
        if record.next_offset is None:
            range_header = f"bytes={record.offset}-"
        else:
            range_header = f"bytes={record.offset}-{record.next_offset - 1}"
        response = requests.get(
            self._grib_url(cycle_utc, forecast_hour),
            headers={"Range": range_header},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _grib_url(cycle_utc: datetime, forecast_hour: int) -> str:
        return f"{S3_BASE}/hrrr.{cycle_utc:%Y%m%d}/conus/hrrr.t{cycle_utc:%H}z.wrfsfcf{forecast_hour:02d}.grib2"


def enrich_dataset_with_hrrr(
    dataset: pd.DataFrame,
    max_snapshots: int | None = None,
    max_snapshots_per_year: int | None = None,
    sample_strategy: str = "head",
    progress_every: int = 25,
    client: HRRRArchiveClient | None = None,
) -> pd.DataFrame:
    client = client or HRRRArchiveClient()
    frame = dataset.copy()
    frame["snapshot_time_utc"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    frame["snapshot_key"] = frame["station"].astype(str) + "|" + frame["snapshot_time_utc"].astype(str)
    unique = frame[["snapshot_key", "station", "snapshot_time_utc"]].drop_duplicates().sort_values("snapshot_time_utc")
    unique["year"] = unique["snapshot_time_utc"].dt.year
    if max_snapshots_per_year is not None:
        unique = unique.groupby("year", group_keys=False).apply(
            lambda group: _sample_snapshot_group(group, max_snapshots_per_year, sample_strategy),
        )
    if max_snapshots is not None:
        unique = _sample_snapshot_group(unique, max_snapshots, sample_strategy)

    feature_rows = []
    total = len(unique)
    started_at = time.monotonic()
    cache_hits = 0
    cache_misses = 0
    for idx, row in enumerate(unique.itertuples(index=False), start=1):
        station = get_station(row.station)
        was_cached = client.has_cached_features(station=station, as_of_utc=row.snapshot_time_utc.to_pydatetime())
        features = client.fetch_remaining_day_features(station=station, as_of_utc=row.snapshot_time_utc.to_pydatetime())
        if was_cached:
            cache_hits += 1
        else:
            cache_misses += 1
        features["snapshot_key"] = row.snapshot_key
        feature_rows.append(features)
        if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
            elapsed = max(0.001, time.monotonic() - started_at)
            rate = idx / elapsed
            remaining = (total - idx) / rate if rate else 0.0
            print(
                (
                    f"HRRR enrich {idx}/{total} ({idx / total:.1%}) "
                    f"station={row.station} snapshot={row.snapshot_time_utc} "
                    f"cache_hits={cache_hits} cache_misses={cache_misses} "
                    f"rate={rate:.2f}/s eta_minutes={remaining / 60:.1f}"
                ),
                file=sys.stderr,
                flush=True,
            )

    if not feature_rows:
        return frame.drop(columns=["snapshot_time_utc", "snapshot_key"])

    hrrr_frame = pd.DataFrame(feature_rows)
    enriched = frame.merge(hrrr_frame, on="snapshot_key", how="left")
    for column in hrrr_frame.columns:
        if column not in {"snapshot_key", "hrrr_cycle_utc"} and column in enriched:
            enriched[column] = pd.to_numeric(enriched[column], errors="coerce")
    enriched["hrrr_remaining_max_minus_threshold"] = enriched["hrrr_remaining_max"] - enriched["threshold"]
    enriched["hrrr_current_temp_minus_current_temp"] = enriched["hrrr_current_temp"] - enriched["current_temp"]
    if "hrrr_temp_next_3h_max" in enriched:
        enriched["hrrr_temp_next_3h_max_minus_threshold"] = enriched["hrrr_temp_next_3h_max"] - enriched["threshold"]
    return enriched.drop(columns=["snapshot_time_utc", "snapshot_key"])


def _sample_snapshot_group(group: pd.DataFrame, limit: int, strategy: str) -> pd.DataFrame:
    group = group.sort_values("snapshot_time_utc")
    if len(group) <= limit:
        return group
    if strategy == "head":
        return group.head(limit)
    if strategy == "even":
        indices = np.linspace(0, len(group) - 1, limit).round().astype(int)
        return group.iloc[sorted(set(indices))]
    raise ValueError(f"Unknown HRRR sample strategy: {strategy}")


class HRRRFeatureCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=60)
        self.connection.execute("pragma journal_mode=wal")
        self.connection.execute("pragma busy_timeout=60000")
        self.connection.execute(
            """
            create table if not exists hrrr_features (
                cache_key text primary key,
                station text not null,
                as_of_utc text not null,
                cycle_lag_hours integer not null,
                max_forecast_hour integer not null,
                forecast_stride_hours integer not null,
                features_json text not null,
                created_at text not null default current_timestamp
            )
            """
        )
        self.connection.execute(
            """
            create table if not exists hrrr_point_rows (
                cache_key text primary key,
                station text not null,
                cycle_utc text not null,
                forecast_hour integer not null,
                row_json text not null,
                created_at text not null default current_timestamp
            )
            """
        )
        self.connection.commit()

    def get(
        self,
        station: str,
        as_of_utc: datetime,
        cycle_lag_hours: int,
        max_forecast_hour: int,
        forecast_stride_hours: int,
    ) -> dict[str, float] | None:
        key = _cache_key(station, as_of_utc, cycle_lag_hours, max_forecast_hour, forecast_stride_hours)
        row = self.connection.execute(
            "select features_json from hrrr_features where cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(
        self,
        station: str,
        as_of_utc: datetime,
        cycle_lag_hours: int,
        max_forecast_hour: int,
        forecast_stride_hours: int,
        features: dict[str, float],
    ) -> None:
        key = _cache_key(station, as_of_utc, cycle_lag_hours, max_forecast_hour, forecast_stride_hours)
        self.connection.execute(
            """
            insert or replace into hrrr_features (
                cache_key, station, as_of_utc, cycle_lag_hours,
                max_forecast_hour, forecast_stride_hours, features_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                station,
                as_of_utc.isoformat(),
                cycle_lag_hours,
                max_forecast_hour,
                forecast_stride_hours,
                json.dumps(_json_safe_features(features), sort_keys=True),
            ),
        )
        self.connection.commit()

    def get_point_row(self, station: str, cycle_utc: datetime, forecast_hour: int) -> dict[str, float] | None:
        key = _point_row_cache_key(station, cycle_utc, forecast_hour)
        row = self.connection.execute(
            "select row_json from hrrr_point_rows where cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set_point_row(self, station: str, cycle_utc: datetime, forecast_hour: int, row: dict[str, float]) -> None:
        key = _point_row_cache_key(station, cycle_utc, forecast_hour)
        self.connection.execute(
            """
            insert or replace into hrrr_point_rows (
                cache_key, station, cycle_utc, forecast_hour, row_json
            )
            values (?, ?, ?, ?, ?)
            """,
            (
                key,
                station,
                cycle_utc.isoformat(),
                forecast_hour,
                json.dumps(_json_safe_features(row), sort_keys=True),
            ),
        )
        self.connection.commit()


def _cache_key(
    station: str,
    as_of_utc: datetime,
    cycle_lag_hours: int,
    max_forecast_hour: int,
    forecast_stride_hours: int,
) -> str:
    return "|".join(
        [
            station,
            as_of_utc.isoformat(),
            f"lag={cycle_lag_hours}",
            f"maxfh={max_forecast_hour}",
            f"stride={forecast_stride_hours}",
            "v2",
        ]
    )


def _point_row_cache_key(station: str, cycle_utc: datetime, forecast_hour: int) -> str:
    return "|".join([station, cycle_utc.isoformat(), f"fh={forecast_hour}", "point_v1"])


def _json_safe_features(features: dict[str, float]) -> dict[str, float | str | None]:
    safe: dict[str, float | str | None] = {}
    for key, value in features.items():
        if isinstance(value, float) and np.isnan(value):
            safe[key] = None
        else:
            safe[key] = value
    return safe


def summarize_hrrr_rows(frame: pd.DataFrame) -> dict[str, float]:
    head = frame.iloc[0]
    next_3h = frame.loc[frame["forecast_hour"] <= frame["forecast_hour"].iloc[0] + 3]
    return {
        "hrrr_current_temp": _safe_float(head.get("tmpf")),
        "hrrr_remaining_max": _safe_float(frame["tmpf"].max()),
        "hrrr_remaining_min": _safe_float(frame["tmpf"].min()),
        "hrrr_temp_next_3h_max": _safe_float(next_3h["tmpf"].max()),
        "hrrr_temp_next_3h_mean": _safe_float(next_3h["tmpf"].mean()),
        "hrrr_temp_trend_next_3h": _safe_float(next_3h["tmpf"].iloc[-1] - head["tmpf"]) if len(next_3h) else np.nan,
        "hrrr_dewpoint_current": _safe_float(head.get("dwpf")),
        "hrrr_dewpoint_next_3h_mean": _safe_float(next_3h["dwpf"].mean()) if "dwpf" in next_3h else np.nan,
        "hrrr_dewpoint_remaining_mean": _safe_float(frame["dwpf"].mean()) if "dwpf" in frame else np.nan,
        "hrrr_rh_current": _safe_float(head.get("rh")),
        "hrrr_rh_next_3h_mean": _safe_float(next_3h["rh"].mean()) if "rh" in next_3h else np.nan,
        "hrrr_rh_remaining_mean": _safe_float(frame["rh"].mean()) if "rh" in frame else np.nan,
        "hrrr_wind_speed_current": _safe_float(head.get("wind_speed_mph")),
        "hrrr_wind_speed_next_3h_mean": _safe_float(next_3h["wind_speed_mph"].mean()) if "wind_speed_mph" in next_3h else np.nan,
        "hrrr_wind_speed_remaining_max": _safe_float(frame["wind_speed_mph"].max()) if "wind_speed_mph" in frame else np.nan,
        "hrrr_gust_remaining_max": _safe_float(frame["gust_mph"].max()) if "gust_mph" in frame else np.nan,
        "hrrr_cloud_cover_current": _safe_float(head.get("tcdc")),
        "hrrr_cloud_cover_next_3h_mean": _safe_float(next_3h["tcdc"].mean()) if "tcdc" in next_3h else np.nan,
        "hrrr_cloud_cover_remaining_mean": _safe_float(frame["tcdc"].mean()) if "tcdc" in frame else np.nan,
        "hrrr_cloud_cover_remaining_max": _safe_float(frame["tcdc"].max()) if "tcdc" in frame else np.nan,
        "hrrr_shortwave_next_3h_mean": _safe_float(next_3h["dswrf"].mean()) if "dswrf" in next_3h else np.nan,
        "hrrr_shortwave_remaining_max": _safe_float(frame["dswrf"].max()) if "dswrf" in frame else np.nan,
        "hrrr_forecast_hours_count": float(len(frame)),
    }


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _parse_index(text: str) -> list[IndexRecord]:
    partial = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) < 6:
            continue
        partial.append(
            IndexRecord(
                number=int(parts[0]),
                offset=int(parts[1]),
                date_token=parts[2],
                variable=parts[3],
                level=parts[4],
                forecast_label=parts[5],
            )
        )
    records = []
    for idx, record in enumerate(partial):
        next_offset = partial[idx + 1].offset if idx + 1 < len(partial) else None
        records.append(
            IndexRecord(
                number=record.number,
                offset=record.offset,
                date_token=record.date_token,
                variable=record.variable,
                level=record.level,
                forecast_label=record.forecast_label,
                next_offset=next_offset,
            )
        )
    return records
