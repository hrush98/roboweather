#!/usr/bin/env python3
"""Build a bucket YES probability calibration artifact.

The artifact is intentionally probability-only: it stores Platt fits for
P(bucket YES wins) by model/station with model-global fallbacks. It does not
encode execution depth, portfolio caps, or trading policy decisions.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.bucket_probability_calibration import (  # noqa: E402
    CATBOOST_MODEL,
    DEFAULT_DB,
    DYNAMIC_TUNED_MODEL,
    BucketExample,
    binary_summary,
    fit_platt,
    load_examples,
    lookup_platt,
)

DEFAULT_OUT = Path.home() / ".local/state/roboweather/bucket_calibration_pm_us12_high_temp.json"
ARTIFACT_VERSION = 1


def build_artifact(
    *,
    db_path: Path,
    feature: str,
    min_samples: int,
    generated_at: str | None = None,
) -> dict[str, Any]:
    examples, snapshots = load_examples(db_path)
    fits = fit_platt(examples, feature=feature, min_samples=min_samples)
    scored_rows = score_examples(examples, fits)
    model_counts = Counter(example.model_name for example in examples)
    station_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for example in examples:
        station_counts[example.model_name][example.station] += 1

    return {
        "version": ARTIFACT_VERSION,
        "kind": "bucket_yes_platt_calibration",
        "model_family": "pm_us12_high_temp_obs_bucket",
        "market_family": "HIGH_TEMP",
        "target": "P(bucket YES wins)",
        "feature": feature,
        "min_samples": min_samples,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "source": {
            "db": str(db_path),
            "models": [DYNAMIC_TUNED_MODEL, CATBOOST_MODEL],
            "strategy_bucket": "HIGH_CONVICTION",
            "resolved_market_date_min": min((example.market_date for example in examples), default=None),
            "resolved_market_date_max": max((example.market_date for example in examples), default=None),
            "bucket_examples": len(examples),
            "multiclass_snapshots": len(snapshots),
            "model_example_counts": dict(sorted(model_counts.items())),
            "station_example_counts": {
                model_name: dict(sorted(counts.items()))
                for model_name, counts in sorted(station_counts.items())
            },
        },
        "fallback_order": ["model_station", "model_global"],
        "fits": render_fits(fits),
        "training_diagnostics": {
            "note": "In-sample diagnostics only; use bucket_probability_calibration.py for walk-forward validation.",
            "binary": [
                binary_summary("raw", scored_rows, "raw"),
                binary_summary("platt", [row for row in scored_rows if row.get("platt") is not None], "platt"),
            ],
        },
    }


def score_examples(examples: list[BucketExample], fits: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    rows = []
    for example in examples:
        fit = lookup_platt(fits, example)
        rows.append(
            {
                "model_name": example.model_name,
                "station": example.station,
                "market_date": example.market_date,
                "target": example.target,
                "raw": example.raw_probability,
                "platt": fit.predict(example.raw_probability) if fit is not None else None,
            }
        )
    return rows


def render_fits(fits: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    rows = []
    for (model_name, station), fit in sorted(fits.items()):
        rows.append(
            {
                "model_name": model_name,
                "station": station,
                "scope": "model_global" if station == "*" else "model_station",
                "intercept": fit.intercept,
                "coef": fit.coef,
                "n": fit.n,
            }
        )
    return rows


def write_artifact(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_artifact(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    required = {"version", "kind", "feature", "fits", "source"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing required artifact keys: {', '.join(missing)}")
    fits = payload.get("fits")
    if not isinstance(fits, list) or not fits:
        raise ValueError("artifact contains no fits")
    global_fits = [fit for fit in fits if fit.get("scope") == "model_global"]
    if len(global_fits) < 2:
        raise ValueError("artifact should contain model-global fallbacks for both live models")
    return {
        "fits": len(fits),
        "global_fits": len(global_fits),
        "models": sorted({str(fit.get("model_name")) for fit in fits}),
        "stations": sorted({str(fit.get("station")) for fit in fits if fit.get("station") != "*"}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build bucket YES Platt calibration artifact.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--feature", choices=("logit", "fair"), default="logit")
    parser.add_argument("--min-samples", type=int, default=500)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    payload = build_artifact(
        db_path=args.db,
        feature=args.feature,
        min_samples=args.min_samples,
        generated_at=args.generated_at,
    )
    write_artifact(payload, args.out)
    validation = validate_artifact(args.out)
    print(f"wrote {args.out}")
    print(
        "fits={fits} global_fits={global_fits} models={models} stations={stations}".format(
            fits=validation["fits"],
            global_fits=validation["global_fits"],
            models=",".join(validation["models"]),
            stations=",".join(validation["stations"]),
        )
    )


if __name__ == "__main__":
    main()
