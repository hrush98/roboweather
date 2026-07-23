#!/usr/bin/env python3
"""Evaluate Phase 3 Slice 2 lifecycle coverage and resource-growth gates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.lifecycle import evaluate_tape_lifecycle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--min-recorded-hours", type=float, default=12.0)
    parser.add_argument("--max-discovery-lag-seconds", type=float, default=300.0)
    parser.add_argument("--max-coverage-gap-seconds", type=float, default=5.0)
    parser.add_argument("--max-daily-raw-gib", type=float, default=25.0)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--max-receipt-lag-ms", type=float, default=10_000.0)
    parser.add_argument("--max-rss-mib", type=float, default=1024.0)
    parser.add_argument(
        "--include-markets",
        action="store_true",
        help="Include per-market lifecycle details in addition to the default summary.",
    )
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        report = evaluate_tape_lifecycle(
            catalog,
            min_recorded_hours=args.min_recorded_hours,
            max_discovery_lag_seconds=args.max_discovery_lag_seconds,
            max_coverage_gap_seconds=args.max_coverage_gap_seconds,
            max_daily_raw_bytes=int(args.max_daily_raw_gib * 1024**3),
            retention_days=args.retention_days,
            max_receipt_lag_ms=args.max_receipt_lag_ms,
            max_rss_bytes=int(args.max_rss_mib * 1024**2),
        )
    payload = asdict(report)
    if not args.include_markets:
        payload.pop("markets")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not report.passed and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
