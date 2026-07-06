#!/usr/bin/env python3
"""Collect Polymarket CLOB market events for current shadow candidate token IDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.config import DEFAULT_LIVE_DB
from weather_trader.execution.clob_collector import collect_candidate_clob_events_sync
from weather_trader.execution.store import ExecutionStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_LIVE_DB, help="Live SQLite ledger path.")
    parser.add_argument("--since-timestamp", help="Only subscribe to candidate tokens seen at/after this ISO timestamp.")
    parser.add_argument("--max-messages", type=int, help="Stop after this many WebSocket messages.")
    parser.add_argument("--max-seconds", type=float, help="Stop after this many seconds.")
    args = parser.parse_args()

    store = ExecutionStore(args.db)
    try:
        stats = collect_candidate_clob_events_sync(
            store,
            since_timestamp=args.since_timestamp,
            max_messages=args.max_messages,
            max_seconds=args.max_seconds,
        )
    finally:
        store.close()
    print(json.dumps(stats.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
