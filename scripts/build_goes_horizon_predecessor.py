#!/usr/bin/env python3
"""Build an independently frozen predecessor for an earlier F5 GOES horizon."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import pandas as pd

from scripts.forecast_edge_report import _fixed_support_candidates, _score_bundle
from scripts.forecast_remaining_heating_report import BASELINE_MODEL_ID
from weather_trader.forecasting.evaluation import (
    evaluate_probability_matrix,
    select_horizon_snapshots,
)
from weather_trader.forecasting.goes_horizon import (
    chronological_fit_holdout_dates,
    horizon_contract,
    predecessor_evaluation_contract,
    predecessor_model_contract,
)
from weather_trader.forecasting.nbm_benchmark import fit_convex_weight, metric_differences
from weather_trader.forecasting.remaining_heating import RemainingHeatingModel

DEFAULT_DATASET = (
    ROOT / "data/raw/dataset_2022-01-01_2025-12-31_pm_active_us12_hrrr_enriched.csv"
)
DEFAULT_MODELS = ROOT / "data/models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-hour-local", type=int, default=12)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    horizon = horizon_contract(args.horizon_hour_local)
    out = args.out or ROOT / horizon.predecessor_artifact_directory
    result = build_predecessor(
        args.dataset,
        args.models_dir,
        out,
        horizon_hour_local=args.horizon_hour_local,
    )
    print(json.dumps({
        "status": result["status"],
        "horizon": result["contract"]["horizon_id"],
        "forecast_version": result["contract"]["forecast_version"],
        "weather_weight": result["weights"]["remaining_heating"],
        "holdout_dates": result["cohort"]["holdout_weather_dates"],
    }, indent=2))
    return 0


def build_predecessor(
    dataset_path: Path,
    models_dir: Path,
    out: Path,
    *,
    horizon_hour_local: int,
) -> dict[str, Any]:
    horizon = horizon_contract(horizon_hour_local)
    if horizon.hour_local == 14:
        raise ValueError("the accepted exact-14 predecessor already has its own builder")
    contract = replace(
        predecessor_evaluation_contract(horizon.hour_local),
        bootstrap_samples=5000,
    )
    training_contract = replace(
        contract,
        validation_start="2022-01-01",
        validation_end_exclusive=contract.validation_start,
    )
    dataset = pd.read_csv(dataset_path)
    training = select_horizon_snapshots(dataset, training_contract)
    validation = select_horizon_snapshots(dataset, contract)
    model = RemainingHeatingModel(
        predecessor_model_contract(horizon.hour_local)
    ).fit(training)
    remaining = model.predict_proba(validation)
    baseline_path = models_dir / f"{BASELINE_MODEL_ID}.joblib"
    baseline_bundle = joblib.load(baseline_path)
    baseline = _score_bundle(
        BASELINE_MODEL_ID,
        baseline_bundle,
        validation,
        _fixed_support_candidates(validation, contract),
        contract,
    )
    fit_dates, holdout_dates = chronological_fit_holdout_dates(
        validation["local_date"].astype(str)
    )
    fit_set = set(fit_dates)
    holdout_set = set(holdout_dates)
    fit_mask = validation["local_date"].astype(str).isin(fit_set).to_numpy()
    holdout_mask = validation["local_date"].astype(str).isin(holdout_set).to_numpy()
    targets = validation["target_value"].to_numpy(int)
    dates = validation["local_date"].astype(str).to_numpy()
    weather_weight = fit_convex_weight(
        remaining[fit_mask], baseline[fit_mask], targets[fit_mask], contract
    )
    weather = weather_weight * remaining + (1.0 - weather_weight) * baseline
    comparison = metric_differences(
        weather[holdout_mask],
        baseline[holdout_mask],
        targets[holdout_mask],
        dates[holdout_mask],
        contract,
    )
    artifact = {
        "model_type": "remaining_heating_weather_ensemble",
        "forecast_version": horizon.predecessor_version,
        "horizon_id": horizon.horizon_id,
        "collection_activation_date": horizon.collection_activation_date,
        "evaluation_contract": contract.to_dict(),
        "evaluation_fingerprint": contract.fingerprint,
        "remaining_heating_model": model,
        "baseline_model_id": BASELINE_MODEL_ID,
        "baseline_model": baseline_bundle,
        "remaining_heating_weight": weather_weight,
        "hrrr_baseline_weight": 1.0 - weather_weight,
        "baseline_projection": "condition_on_integer_high_so_far",
        "weight_fit_dates": list(fit_dates),
        "validation_holdout_dates": list(holdout_dates),
        "activation_date": horizon.collection_activation_date,
        "training_dataset_sha256": sha256(dataset_path),
        "baseline_artifact_sha256": sha256(baseline_path),
    }
    result = {
        "status": "FROZEN_AS_F5_CONDITIONAL_CONTROL",
        "verdict": "NOT_AN_INFORMATION_EDGE_CLAIM",
        "contract": {
            "horizon_id": horizon.horizon_id,
            "forecast_version": horizon.predecessor_version,
            "collection_activation_date": horizon.collection_activation_date,
            "evaluation": {**contract.to_dict(), "fingerprint": contract.fingerprint},
            "remaining_heating": asdict(model.contract),
        },
        "cohort": {
            "training_rows": len(training),
            "training_weather_dates": int(training["local_date"].nunique()),
            "validation_rows": len(validation),
            "validation_weather_dates": int(validation["local_date"].nunique()),
            "weight_fit_weather_dates": len(fit_dates),
            "holdout_weather_dates": len(holdout_dates),
            "holdout_first_date": holdout_dates[0],
            "holdout_last_date": holdout_dates[-1],
        },
        "weights": {
            "remaining_heating": weather_weight,
            "hrrr_baseline": 1.0 - weather_weight,
        },
        "fit_period_metrics": {
            "remaining_heating": evaluate_probability_matrix(
                remaining[fit_mask], targets[fit_mask], dates[fit_mask], contract
            ),
            "hrrr_baseline": evaluate_probability_matrix(
                baseline[fit_mask], targets[fit_mask], dates[fit_mask], contract
            ),
        },
        "holdout_weather_stack_minus_hrrr": comparison,
        "limitations": [
            "This is an independently frozen exact-12 conditional control for F5, not evidence that GOES or the predecessor beats the market.",
            "Its remaining-heating model is trained on 2022-2024 rows and its blend weight only on the first 60% of 2025 dates.",
            "The final 40% of 2025 is diagnostic predecessor holdout; F5 settlement-token evidence starts independently on 2026-08-14.",
            "No funded or production consumer is authorized.",
        ],
    }
    publish_immutable(out, artifact, result, render_markdown(result))
    return result


def publish_immutable(
    out: Path,
    artifact: dict[str, Any],
    result: dict[str, Any],
    report: str,
) -> None:
    """Atomically publish once; identical rebuilds are no-ops, changes fail."""
    out.mkdir(parents=True, exist_ok=True)
    targets = (
        out / "remaining_heating_weather_ensemble.joblib",
        out / "result.json",
        out / "report.md",
    )
    temporary = tuple(path.with_name(f".{path.name}.tmp") for path in targets)
    try:
        joblib.dump(artifact, temporary[0])
        temporary[1].write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        temporary[2].write_text(report)
        exists = tuple(path.exists() for path in targets)
        if any(exists):
            if not all(exists):
                raise ValueError(
                    "frozen horizon predecessor is partially published; repair explicitly"
                )
            changed = [
                target.name
                for target, candidate in zip(targets, temporary, strict=True)
                if target.read_bytes() != candidate.read_bytes()
            ]
            if changed:
                raise ValueError(
                    "frozen horizon predecessor changed; create a new version: "
                    + ", ".join(changed)
                )
            return
        for candidate, target in zip(temporary, targets, strict=True):
            candidate.replace(target)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def render_markdown(result: dict[str, Any]) -> str:
    comparison = result["holdout_weather_stack_minus_hrrr"]
    delta = comparison["candidate_minus_reference"]
    interval = comparison["weather_date_clustered_95pct_ci"]
    return "\n".join([
        "# F5 Exact-12 Predecessor",
        "",
        f"Status: **{result['status']}**",
        "",
        f"Verdict: **{result['verdict']}**",
        "",
        f"- Horizon: `{result['contract']['horizon_id']}`",
        f"- Forecast version: `{result['contract']['forecast_version']}`",
        f"- Training rows/dates: {result['cohort']['training_rows']} / {result['cohort']['training_weather_dates']}",
        f"- Validation rows/dates: {result['cohort']['validation_rows']} / {result['cohort']['validation_weather_dates']}",
        f"- Blend-fit dates: {result['cohort']['weight_fit_weather_dates']}",
        f"- Diagnostic holdout dates: {result['cohort']['holdout_weather_dates']}",
        f"- Remaining-heating weight: {result['weights']['remaining_heating']:.6f}",
        f"- Holdout log-loss delta vs HRRR: {delta['log_loss']:.6f} ({interval['log_loss'][0]:.6f}, {interval['log_loss'][1]:.6f})",
        f"- Holdout RPS delta vs HRRR: {delta['rps']:.6f} ({interval['rps'][0]:.6f}, {interval['rps'][1]:.6f})",
        "",
        "This artifact is only the independently frozen conditional predecessor for the exact-12 GOES arm. It is not an information-edge or trading verdict.",
        "",
    ])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
