from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from weather_trader.config import CACHE_DIR, PROCESSED_DIR, ensure_directories
from weather_trader.forecasts.hrrr_archive import HRRRArchiveClient, enrich_dataset_with_hrrr
from weather_trader.stations.metadata import get_station


DEFAULT_CACHE = CACHE_DIR / "hrrr_features.sqlite"
_THREAD_LOCAL = threading.local()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/export checkpointed HRRR station feature cache.")
    parser.add_argument("--dataset", required=True, help="Input same-day observation training CSV.")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="SQLite cache path.")
    parser.add_argument("--output", default=str(PROCESSED_DIR / "dataset_hrrr_enriched.csv"), help="Enriched output CSV.")
    parser.add_argument("--mode", choices=["build-cache", "export", "build-and-export", "status"], default="build-and-export")
    parser.add_argument("--max-snapshots", type=int, default=None)
    parser.add_argument("--max-snapshots-per-year", type=int, default=None)
    parser.add_argument("--sample-strategy", choices=["head", "even"], default="even")
    parser.add_argument("--forecast-stride-hours", type=int, default=3)
    parser.add_argument("--max-forecast-hour", type=int, default=18)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1, help="Parallel station/snapshot extraction workers.")
    args = parser.parse_args()

    ensure_directories()
    dataset = pd.read_csv(args.dataset)
    cache_path = Path(args.cache)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "status":
        print(json.dumps(cache_status(cache_path, dataset), indent=2))
        return

    if args.mode in {"build-cache", "build-and-export"}:
        build_summary = build_cache(
            dataset=dataset,
            cache_path=cache_path,
            max_snapshots=args.max_snapshots,
            max_snapshots_per_year=args.max_snapshots_per_year,
            sample_strategy=args.sample_strategy,
            forecast_stride_hours=args.forecast_stride_hours,
            max_forecast_hour=args.max_forecast_hour,
            progress_every=args.progress_every,
            workers=max(1, args.workers),
        )
        print(json.dumps(build_summary, indent=2), file=sys.stderr)
        if args.mode == "build-cache":
            return

    client = HRRRArchiveClient(
        cache_path=cache_path,
        forecast_stride_hours=args.forecast_stride_hours,
        max_forecast_hour=args.max_forecast_hour,
    )
    enriched = enrich_dataset_with_hrrr(
        dataset=dataset,
        max_snapshots=args.max_snapshots,
        max_snapshots_per_year=args.max_snapshots_per_year,
        sample_strategy=args.sample_strategy,
        progress_every=args.progress_every,
        client=client,
    )

    if args.mode in {"export", "build-and-export"}:
        enriched.to_csv(output_path, index=False)
        hrrr_rows = int(enriched["hrrr_remaining_max"].notna().sum()) if "hrrr_remaining_max" in enriched else 0
    summary = {
        "mode": args.mode,
        "cache": str(cache_path),
        "output": str(output_path) if args.mode in {"export", "build-and-export"} else None,
        "input_rows": int(len(dataset)),
        "output_rows": int(len(enriched)) if args.mode in {"export", "build-and-export"} else None,
        "hrrr_rows": hrrr_rows,
        "forecast_stride_hours": args.forecast_stride_hours,
        "max_forecast_hour": args.max_forecast_hour,
    }
    print(json.dumps(summary, indent=2), file=sys.stderr)


def build_cache(
    dataset: pd.DataFrame,
    cache_path: Path,
    max_snapshots: int | None,
    max_snapshots_per_year: int | None,
    sample_strategy: str,
    forecast_stride_hours: int,
    max_forecast_hour: int,
    progress_every: int,
    workers: int,
) -> dict[str, object]:
    snapshots = selected_snapshots(dataset, max_snapshots, max_snapshots_per_year, sample_strategy)
    total = len(snapshots)
    started_at = time.monotonic()
    completed = 0
    cache_hits = 0
    cache_misses = 0
    errors: list[str] = []
    tasks = [
        {
            "station": row.station,
            "snapshot_time_utc": row.snapshot_time_utc.isoformat(),
            "cache_path": str(cache_path),
            "forecast_stride_hours": forecast_stride_hours,
            "max_forecast_hour": max_forecast_hour,
        }
        for row in snapshots.itertuples(index=False)
    ]

    print(
        f"HRRR cache build starting snapshots={total} workers={workers} cache={cache_path}",
        file=sys.stderr,
        flush=True,
    )
    if workers == 1:
        iterator = (_fetch_one_snapshot(task) for task in tasks)
        for result in iterator:
            completed, cache_hits, cache_misses = _handle_result(
                result, completed, cache_hits, cache_misses, errors, total, started_at, progress_every
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_fetch_one_snapshot, task) for task in tasks]
            for future in as_completed(futures):
                completed, cache_hits, cache_misses = _handle_result(
                    future.result(), completed, cache_hits, cache_misses, errors, total, started_at, progress_every
                )

    return {
        "cache": str(cache_path),
        "snapshots": total,
        "completed": completed,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "errors": errors[:20],
        "workers": workers,
        "elapsed_minutes": round((time.monotonic() - started_at) / 60.0, 2),
    }


def _handle_result(
    result: dict[str, object],
    completed: int,
    cache_hits: int,
    cache_misses: int,
    errors: list[str],
    total: int,
    started_at: float,
    progress_every: int,
) -> tuple[int, int, int]:
    completed += 1
    if result["status"] == "cached":
        cache_hits += 1
    elif result["status"] == "fetched":
        cache_misses += 1
    else:
        errors.append(str(result["error"]))
    if progress_every and (completed == 1 or completed % progress_every == 0 or completed == total):
        elapsed = max(0.001, time.monotonic() - started_at)
        rate = completed / elapsed
        remaining = (total - completed) / rate if rate else 0.0
        print(
            (
                f"HRRR cache {completed}/{total} ({completed / total:.1%}) "
                f"hits={cache_hits} fetched={cache_misses} errors={len(errors)} "
                f"rate={rate:.2f}/s eta_minutes={remaining / 60:.1f}"
            ),
            file=sys.stderr,
            flush=True,
        )
    return completed, cache_hits, cache_misses


def _fetch_one_snapshot(task: dict[str, object]) -> dict[str, object]:
    try:
        client = _thread_client(
            cache_path=Path(str(task["cache_path"])),
            forecast_stride_hours=int(task["forecast_stride_hours"]),
            max_forecast_hour=int(task["max_forecast_hour"]),
        )
        station = get_station(str(task["station"]))
        as_of_utc = pd.Timestamp(str(task["snapshot_time_utc"])).to_pydatetime()
        if client.has_cached_features(station=station, as_of_utc=as_of_utc):
            return {"status": "cached", "station": station.station, "snapshot_time_utc": task["snapshot_time_utc"]}
        client.fetch_remaining_day_features(station=station, as_of_utc=as_of_utc)
        return {"status": "fetched", "station": station.station, "snapshot_time_utc": task["snapshot_time_utc"]}
    except Exception as exc:
        return {
            "status": "error",
            "station": task.get("station"),
            "snapshot_time_utc": task.get("snapshot_time_utc"),
            "error": f"{task.get('station')} {task.get('snapshot_time_utc')}: {exc}",
        }


def _thread_client(cache_path: Path, forecast_stride_hours: int, max_forecast_hour: int) -> HRRRArchiveClient:
    key = f"{cache_path}|{forecast_stride_hours}|{max_forecast_hour}"
    clients = getattr(_THREAD_LOCAL, "clients", None)
    if clients is None:
        clients = {}
        _THREAD_LOCAL.clients = clients
    if key not in clients:
        clients[key] = HRRRArchiveClient(
            cache_path=cache_path,
            forecast_stride_hours=forecast_stride_hours,
            max_forecast_hour=max_forecast_hour,
        )
    return clients[key]


def selected_snapshots(
    dataset: pd.DataFrame,
    max_snapshots: int | None,
    max_snapshots_per_year: int | None,
    sample_strategy: str,
) -> pd.DataFrame:
    frame = dataset.copy()
    frame["snapshot_time_utc"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    frame["snapshot_key"] = frame["station"].astype(str) + "|" + frame["snapshot_time_utc"].astype(str)
    unique = frame[["snapshot_key", "station", "snapshot_time_utc"]].drop_duplicates().sort_values("snapshot_time_utc")
    unique["year"] = unique["snapshot_time_utc"].dt.year
    if max_snapshots_per_year is not None:
        unique = unique.groupby("year", group_keys=False).apply(
            lambda group: sample_snapshot_group(group, max_snapshots_per_year, sample_strategy),
        )
    if max_snapshots is not None:
        unique = sample_snapshot_group(unique, max_snapshots, sample_strategy)
    return unique[["station", "snapshot_time_utc"]].sort_values("snapshot_time_utc")


def sample_snapshot_group(group: pd.DataFrame, limit: int, strategy: str) -> pd.DataFrame:
    group = group.sort_values("snapshot_time_utc")
    if len(group) <= limit:
        return group
    if strategy == "head":
        return group.head(limit)
    if strategy == "even":
        indices = np.linspace(0, len(group) - 1, limit).round().astype(int)
        return group.iloc[sorted(set(indices))]
    raise ValueError(f"Unknown sample strategy: {strategy}")


def cache_status(cache_path: Path, dataset: pd.DataFrame) -> dict[str, object]:
    total_snapshots = unique_snapshot_count(dataset)
    if not cache_path.exists():
        return {
            "cache": str(cache_path),
            "exists": False,
            "cached_snapshots": 0,
            "total_snapshots": total_snapshots,
            "pct_complete": 0.0,
        }
    connection = sqlite3.connect(cache_path)
    try:
        row = connection.execute(
            "select count(*), min(created_at), max(created_at) from hrrr_features"
        ).fetchone()
        station_rows = connection.execute(
            "select station, count(*) from hrrr_features group by station order by station"
        ).fetchall()
    finally:
        connection.close()
    cached = int(row[0] or 0)
    return {
        "cache": str(cache_path),
        "exists": True,
        "cached_snapshots": cached,
        "total_snapshots": total_snapshots,
        "pct_complete": round((cached / total_snapshots) * 100.0, 3) if total_snapshots else 0.0,
        "first_cached_at": row[1],
        "last_cached_at": row[2],
        "cached_by_station": {station: int(count) for station, count in station_rows},
        "approx_enriched_training_rows": cached * 9,
    }


def unique_snapshot_count(dataset: pd.DataFrame) -> int:
    frame = dataset.copy()
    frame["snapshot_time_utc"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    return int((frame["station"].astype(str) + "|" + frame["snapshot_time_utc"].astype(str)).nunique())


if __name__ == "__main__":
    main()
