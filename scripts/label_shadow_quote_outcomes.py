#!/usr/bin/env python3
"""Persist conservative/base/optimistic shadow quote outcome labels."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.config import DEFAULT_LIVE_DB
from weather_trader.execution.contracts import utc_now_iso
from weather_trader.execution.shadow_outcomes import label_shadow_quote_outcome
from weather_trader.execution.store import ExecutionStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_LIVE_DB, help="Live SQLite ledger path.")
    parser.add_argument("--since-timestamp", help="Only label quote intents created at/after this ISO timestamp.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = ExecutionStore(args.db)
    labeled_at = utc_now_iso()
    labels = []
    try:
        for item in store.shadow_quote_label_inputs(since_timestamp=args.since_timestamp, limit=args.limit):
            label = label_shadow_quote_outcome(
                item["quote"],
                feed_events=item["feed_events"],
                book_snapshots=item["book_snapshots"],
                labeled_at=labeled_at,
            )
            labels.append(label)
            if not args.dry_run:
                store.upsert_live_shadow_quote_outcome(label)
    finally:
        store.close()
    print(
        json.dumps(
            {
                "db": str(args.db),
                "dry_run": args.dry_run,
                "labels": len(labels),
                "with_feed_events": sum(1 for label in labels if label["feed_event_count"] > 0),
                "with_book_snapshots": sum(1 for label in labels if label["book_snapshot_count"] > 0),
                "conservative_fills": sum(1 for label in labels if label["conservative_fill"]),
                "base_fills": sum(1 for label in labels if label["base_fill"]),
                "optimistic_fills": sum(1 for label in labels if label["optimistic_fill"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
