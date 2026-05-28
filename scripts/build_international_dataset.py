from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.config import RAW_DIR, ensure_directories
from weather_trader.features.international_dataset_builder import build_international_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Celsius same-day threshold datasets for target international weather cities.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--metric", choices=("high", "low"), default="high")
    parser.add_argument("--stations", help="Comma-separated ICAO observation station IDs. Defaults to the 10 target cities.")
    parser.add_argument("--output", help="Output CSV path.")
    args = parser.parse_args()

    ensure_directories()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    station_ids = [value.strip().upper() for value in args.stations.split(",") if value.strip()] if args.stations else None
    dataset = build_international_dataset(start=start, end=end, metric=args.metric, station_ids=station_ids)
    station_label = "target10" if station_ids is None else "_".join(station_ids).lower()
    output = Path(args.output) if args.output else RAW_DIR / f"dataset_international_celsius_{args.metric}_{args.start}_{args.end}_{station_label}.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output, index=False)
    print(f"wrote {len(dataset)} rows to {output}")


if __name__ == "__main__":
    main()
