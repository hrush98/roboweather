#!/usr/bin/env python3
"""Run the bounded Phase 3D C4-C6 dry-run scheduler."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.scheduler import (
    BoundedDiscoveryScheduler,
    SchedulerConfig,
    SchedulerProcessLock,
)


DEFAULT_STATE = Path.home() / ".local/state/roboweather"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--research-db",
        type=Path,
        default=DEFAULT_STATE / "research_2026-05-08_multimodel.sqlite",
    )
    parser.add_argument(
        "--tape-catalog",
        type=Path,
        default=DEFAULT_STATE / "market_tape/catalog.sqlite",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_STATE / "discovery/catalog.sqlite",
    )
    parser.add_argument(
        "--source-start-date",
        default=os.environ.get("ROBOWEATHER_DISCOVERY_SOURCE_START_DATE", "2026-07-23"),
    )
    parser.add_argument("--interval-seconds", type=int, default=6 * 60 * 60)
    parser.add_argument("--discovery-cadence-seconds", type=int, default=7 * 24 * 60 * 60)
    parser.add_argument("--task-timeout-seconds", type=int, default=15 * 60)
    parser.add_argument("--maximum-cycle-runtime-seconds", type=int, default=30 * 60)
    parser.add_argument("--maximum-registry-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    config = SchedulerConfig(
        research_db=args.research_db,
        tape_catalog=args.tape_catalog,
        registry=args.registry,
        source_start_date=args.source_start_date,
        python_executable=Path(sys.executable),
        interval_seconds=args.interval_seconds,
        discovery_cadence_seconds=args.discovery_cadence_seconds,
        task_timeout_seconds=args.task_timeout_seconds,
        maximum_cycle_runtime_seconds=args.maximum_cycle_runtime_seconds,
        maximum_registry_bytes=args.maximum_registry_bytes,
    )
    scheduler = BoundedDiscoveryScheduler(config)
    with SchedulerProcessLock(args.registry):
        while True:
            result = scheduler.run_cycle(now_utc=datetime.now(timezone.utc))
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
