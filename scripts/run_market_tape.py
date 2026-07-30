#!/usr/bin/env python3
"""Run the policy-independent Phase 3 weather market-tape collector."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.collector import collect_market_tape


def _remaining_overall_seconds(
    state_path: Path,
    overall_max_seconds: float,
    *,
    now: datetime | None = None,
) -> float:
    """Return a restart-stable remaining runtime, creating its anchor once."""
    if overall_max_seconds <= 0:
        raise ValueError("overall_max_seconds must be positive")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        payload = json.loads(state_path.read_text())
    except FileNotFoundError:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = current + timedelta(seconds=overall_max_seconds)
        payload = {
            "started_at_utc": current.isoformat(),
            "deadline_at_utc": deadline.isoformat(),
        }
        try:
            with state_path.open("x") as handle:
                json.dump(payload, handle)
        except FileExistsError:
            payload = json.loads(state_path.read_text())
    deadline_text = payload.get("deadline_at_utc")
    if not isinstance(deadline_text, str):
        raise ValueError(f"invalid lifecycle deadline state: {state_path}")
    deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError(f"lifecycle deadline must be timezone-aware: {state_path}")
    return max(0.0, (deadline - current).total_seconds())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--refresh-seconds", type=float, default=300.0)
    parser.add_argument("--queue-size", type=int, default=10000)
    parser.add_argument("--market-limit", type=int, default=50000)
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument(
        "--overall-max-seconds",
        type=float,
        help="Overall wall-clock budget preserved across process restarts.",
    )
    parser.add_argument(
        "--deadline-state-file",
        type=Path,
        help="State file used with --overall-max-seconds to preserve the deadline.",
    )
    parser.add_argument("--rotation-seconds", type=int, default=3600)
    parser.add_argument("--telemetry-seconds", type=float, default=30.0)
    parser.add_argument("--max-reconnect-attempts", type=int, default=8)
    parser.add_argument("--reconnect-initial-seconds", type=float, default=1.0)
    parser.add_argument("--reconnect-max-seconds", type=float, default=30.0)
    parser.add_argument("--max-receipt-lag-seconds", type=float, default=10.0)
    parser.add_argument("--checkpoint-every", type=int, default=1000)
    args = parser.parse_args()
    if (args.overall_max_seconds is None) != (args.deadline_state_file is None):
        parser.error("--overall-max-seconds and --deadline-state-file must be used together")
    max_seconds = args.max_seconds
    if args.overall_max_seconds is not None:
        remaining = _remaining_overall_seconds(
            args.deadline_state_file.expanduser(),
            args.overall_max_seconds,
        )
        if remaining <= 0:
            print(json.dumps({"finish_reason": "overall_deadline_elapsed"}, indent=2))
            return
        max_seconds = remaining if max_seconds is None else min(max_seconds, remaining)
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        stats = asyncio.run(
            collect_market_tape(
                catalog,
                raw_directory=args.raw_dir.expanduser(),
                refresh_seconds=args.refresh_seconds,
                queue_size=args.queue_size,
                market_limit=args.market_limit,
                max_messages=args.max_messages,
                max_seconds=max_seconds,
                rotation_seconds=args.rotation_seconds,
                telemetry_seconds=args.telemetry_seconds,
                max_reconnect_attempts=args.max_reconnect_attempts,
                reconnect_initial_seconds=args.reconnect_initial_seconds,
                reconnect_max_seconds=args.reconnect_max_seconds,
                max_receipt_lag_seconds=args.max_receipt_lag_seconds,
                checkpoint_every=args.checkpoint_every,
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
