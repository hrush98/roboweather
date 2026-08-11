#!/usr/bin/env python3
"""Developer surface for Phase 3D decision-cache benchmarks and backfills.

This is a D1-D2 verification tool, not the final discovery operator command.
After D5, operators use scripts/run_discovery.py exclusively.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.decision_cache import (
    DecisionCacheContract,
    ExecutableDecisionCache,
    benchmark_decision_grain,
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
        "--cache",
        type=Path,
        default=DEFAULT_STATE / "discovery/decision_cache.sqlite",
    )
    parser.add_argument("--source-start-date", default="2026-07-23")
    parser.add_argument("--sealed-research-watermark", type=int)
    parser.add_argument("--availability-bucket-seconds", type=int, default=60)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--pre-signal-seconds", type=int, default=60)
    parser.add_argument("--mapping-batch-size", type=int, default=2_000)
    parser.add_argument("--replay-batch-size", type=int, default=100)
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()

    contract = DecisionCacheContract(
        availability_bucket_seconds=args.availability_bucket_seconds,
        latency_ms=args.latency_ms,
        pre_signal_seconds=args.pre_signal_seconds,
    )
    with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
        if args.benchmark_only:
            result = benchmark_decision_grain(
                research,
                tape,
                contract=contract,
                source_start_date=args.source_start_date,
                sealed_research_watermark=args.sealed_research_watermark,
                batch_size=args.mapping_batch_size,
            )
        else:
            with ExecutableDecisionCache(args.cache) as cache:
                result = cache.refresh(
                    research,
                    tape,
                    contract=contract,
                    source_start_date=args.source_start_date,
                    sealed_research_watermark=args.sealed_research_watermark,
                    mapping_batch_size=args.mapping_batch_size,
                    replay_batch_size=args.replay_batch_size,
                    progress=_progress,
                )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.expanduser()}?mode=ro", uri=True)
    connection.execute("pragma query_only=ON")
    connection.execute("pragma busy_timeout=30000")
    return connection


def _progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
