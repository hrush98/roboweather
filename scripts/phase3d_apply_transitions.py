#!/usr/bin/env python3
"""Compatibility-only C5 transition CLI; operators use run_discovery.py."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.discovery.transitions import ResearchRoleTransitionEngine


DEFAULT_REGISTRY = Path.home() / ".local/state/roboweather/discovery/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--effective-at-timestamp")
    args = parser.parse_args()

    effective = (
        _utc(args.effective_at_timestamp)
        if args.effective_at_timestamp
        else datetime.now(timezone.utc)
    )
    with DiscoveryRegistry(args.registry) as registry:
        result = ResearchRoleTransitionEngine(registry).apply(
            effective_at_utc=effective.isoformat()
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
