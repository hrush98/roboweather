#!/usr/bin/env python3
"""Causally join one immutable decision-timing JSON record to market tape."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import DecisionTiming
from weather_trader.tape.decision_sources import decision_timing_from_execution_quote
from weather_trader.tape.joins import join_decision_to_tape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--decision-json", type=Path)
    source.add_argument(
        "--execution-db",
        type=Path,
        help="Read a persisted price-sheet quote from an execution ledger.",
    )
    parser.add_argument("--quote-id", help="Execution-ledger quote ID; defaults to latest postable.")
    parser.add_argument(
        "--activation-timestamp",
        help="Frozen hypothesis activation when it is not embedded in the price sheet.",
    )
    parser.add_argument("--hypothesis-version", help="Override the derived price-sheet/quote-arm version.")
    parser.add_argument("--latency-ms", type=int, default=0, help="Execution-ledger latency arm.")
    parser.add_argument("--pre-signal-seconds", type=float, default=60.0)
    parser.add_argument("segments", type=Path, nargs="+")
    args = parser.parse_args()
    if args.decision_json is not None:
        if args.quote_id is not None:
            parser.error("--quote-id requires --execution-db")
        decision = DecisionTiming(
            **json.loads(args.decision_json.read_text(encoding="utf-8"))
        )
    else:
        decision = decision_timing_from_execution_quote(
            args.execution_db,
            quote_id=args.quote_id,
            activation_timestamp=args.activation_timestamp,
            hypothesis_version=args.hypothesis_version,
            latency_ms=args.latency_ms,
        )
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        joined = join_decision_to_tape(
            catalog,
            [path.expanduser() for path in args.segments],
            decision,
            pre_signal_seconds=args.pre_signal_seconds,
        )
    payload = asdict(joined)
    payload["decision_timing"] = asdict(decision)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not joined.coverage_valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
