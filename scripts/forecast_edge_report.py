#!/usr/bin/env python3
"""Produce the frozen F0B fixed-support baseline and prediction-pruning report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression

from weather_trader.forecasting.evaluation import (
    EvaluationContract,
    evaluate_probability_matrix,
    pairwise_prediction_diagnostics,
    prune_behavioral_duplicates,
    select_horizon_snapshots,
)
from weather_trader.models.high_regressor import entry_window


DEFAULT_DATASET = ROOT / "data/raw/dataset_2022-01-01_2025-12-31_pm_active_us12_hrrr_enriched.csv"
DEFAULT_MODELS = ROOT / "data/models"
DEFAULT_OUT = ROOT / "reports/forecast-edge/f0b-current"
FAMILIES = ("mvp", "dynamic_bucket", "dynamic_bucket_tuned", "catboost_bucket", "high_regression", "ngboost_normal")
SOURCE_SETS = ("obs", "hrrr_rich", "metar_hrrr_rich")
MODEL_NAMES = tuple(f"{family}{'' if source == 'obs' else '_' + source}_pm_active_us12_obs_2022_2025" for source in SOURCE_SETS for family in FAMILIES)
FROZEN_ROLE_REPRESENTATIVES = {
    "observation_only": "mvp_pm_active_us12_obs_2022_2025",
    "hrrr": "mvp_hrrr_rich_pm_active_us12_obs_2022_2025",
    "distributional": "ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    result = run_report(args.dataset, args.models_dir, args.out, args.bootstrap_samples)
    print(json.dumps({"status": result["status"], "contract_fingerprint": result["contract"]["fingerprint"], "models": len(result["models"]), "retained": result["frozen_minimal_baseline"]}, indent=2))


def run_report(dataset_path: Path, models_dir: Path, out: Path, bootstrap_samples: int = 2000) -> dict[str, Any]:
    contract = EvaluationContract(bootstrap_samples=bootstrap_samples)
    dataset = pd.read_csv(dataset_path)
    snapshots = select_horizon_snapshots(dataset, contract)
    candidates = _fixed_support_candidates(snapshots, contract)
    probabilities: dict[str, np.ndarray] = {}
    metrics: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    model_records = []
    for name in MODEL_NAMES:
        path = models_dir / f"{name}.joblib"
        if not path.exists():
            raise FileNotFoundError(path)
        bundle = joblib.load(path)
        matrix = _score_bundle(name, bundle, snapshots, candidates, contract)
        probabilities[name] = matrix.astype(np.float32)
        hashes[name] = _sha256(path)
        metrics[name] = evaluate_probability_matrix(matrix, snapshots["target_value"], snapshots["local_date"], contract)
        model_records.append({"model_id": name, "model_type": bundle.get("model_type") or "threshold_classifier", "artifact_sha256": hashes[name], "feature_columns": list(bundle.get("feature_columns") or [])})
    pairwise = pairwise_prediction_diagnostics(probabilities, contract.support, hashes)
    exact_pruning = prune_behavioral_duplicates(MODEL_NAMES, pairwise, contract)
    role_pruning, retained = _freeze_minimal_baseline(pairwise)
    result = {
        "status": "COMPLETE",
        "contract": {**contract.to_dict(), "fingerprint": contract.fingerprint},
        "inputs": {"dataset": str(dataset_path), "dataset_sha256": _sha256(dataset_path), "models_dir": str(models_dir)},
        "cohort": {"rows": len(snapshots), "weather_dates": snapshots["local_date"].nunique(), "stations": snapshots["station"].nunique(), "first_date": str(snapshots["local_date"].min()), "last_date": str(snapshots["local_date"].max())},
        "models": model_records,
        "metrics": metrics,
        "pairwise": pairwise,
        "exact_or_behavioral_duplicate_pruning": exact_pruning,
        "role_pruning": role_pruning,
        "frozen_minimal_baseline": retained,
        "market_baseline": {"status": "CONTRACT_FROZEN_NOT_SCORED", "reason": "The 2022-2025 weather training corpus has no causally timestamped complete market ladders; future comparisons must use actual complete observed ladders and identical rows."},
        "legacy_metric_verdict": "REJECT_OUTCOME_CONDITIONED_SYNTHETIC_LADDER_SCORES",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    pd.DataFrame([{"model_id": name, "rows": record["rows"], "weather_dates": record["weather_dates"], **record["metrics"]} for name, record in metrics.items()]).to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(pairwise).to_csv(out / "pairwise.csv", index=False)
    pd.DataFrame(role_pruning).to_csv(out / "pruning.csv", index=False)
    (out / "report.md").write_text(_render_markdown(result))
    return result


def _fixed_support_candidates(snapshots: pd.DataFrame, contract: EvaluationContract) -> pd.DataFrame:
    support = contract.support.values
    frame = snapshots.loc[snapshots.index.repeat(len(support))].reset_index(drop=True)
    frame["support_value"] = np.tile(support, len(snapshots))
    frame["synthetic_ladder_id"] = np.repeat(np.arange(len(snapshots)), len(support)).astype(str)
    lower = frame["support_value"].astype(float)
    upper = lower + 1.0
    lower.loc[frame["support_value"] == contract.support.minimum] = np.nan
    upper.loc[frame["support_value"] == contract.support.maximum] = np.nan
    frame["bucket_lower"], frame["bucket_upper"] = lower, upper
    frame["bucket_span"] = 1.0
    frame["is_left_tail"] = frame["bucket_lower"].isna().astype(int)
    frame["is_right_tail"] = frame["bucket_upper"].isna().astype(int)
    for stem, source in [("current_temp", "current_temp"), ("max_so_far", "max_temp_so_far"), ("min_so_far", "min_temp_so_far")]:
        if source in frame:
            frame[f"lower_minus_{stem}"] = frame["bucket_lower"] - pd.to_numeric(frame[source], errors="coerce")
            frame[f"upper_minus_{stem}"] = frame["bucket_upper"] - pd.to_numeric(frame[source], errors="coerce")
    hrrr = pd.to_numeric(frame.get("hrrr_current_temp"), errors="coerce")
    frame["hrrr_lower_minus_current_temp"] = frame["bucket_lower"] - hrrr
    frame["hrrr_upper_minus_current_temp"] = frame["bucket_upper"] - hrrr
    for metric in ["max", "min"]:
        source = pd.to_numeric(frame.get(f"hrrr_remaining_{metric}"), errors="coerce")
        frame[f"hrrr_remaining_{metric}_minus_lower"] = source - frame["bucket_lower"]
        frame[f"hrrr_remaining_{metric}_minus_upper"] = source - frame["bucket_upper"]
    return frame


def _score_bundle(name: str, bundle: dict[str, Any], snapshots: pd.DataFrame, candidates: pd.DataFrame, contract: EvaluationContract) -> np.ndarray:
    model_type = bundle.get("model_type") or "threshold_classifier"
    features = list(bundle.get("feature_columns") or [])
    count, width = len(snapshots), len(contract.support.values)
    if model_type in {"dynamic_bucket", "catboost_bucket"}:
        frame = _with_features(candidates, features)
        raw = bundle["model"].predict_proba(frame[features])[:, 1].reshape(count, width)
        return _normalize(raw)
    if model_type == "threshold_classifier":
        return _threshold_matrix(bundle["model"], snapshots, features, contract)
    if model_type == "high_regression_empirical_residual":
        frame = _with_features(snapshots.copy(), features)
        mean = bundle["model"].predict(frame[features])
        return _empirical_matrix(mean, snapshots, bundle["residuals"], contract)
    if model_type == "ngboost_normal_crps":
        frame = _with_features(snapshots.copy(), features)
        model = bundle["model"]
        distribution = model["ngboost"].pred_dist(model["preprocessor"].transform(frame[features]))
        mean = np.asarray(distribution.loc if hasattr(distribution, "loc") else distribution.mean(), dtype=float)
        scale = np.asarray(distribution.scale if hasattr(distribution, "scale") else distribution.params["scale"], dtype=float)
        return _normal_matrix(mean, scale, contract)
    raise ValueError(f"unsupported model type for {name}: {model_type}")


def _threshold_matrix(model: Any, snapshots: pd.DataFrame, features: list[str], contract: EvaluationContract) -> np.ndarray:
    cutpoints = np.arange(contract.support.minimum + 1, contract.support.maximum + 1)
    frame = snapshots.loc[snapshots.index.repeat(len(cutpoints))].reset_index(drop=True)
    frame["threshold"] = np.tile(cutpoints, len(snapshots)).astype(float)
    frame["threshold_minus_current_temp"] = frame["threshold"] - pd.to_numeric(frame["current_temp"], errors="coerce")
    frame["threshold_minus_max_so_far"] = frame["threshold"] - pd.to_numeric(frame["max_temp_so_far"], errors="coerce")
    if "min_temp_so_far" in frame:
        frame["threshold_minus_min_so_far"] = frame["threshold"] - pd.to_numeric(frame["min_temp_so_far"], errors="coerce")
    if "hrrr_remaining_max" in frame:
        frame["hrrr_remaining_max_minus_threshold"] = pd.to_numeric(frame["hrrr_remaining_max"], errors="coerce") - frame["threshold"]
    frame = _with_features(frame, features)
    survival = model.predict_proba(frame[features])[:, 1].reshape(len(snapshots), len(cutpoints))
    for index in range(len(survival)):
        survival[index] = IsotonicRegression(increasing=False, y_min=0, y_max=1).fit_transform(cutpoints, survival[index])
    matrix = np.empty((len(snapshots), len(cutpoints) + 1))
    matrix[:, 0], matrix[:, -1] = 1 - survival[:, 0], survival[:, -1]
    matrix[:, 1:-1] = survival[:, :-1] - survival[:, 1:]
    return _normalize(matrix)


def _empirical_matrix(mean: np.ndarray, snapshots: pd.DataFrame, residuals: pd.DataFrame, contract: EvaluationContract) -> np.ndarray:
    residual_frame = residuals.copy()
    if "window" not in residual_frame:
        residual_frame["window"] = residual_frame["hour_local"].map(entry_window)
    pools = {str(key): np.sort(group["residual"].dropna().to_numpy(float)) for key, group in residual_frame.groupby("window", observed=True)}
    fallback = np.sort(residual_frame["residual"].dropna().to_numpy(float))
    values = contract.support.values
    matrix = np.zeros((len(snapshots), len(values)))
    for index, (_, row) in enumerate(snapshots.iterrows()):
        pool = pools.get(entry_window(row["hour_local"]), fallback)
        cuts = np.searchsorted(pool, values[1:] - mean[index], side="left") / len(pool)
        matrix[index, 0], matrix[index, -1] = cuts[0], 1 - cuts[-1]
        matrix[index, 1:-1] = np.diff(cuts)
    return _normalize(matrix)


def _normal_matrix(mean: np.ndarray, scale: np.ndarray, contract: EvaluationContract) -> np.ndarray:
    values = contract.support.values
    cuts = norm.cdf(values[None, 1:], loc=mean[:, None], scale=np.maximum(scale[:, None], 1e-6))
    matrix = np.empty((len(mean), len(values)))
    matrix[:, 0], matrix[:, -1] = cuts[:, 0], 1 - cuts[:, -1]
    matrix[:, 1:-1] = np.diff(cuts, axis=1)
    return _normalize(matrix)


def _freeze_minimal_baseline(pairwise: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    roles = {}
    for name in MODEL_NAMES:
        if name.startswith(("high_regression", "ngboost_normal")):
            role = "distributional"
        elif "_hrrr_rich_" not in name:
            role = "observation_only"
        else:
            role = "hrrr"
        roles[name] = role
    lookup = {(row["model_a"], row["model_b"]): row for row in pairwise}
    retained, decisions = [], []
    for name in MODEL_NAMES:
        role, representative = roles[name], FROZEN_ROLE_REPRESENTATIVES[roles[name]]
        if name == representative:
            retained.append(name)
            decisions.append({"model_id": name, "role": role, "decision": "RETAIN", "representative": name, "reason": "frozen outcome-blind role representative"})
            continue
        pair = lookup.get((name, representative)) or lookup.get((representative, name))
        decisions.append({"model_id": name, "role": role, "decision": "RETIRE_ESTIMATOR_VARIANT", "representative": representative, "reason": "same information-set role; estimator count is not independent evidence", "probability_correlation_to_representative": pair["probability_correlation"] if pair else None, "mean_total_variation_to_representative": pair["mean_total_variation"] if pair else None})
    distinct_pair = lookup.get((retained[1], retained[2])) or lookup.get((retained[2], retained[1]))
    if distinct_pair and distinct_pair["probability_correlation"] >= 0.999 and distinct_pair["mean_total_variation"] <= 0.005:
        retained.remove(FROZEN_ROLE_REPRESENTATIVES["distributional"])
        for row in decisions:
            if row["role"] == "distributional" and row["decision"] == "RETAIN":
                row.update(decision="COLLAPSE_DUPLICATE", representative=FROZEN_ROLE_REPRESENTATIVES["hrrr"], reason="distributional control is not behaviorally distinct")
    return decisions, retained


def _with_features(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    for column in features:
        if column not in frame:
            frame[column] = np.nan
    return frame


def _normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.clip(np.asarray(matrix, dtype=float), 0, None)
    totals = matrix.sum(axis=1)
    if (totals <= 0).any() or not np.isfinite(matrix).all():
        raise ValueError("model produced invalid fixed-support distribution")
    return matrix / totals[:, None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_markdown(result: dict[str, Any]) -> str:
    lines = ["# F0B Forecast Baseline And Evaluation Repair", "", f"Status: {result['status']}", "", f"Contract: `{result['contract']['version']}` / `{result['contract']['fingerprint']}`", "", "## Verdict", "", "Legacy model metrics based on ladders centered on the realized high are rejected. The frozen replacement uses one latest causal snapshot at or before 14:00 local per station/date, a -20F..130F support fixed before outcomes, full-distribution scores, and weather-date clustered uncertainty.", "", "The minimal baseline is:"]
    lines.extend(f"- `{name}`" for name in result["frozen_minimal_baseline"])
    lines.extend(["", "Estimator variants outside those information-set roles are diagnostics, not independent evidence. Market-relative scoring remains fail-closed until causally timestamped complete observed ladders exist on identical rows.", "", "## Cohort", "", f"- {result['cohort']['rows']} station/date rows across {result['cohort']['weather_dates']} weather dates and {result['cohort']['stations']} stations.", f"- Dates: {result['cohort']['first_date']} through {result['cohort']['last_date']}.", "", "## Fixed-Support Metrics", "", "| model | log loss | RPS | threshold Brier | top accuracy |", "| --- | ---: | ---: | ---: | ---: |"])
    for name in result["frozen_minimal_baseline"]:
        metrics = result["metrics"][name]["metrics"]
        lines.append(f"| {name} | {metrics['log_loss']:.4f} | {metrics['rps']:.4f} | {metrics['threshold_brier']:.6f} | {metrics['top_bucket_accuracy']:.3f} |")
    lines.extend(["", "Generated CSVs contain all pairwise correlations and pruning decisions. These weather-only results authorize neither pricing promotion nor funded trading.", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    main()
