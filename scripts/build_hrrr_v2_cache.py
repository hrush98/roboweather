from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from weather_trader.config import CACHE_DIR, PROCESSED_DIR, ensure_directories
from weather_trader.forecasts.hrrr_v2 import HRRRV2Store, build_hrrr_v2_cache, materialize_hrrr_v2_features
from weather_trader.stations.metadata import get_station, list_stations


DEFAULT_CACHE = CACHE_DIR / "hrrr_v2.sqlite"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/export batched HRRR v2 point cache.")
    parser.add_argument("--dataset", required=True, help="Input same-day observation training CSV.")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="SQLite v2 point cache path.")
    parser.add_argument("--output", default=str(PROCESSED_DIR / "dataset_hrrr_v2_enriched.csv"), help="Enriched output CSV.")
    parser.add_argument("--mode", choices=["build-cache", "export", "build-and-export", "status"], default="build-and-export")
    parser.add_argument("--stations", default="all", help="'all', 'dataset', or comma-separated station IDs to extract per HRRR file.")
    parser.add_argument("--max-snapshots", type=int, default=None)
    parser.add_argument("--max-snapshots-per-year", type=int, default=None)
    parser.add_argument("--sample-strategy", choices=["head", "even"], default="even")
    parser.add_argument("--forecast-stride-hours", type=int, default=3)
    parser.add_argument("--max-forecast-hour", type=int, default=18)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--workers", type=int, default=4, help="Parallel HRRR file extraction workers.")
    args = parser.parse_args()

    ensure_directories()
    dataset = pd.read_csv(args.dataset)
    cache_path = Path(args.cache)

    if args.mode == "status":
        store = HRRRV2Store(cache_path)
        try:
            print(json.dumps(store.status(), indent=2))
        finally:
            store.close()
        return

    stations = _selected_stations(args.stations, dataset)
    if args.mode in {"build-cache", "build-and-export"}:
        summary = build_hrrr_v2_cache(
            dataset=dataset,
            cache_path=cache_path,
            stations=stations,
            max_snapshots=args.max_snapshots,
            max_snapshots_per_year=args.max_snapshots_per_year,
            sample_strategy=args.sample_strategy,
            forecast_stride_hours=args.forecast_stride_hours,
            max_forecast_hour=args.max_forecast_hour,
            workers=max(1, args.workers),
            progress_every=args.progress_every,
        )
        print(json.dumps(summary, indent=2), file=sys.stderr)
        if args.mode == "build-cache":
            return

    enriched = materialize_hrrr_v2_features(
        dataset=dataset,
        cache_path=cache_path,
        max_snapshots=args.max_snapshots,
        max_snapshots_per_year=args.max_snapshots_per_year,
        sample_strategy=args.sample_strategy,
        forecast_stride_hours=args.forecast_stride_hours,
        max_forecast_hour=args.max_forecast_hour,
        progress_every=args.progress_every,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(output_path, index=False)
    summary = {
        "mode": args.mode,
        "cache": str(cache_path),
        "output": str(output_path),
        "input_rows": int(len(dataset)),
        "output_rows": int(len(enriched)),
        "hrrr_rows": int(enriched["hrrr_remaining_max"].notna().sum()) if "hrrr_remaining_max" in enriched else 0,
        "stations_extracted_per_file": [station.station for station in stations],
    }
    print(json.dumps(summary, indent=2), file=sys.stderr)


def _selected_stations(value: str, dataset: pd.DataFrame):
    if value == "all":
        return list_stations(initial_only=False)
    if value == "dataset":
        return [get_station(station) for station in sorted(dataset["station"].astype(str).str.upper().unique())]
    return [get_station(station.strip()) for station in value.split(",") if station.strip()]


if __name__ == "__main__":
    main()

