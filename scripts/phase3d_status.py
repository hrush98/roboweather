#!/usr/bin/env python3
"""Report Phase 3D scheduler, watermark, role, scorecard, and failure status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.status import discovery_status_report


DEFAULT_REGISTRY = Path.home() / ".local/state/roboweather/discovery/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--stale-after-seconds", type=int, default=36 * 60 * 60)
    args = parser.parse_args()
    report = discovery_status_report(
        args.registry, stale_after_seconds=args.stale_after_seconds
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"HEALTHY", "NOT_INITIALIZED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
