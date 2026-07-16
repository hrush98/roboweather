#!/usr/bin/env python3
"""Check the latest Phase 3 recorder session, telemetry, coverage, and segments."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.health import evaluate_tape_health


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--stale-after-seconds", type=float, default=120.0)
    parser.add_argument("--max-receipt-lag-ms", type=float, default=10_000.0)
    parser.add_argument("--max-rss-mib", type=float, default=1024.0)
    parser.add_argument("--no-verify-segments", action="store_true")
    parser.add_argument("--no-fail", action="store_true")
    args = parser.parse_args()
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        report = evaluate_tape_health(
            catalog,
            stale_after_seconds=args.stale_after_seconds,
            max_receipt_lag_ms=args.max_receipt_lag_ms,
            max_rss_bytes=int(args.max_rss_mib * 1024 * 1024),
            verify_segments=not args.no_verify_segments,
        )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    if not report.healthy and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
