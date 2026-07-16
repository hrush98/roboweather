#!/usr/bin/env python3
"""Reconstruct one token's L2 book at a causal tape boundary."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.books import reconstruct_at


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-id", required=True)
    boundary = parser.add_mutually_exclusive_group(required=True)
    boundary.add_argument("--at", help="Inclusive UTC receipt timestamp")
    boundary.add_argument("--event-id", help="Inclusive stable tape event ID")
    parser.add_argument("segments", type=Path, nargs="+")
    args = parser.parse_args()
    book = reconstruct_at(
        [path.expanduser() for path in args.segments],
        token_id=args.token_id,
        received_at_or_before=args.at,
        event_id=args.event_id,
    )
    print(json.dumps(asdict(book), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
