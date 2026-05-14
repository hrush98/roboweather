from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import threading
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

from weather_trader.forecasts.hrrr_archive import (
    FEATURE_RECORDS,
    HRRRArchiveClient,
    IndexRecord,
    _parse_index,
    summarize_hrrr_rows,
)
from weather_trader.forecasts.hrrr_client import _nearest_index
from weather_trader.stations.metadata import Station, get_station, list_stations


EXTRACTOR_VERSION = "hrrr_v2_points_1"


@dataclass(frozen=True)
class HRRRExtractionTask:
    cycle_utc: datetime
    forecast_hour: int

    @property
    def key(self) -> tuple[str, int]:
        return self.cycle_utc.isoformat(), self.forecast_hour


@dataclass
class HRRRV2Store:
    path: Path
    _connection: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, timeout=60)
            self._connection.execute("pragma journal_mode=wal")
            self._connection.execute("pragma busy_timeout=60000")
            self._migrate()
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _migrate(self) -> None:
        self.connection.execute(
            """
            create table if not exists hrrr_point_forecasts (
                station text not null,
                cycle_utc text not null,
                forecast_hour integer not null,
                valid_utc text not null,
                tmpf real,
                dwpf real,
                rh real,
                u10 real,
                v10 real,
                wind_speed_mph real,
                gust_mph real,
                tcdc real,
                dswrf real,
                source_model text not null,
                product text not null,
                extractor_version text not null,
                created_at text not null default current_timestamp,
                primary key (station, cycle_utc, forecast_hour, extractor_version)
            )
            """
        )
        self.connection.execute(
            """
            create table if not exists hrrr_extract_tasks (
                cycle_utc text not null,
                forecast_hour integer not null,
                status text not null,
                station_count integer not null default 0,
                row_count integer not null default 0,
                attempt_count integer not null default 0,
                error text,
                started_at text,
                finished_at text,
                elapsed_seconds real,
                extractor_version text not null,
                primary key (cycle_utc, forecast_hour, extractor_version)
            )
            """
        )
        self.connection.execute(
            "create index if not exists idx_hrrr_points_station_cycle on hrrr_point_forecasts (station, cycle_utc, forecast_hour)"
        )
        self.connection.execute(
            "create index if not exists idx_hrrr_tasks_status on hrrr_extract_tasks (status, cycle_utc, forecast_hour)"
        )
        self.connection.commit()

    def has_complete_task(self, task: HRRRExtractionTask) -> bool:
        row = self.connection.execute(
            """
            select 1 from hrrr_extract_tasks
            where cycle_utc = ? and forecast_hour = ? and extractor_version = ? and status = 'done'
            """,
            (*task.key, EXTRACTOR_VERSION),
        ).fetchone()
        return row is not None

    def mark_running(self, task: HRRRExtractionTask) -> None:
        self.connection.execute(
            """
            insert into hrrr_extract_tasks (
                cycle_utc, forecast_hour, status, attempt_count, started_at, extractor_version
            )
            values (?, ?, 'running', 1, current_timestamp, ?)
            on conflict(cycle_utc, forecast_hour, extractor_version) do update set
                status = 'running',
                attempt_count = attempt_count + 1,
                error = null,
                started_at = current_timestamp,
                finished_at = null,
                elapsed_seconds = null
            """,
            (*task.key, EXTRACTOR_VERSION),
        )
        self.connection.commit()

    def mark_failed(self, task: HRRRExtractionTask, error: str, elapsed_seconds: float) -> None:
        self.connection.execute(
            """
            insert into hrrr_extract_tasks (
                cycle_utc, forecast_hour, status, attempt_count, error, finished_at, elapsed_seconds, extractor_version
            )
            values (?, ?, 'failed', 1, ?, current_timestamp, ?, ?)
            on conflict(cycle_utc, forecast_hour, extractor_version) do update set
                status = 'failed',
                error = excluded.error,
                finished_at = current_timestamp,
                elapsed_seconds = excluded.elapsed_seconds
            """,
            (*task.key, error[:1000], elapsed_seconds, EXTRACTOR_VERSION),
        )
        self.connection.commit()

    def write_task_rows(self, task: HRRRExtractionTask, rows: list[dict[str, object]], elapsed_seconds: float) -> None:
        with self.connection:
            self.connection.executemany(
                """
                insert or replace into hrrr_point_forecasts (
                    station, cycle_utc, forecast_hour, valid_utc,
                    tmpf, dwpf, rh, u10, v10, wind_speed_mph, gust_mph, tcdc, dswrf,
                    source_model, product, extractor_version
                )
                values (
                    :station, :cycle_utc, :forecast_hour, :valid_utc,
                    :tmpf, :dwpf, :rh, :u10, :v10, :wind_speed_mph, :gust_mph, :tcdc, :dswrf,
                    :source_model, :product, :extractor_version
                )
                """,
                rows,
            )
            self.connection.execute(
                """
                insert into hrrr_extract_tasks (
                    cycle_utc, forecast_hour, status, station_count, row_count,
                    attempt_count, finished_at, elapsed_seconds, extractor_version
                )
                values (?, ?, 'done', ?, ?, 1, current_timestamp, ?, ?)
                on conflict(cycle_utc, forecast_hour, extractor_version) do update set
                    status = 'done',
                    station_count = excluded.station_count,
                    row_count = excluded.row_count,
                    error = null,
                    finished_at = current_timestamp,
                    elapsed_seconds = excluded.elapsed_seconds
                """,
                (*task.key, len({str(row["station"]) for row in rows}), len(rows), elapsed_seconds, EXTRACTOR_VERSION),
            )

    def get_point_rows(self, station: str, cycle_utc: datetime, forecast_hours: list[int]) -> pd.DataFrame:
        if not forecast_hours:
            return pd.DataFrame()
        placeholders = ",".join("?" for _ in forecast_hours)
        params: list[object] = [station, cycle_utc.isoformat(), EXTRACTOR_VERSION, *forecast_hours]
        return pd.read_sql_query(
            f"""
            select forecast_hour, tmpf, dwpf, rh, u10, v10, wind_speed_mph, gust_mph, tcdc, dswrf
            from hrrr_point_forecasts
            where station = ? and cycle_utc = ? and extractor_version = ?
              and forecast_hour in ({placeholders})
            order by forecast_hour
            """,
            self.connection,
            params=params,
        )

    def status(self) -> dict[str, object]:
        task_rows = self.connection.execute(
            """
            select status, count(*), coalesce(sum(row_count), 0)
            from hrrr_extract_tasks
            where extractor_version = ?
            group by status
            """,
            (EXTRACTOR_VERSION,),
        ).fetchall()
        station_rows = self.connection.execute(
            """
            select station, count(*)
            from hrrr_point_forecasts
            where extractor_version = ?
            group by station
            order by station
            """,
            (EXTRACTOR_VERSION,),
        ).fetchall()
        return {
            "cache": str(self.path),
            "tasks": {status: {"tasks": int(count), "rows": int(rows)} for status, count, rows in task_rows},
            "rows_by_station": {station: int(count) for station, count in station_rows},
        }


@dataclass
class GribRangeHRRRSource:
    timeout_seconds: int = 120
    _idx_cache: dict[tuple[datetime, int], list[IndexRecord]] = field(default_factory=dict)
    _idx_lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch_point_rows(self, task: HRRRExtractionTask, stations: list[Station]) -> list[dict[str, object]]:
        records = self._feature_records(task.cycle_utc, task.forecast_hour)
        payloads = [
            (name, transform, self._download_byte_range(task.cycle_utc, task.forecast_hour, record))
            for name, record, transform in records.values()
        ]
        station_indices: dict[str, tuple[int, int]] = {}
        values_by_station: dict[str, dict[str, float]] = {
            station.station: {name: np.nan for name, *_ in FEATURE_RECORDS} for station in stations
        }
        with NamedTemporaryFile(suffix=".grib2") as handle:
            for _, _, payload in payloads:
                handle.write(payload)
            handle.flush()
            if pygrib is None:
                raise RuntimeError("pygrib is required for HRRR archive GRIB decoding")
            messages = pygrib.open(handle.name)
            try:
                for idx, message in enumerate(messages):
                    name, transform, _ = payloads[idx]
                    if not station_indices:
                        data, lats, lons = message.data()
                        station_indices = {
                            station.station: _nearest_index(lats, lons, station.latitude, station.longitude)
                            for station in stations
                        }
                    else:
                        data = message.values
                    for station in stations:
                        lat_idx, lon_idx = station_indices[station.station]
                        value = float(data[lat_idx, lon_idx])
                        values_by_station[station.station][name] = float(transform(value)) if transform is not None else value
            finally:
                messages.close()

        rows = []
        valid_utc = task.cycle_utc + pd.Timedelta(hours=task.forecast_hour)
        for station in stations:
            values = values_by_station[station.station]
            u10 = values.get("u10", np.nan)
            v10 = values.get("v10", np.nan)
            wind_speed = float(np.hypot(u10, v10) * 2.23694) if pd.notna(u10) and pd.notna(v10) else np.nan
            rows.append(
                _sqlite_safe_row(
                    {
                        "station": station.station,
                        "cycle_utc": task.cycle_utc.isoformat(),
                        "forecast_hour": task.forecast_hour,
                        "valid_utc": valid_utc.isoformat(),
                        "tmpf": values.get("tmpf", np.nan),
                        "dwpf": values.get("dwpf", np.nan),
                        "rh": values.get("rh", np.nan),
                        "u10": u10,
                        "v10": v10,
                        "wind_speed_mph": wind_speed,
                        "gust_mph": values.get("gust_mph", np.nan),
                        "tcdc": values.get("tcdc", np.nan),
                        "dswrf": values.get("dswrf", np.nan),
                        "source_model": "hrrr",
                        "product": "wrfsfc",
                        "extractor_version": EXTRACTOR_VERSION,
                    }
                )
            )
        return rows

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

    def _load_index(self, cycle_utc: datetime, forecast_hour: int) -> list[IndexRecord]:
        key = (cycle_utc, forecast_hour)
        with self._idx_lock:
            if key in self._idx_cache:
                return self._idx_cache[key]
        response = requests.get(HRRRArchiveClient._grib_url(cycle_utc, forecast_hour) + ".idx", timeout=self.timeout_seconds)
        response.raise_for_status()
        records = _parse_index(response.text)
        with self._idx_lock:
            self._idx_cache[key] = records
        return records

    def _download_byte_range(self, cycle_utc: datetime, forecast_hour: int, record: IndexRecord) -> bytes:
        if record.next_offset is None:
            range_header = f"bytes={record.offset}-"
        else:
            range_header = f"bytes={record.offset}-{record.next_offset - 1}"
        response = requests.get(
            HRRRArchiveClient._grib_url(cycle_utc, forecast_hour),
            headers={"Range": range_header},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.content


def selected_snapshots(
    dataset: pd.DataFrame,
    max_snapshots: int | None = None,
    max_snapshots_per_year: int | None = None,
    sample_strategy: str = "even",
) -> pd.DataFrame:
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
    return unique[["snapshot_key", "station", "snapshot_time_utc"]].sort_values("snapshot_time_utc")


def build_extraction_tasks(
    dataset: pd.DataFrame,
    *,
    max_snapshots: int | None = None,
    max_snapshots_per_year: int | None = None,
    sample_strategy: str = "even",
    forecast_stride_hours: int = 3,
    max_forecast_hour: int = 18,
) -> list[HRRRExtractionTask]:
    snapshots = selected_snapshots(dataset, max_snapshots, max_snapshots_per_year, sample_strategy)
    planner = HRRRArchiveClient(forecast_stride_hours=forecast_stride_hours, max_forecast_hour=max_forecast_hour)
    tasks: dict[tuple[str, int], HRRRExtractionTask] = {}
    for row in snapshots.itertuples(index=False):
        station = get_station(row.station)
        as_of_utc = row.snapshot_time_utc.to_pydatetime()
        cycle, forecast_hours = planner.forecast_plan(station=station, as_of_utc=as_of_utc)
        for forecast_hour in forecast_hours:
            task = HRRRExtractionTask(cycle_utc=cycle, forecast_hour=forecast_hour)
            tasks[task.key] = task
    return sorted(tasks.values(), key=lambda task: (task.cycle_utc, task.forecast_hour))


def build_hrrr_v2_cache(
    dataset: pd.DataFrame,
    cache_path: Path,
    stations: list[Station] | None = None,
    *,
    max_snapshots: int | None = None,
    max_snapshots_per_year: int | None = None,
    sample_strategy: str = "even",
    forecast_stride_hours: int = 3,
    max_forecast_hour: int = 18,
    workers: int = 4,
    progress_every: int = 25,
    source: GribRangeHRRRSource | None = None,
) -> dict[str, object]:
    store = HRRRV2Store(cache_path)
    source = source or GribRangeHRRRSource()
    stations = stations or list_stations(initial_only=False)
    tasks = [
        task
        for task in build_extraction_tasks(
            dataset,
            max_snapshots=max_snapshots,
            max_snapshots_per_year=max_snapshots_per_year,
            sample_strategy=sample_strategy,
            forecast_stride_hours=forecast_stride_hours,
            max_forecast_hour=max_forecast_hour,
        )
        if not store.has_complete_task(task)
    ]
    total = len(tasks)
    started_at = time.monotonic()
    completed = 0
    fetched = 0
    errors: list[str] = []
    print(
        f"HRRR v2 extract starting tasks={total} stations={len(stations)} workers={workers} cache={cache_path}",
        file=sys.stderr,
        flush=True,
    )
    try:
        if workers <= 1:
            for task in tasks:
                result = _fetch_task_rows(task, stations, source)
                fetched += _persist_result(store, result, errors)
                completed += 1
                _print_progress("HRRR v2 extract", completed, total, fetched, errors, started_at, progress_every)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_fetch_task_rows, task, stations, source): task for task in tasks}
                for future in as_completed(futures):
                    result = future.result()
                    fetched += _persist_result(store, result, errors)
                    completed += 1
                    _print_progress("HRRR v2 extract", completed, total, fetched, errors, started_at, progress_every)
    finally:
        summary = store.status()
        store.close()
    summary.update(
        {
            "tasks_requested": total,
            "tasks_completed_this_run": completed,
            "tasks_fetched_this_run": fetched,
            "errors": errors[:20],
            "elapsed_minutes": round((time.monotonic() - started_at) / 60.0, 2),
        }
    )
    return summary


def materialize_hrrr_v2_features(
    dataset: pd.DataFrame,
    cache_path: Path,
    *,
    max_snapshots: int | None = None,
    max_snapshots_per_year: int | None = None,
    sample_strategy: str = "even",
    forecast_stride_hours: int = 3,
    max_forecast_hour: int = 18,
    progress_every: int = 250,
) -> pd.DataFrame:
    store = HRRRV2Store(cache_path)
    frame = dataset.copy()
    frame["snapshot_time_utc"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    frame["snapshot_key"] = frame["station"].astype(str) + "|" + frame["snapshot_time_utc"].astype(str)
    snapshots = selected_snapshots(frame, max_snapshots, max_snapshots_per_year, sample_strategy)
    planner = HRRRArchiveClient(forecast_stride_hours=forecast_stride_hours, max_forecast_hour=max_forecast_hour)
    feature_rows = []
    started_at = time.monotonic()
    try:
        total = len(snapshots)
        for idx, row in enumerate(snapshots.itertuples(index=False), start=1):
            station = get_station(row.station)
            as_of_utc = row.snapshot_time_utc.to_pydatetime()
            cycle, forecast_hours = planner.forecast_plan(station=station, as_of_utc=as_of_utc)
            point_rows = store.get_point_rows(station.station, cycle, forecast_hours)
            if point_rows.empty:
                features = {"hrrr_cycle_utc": cycle.isoformat(), "hrrr_current_temp": np.nan, "hrrr_remaining_max": np.nan}
            else:
                features = summarize_hrrr_rows(point_rows)
                features["hrrr_cycle_utc"] = cycle.isoformat()
            features["snapshot_key"] = row.snapshot_key
            feature_rows.append(features)
            if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
                _print_materialize_progress(idx, total, started_at)
    finally:
        store.close()
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


def _fetch_task_rows(
    task: HRRRExtractionTask,
    stations: list[Station],
    source: GribRangeHRRRSource,
) -> dict[str, object]:
    started_at = time.monotonic()
    try:
        rows = source.fetch_point_rows(task, stations)
        return {"status": "fetched", "task": task, "rows": rows, "elapsed_seconds": time.monotonic() - started_at}
    except Exception as exc:
        return {"status": "error", "task": task, "error": str(exc), "elapsed_seconds": time.monotonic() - started_at}


def _persist_result(store: HRRRV2Store, result: dict[str, object], errors: list[str]) -> int:
    task = result["task"]
    assert isinstance(task, HRRRExtractionTask)
    elapsed_seconds = float(result["elapsed_seconds"])
    if result["status"] == "fetched":
        rows = result["rows"]
        assert isinstance(rows, list)
        store.write_task_rows(task, rows, elapsed_seconds)
        return 1
    error = str(result["error"])
    store.mark_failed(task, error, elapsed_seconds)
    errors.append(f"{task.cycle_utc.isoformat()} f{task.forecast_hour}: {error}")
    return 0


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


def _sqlite_safe_row(row: dict[str, object]) -> dict[str, object]:
    safe = {}
    for key, value in row.items():
        if isinstance(value, float) and np.isnan(value):
            safe[key] = None
        else:
            safe[key] = value
    return safe


def _print_progress(
    label: str,
    completed: int,
    total: int,
    fetched: int,
    errors: list[str],
    started_at: float,
    progress_every: int,
) -> None:
    if not progress_every or not total:
        return
    if completed != 1 and completed % progress_every != 0 and completed != total:
        return
    elapsed = max(0.001, time.monotonic() - started_at)
    rate = completed / elapsed
    remaining = (total - completed) / rate if rate else 0.0
    print(
        (
            f"{label} {completed}/{total} ({completed / total:.1%}) "
            f"fetched={fetched} errors={len(errors)} rate={rate:.2f}/s eta_minutes={remaining / 60:.1f}"
        ),
        file=sys.stderr,
        flush=True,
    )


def _print_materialize_progress(completed: int, total: int, started_at: float) -> None:
    elapsed = max(0.001, time.monotonic() - started_at)
    rate = completed / elapsed
    remaining = (total - completed) / rate if rate else 0.0
    print(
        f"HRRR v2 materialize {completed}/{total} ({completed / total:.1%}) rate={rate:.2f}/s eta_minutes={remaining / 60:.1f}",
        file=sys.stderr,
        flush=True,
    )
