#!/usr/bin/env python3
"""Walk-forward replay of model-level calibration on raw candidate distributions.

This script tests whether probability calibration improves the live US high-temp
bucket-consensus sleeve when candidate selection is recomputed from the raw
per-bucket candidate distribution, instead of merely gating already-selected
snapshot rows.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.linear_model import LogisticRegression

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import bucket_won, return_risk, sharpe  # noqa: E402
from scripts.snapshot_opportunity_sweep import (  # noqa: E402
    POLICY_SEARCH_CONSENSUS_GROUPS,
    PolicySearchSpec,
    build_consensus_rows,
    entry_price,
    first_policy_rows,
    float_or_none,
    load_snapshot_rows,
    row_matches_policy_spec,
    selected_fair,
    sort_key,
)

DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025"
CATBOOST_MODEL = "catboost_bucket_pm_active_us12_obs_2022_2025"
LIVE_CONSENSUS_GROUP = "obs_bucket_consensus"
LIVE_MODELS = frozenset({DYNAMIC_TUNED_MODEL, CATBOOST_MODEL})


@dataclass(frozen=True)
class CalibrationFit:
    intercept: float
    coef: float
    feature: str
    n: int

    def predict(self, fair: float) -> float:
        x = logit(fair) if self.feature == "logit" else fair
        return sigmoid(self.intercept + self.coef * x)


def sigmoid(value: float) -> float:
    if value >= 0.0:
        exp_value = math.exp(-value)
        return 1.0 / (1.0 + exp_value)
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def logit(probability: float) -> float:
    probability = min(0.999, max(0.001, probability))
    return math.log(probability / (1.0 - probability))


def live_consensus_spec() -> PolicySearchSpec:
    return PolicySearchSpec(
        name="pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first",
        source="consensus",
        strategy_bucket="HIGH_CONVICTION",
        model_group=LIVE_CONSENSUS_GROUP,
        entry_price_min=0.05,
        entry_price_max=0.50,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        rows = load_snapshot_rows(db, market_family="HIGH_TEMP", us_high_temp_only=True)
        raw_json_by_id = {
            int(row["id"]): str(row["raw_json"] or "{}")
            for row in db.execute(
                """
                select id, raw_json
                from prediction_snapshots
                where coalesce(market_family, 'HIGH_TEMP') = 'HIGH_TEMP'
                  and station like 'K%'
                  and model_name in (?, ?)
                """,
                (DYNAMIC_TUNED_MODEL, CATBOOST_MODEL),
            ).fetchall()
        }
        for row in rows:
            row["raw_json"] = raw_json_by_id.get(int(row["id"]), "{}")
        return rows
    finally:
        db.close()


def selected_training_examples(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str, str, float, int]]:
    examples: list[tuple[str, str, str, float, int]] = []
    for row in rows:
        if row.get("model_name") not in LIVE_MODELS or row.get("correct") is None:
            continue
        fair = selected_fair(row)
        side = str(row.get("selected_side") or "")
        if fair is None or side not in {"BUY_YES", "BUY_NO"} or not 0.0 < float(fair) < 1.0:
            continue
        examples.append((str(row["model_name"]), str(row["station"]), side, float(fair), int(row["correct"])))
    return examples


def candidate_training_examples(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str, str, float, int]]:
    examples: list[tuple[str, str, str, float, int]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if row.get("model_name") not in LIVE_MODELS or row.get("strategy_bucket") != "HIGH_CONVICTION":
            continue
        final_temp = float_or_none(row.get("final_high_tmpf"))
        if final_temp is None:
            continue
        for candidate in candidate_distribution(row):
            bucket = candidate.get("bucket")
            if not bucket:
                continue
            yes_won = bucket_won(float(final_temp), str(bucket))
            for side, fair_key in (("BUY_YES", "fair_yes"), ("BUY_NO", "fair_no")):
                fair = float_or_none(candidate.get(fair_key))
                if fair is None or not 0.0 < fair < 1.0:
                    continue
                key = (row.get("model_name"), row.get("station"), row.get("timestamp"), bucket, side, fair)
                if key in seen:
                    continue
                seen.add(key)
                won = yes_won if side == "BUY_YES" else not yes_won
                examples.append((str(row["model_name"]), str(row["station"]), side, fair, 1 if won else 0))
    return examples


def fit_calibrators(
    examples: list[tuple[str, str, str, float, int]],
    *,
    feature: str,
    min_samples: int,
    include_side: bool,
) -> dict[tuple[str, str, str], CalibrationFit]:
    grouped: dict[tuple[str, str, str], list[tuple[float, int]]] = defaultdict(list)
    for model_name, station, side, fair, correct in examples:
        grouped[(model_name, station, side if include_side else "*")].append((fair, correct))
        grouped[(model_name, "*", side if include_side else "*")].append((fair, correct))
        if include_side:
            grouped[(model_name, station, "*")].append((fair, correct))
            grouped[(model_name, "*", "*")].append((fair, correct))

    fits: dict[tuple[str, str, str], CalibrationFit] = {}
    for key, values in grouped.items():
        if len(values) < min_samples:
            continue
        y = np.array([correct for _, correct in values])
        if len(set(y.tolist())) < 2:
            continue
        if feature == "logit":
            x = np.array([[logit(fair)] for fair, _ in values])
        elif feature == "fair":
            x = np.array([[fair] for fair, _ in values])
        else:
            raise ValueError(f"unsupported feature transform: {feature}")
        model = LogisticRegression(C=1e10, solver="lbfgs", max_iter=1000)
        model.fit(x, y)
        fits[key] = CalibrationFit(float(model.intercept_[0]), float(model.coef_[0][0]), feature, len(values))
    return fits


def lookup_fit(
    fits: dict[tuple[str, str, str], CalibrationFit],
    *,
    model_name: str,
    station: str,
    side: str,
) -> CalibrationFit | None:
    for key in (
        (model_name, station, side),
        (model_name, station, "*"),
        (model_name, "*", side),
        (model_name, "*", "*"),
    ):
        if key in fits:
            return fits[key]
    return None


def candidate_distribution(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        raw = json.loads(str(row.get("raw_json") or "{}"))
    except json.JSONDecodeError:
        return []
    candidates = raw.get("candidate_distribution")
    return candidates if isinstance(candidates, list) else []


def recalibrated_model_rows(
    rows: Iterable[dict[str, Any]],
    *,
    fits: dict[tuple[str, str, str], CalibrationFit],
    model_edge_min: float,
    entry_min: float,
    entry_max: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        model_name = str(row.get("model_name") or "")
        if model_name not in LIVE_MODELS or row.get("strategy_bucket") != "HIGH_CONVICTION":
            continue
        final_temp = float_or_none(row.get("final_high_tmpf"))
        if final_temp is None:
            continue
        selected = best_calibrated_candidate(
            row,
            fits=fits,
            model_edge_min=model_edge_min,
            entry_min=entry_min,
            entry_max=entry_max,
        )
        if selected is None:
            continue
        candidate, side, entry, calibrated_fair, edge = selected
        yes_won = bucket_won(float(final_temp), str(candidate["bucket"]))
        correct = yes_won if side == "BUY_YES" else not yes_won
        item = dict(row)
        item["selected_market_id"] = candidate.get("market_id")
        item["selected_bucket"] = candidate.get("bucket")
        item["selected_side"] = side
        item["selected_edge"] = edge
        item["selected_yes_ask"] = float_or_none(candidate.get("yes_ask"))
        item["selected_no_ask"] = float_or_none(candidate.get("no_ask"))
        if side == "BUY_YES":
            item["selected_fair_yes"] = calibrated_fair
            item["selected_fair_no"] = 1.0 - calibrated_fair
        else:
            item["selected_fair_no"] = calibrated_fair
            item["selected_fair_yes"] = 1.0 - calibrated_fair
        item["entry_price"] = entry
        item["selected_fair"] = calibrated_fair
        item["correct"] = 1 if correct else 0
        item["paper_pnl"] = (1.0 - entry) if correct else -entry
        item["source"] = model_name
        output.append(item)
    return sorted(output, key=sort_key)


def best_calibrated_candidate(
    row: dict[str, Any],
    *,
    fits: dict[tuple[str, str, str], CalibrationFit],
    model_edge_min: float,
    entry_min: float,
    entry_max: float,
) -> tuple[dict[str, Any], str, float, float, float] | None:
    best: tuple[dict[str, Any], str, float, float, float] | None = None
    model_name = str(row.get("model_name") or "")
    station = str(row.get("station") or "")
    for candidate in candidate_distribution(row):
        if candidate.get("strategy_bucket") not in {None, row.get("strategy_bucket")}:
            continue
        if not candidate.get("market_id") or not candidate.get("bucket"):
            continue
        for side, ask_key, fair_key in (
            ("BUY_YES", "yes_ask", "fair_yes"),
            ("BUY_NO", "no_ask", "fair_no"),
        ):
            entry = float_or_none(candidate.get(ask_key))
            raw_fair = float_or_none(candidate.get(fair_key))
            if entry is None or raw_fair is None:
                continue
            if not entry_min <= entry <= entry_max or not 0.0 < raw_fair < 1.0:
                continue
            fit = lookup_fit(fits, model_name=model_name, station=station, side=side)
            if fit is None:
                continue
            calibrated_fair = fit.predict(raw_fair)
            edge = calibrated_fair - entry
            if edge < model_edge_min:
                continue
            if best is None or edge > best[4]:
                best = (candidate, side, entry, calibrated_fair, edge)
    return best


def raw_live_candidates(rows: list[dict[str, Any]], spec: PolicySearchSpec) -> list[dict[str, Any]]:
    consensus = build_consensus_rows(rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS)
    return first_policy_rows(spec, [row for row in consensus if row_matches_policy_spec(spec, row)])


def calibrated_live_candidates(
    rows: list[dict[str, Any]],
    *,
    fits: dict[tuple[str, str, str], CalibrationFit],
    spec: PolicySearchSpec,
    model_edge_min: float,
) -> list[dict[str, Any]]:
    model_rows = recalibrated_model_rows(
        rows,
        fits=fits,
        model_edge_min=model_edge_min,
        entry_min=spec.entry_price_min if spec.entry_price_min is not None else 0.0,
        entry_max=spec.entry_price_max if spec.entry_price_max is not None else 1.0,
    )
    consensus = build_consensus_rows(model_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS)
    return first_policy_rows(spec, [row for row in consensus if row_matches_policy_spec(spec, row)])


def summarize(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("paper_pnl") is not None and entry_price(row) is not None]
    risk = sum(float(entry_price(row)) for row in resolved)
    pnl = sum(float(row["paper_pnl"]) for row in resolved)
    probability_rows = [
        row
        for row in resolved
        if row.get("correct") is not None and selected_fair(row) is not None and 0.0 <= float(selected_fair(row)) <= 1.0
    ]
    return {
        "label": label,
        "rows": len(rows),
        "resolved": len(resolved),
        "win_rate": mean(1.0 if int(row["correct"]) else 0.0 for row in resolved if row.get("correct") is not None),
        "risk": risk if resolved else None,
        "pnl": pnl if resolved else None,
        "rr": return_risk(pnl, risk) if resolved else None,
        "sharpe": sharpe([float(row["paper_pnl"]) for row in resolved]),
        "avg_entry": mean(float(entry_price(row)) for row in resolved),
        "avg_fair": mean(float(selected_fair(row)) for row in resolved if selected_fair(row) is not None),
        "avg_edge": mean(float(row["selected_edge"]) for row in resolved if row.get("selected_edge") is not None),
        "brier": brier_score(probability_rows),
        "log_loss": log_loss(probability_rows),
    }


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def brier_score(rows: Iterable[dict[str, Any]]) -> float | None:
    values = []
    for row in rows:
        probability = selected_fair(row)
        if probability is None or row.get("correct") is None:
            continue
        values.append((float(probability) - float(int(row["correct"]))) ** 2)
    return mean(values)


def log_loss(rows: Iterable[dict[str, Any]]) -> float | None:
    values = []
    for row in rows:
        probability = selected_fair(row)
        if probability is None or row.get("correct") is None:
            continue
        probability = min(0.999, max(0.001, float(probability)))
        target = int(row["correct"])
        values.append(-(target * math.log(probability) + (1 - target) * math.log(1.0 - probability)))
    return mean(values)


def replay(
    rows: list[dict[str, Any]],
    *,
    start_date: str,
    feature: str,
    min_samples: int,
    include_side: bool,
    model_edge_min: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec = live_consensus_spec()
    raw_rows: list[dict[str, Any]] = []
    calibrated_selected_fit_rows: list[dict[str, Any]] = []
    calibrated_candidate_fit_rows: list[dict[str, Any]] = []
    dates = sorted({str(row.get("market_date")) for row in rows if str(row.get("market_date")) >= start_date})
    for market_date in dates:
        train_rows = [row for row in rows if str(row.get("market_date")) < market_date]
        day_rows = [row for row in rows if str(row.get("market_date")) == market_date]
        raw_rows.extend(raw_live_candidates(day_rows, spec))

        selected_fits = fit_calibrators(
            selected_training_examples(train_rows),
            feature=feature,
            min_samples=min_samples,
            include_side=include_side,
        )
        candidate_fits = fit_calibrators(
            candidate_training_examples(train_rows),
            feature=feature,
            min_samples=min_samples,
            include_side=include_side,
        )
        calibrated_selected_fit_rows.extend(
            calibrated_live_candidates(day_rows, fits=selected_fits, spec=spec, model_edge_min=model_edge_min)
        )
        calibrated_candidate_fit_rows.extend(
            calibrated_live_candidates(day_rows, fits=candidate_fits, spec=spec, model_edge_min=model_edge_min)
        )
    return raw_rows, calibrated_selected_fit_rows, calibrated_candidate_fit_rows


def render_summary(rows: list[dict[str, Any]]) -> str:
    headers = ("variant", "rows", "win", "rr", "pnl", "risk", "avg_entry", "avg_fair", "avg_edge", "brier", "logloss", "sharpe")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {label} | {rows} | {win_rate} | {rr} | {pnl} | {risk} | {avg_entry} | {avg_fair} | {avg_edge} | {brier} | {log_loss} | {sharpe} |".format(
                label=row["label"],
                rows=row["rows"],
                win_rate=format_pct(row["win_rate"]),
                rr=format_float(row["rr"]),
                pnl=format_float(row["pnl"]),
                risk=format_float(row["risk"]),
                avg_entry=format_float(row["avg_entry"]),
                avg_fair=format_float(row["avg_fair"]),
                avg_edge=format_float(row["avg_edge"]),
                brier=format_float(row["brier"]),
                log_loss=format_float(row["log_loss"]),
                sharpe=format_float(row["sharpe"]),
            )
        )
    return "\n".join(lines)


def render_reliability(label: str, rows: list[dict[str, Any]]) -> str:
    bins = (
        (0.0, 0.2, "0.00-0.20"),
        (0.2, 0.4, "0.20-0.40"),
        (0.4, 0.6, "0.40-0.60"),
        (0.6, 0.8, "0.60-0.80"),
        (0.8, 1.000001, "0.80-1.00"),
    )
    lines = [
        f"### Reliability: {label}",
        "",
        "| fair band | rows | win | avg fair | rr | pnl | risk |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    resolved = [row for row in rows if row.get("paper_pnl") is not None and row.get("correct") is not None and selected_fair(row) is not None]
    for low, high, band_label in bins:
        band_rows = [row for row in resolved if low <= float(selected_fair(row)) < high]
        summary = summarize(band_label, band_rows)
        lines.append(
            "| {band} | {rows} | {win} | {fair} | {rr} | {pnl} | {risk} |".format(
                band=band_label,
                rows=summary["resolved"],
                win=format_pct(summary["win_rate"]),
                fair=format_float(summary["avg_fair"]),
                rr=format_float(summary["rr"]),
                pnl=format_float(summary["pnl"]),
                risk=format_float(summary["risk"]),
            )
        )
    return "\n".join(lines)


def render_examples(label: str, rows: list[dict[str, Any]], *, limit: int = 12) -> str:
    lines = [
        f"### {label}",
        "",
        "| date | station | bucket | side | entry | fair | edge | correct | pnl |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=sort_key)[:limit]:
        lines.append(
            "| {date} | {station} | {bucket} | {side} | {entry} | {fair} | {edge} | {correct} | {pnl} |".format(
                date=row.get("market_date"),
                station=row.get("station"),
                bucket=row.get("selected_bucket"),
                side=row.get("selected_side"),
                entry=format_float(entry_price(row)),
                fair=format_float(selected_fair(row)),
                edge=format_float(row.get("selected_edge")),
                correct=row.get("correct"),
                pnl=format_float(row.get("paper_pnl")),
            )
        )
    return "\n".join(lines)


def format_float(value: Any) -> str:
    number = float_or_none(value)
    return "n/a" if number is None else f"{number:.3f}"


def format_pct(value: Any) -> str:
    number = float_or_none(value)
    return "n/a" if number is None else f"{number:.1%}"


def parse_float_list(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_feature_list(value: str) -> list[str]:
    features = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [feature for feature in features if feature not in {"logit", "fair"}]
    if invalid:
        raise ValueError(f"unsupported feature values: {', '.join(invalid)}")
    return features


def run_grid(
    rows: list[dict[str, Any]],
    *,
    start_date: str,
    features: list[str],
    min_samples_values: list[int],
    edge_values: list[float],
    side_values: list[bool],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for feature in features:
        for min_samples in min_samples_values:
            for include_side in side_values:
                for edge_min in edge_values:
                    raw_rows, selected_fit_rows, candidate_fit_rows = replay(
                        rows,
                        start_date=start_date,
                        feature=feature,
                        min_samples=min_samples,
                        include_side=include_side,
                        model_edge_min=edge_min,
                    )
                    for source, variant_rows in (
                        ("raw", raw_rows),
                        ("selected_rows", selected_fit_rows),
                        ("candidate_universe", candidate_fit_rows),
                    ):
                        summary = summarize(source, variant_rows)
                        summary.update(
                            {
                                "feature": feature,
                                "min_samples": min_samples,
                                "side_aware": include_side,
                                "model_edge_min": edge_min,
                                "source": source,
                            }
                        )
                        output.append(summary)
    return output


def render_grid(rows: list[dict[str, Any]]) -> str:
    headers = (
        "feature",
        "min_n",
        "side",
        "edge",
        "variant",
        "rows",
        "win",
        "rr",
        "pnl",
        "risk",
        "avg_entry",
        "avg_fair",
        "brier",
        "logloss",
    )
    lines = [
        "| " + " | ".join(headers) + " |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=grid_sort_key):
        lines.append(
            "| {feature} | {min_samples} | {side_aware} | {edge} | {source} | {rows} | {win} | {rr} | {pnl} | {risk} | {avg_entry} | {avg_fair} | {brier} | {log_loss} |".format(
                feature=row["feature"],
                min_samples=row["min_samples"],
                side_aware="yes" if row["side_aware"] else "no",
                edge=format_float(row["model_edge_min"]),
                source=row["source"],
                rows=row["rows"],
                win=format_pct(row["win_rate"]),
                rr=format_float(row["rr"]),
                pnl=format_float(row["pnl"]),
                risk=format_float(row["risk"]),
                avg_entry=format_float(row["avg_entry"]),
                avg_fair=format_float(row["avg_fair"]),
                brier=format_float(row["brier"]),
                log_loss=format_float(row["log_loss"]),
            )
        )
    return "\n".join(lines)


def grid_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    rr = float_or_none(row.get("rr"))
    pnl = float_or_none(row.get("pnl"))
    return (
        0 if row.get("source") != "raw" else 1,
        -(rr if rr is not None else -999.0),
        -(pnl if pnl is not None else -999.0),
        str(row.get("feature")),
        int(row.get("min_samples") or 0),
        float(row.get("model_edge_min") or 0.0),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward calibrated candidate replay for live US high-temp consensus.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2026-05-24")
    parser.add_argument("--feature", choices=("logit", "fair"), default="logit")
    parser.add_argument("--min-samples", type=int, default=100)
    parser.add_argument("--model-edge-min", type=float, default=0.0)
    parser.add_argument("--side", action=argparse.BooleanOptionalAction, default=True, help="Fit side-aware calibrators.")
    parser.add_argument("--examples", type=int, default=8)
    parser.add_argument("--grid", action="store_true", help="Run a compact parameter grid instead of a single replay.")
    parser.add_argument("--grid-features", default="logit", help="Comma-separated feature transforms for --grid.")
    parser.add_argument("--grid-min-samples", default="100,200", help="Comma-separated min sample values for --grid.")
    parser.add_argument("--grid-edge-mins", default="0.10,0.20", help="Comma-separated calibrated model-edge floors for --grid.")
    parser.add_argument("--grid-side", choices=("both", "yes", "no"), default="yes", help="Side-awareness values for --grid.")
    args = parser.parse_args()

    rows = load_rows(args.db)
    if args.grid:
        side_values = {"both": [True, False], "yes": [True], "no": [False]}[args.grid_side]
        grid_rows = run_grid(
            rows,
            start_date=args.start_date,
            features=parse_feature_list(args.grid_features),
            min_samples_values=parse_int_list(args.grid_min_samples),
            edge_values=parse_float_list(args.grid_edge_mins),
            side_values=side_values,
        )
        print("# Calibrated Candidate Replay Grid")
        print()
        print(f"- db: {args.db}")
        print(f"- start_date: {args.start_date}")
        print(f"- base_rows: {len(rows)}")
        print()
        print(render_grid(grid_rows))
        return

    raw_rows, selected_fit_rows, candidate_fit_rows = replay(
        rows,
        start_date=args.start_date,
        feature=args.feature,
        min_samples=args.min_samples,
        include_side=args.side,
        model_edge_min=args.model_edge_min,
    )
    summaries = [
        summarize("raw live selection", raw_rows),
        summarize("calibrated from selected rows", selected_fit_rows),
        summarize("calibrated from candidate universe", candidate_fit_rows),
    ]
    print("# Calibrated Candidate Replay")
    print()
    print(f"- db: {args.db}")
    print(f"- start_date: {args.start_date}")
    print(f"- feature: {args.feature}")
    print(f"- min_samples: {args.min_samples}")
    print(f"- side_aware: {args.side}")
    print(f"- model_edge_min: {args.model_edge_min:+.3f}")
    print(f"- base_rows: {len(rows)}")
    print()
    print(render_summary(summaries))
    print()
    print(render_reliability("raw live selection", raw_rows))
    print()
    print(render_reliability("calibrated from selected rows", selected_fit_rows))
    print()
    print(render_reliability("calibrated from candidate universe", candidate_fit_rows))
    print()
    print(render_examples("Raw live selection examples", raw_rows, limit=args.examples))
    print()
    print(render_examples("Selected-row calibration examples", selected_fit_rows, limit=args.examples))
    print()
    print(render_examples("Candidate-universe calibration examples", candidate_fit_rows, limit=args.examples))


if __name__ == "__main__":
    main()
