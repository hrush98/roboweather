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
from weather_trader.tape.joins import join_decision_to_tape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--decision-json", type=Path, required=True)
    parser.add_argument("--pre-signal-seconds", type=float, default=60.0)
    parser.add_argument("segments", type=Path, nargs="+")
    args = parser.parse_args()
    decision = DecisionTiming(**json.loads(args.decision_json.read_text(encoding="utf-8")))
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        joined = join_decision_to_tape(
            catalog,
            [path.expanduser() for path in args.segments],
            decision,
            pre_signal_seconds=args.pre_signal_seconds,
        )
    print(json.dumps(asdict(joined), indent=2, sort_keys=True))
    if not joined.coverage_valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
