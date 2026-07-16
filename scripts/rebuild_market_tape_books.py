#!/usr/bin/env python3
"""Deterministically rebuild and persist L2 checkpoints from raw tape segments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.books import BookReconstructor
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.storage import iter_segment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("segments", type=Path, nargs="+")
    parser.add_argument("--checkpoint-every", type=int, default=1000)
    args = parser.parse_args()
    if args.checkpoint_every < 1:
        parser.error("--checkpoint-every must be positive")
    reconstructor = BookReconstructor()
    counts: dict[str, int] = {}
    events = checkpoints = 0
    with TapeCatalog(args.catalog.expanduser()) as catalog:
        for path in sorted(args.segments):
            for event in iter_segment(path.expanduser()):
                book = reconstructor.apply(event)
                events += 1
                counts[event.token_id] = counts.get(event.token_id, 0) + 1
                if book.valid and (
                    event.event_type == "book" or counts[event.token_id] % args.checkpoint_every == 0
                ):
                    catalog.record_checkpoint(reconstructor.checkpoint(event))
                    checkpoints += 1
    print(json.dumps({"events": events, "checkpoints": checkpoints, "tokens": len(counts)}, indent=2))


if __name__ == "__main__":
    main()
