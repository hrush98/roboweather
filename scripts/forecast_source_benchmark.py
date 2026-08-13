#!/usr/bin/env python3
"""Run the F2 WeatherNext/NBM identical-coverage forecast benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_trader.config import DEFAULT_STATE_DIR
from weather_trader.forecasting.nbm_benchmark import run_benchmark


DEFAULT_DB = DEFAULT_STATE_DIR / "research_2026-05-08_multimodel.sqlite"
DEFAULT_CACHE = DEFAULT_STATE_DIR / "forecast_sources" / "nbm_v5_archive"
DEFAULT_OUT = ROOT / "reports" / "forecast-edge" / "f2-current"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = run_benchmark(
        args.db,
        args.cache_dir,
        args.out,
        bootstrap_samples=args.bootstrap_samples,
        max_workers=args.max_workers,
        refresh=args.refresh,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "verdict": result["verdict"],
                "coverage": result["coverage"],
                "acceptance_checks": result["acceptance_checks"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
