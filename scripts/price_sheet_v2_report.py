#!/usr/bin/env python3
"""Build Price Sheet V2a calibration, conservative-price, and economic diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.pricing.calibration import (
    CalibrationBaseline,
    WalkForwardCalibrationConfig,
    load_v2a_dataset_artifact,
    walk_forward_calibration,
    write_calibration_artifact,
)
from weather_trader.pricing.price_sheet_v2 import (
    V2APricingConfig,
    build_v2a_price_sheets,
    write_v2a_price_sheet_artifact,
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
    parser.add_argument(
        "--selected-calibration-baseline",
        choices=[CalibrationBaseline.POOLED_PLATT.value, CalibrationBaseline.MARKET_AWARE.value],
        help=(
            "Explicitly freeze one V2a baseline for this report. When omitted, both fitted "
            "baselines are research comparisons and the combined gate fails closed."
        ),
    )
    parser.add_argument("--uncertainty-quantile", type=float, default=0.80)
    parser.add_argument("--min-prior-oof-market-dates", type=int, default=5)
    parser.add_argument("--minimum-uncertainty-reserve", type=float, default=0.02)
    parser.add_argument("--minimum-profit-reserve", type=float, default=0.05)
    parser.add_argument("--known-cost-reserve", type=float, default=0.01)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--extreme-raw-fair-threshold", type=float, default=0.95)
    parser.add_argument("--minimum-gate-market-dates", type=int, default=5)
    parser.add_argument("--current-window-days", type=int, default=7)
    parser.add_argument(
        "--untouched-forward-start-date",
        help="Optional predeclared market_date lower bound for a genuinely untouched forward window.",
    )
    args = parser.parse_args()

    dataset = load_v2a_dataset_artifact(args.dataset_dir)
    config = WalkForwardCalibrationConfig(
        min_training_market_dates=args.min_training_market_dates,
        regularization_c=args.regularization_c,
    )
    artifact = walk_forward_calibration(dataset, config=config)
    write_calibration_artifact(artifact, args.out)

    selected = (
        CalibrationBaseline(args.selected_calibration_baseline)
        if args.selected_calibration_baseline
        else None
    )
    candidates = {}
    for baseline in (CalibrationBaseline.POOLED_PLATT, CalibrationBaseline.MARKET_AWARE):
        pricing_config = V2APricingConfig(
            calibration_baseline=baseline,
            promotion_baseline_frozen=baseline == selected,
            uncertainty_quantile=args.uncertainty_quantile,
            minimum_prior_oof_market_dates=args.min_prior_oof_market_dates,
            minimum_uncertainty_reserve=args.minimum_uncertainty_reserve,
            minimum_profit_reserve=args.minimum_profit_reserve,
            known_cost_reserve=args.known_cost_reserve,
            tick_size=args.tick_size,
            extreme_raw_fair_threshold=args.extreme_raw_fair_threshold,
            minimum_gate_market_dates=args.minimum_gate_market_dates,
            current_window_days=args.current_window_days,
            untouched_forward_start_date=args.untouched_forward_start_date,
        )
        price_sheets = build_v2a_price_sheets(dataset, artifact, config=pricing_config)
        write_v2a_price_sheet_artifact(price_sheets, args.out / f"pricing_{baseline.value}")
        candidates[baseline.value] = price_sheets.manifest()

    selected_report = candidates.get(selected.value) if selected is not None else None
    combined_gate_reasons = []
    if selected is None:
        combined_gate_reasons.append("NO_CALIBRATOR_SELECTED")
    elif not selected_report["report"]["promotion_gate"]["passed"]:
        combined_gate_reasons.extend(selected_report["report"]["promotion_gate"]["reasons"])
    combined = {
        "output_dir": str(args.out),
        "calibration": artifact.manifest(),
        "selected_calibration_baseline": selected.value if selected is not None else None,
        "candidate_price_sheets": candidates,
        "promotion_gate": {
            "passed": not combined_gate_reasons,
            "disposition": "SHADOW_CANDIDATE" if not combined_gate_reasons else "RESEARCH_ONLY",
            "reasons": combined_gate_reasons,
        },
    }
    (args.out / "price_sheet_report.json").write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(_console_summary(combined), indent=2, sort_keys=True))
    return 0


def _console_summary(report: dict) -> dict:
    candidates = {}
    for baseline, manifest in report["candidate_price_sheets"].items():
        pricing_report = manifest["report"]
        broad = pricing_report["windows"]["broad_evaluation"]
        candidates[baseline] = {
            "rows": broad["rows"],
            "market_dates": broad["market_dates"],
            "eligible_rows": broad["eligible_rows"],
            "skip_reasons": broad["skip_reasons"],
            "theoretical_v2a_maximum_quote": broad["theoretical_economics"]["v2a_maximum_quote"],
            "promotion_gate": pricing_report["promotion_gate"],
            "artifact_dir": f"pricing_{baseline}",
        }
    return {
        "output_dir": report["output_dir"],
        "selected_calibration_baseline": report["selected_calibration_baseline"],
        "promotion_gate": report["promotion_gate"],
        "calibration_rows": report["calibration"]["prediction_rows"],
        "calibration_market_dates": report["calibration"]["evaluation_market_dates"],
        "candidates": candidates,
        "full_report": "price_sheet_report.json",
    }


if __name__ == "__main__":
    raise SystemExit(main())
