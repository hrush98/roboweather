#!/usr/bin/env python3
"""Collect forward-causal GOES ABI DSR artifacts for active US-high markets."""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.forecast_source_catalog import DEFAULT_CATALOG, DEFAULT_RAW, load_targets, parse_timestamp
from weather_trader.forecasting.goes_heating import (
    GOES_DSR_SOURCE_ID,
    discover_dsr_requests,
    satellite_for_longitude,
)
from weather_trader.forecasting.source_catalog import BoundedCollector, ForecastSourceCatalog
from weather_trader.stations.metadata import get_station

UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--as-of", type=parse_timestamp)
    parser.add_argument("--window-start-local", type=time.fromisoformat, default=time(10, 0))
    parser.add_argument("--decision-time-local", type=time.fromisoformat, default=time(14, 0))
    parser.add_argument("--lookback-hours", type=float, default=1.5)
    parser.add_argument("--max-artifacts", type=int, default=24)
    parser.add_argument("--max-bytes", type=int, default=384 * 1024 * 1024)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = (args.as_of or datetime.now(UTC)).astimezone(UTC)
    targets = load_targets(args.research_db, as_of_utc=as_of, target_days=1)
    active = active_targets(
        targets,
        as_of_utc=as_of,
        window_start_local=args.window_start_local,
        decision_time_local=args.decision_time_local,
    )
    satellites = {satellite_for_longitude(target.longitude) for target in active}
    earliest = as_of - timedelta(hours=args.lookback_hours)
    known: set[str] = set()
    if args.catalog.exists():
        with ForecastSourceCatalog(args.catalog, args.raw_dir, read_only=True) as catalog:
            known = {
                str(row["source_key"])
                for row in catalog.connection.execute(
                    "select source_key from source_artifacts where source_id=?",
                    (GOES_DSR_SOURCE_ID,),
                )
            }
    requests = discover_dsr_requests(
        satellites,
        as_of_utc=as_of,
        earliest_scan_at_utc=earliest,
        existing_source_keys=known,
    ) if satellites else []
    plan = {
        "as_of_utc": as_of.isoformat(),
        "active_targets": sorted(f"{item.station}:{item.market_date}" for item in active),
        "satellites": sorted(satellites),
        "known_artifacts": len(known),
        "new_requests": len(requests),
        "planned_bytes": sum(int(item.metadata["listed_byte_count"]) for item in requests),
        "sample_keys": [item.source_key for item in requests[:5]],
    }
    if args.plan_only or not requests:
        print(json.dumps({"plan": plan, "collection": None}, indent=2))
        return 0
    with ForecastSourceCatalog(args.catalog, args.raw_dir) as catalog:
        summary = BoundedCollector(catalog, timeout_seconds=args.timeout).collect(
            requests,
            max_artifacts=args.max_artifacts,
            max_bytes=args.max_bytes,
        )
    print(json.dumps({"plan": plan, "collection": summary}, indent=2))
    return 0 if summary["status"] != "FAILED" else 2


def active_targets(
    targets: list,
    *,
    as_of_utc: datetime,
    window_start_local: time,
    decision_time_local: time,
) -> list:
    output = []
    for target in targets:
        station = get_station(target.station)
        zone = ZoneInfo(station.timezone)
        start = datetime.combine(target.market_date, window_start_local, tzinfo=zone)
        decision = datetime.combine(target.market_date, decision_time_local, tzinfo=zone)
        if start.astimezone(UTC) <= as_of_utc <= decision.astimezone(UTC):
            output.append(target)
    return output


if __name__ == "__main__":
    raise SystemExit(main())
