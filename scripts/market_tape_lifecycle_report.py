#!/usr/bin/env python3
"""Evaluate Phase 3 Slice 2 lifecycle coverage and resource-growth gates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.lifecycle import evaluate_tape_lifecycle


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument(
        "--validation-start",
        type=_timestamp,
        help="Include sessions started at or after this timestamp (inclusive).",
    )
    parser.add_argument(
        "--validation-end",
        type=_timestamp,
        help="Include sessions started before this timestamp and ignore later evidence.",
    )
    parser.add_argument(
        "--session-id",
        action="append",
        default=None,
        help="Evaluate only this exact collector session; repeat to select a restarted run.",
    )
    parser.add_argument(
        "--validation-run-id",
        help="Evaluate every session persisted under this validation cohort.",
    )
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
            validation_start_at=args.validation_start,
            validation_end_at=args.validation_end,
            validation_session_ids=tuple(args.session_id) if args.session_id else None,
            validation_run_id=args.validation_run_id,
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
