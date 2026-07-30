#!/usr/bin/env python3
"""Build Price Sheet V2a walk-forward calibration baselines and diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.pricing.calibration import (
    WalkForwardCalibrationConfig,
    load_v2a_dataset_artifact,
    walk_forward_calibration,
    write_calibration_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        required=True,
        help="One signal-spec directory emitted by build_price_sheet_v2_dataset.py.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Generated calibration artifact directory (do not commit).")
    parser.add_argument("--min-training-market-dates", type=int, default=5)
    parser.add_argument("--regularization-c", type=float, default=1.0)
    args = parser.parse_args()

    dataset = load_v2a_dataset_artifact(args.dataset_dir)
    config = WalkForwardCalibrationConfig(
        min_training_market_dates=args.min_training_market_dates,
        regularization_c=args.regularization_c,
    )
    artifact = walk_forward_calibration(dataset, config=config)
    write_calibration_artifact(artifact, args.out)
    print(json.dumps({"output_dir": str(args.out), **artifact.manifest()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
