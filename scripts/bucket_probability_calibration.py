#!/usr/bin/env python3
"""Walk-forward calibration diagnostics for bucket YES probabilities.

This script evaluates probability calibration directly, independent of order
execution. It calibrates the model's per-bucket YES probability from raw
candidate distributions and reports binary bucket reliability plus multiclass
distribution metrics.
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

from scripts.policy_leaderboard import bucket_won  # noqa: E402

DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025"
CATBOOST_MODEL = "catboost_bucket_pm_active_us12_obs_2022_2025"
LIVE_MODELS = (DYNAMIC_TUNED_MODEL, CATBOOST_MODEL)
TEMPERATURE_GRID = tuple(float(value) for value in np.geomspace(0.25, 8.0, 80))


@dataclass(frozen=True)
class BucketExample:
    model_name: str
    station: str
    market_date: str
    snapshot_id: int
    bucket: str
    raw_probability: float
    target: int


@dataclass(frozen=True)
class SnapshotDistribution:
    model_name: str
    station: str
    market_date: str
    snapshot_id: int
    probabilities: tuple[float, ...]
    target_index: int


@dataclass(frozen=True)
class PlattFit:
    intercept: float
    coef: float
    feature: str
    n: int

    def predict(self, probability: float) -> float:
        x = logit(probability) if self.feature == "logit" else probability
        return sigmoid(self.intercept + self.coef * x)


def sigmoid(value: float) -> float:
    if value >= 0.0:
        exp_value = math.exp(-value)
        return 1.0 / (1.0 + exp_value)
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def logit(probability: float) -> float:
    probability = clip_probability(probability)
    return math.log(probability / (1.0 - probability))


def clip_probability(probability: float) -> float:
    return min(0.999, max(0.001, probability))


def load_examples(db_path: Path) -> tuple[list[BucketExample], list[SnapshotDistribution]]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            select
                ps.id,
                ps.model_name,
                ps.station,
                ps.market_date,
                ps.raw_json,
                sdo.final_high_tmpf
            from prediction_snapshots ps
            join station_date_outcomes sdo
              on sdo.station = ps.station
             and sdo.market_date = ps.market_date
            where ps.model_name in (?, ?)
              and coalesce(ps.market_family, 'HIGH_TEMP') = 'HIGH_TEMP'
              and ps.strategy_bucket = 'HIGH_CONVICTION'
              and ps.station like 'K%'
              and sdo.final_high_tmpf is not null
            order by ps.market_date, ps.timestamp, ps.id
            """,
            LIVE_MODELS,
        ).fetchall()
    finally:
        db.close()

    examples: list[BucketExample] = []
    snapshots: list[SnapshotDistribution] = []
    for row in rows:
        candidates = candidate_distribution(str(row["raw_json"] or "{}"))
        if not candidates:
            continue
        snapshot_examples: list[BucketExample] = []
        probabilities: list[float] = []
        target_indices: list[int] = []
        for candidate in candidates:
            bucket = candidate.get("bucket")
            probability = candidate.get("fair_yes")
            if bucket is None or probability is None:
                continue
            probability_value = float(probability)
            if not math.isfinite(probability_value) or probability_value < 0.0:
                continue
            yes_won = bucket_won(float(row["final_high_tmpf"]), str(bucket))
            target = 1 if yes_won else 0
            if target:
                target_indices.append(len(probabilities))
            probabilities.append(max(0.0, probability_value))
            snapshot_examples.append(
                BucketExample(
                    model_name=str(row["model_name"]),
                    station=str(row["station"]),
                    market_date=str(row["market_date"]),
                    snapshot_id=int(row["id"]),
                    bucket=str(bucket),
                    raw_probability=max(0.0, probability_value),
                    target=target,
                )
            )
        examples.extend(snapshot_examples)
        if len(target_indices) == 1 and probabilities:
            snapshots.append(
                SnapshotDistribution(
                    model_name=str(row["model_name"]),
                    station=str(row["station"]),
                    market_date=str(row["market_date"]),
                    snapshot_id=int(row["id"]),
                    probabilities=tuple(probabilities),
                    target_index=target_indices[0],
                )
            )
    return examples, snapshots


def candidate_distribution(raw_json: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    candidates = raw.get("candidate_distribution")
    return candidates if isinstance(candidates, list) else []


def fit_platt(
    examples: Iterable[BucketExample],
    *,
    feature: str,
    min_samples: int,
) -> dict[tuple[str, str], PlattFit]:
    grouped: dict[tuple[str, str], list[BucketExample]] = defaultdict(list)
    for example in examples:
        grouped[(example.model_name, example.station)].append(example)
        grouped[(example.model_name, "*")].append(example)

    fits: dict[tuple[str, str], PlattFit] = {}
    for key, group in grouped.items():
        if len(group) < min_samples:
            continue
        y = np.array([example.target for example in group])
        if len(set(y.tolist())) < 2:
            continue
        if feature == "logit":
            x = np.array([[logit(example.raw_probability)] for example in group])
        elif feature == "fair":
            x = np.array([[clip_probability(example.raw_probability)] for example in group])
        else:
            raise ValueError(f"unsupported feature: {feature}")
        model = LogisticRegression(C=1e10, solver="lbfgs", max_iter=1000)
        model.fit(x, y)
        fits[key] = PlattFit(float(model.intercept_[0]), float(model.coef_[0][0]), feature, len(group))
    return fits


def lookup_platt(fits: dict[tuple[str, str], PlattFit], example: BucketExample) -> PlattFit | None:
    return fits.get((example.model_name, example.station)) or fits.get((example.model_name, "*"))


def fit_temperatures(
    snapshots: Iterable[SnapshotDistribution],
    *,
    min_snapshots: int,
) -> dict[tuple[str, str], float]:
    grouped: dict[tuple[str, str], list[SnapshotDistribution]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[(snapshot.model_name, snapshot.station)].append(snapshot)
        grouped[(snapshot.model_name, "*")].append(snapshot)

    temperatures: dict[tuple[str, str], float] = {}
    for key, group in grouped.items():
        if len(group) < min_snapshots:
            continue
        best_temperature = min(TEMPERATURE_GRID, key=lambda value: multiclass_log_loss_for_temperature(group, value))
        temperatures[key] = float(best_temperature)
    return temperatures


def lookup_temperature(temperatures: dict[tuple[str, str], float], snapshot: SnapshotDistribution) -> float:
    return temperatures.get((snapshot.model_name, snapshot.station)) or temperatures.get((snapshot.model_name, "*")) or 1.0


def distribution_with_temperature(probabilities: Iterable[float], temperature: float) -> list[float]:
    raw = np.array([clip_probability(float(value)) for value in probabilities], dtype=float)
    logits = np.log(raw) / max(0.001, temperature)
    logits = logits - np.max(logits)
    exp_values = np.exp(logits)
    total = float(np.sum(exp_values))
    if total <= 0.0:
        return [1.0 / len(raw)] * len(raw)
    return [float(value / total) for value in exp_values]


def normalize(probabilities: Iterable[float]) -> list[float]:
    values = [max(0.0, float(value)) for value in probabilities]
    total = sum(values)
    if total <= 0.0:
        return [1.0 / len(values)] * len(values) if values else []
    return [value / total for value in values]


def multiclass_log_loss_for_temperature(snapshots: Iterable[SnapshotDistribution], temperature: float) -> float:
    losses = []
    for snapshot in snapshots:
        distribution = distribution_with_temperature(snapshot.probabilities, temperature)
        losses.append(-math.log(clip_probability(distribution[snapshot.target_index])))
    return sum(losses) / len(losses) if losses else float("inf")


def walk_forward(
    examples: list[BucketExample],
    snapshots: list[SnapshotDistribution],
    *,
    start_date: str,
    feature: str,
    min_samples: int,
    min_temperature_snapshots: int,
) -> dict[str, Any]:
    dates = sorted({example.market_date for example in examples if example.market_date >= start_date})
    binary_rows: list[dict[str, Any]] = []
    multiclass_rows: list[dict[str, Any]] = []
    examples_by_date = group_by_date(examples)
    snapshots_by_date = group_by_date(snapshots)
    for market_date in dates:
        train_examples = [example for example in examples if example.market_date < market_date]
        train_snapshots = [snapshot for snapshot in snapshots if snapshot.market_date < market_date]
        day_examples = examples_by_date.get(market_date, [])
        day_snapshots = snapshots_by_date.get(market_date, [])
        platt_fits = fit_platt(train_examples, feature=feature, min_samples=min_samples)
        temperatures = fit_temperatures(train_snapshots, min_snapshots=min_temperature_snapshots)
        binary_rows.extend(score_binary_examples(day_examples, platt_fits))
        multiclass_rows.extend(score_multiclass_snapshots(day_snapshots, platt_fits, temperatures))
    return {"binary": binary_rows, "multiclass": multiclass_rows}


def group_by_date(items: Iterable[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for item in items:
        grouped[item.market_date].append(item)
    return grouped


def score_binary_examples(examples: Iterable[BucketExample], platt_fits: dict[tuple[str, str], PlattFit]) -> list[dict[str, Any]]:
    rows = []
    for example in examples:
        fit = lookup_platt(platt_fits, example)
        platt_probability = fit.predict(example.raw_probability) if fit is not None else None
        rows.append(
            {
                "model_name": example.model_name,
                "station": example.station,
                "market_date": example.market_date,
                "bucket": example.bucket,
                "target": example.target,
                "raw": clip_probability(example.raw_probability),
                "platt": platt_probability,
            }
        )
    return rows


def score_multiclass_snapshots(
    snapshots: Iterable[SnapshotDistribution],
    platt_fits: dict[tuple[str, str], PlattFit],
    temperatures: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    rows = []
    for snapshot in snapshots:
        raw_distribution = normalize(snapshot.probabilities)
        platt_values = []
        for probability in snapshot.probabilities:
            example = BucketExample(snapshot.model_name, snapshot.station, snapshot.market_date, snapshot.snapshot_id, "", probability, 0)
            fit = lookup_platt(platt_fits, example)
            platt_values.append(fit.predict(probability) if fit is not None else clip_probability(probability))
        platt_distribution = normalize(platt_values)
        temperature_distribution = distribution_with_temperature(snapshot.probabilities, lookup_temperature(temperatures, snapshot))
        rows.append(
            {
                "model_name": snapshot.model_name,
                "station": snapshot.station,
                "market_date": snapshot.market_date,
                "target_index": snapshot.target_index,
                "raw": raw_distribution[snapshot.target_index],
                "platt_norm": platt_distribution[snapshot.target_index],
                "temperature": temperature_distribution[snapshot.target_index],
                "raw_top_hit": int(max(range(len(raw_distribution)), key=lambda i: raw_distribution[i]) == snapshot.target_index),
                "platt_top_hit": int(max(range(len(platt_distribution)), key=lambda i: platt_distribution[i]) == snapshot.target_index),
                "temperature_top_hit": int(max(range(len(temperature_distribution)), key=lambda i: temperature_distribution[i]) == snapshot.target_index),
                "raw_mass": sum(snapshot.probabilities),
                "platt_mass": sum(platt_values),
            }
        )
    return rows


def summarize_binary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        binary_summary("raw", rows, "raw"),
        binary_summary("platt", [row for row in rows if row.get("platt") is not None], "platt"),
    ]


def binary_summary(label: str, rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    return {
        "variant": label,
        "rows": len(rows),
        "positives": sum(int(row["target"]) for row in rows),
        "base_rate": mean(float(row["target"]) for row in rows),
        "avg_probability": mean(float(row[probability_key]) for row in rows if row.get(probability_key) is not None),
        "brier": mean((float(row[probability_key]) - float(row["target"])) ** 2 for row in rows if row.get(probability_key) is not None),
        "log_loss": mean(binary_log_loss(float(row[probability_key]), int(row["target"])) for row in rows if row.get(probability_key) is not None),
    }


def summarize_multiclass(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        multiclass_summary("raw_norm", rows, "raw", "raw_top_hit"),
        multiclass_summary("platt_norm", rows, "platt_norm", "platt_top_hit"),
        multiclass_summary("temperature", rows, "temperature", "temperature_top_hit"),
    ]


def multiclass_summary(label: str, rows: list[dict[str, Any]], probability_key: str, top_hit_key: str) -> dict[str, Any]:
    return {
        "variant": label,
        "snapshots": len(rows),
        "top_hit": mean(float(row[top_hit_key]) for row in rows),
        "target_probability": mean(float(row[probability_key]) for row in rows),
        "log_loss": mean(-math.log(clip_probability(float(row[probability_key]))) for row in rows),
        "raw_mass": mean(float(row["raw_mass"]) for row in rows),
        "platt_mass": mean(float(row["platt_mass"]) for row in rows),
    }


def binary_log_loss(probability: float, target: int) -> float:
    probability = clip_probability(probability)
    return -(target * math.log(probability) + (1 - target) * math.log(1.0 - probability))


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def reliability_rows(rows: list[dict[str, Any]], probability_key: str) -> list[dict[str, Any]]:
    bins = (
        (0.0, 0.1, "0.00-0.10"),
        (0.1, 0.2, "0.10-0.20"),
        (0.2, 0.4, "0.20-0.40"),
        (0.4, 0.6, "0.40-0.60"),
        (0.6, 0.8, "0.60-0.80"),
        (0.8, 1.000001, "0.80-1.00"),
    )
    output = []
    scored = [row for row in rows if row.get(probability_key) is not None]
    for low, high, label in bins:
        band = [row for row in scored if low <= float(row[probability_key]) < high]
        output.append(
            {
                "band": label,
                "rows": len(band),
                "win_rate": mean(float(row["target"]) for row in band),
                "avg_probability": mean(float(row[probability_key]) for row in band),
                "brier": mean((float(row[probability_key]) - float(row["target"])) ** 2 for row in band),
            }
        )
    return output


def render_binary_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| variant | rows | positives | base_rate | avg_prob | brier | logloss |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {rows} | {positives} | {base_rate} | {avg_probability} | {brier} | {log_loss} |".format(
                variant=row["variant"],
                rows=row["rows"],
                positives=row["positives"],
                base_rate=format_pct(row["base_rate"]),
                avg_probability=format_float(row["avg_probability"]),
                brier=format_float(row["brier"]),
                log_loss=format_float(row["log_loss"]),
            )
        )
    return "\n".join(lines)


def render_multiclass_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| variant | snapshots | top_hit | target_prob | logloss | raw_mass | platt_mass |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {snapshots} | {top_hit} | {target_probability} | {log_loss} | {raw_mass} | {platt_mass} |".format(
                variant=row["variant"],
                snapshots=row["snapshots"],
                top_hit=format_pct(row["top_hit"]),
                target_probability=format_float(row["target_probability"]),
                log_loss=format_float(row["log_loss"]),
                raw_mass=format_float(row["raw_mass"]),
                platt_mass=format_float(row["platt_mass"]),
            )
        )
    return "\n".join(lines)


def render_reliability(title: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"### {title}",
        "",
        "| prob band | rows | observed | avg_prob | brier |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {band} | {rows} | {win_rate} | {avg_probability} | {brier} |".format(
                band=row["band"],
                rows=row["rows"],
                win_rate=format_pct(row["win_rate"]),
                avg_probability=format_float(row["avg_probability"]),
                brier=format_float(row["brier"]),
            )
        )
    return "\n".join(lines)


def format_float(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1%}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward bucket YES probability calibration diagnostics.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2026-05-24")
    parser.add_argument("--feature", choices=("logit", "fair"), default="logit")
    parser.add_argument("--min-samples", type=int, default=500)
    parser.add_argument("--min-temperature-snapshots", type=int, default=50)
    args = parser.parse_args()

    examples, snapshots = load_examples(args.db)
    result = walk_forward(
        examples,
        snapshots,
        start_date=args.start_date,
        feature=args.feature,
        min_samples=args.min_samples,
        min_temperature_snapshots=args.min_temperature_snapshots,
    )
    print("# Bucket Probability Calibration")
    print()
    print(f"- db: {args.db}")
    print(f"- start_date: {args.start_date}")
    print(f"- feature: {args.feature}")
    print(f"- min_samples: {args.min_samples}")
    print(f"- min_temperature_snapshots: {args.min_temperature_snapshots}")
    print(f"- bucket_examples: {len(examples)}")
    print(f"- multiclass_snapshots: {len(snapshots)}")
    print()
    print("## Binary Bucket YES Calibration")
    print()
    print(render_binary_summary(summarize_binary(result["binary"])))
    print()
    print(render_reliability("Raw bucket YES reliability", reliability_rows(result["binary"], "raw")))
    print()
    print(render_reliability("Platt bucket YES reliability", reliability_rows(result["binary"], "platt")))
    print()
    print("## Multiclass Distribution Calibration")
    print()
    print(render_multiclass_summary(summarize_multiclass(result["multiclass"])))


if __name__ == "__main__":
    main()
