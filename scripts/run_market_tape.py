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
            )
        )
    print(json.dumps({**stats.__dict__, "segment_path": str(stats.segment_path) if stats.segment_path else None}, indent=2))


if __name__ == "__main__":
    main()
