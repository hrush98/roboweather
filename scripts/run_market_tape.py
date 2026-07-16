#!/usr/bin/env python3
"""Run the policy-independent Phase 3 weather market-tape collector."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.collector import collect_market_tape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--refresh-seconds", type=float, default=300.0)
    parser.add_argument("--queue-size", type=int, default=10000)
    parser.add_argument("--market-limit", type=int, default=50000)
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--rotation-seconds", type=int, default=3600)
    parser.add_argument("--telemetry-seconds", type=float, default=30.0)
    parser.add_argument("--max-reconnect-attempts", type=int, default=8)
    parser.add_argument("--reconnect-initial-seconds", type=float, default=1.0)
    parser.add_argument("--reconnect-max-seconds", type=float, default=30.0)
    args = parser.parse_args()
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        stats = asyncio.run(
            collect_market_tape(
                catalog,
                raw_directory=args.raw_dir.expanduser(),
                refresh_seconds=args.refresh_seconds,
                queue_size=args.queue_size,
                market_limit=args.market_limit,
                max_messages=args.max_messages,
                max_seconds=args.max_seconds,
                rotation_seconds=args.rotation_seconds,
                telemetry_seconds=args.telemetry_seconds,
                max_reconnect_attempts=args.max_reconnect_attempts,
                reconnect_initial_seconds=args.reconnect_initial_seconds,
                reconnect_max_seconds=args.reconnect_max_seconds,
            )
        )
    payload = {
        **stats.__dict__,
        "segment_path": str(stats.segment_path) if stats.segment_path else None,
        "segment_paths": [str(path) for path in stats.segment_paths],
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
