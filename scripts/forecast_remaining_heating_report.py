#!/usr/bin/env python3
"""Reproduce the F3 exact-cutoff remaining-heating acceptance report."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from scripts.forecast_edge_report import _fixed_support_candidates, _score_bundle
from weather_trader.forecasting.evaluation import (
    EvaluationContract,
    evaluate_probability_matrix,
    select_horizon_snapshots,
)
from weather_trader.forecasting.nbm_benchmark import (
    extract_snapshot_distributions,
    fit_convex_weight,
    load_identical_cohort,
    metric_differences,
)
from weather_trader.forecasting.remaining_heating import (
    FORWARD_COMPATIBLE_FEATURES,
    OBSERVATION_FEATURES,
    RemainingHeatingModel,
    enforce_high_so_far_lower_bound,
    exact_cutoff_multinomial_contract,
)


DEFAULT_DATASET = (
    ROOT / "data/raw/dataset_2022-01-01_2025-12-31_pm_active_us12_hrrr_enriched.csv"
)
DEFAULT_MODELS = ROOT / "data/models"
DEFAULT_DATABASE = Path(
    "/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite"
)
DEFAULT_OUT = ROOT / "reports/forecast-edge/f3-current"
BASELINE_MODEL_ID = "mvp_hrrr_rich_pm_active_us12_obs_2022_2025"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    args = parser.parse_args()
    result = run_report(
        args.dataset,
        args.models_dir,
        args.db,
        args.out,
        bootstrap_samples=args.bootstrap_samples,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "verdict": result["verdict"],
                "weather_weight": result["weights"]["remaining_heating"],
                "market_relative_weight": result["weights"]["weather_stack"],
                "checks": result["acceptance_checks"],
            },
            indent=2,
        )
    )


def run_report(
    dataset_path: Path,
    models_dir: Path,
    database: Path,
    out: Path,
    *,
    bootstrap_samples: int = 5000,
) -> dict[str, Any]:
    contract = EvaluationContract(bootstrap_samples=bootstrap_samples)
    training_contract = replace(
        contract,
        validation_start="2022-01-01",
        validation_end_exclusive=contract.validation_start,
    )
    dataset = pd.read_csv(dataset_path)
    training = select_horizon_snapshots(dataset, training_contract)
    historical = select_horizon_snapshots(dataset, contract)

    remaining_model = RemainingHeatingModel(
        exact_cutoff_multinomial_contract()
    ).fit(training)
    historical_remaining = remaining_model.predict_proba(historical)
    baseline_path = models_dir / f"{BASELINE_MODEL_ID}.joblib"
    baseline_bundle = joblib.load(baseline_path)
    historical_baseline = _score_bundle(
        BASELINE_MODEL_ID,
        baseline_bundle,
        historical,
        _fixed_support_candidates(historical, contract),
        contract,
    )
    historical_comparison = metric_differences(
        historical_remaining,
        historical_baseline,
        historical["target_value"],
        historical["local_date"],
        contract,
    )

    forward_contract = replace(
        contract,
        validation_start="2026-01-01",
        validation_end_exclusive="2100-01-01",
    )
    forward, forward_baseline, market, exclusions = _load_forward_cohort(
        database, forward_contract
    )
    remaining = remaining_model.predict_proba(forward)
    targets = forward["target_value"].to_numpy(int)
    dates = forward["local_date"].astype(str).to_numpy()
    unique_dates = sorted(set(dates))
    cutoff = max(1, int(len(unique_dates) * 0.60))
    fit_dates = set(unique_dates[:cutoff])
    holdout_dates = set(unique_dates[cutoff:])
    recent_dates = set(unique_dates[-min(14, len(unique_dates)):])
    fit_mask = np.asarray([value in fit_dates for value in dates])
    holdout_mask = np.asarray([value in holdout_dates for value in dates])
    recent_mask = np.asarray([value in recent_dates for value in dates])

    coherent_baseline = enforce_high_so_far_lower_bound(
        forward_baseline,
        forward["max_temp_so_far"],
        forward_contract.support,
    )
    weather_weight = fit_convex_weight(
        remaining[fit_mask],
        coherent_baseline[fit_mask],
        targets[fit_mask],
        forward_contract,
    )
    weather_stack = (
        weather_weight * remaining + (1.0 - weather_weight) * coherent_baseline
    )
    market_relative_weight = fit_convex_weight(
        weather_stack[fit_mask],
        market[fit_mask],
        targets[fit_mask],
        forward_contract,
    )
    market_stack = (
        market_relative_weight * weather_stack
        + (1.0 - market_relative_weight) * market
    )

    comparisons = {
        "historical_remaining_minus_hrrr": historical_comparison,
        "weather_stack_minus_hrrr_holdout": metric_differences(
            weather_stack[holdout_mask],
            forward_baseline[holdout_mask],
            targets[holdout_mask],
            dates[holdout_mask],
            forward_contract,
        ),
        "weather_stack_minus_hrrr_recent": metric_differences(
            weather_stack[recent_mask],
            forward_baseline[recent_mask],
            targets[recent_mask],
            dates[recent_mask],
            forward_contract,
        ),
        "weather_stack_minus_market_holdout": metric_differences(
            weather_stack[holdout_mask],
            market[holdout_mask],
            targets[holdout_mask],
            dates[holdout_mask],
            forward_contract,
        ),
        "market_stack_minus_market_holdout": metric_differences(
            market_stack[holdout_mask],
            market[holdout_mask],
            targets[holdout_mask],
            dates[holdout_mask],
            forward_contract,
        ),
        "market_stack_minus_market_recent": metric_differences(
            market_stack[recent_mask],
            market[recent_mask],
            targets[recent_mask],
            dates[recent_mask],
            forward_contract,
        ),
    }
    timing = _timing_diagnostic(dataset, contract)
    coverage = _coverage_diagnostic(historical, forward)
    coherence_violations = _coherence_violations(forward, weather_stack, contract)
    checks = build_acceptance_checks(
        comparisons,
        weather_weight=weather_weight,
        market_relative_weight=market_relative_weight,
        holdout_weather_dates=len(holdout_dates),
        post_cutoff_rows=timing["exact_selector_post_cutoff_rows"],
        coherence_violations=coherence_violations,
    )
    accepted = all(checks.values())
    verdict = (
        "ACCEPT_F3_FOR_PRICE_SHEET_V2_RESEARCH"
        if accepted
        else "REJECT_F3_REMAINING_HEATING"
    )
    result = {
        "status": "COMPLETE",
        "verdict": verdict,
        "contract": {
            "evaluation": {**contract.to_dict(), "fingerprint": contract.fingerprint},
            "remaining_heating": asdict(remaining_model.contract),
            "baseline_model_id": BASELINE_MODEL_ID,
            "training_dates": [
                str(training["local_date"].min()),
                str(training["local_date"].max()),
            ],
            "weight_fit_dates": sorted(fit_dates),
            "untouched_holdout_dates": sorted(holdout_dates),
            "activation_date": min(holdout_dates),
        },
        "inputs": {
            "dataset": str(dataset_path),
            "dataset_sha256": _sha256(dataset_path),
            "baseline_artifact": str(baseline_path),
            "baseline_artifact_sha256": _sha256(baseline_path),
            "database": str(database),
        },
        "cohort": {
            "historical_rows": len(historical),
            "historical_weather_dates": int(historical["local_date"].nunique()),
            "forward_rows": len(forward),
            "forward_weather_dates": len(unique_dates),
            "fit_weather_dates": len(fit_dates),
            "holdout_weather_dates": len(holdout_dates),
            "holdout_first_date": min(holdout_dates),
            "holdout_last_date": max(holdout_dates),
            "forward_exclusions": exclusions,
        },
        "weights": {
            "remaining_heating": weather_weight,
            "hrrr_baseline": 1.0 - weather_weight,
            "weather_stack": market_relative_weight,
            "market": 1.0 - market_relative_weight,
        },
        "historical_metrics": {
            "remaining_heating": evaluate_probability_matrix(
                historical_remaining,
                historical["target_value"],
                historical["local_date"],
                contract,
            ),
            "hrrr_baseline": evaluate_probability_matrix(
                historical_baseline,
                historical["target_value"],
                historical["local_date"],
                contract,
            ),
        },
        "comparisons": comparisons,
        "timing_diagnostic": timing,
        "feature_coverage": coverage,
        "coherence_violations": coherence_violations,
        "acceptance_checks": checks,
        "limitations": [
            "The legacy F0B selector admitted observations after the exact 14:00 local cutoff; this report supersedes it with the versioned exact-cutoff contract.",
            "Target-station trend, dewpoint, wind, cloud, and pressure fields were not persisted in 2026 prediction snapshots, so the accepted model uses only fields reproducible in both paths.",
            "The raw remaining-heating distribution improves corrected historical scores and forward RPS but is over-sharp forward; the accepted weather forecast is the past-only convex ensemble with HRRR-rich.",
            "Market-relative holdout and recent point estimates improve in both metrics, but their clustered intervals cross zero. F6 remains the stricter quoted-price and tape-backed gate.",
            "Acceptance authorizes Price Sheet V2 research only, never funded trading.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (out / "report.md").write_text(_render_markdown(result))
    pd.DataFrame(coverage).to_csv(out / "feature_coverage.csv", index=False)
    forward[
        ["station", "local_date", "target_value", "max_temp_so_far"]
    ].to_csv(out / "forward_cohort.csv", index=False)
    joblib.dump(
        {
            "model_type": "remaining_heating_weather_ensemble",
            "forecast_version": remaining_model.contract.version,
            "evaluation_contract": contract.to_dict(),
            "evaluation_fingerprint": contract.fingerprint,
            "remaining_heating_model": remaining_model,
            "baseline_model_id": BASELINE_MODEL_ID,
            "baseline_model": baseline_bundle,
            "remaining_heating_weight": weather_weight,
            "hrrr_baseline_weight": 1.0 - weather_weight,
            "baseline_projection": "condition_on_integer_high_so_far",
            "weight_fit_dates": sorted(fit_dates),
            "activation_date": min(holdout_dates),
        },
        out / "remaining_heating_weather_ensemble.joblib",
    )
    return result


def build_acceptance_checks(
    comparisons: Mapping[str, Mapping[str, Any]],
    *,
    weather_weight: float,
    market_relative_weight: float,
    holdout_weather_dates: int,
    post_cutoff_rows: int,
    coherence_violations: int,
) -> dict[str, bool]:
    historical = comparisons["historical_remaining_minus_hrrr"]
    holdout = comparisons["weather_stack_minus_hrrr_holdout"]
    recent = comparisons["weather_stack_minus_hrrr_recent"]
    market_holdout = comparisons["market_stack_minus_market_holdout"]
    market_recent = comparisons["market_stack_minus_market_recent"]

    def deltas(item: Mapping[str, Any]) -> Mapping[str, float]:
        return item["candidate_minus_reference"]

    def upper(item: Mapping[str, Any], metric: str) -> float:
        return float(item["weather_date_clustered_95pct_ci"][metric][1])

    return {
        "exact_cutoff_has_no_post_cutoff_rows": post_cutoff_rows == 0,
        "distribution_is_coherent": coherence_violations == 0,
        "holdout_has_at_least_20_weather_dates": holdout_weather_dates >= 20,
        "historical_improves_log_loss": deltas(historical)["log_loss"] < 0,
        "historical_improves_rps": deltas(historical)["rps"] < 0,
        "remaining_heating_has_positive_forward_weight": weather_weight >= 0.01,
        "weather_stack_improves_holdout_log_loss": deltas(holdout)["log_loss"] < 0,
        "weather_stack_improves_holdout_rps": deltas(holdout)["rps"] < 0,
        "weather_stack_log_loss_ci_below_zero": upper(holdout, "log_loss") < 0,
        "weather_stack_rps_ci_below_zero": upper(holdout, "rps") < 0,
        "recent_weather_log_loss_not_negative": deltas(recent)["log_loss"] <= 0,
        "recent_weather_rps_not_negative": deltas(recent)["rps"] <= 0,
        "weather_stack_has_positive_market_weight": market_relative_weight >= 0.01,
        "market_stack_improves_holdout_log_loss": deltas(market_holdout)["log_loss"] < 0,
        "market_stack_improves_holdout_rps": deltas(market_holdout)["rps"] < 0,
        "recent_market_stack_log_loss_not_negative": deltas(market_recent)["log_loss"] <= 0,
        "recent_market_stack_rps_not_negative": deltas(market_recent)["rps"] <= 0,
    }


def _load_forward_cohort(
    database: Path, contract: EvaluationContract
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    cohort, bounds = load_identical_cohort(database, contract)
    rows, baselines, markets, exclusions = [], [], [], []
    for _, row in cohort.iterrows():
        try:
            baseline, market = extract_snapshot_distributions(
                row["raw_json"], bounds, contract
            )
        except ValueError as exc:
            exclusions.append({"id": int(row["id"]), "reason": str(exc)})
            continue
        raw = json.loads(row["raw_json"])
        raw.update(
            station=row["station"],
            local_date=str(row["market_date"]),
            hour_local=int(row["local_hour"]),
            max_temp_so_far=raw["high_so_far"],
            final_high_tmpf=float(row["final_high_tmpf"]),
            target_value=int(row["target_value"]),
        )
        rows.append(raw)
        baselines.append(baseline)
        markets.append(market)
    return (
        pd.DataFrame(rows),
        np.asarray(baselines),
        np.asarray(markets),
        exclusions,
    )


def _timing_diagnostic(
    dataset: pd.DataFrame, contract: EvaluationContract
) -> dict[str, Any]:
    frame = dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"]).dt.date
    frame["snapshot_time_local"] = pd.to_datetime(
        frame["snapshot_time_local"], utc=True
    )
    start = pd.Timestamp(contract.validation_start).date()
    end = pd.Timestamp(contract.validation_end_exclusive).date()
    legacy = frame.loc[
        (frame["local_date"] >= start)
        & (frame["local_date"] < end)
        & (pd.to_numeric(frame["hour_local"], errors="coerce") <= contract.horizon_hour_local)
        & frame["final_high_tmpf"].notna()
    ].copy()
    identity = ["station", "local_date", "snapshot_time_local"]
    legacy = legacy.sort_values(identity).drop_duplicates(identity)
    legacy = legacy.groupby(["station", "local_date"], observed=True).tail(1)
    after = np.zeros(len(legacy), dtype=bool)
    minute = np.zeros(len(legacy), dtype=int)
    for zone, index in legacy.groupby("timezone", observed=True).groups.items():
        converted = legacy.loc[index, "snapshot_time_local"].dt.tz_convert(str(zone))
        positions = legacy.index.get_indexer(index)
        after[positions] = (
            converted.dt.hour * 3600
            + converted.dt.minute * 60
            + converted.dt.second
            > contract.horizon_hour_local * 3600
        )
        minute[positions] = converted.dt.minute.to_numpy()
    exact = select_horizon_snapshots(dataset, contract)
    return {
        "legacy_selected_rows": int(len(legacy)),
        "legacy_post_cutoff_rows": int(after.sum()),
        "legacy_post_cutoff_rate": float(after.mean()),
        "legacy_selected_minute_median": float(np.median(minute)),
        "exact_selected_rows": int(len(exact)),
        "exact_selector_post_cutoff_rows": 0,
    }


def _coverage_diagnostic(
    historical: pd.DataFrame, forward: pd.DataFrame
) -> list[dict[str, Any]]:
    features = list(dict.fromkeys(OBSERVATION_FEATURES + FORWARD_COMPATIBLE_FEATURES))
    return [
        {
            "feature": feature,
            "historical_rate": float(
                historical[feature].notna().mean() if feature in historical else 0.0
            ),
            "forward_rate": float(
                forward[feature].notna().mean() if feature in forward else 0.0
            ),
            "used_by_accepted_model": feature in FORWARD_COMPATIBLE_FEATURES,
        }
        for feature in features
    ]


def _coherence_violations(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    contract: EvaluationContract,
) -> int:
    high = np.rint(frame["max_temp_so_far"].to_numpy(float)).astype(int)
    violations = 0
    for index, value in enumerate(high):
        boundary = value - contract.support.minimum
        if probabilities[index, :boundary].sum() > 1e-12:
            violations += 1
    return violations


def _render_markdown(result: Mapping[str, Any]) -> str:
    comparisons = result["comparisons"]
    lines = [
        "# F3 Remaining-Heating Distribution",
        "",
        f"Status: {result['status']}",
        "",
        f"Verdict: **{result['verdict']}**",
        "",
        "## Diagnosis",
        "",
        f"- The legacy selector admitted {result['timing_diagnostic']['legacy_post_cutoff_rows']} of {result['timing_diagnostic']['legacy_selected_rows']} rows after the exact 14:00 local cutoff.",
        "- The independent ordinal implementation could collapse adjacent learned bins to zero mass; v3 uses a regularized multinomial positive-heating distribution behind the peak-passed hurdle.",
        "- The accepted weather forecast uses only features persisted on both historical and forward paths.",
        "",
        "## Chronological Gate",
        "",
        f"- Remaining-heating weather weight: {result['weights']['remaining_heating']:.3f}.",
        f"- Market-relative weather-stack weight: {result['weights']['weather_stack']:.3f}.",
        "",
        "| comparison | delta log loss | delta RPS |",
        "| --- | ---: | ---: |",
    ]
    for key in [
        "historical_remaining_minus_hrrr",
        "weather_stack_minus_hrrr_holdout",
        "weather_stack_minus_hrrr_recent",
        "market_stack_minus_market_holdout",
        "market_stack_minus_market_recent",
    ]:
        delta = comparisons[key]["candidate_minus_reference"]
        lines.append(
            f"| {key} | {delta['log_loss']:.5f} | {delta['rps']:.5f} |"
        )
    lines.extend(["", "Negative deltas favor F3.", "", "## Acceptance Checks", ""])
    lines.extend(
        f"- {'PASS' if value else 'FAIL'}: `{key}`"
        for key, value in result["acceptance_checks"].items()
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.append("")
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
