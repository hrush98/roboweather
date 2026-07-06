#!/usr/bin/env python3
"""Walk-forward calibration and policy replay for top-N YES bucket clusters.

The cluster target is binary: did the final temperature land in any of the
model's top-N YES buckets at that snapshot?  This script compares raw top-N
probability mass against walk-forward calibrated cluster hit probabilities, then
replays a dutched YES basket policy using either raw or calibrated probability.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import bucket_won, return_risk, sharpe  # noqa: E402
from scripts.top_bucket_basket_replay import (  # noqa: E402
    CONSENSUS_GROUPS,
    DEFAULT_DB,
    bucket_alive,
    candidate_distribution,
    consensus_candidates,
    float_or_none,
    load_snapshot_rows,
)


@dataclass(frozen=True)
class ClusterExample:
    source: str
    station: str
    market_date: str
    timestamp: str
    decision_time_utc: str
    obs_delay_bucket: str
    top_n: int
    buckets: tuple[str, ...]
    ask_sum: float
    raw_mass: float
    gap_to_next: float
    entropy: float
    hit: bool
    variant: str = "raw"
    probability: float | None = None

    @property
    def selected_probability(self) -> float:
        return self.raw_mass if self.probability is None else self.probability

    @property
    def edge(self) -> float:
        return self.selected_probability - self.ask_sum

    @property
    def pnl(self) -> float:
        return 1.0 - self.ask_sum if self.hit else -self.ask_sum


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate and replay top-N YES bucket cluster baskets.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--market-family", default="LOW_TEMP")
    parser.add_argument("--strategy-bucket", default="HIGH_CONVICTION")
    parser.add_argument("--start-date", default="2026-06-10", help="First date for walk-forward calibration scoring.")
    parser.add_argument("--end-date")
    parser.add_argument("--global-low-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-us-low", action="store_true")
    parser.add_argument("--allow-dead-buckets", action="store_true")
    parser.add_argument("--top-n", type=int, action="append", help="Cluster size. May be repeated.")
    parser.add_argument("--min-train", type=int, default=200)
    parser.add_argument("--max-ask-sum", type=float, action="append")
    parser.add_argument("--min-edge", type=float, action="append")
    parser.add_argument("--selector", choices=("first_eligible", "best_edge_intraday", "cheapest_intraday"), action="append")
    parser.add_argument("--min-policy-n", type=int, default=6)
    parser.add_argument("--top", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_snapshot_rows(
        Path(args.db).expanduser(),
        market_family=args.market_family,
        strategy_bucket=args.strategy_bucket,
        start_date=None,
        end_date=args.end_date,
        global_low_only=args.global_low_only,
        include_us_low=args.include_us_low,
    )
    top_ns = args.top_n or [2, 3]
    clusters = build_clusters(rows, top_ns=top_ns, require_alive=not args.allow_dead_buckets)
    scored = walk_forward_score(clusters, start_date=args.start_date, min_train=args.min_train)

    max_ask_sums = args.max_ask_sum or [0.25, 0.30, 0.35, 0.40, 0.50, 0.60]
    min_edges = args.min_edge or [0.00, 0.05, 0.10, 0.15]
    selectors = args.selector or ["first_eligible", "cheapest_intraday"]
    policy_rows = policy_grid(scored, max_ask_sums=max_ask_sums, min_edges=min_edges, selectors=selectors, min_n=args.min_policy_n)

    print(render_report(args, rows, clusters, scored, policy_rows[: args.top]))


def build_clusters(
    rows: Iterable[dict[str, Any]],
    *,
    top_ns: Iterable[int],
    require_alive: bool,
) -> list[ClusterExample]:
    output: list[ClusterExample] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        source = str(row["model_name"])
        for top_n in top_ns:
            cluster = cluster_from_candidates(source, row, candidate_distribution(row), top_n=top_n, require_alive=require_alive)
            if cluster is None:
                continue
            key = (cluster.source, cluster.station, cluster.market_date, cluster.timestamp, cluster.obs_delay_bucket, cluster.top_n)
            if key in seen:
                continue
            seen.add(key)
            output.append(cluster)

    by_key: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        model_name = str(row["model_name"])
        for group_name, models in CONSENSUS_GROUPS.items():
            if model_name not in models:
                continue
            key = (
                group_name,
                str(row["station"]),
                str(row["market_date"]),
                str(row["decision_time_utc"]),
                str(row["obs_delay_bucket"]),
            )
            by_key[key][model_name] = row

    for key, by_model in by_key.items():
        group_name = key[0]
        required = CONSENSUS_GROUPS[group_name]
        if any(model_name not in by_model for model_name in required):
            continue
        rows_for_group = [by_model[model_name] for model_name in required]
        candidates = consensus_candidates(rows_for_group)
        base = max(rows_for_group, key=lambda row: str(row["timestamp"]))
        for top_n in top_ns:
            cluster = cluster_from_candidates(group_name, base, candidates, top_n=top_n, require_alive=require_alive)
            if cluster is not None:
                output.append(cluster)
    return sorted(output, key=sort_key)


def cluster_from_candidates(
    source: str,
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    top_n: int,
    require_alive: bool,
) -> ClusterExample | None:
    tradable = []
    for candidate in candidates:
        bucket = candidate.get("bucket")
        fair_yes = float_or_none(candidate.get("fair_yes"))
        yes_ask = float_or_none(candidate.get("yes_ask"))
        if bucket is None or fair_yes is None or yes_ask is None:
            continue
        if not math.isfinite(fair_yes) or not math.isfinite(yes_ask):
            continue
        if fair_yes < 0.0 or yes_ask <= 0.0 or yes_ask >= 1.0:
            continue
        if require_alive and not bucket_alive(row, str(bucket)):
            continue
        tradable.append((str(bucket), fair_yes, yes_ask))
    if len(tradable) < top_n:
        return None

    ranked = sorted(tradable, key=lambda item: (-item[1], item[2], item[0]))
    top = ranked[:top_n]
    buckets = tuple(item[0] for item in top)
    final_temp = final_temperature(row)
    if final_temp is None:
        return None
    raw_mass = clip_probability(sum(item[1] for item in top))
    next_probability = ranked[top_n][1] if len(ranked) > top_n else 0.0
    gap_to_next = max(0.0, top[-1][1] - next_probability)
    return ClusterExample(
        source=source,
        station=str(row["station"]),
        market_date=str(row["market_date"]),
        timestamp=str(row["timestamp"]),
        decision_time_utc=str(row["decision_time_utc"]),
        obs_delay_bucket=str(row["obs_delay_bucket"]),
        top_n=top_n,
        buckets=buckets,
        ask_sum=sum(item[2] for item in top),
        raw_mass=raw_mass,
        gap_to_next=gap_to_next,
        entropy=entropy(item[1] for item in top),
        hit=any(bucket_won(final_temp, bucket) for bucket in buckets),
    )


def final_temperature(row: dict[str, Any]) -> float | None:
    market_family = str(row.get("market_family") or "LOW_TEMP")
    if market_family == "HIGH_TEMP":
        return float_or_none(row.get("final_high_tmpf"))
    return float_or_none(row.get("final_low_tmpf"))


def entropy(values: Iterable[float]) -> float:
    items = [max(0.0, float(value)) for value in values]
    total = sum(items)
    if total <= 0.0:
        return 0.0
    output = 0.0
    for value in items:
        probability = value / total
        if probability > 0.0:
            output -= probability * math.log(probability)
    return output


def walk_forward_score(clusters: list[ClusterExample], *, start_date: str, min_train: int) -> list[ClusterExample]:
    dates = sorted({cluster.market_date for cluster in clusters if cluster.market_date >= start_date})
    scored: list[ClusterExample] = []
    for market_date in dates:
        train = [cluster for cluster in clusters if cluster.market_date < market_date]
        day = [cluster for cluster in clusters if cluster.market_date == market_date]
        fits = fit_calibrators(train, min_train=min_train)
        for cluster in day:
            scored.append(replace(cluster, variant="raw", probability=cluster.raw_mass))
            for variant in ("platt_mass", "context"):
                fit = fits.get((variant, cluster.source, cluster.top_n))
                if fit is None:
                    continue
                probability = predict_fit(fit, cluster, variant)
                scored.append(replace(cluster, variant=variant, probability=probability))
    return scored


def fit_calibrators(clusters: list[ClusterExample], *, min_train: int) -> dict[tuple[str, str, int], Any]:
    grouped: dict[tuple[str, int], list[ClusterExample]] = defaultdict(list)
    for cluster in clusters:
        grouped[(cluster.source, cluster.top_n)].append(cluster)

    fits: dict[tuple[str, str, int], Any] = {}
    for (source, top_n), items in grouped.items():
        if len(items) < min_train or len({item.hit for item in items}) < 2:
            continue
        y = np.array([1 if item.hit else 0 for item in items])

        x_platt = np.array([[logit(item.raw_mass)] for item in items], dtype=float)
        platt = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        platt.fit(x_platt, y)
        fits[("platt_mass", source, top_n)] = platt

        x_context = np.array([context_features(item) for item in items], dtype=float)
        context = make_pipeline(StandardScaler(), LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000))
        context.fit(x_context, y)
        fits[("context", source, top_n)] = context
    return fits


def predict_fit(fit: Any, cluster: ClusterExample, variant: str) -> float:
    if variant == "platt_mass":
        features = np.array([[logit(cluster.raw_mass)]], dtype=float)
    elif variant == "context":
        features = np.array([context_features(cluster)], dtype=float)
    else:
        raise ValueError(f"unsupported variant: {variant}")
    return clip_probability(float(fit.predict_proba(features)[0][1]))


def context_features(cluster: ClusterExample) -> list[float]:
    return [
        logit(cluster.raw_mass),
        cluster.ask_sum,
        cluster.raw_mass - cluster.ask_sum,
        cluster.gap_to_next,
        cluster.entropy,
    ]


def policy_grid(
    clusters: list[ClusterExample],
    *,
    max_ask_sums: Iterable[float],
    min_edges: Iterable[float],
    selectors: Iterable[str],
    min_n: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for selector in selectors:
        for max_ask_sum in max_ask_sums:
            for min_edge in min_edges:
                eligible = [
                    cluster
                    for cluster in clusters
                    if cluster.ask_sum <= max_ask_sum and cluster.edge >= min_edge
                ]
                selected = select_clusters(eligible, selector)
                grouped: dict[tuple[str, str, int], list[ClusterExample]] = defaultdict(list)
                for cluster in selected:
                    grouped[(cluster.variant, cluster.source, cluster.top_n)].append(cluster)
                for (variant, source, top_n), items in grouped.items():
                    if len(items) < min_n:
                        continue
                    row = summarize_policy(items)
                    row.update(
                        {
                            "selector": selector,
                            "variant": variant,
                            "source": source,
                            "top_n": top_n,
                            "max_ask_sum": max_ask_sum,
                            "min_edge": min_edge,
                        }
                    )
                    rows.append(row)
    rows.sort(key=lambda row: (row["rr"] is None, -(row["rr"] or -999.0), -row["n"]))
    return rows


def select_clusters(clusters: Iterable[ClusterExample], selector: str) -> list[ClusterExample]:
    grouped: dict[tuple[str, str, str, str, int], list[ClusterExample]] = defaultdict(list)
    for cluster in clusters:
        grouped[(cluster.variant, cluster.source, cluster.station, cluster.market_date, cluster.top_n)].append(cluster)
    selected = []
    for items in grouped.values():
        if selector == "first_eligible":
            selected.append(min(items, key=lambda item: (item.timestamp, item.decision_time_utc)))
        elif selector == "best_edge_intraday":
            selected.append(max(items, key=lambda item: (item.edge, -item.ask_sum, item.timestamp)))
        elif selector == "cheapest_intraday":
            selected.append(min(items, key=lambda item: (item.ask_sum, -item.edge, item.timestamp)))
        else:
            raise ValueError(f"unsupported selector: {selector}")
    return selected


def summarize_probability(clusters: list[ClusterExample]) -> dict[str, Any]:
    return {
        "n": len(clusters),
        "hit_rate": mean(1.0 if item.hit else 0.0 for item in clusters),
        "avg_probability": mean(item.selected_probability for item in clusters),
        "brier": mean((item.selected_probability - (1.0 if item.hit else 0.0)) ** 2 for item in clusters),
        "log_loss": mean(binary_log_loss(item.selected_probability, item.hit) for item in clusters),
        "avg_ask_sum": mean(item.ask_sum for item in clusters),
        "avg_edge": mean(item.edge for item in clusters),
    }


def summarize_policy(clusters: list[ClusterExample]) -> dict[str, Any]:
    risk = sum(item.ask_sum for item in clusters)
    pnl = sum(item.pnl for item in clusters)
    return {
        "n": len(clusters),
        "hit_rate": mean(1.0 if item.hit else 0.0 for item in clusters),
        "rr": return_risk(pnl, risk),
        "sharpe": sharpe([item.pnl for item in clusters]),
        "pnl": pnl,
        "risk": risk,
        "avg_ask_sum": mean(item.ask_sum for item in clusters),
        "avg_probability": mean(item.selected_probability for item in clusters),
        "avg_edge": mean(item.edge for item in clusters),
    }


def render_report(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    clusters: list[ClusterExample],
    scored: list[ClusterExample],
    policy_rows: list[dict[str, Any]],
) -> str:
    lines = [
        "# Top Bucket Cluster Calibration Replay",
        "",
        f"- db: {Path(args.db).expanduser()}",
        f"- market_family: {args.market_family}",
        f"- strategy_bucket: {args.strategy_bucket}",
        f"- start_date: {args.start_date}",
        f"- end_date: {args.end_date}",
        f"- global_low_only: {args.global_low_only}",
        f"- include_us_low: {args.include_us_low}",
        f"- require_alive_buckets: {not args.allow_dead_buckets}",
        f"- snapshot_rows: {len(rows)}",
        f"- cluster_examples: {len(clusters)}",
        f"- scored_examples: {len(scored)}",
        "",
        "## Baseline Raw Cluster Calibration",
        "",
    ]
    lines.extend(render_probability_table(raw_probability_rows(clusters)))
    lines.extend(
        [
            "",
            "## Walk-Forward Probability Calibration",
            "",
        ]
    )
    lines.extend(render_probability_table(walk_forward_probability_rows(scored)))
    lines.extend(
        [
            "",
            "## Policy Replay",
            "",
            "| selector | variant | source | top_n | max_ask | min_edge | n | hit | rr | sharpe | pnl | risk | avg ask | avg prob | avg edge |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in policy_rows:
        lines.append(
            "| {selector} | {variant} | {source} | {top_n} | {max_ask_sum:.2f} | {min_edge:.2f} | {n} | {hit_rate} | {rr} | {sharpe} | {pnl:.3f} | {risk:.3f} | {avg_ask_sum} | {avg_probability} | {avg_edge} |".format(
                **{
                    **row,
                    "hit_rate": fmt(row["hit_rate"]),
                    "rr": fmt(row["rr"]),
                    "sharpe": fmt(row["sharpe"]),
                    "avg_ask_sum": fmt(row["avg_ask_sum"]),
                    "avg_probability": fmt(row["avg_probability"]),
                    "avg_edge": fmt(row["avg_edge"]),
                }
            )
        )
    if not policy_rows:
        lines.append("")
        lines.append("No policy variants met the sample threshold.")
    return "\n".join(lines)


def raw_probability_rows(clusters: list[ClusterExample]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], list[ClusterExample]] = defaultdict(list)
    for cluster in clusters:
        grouped[(cluster.source, cluster.top_n)].append(cluster)
    rows = []
    for (source, top_n), items in sorted(grouped.items()):
        row = summarize_probability([replace(item, variant="raw", probability=item.raw_mass) for item in items])
        row.update({"variant": "raw", "source": source, "top_n": top_n})
        rows.append(row)
    return rows


def walk_forward_probability_rows(scored: list[ClusterExample]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[ClusterExample]] = defaultdict(list)
    for cluster in scored:
        grouped[(cluster.variant, cluster.source, cluster.top_n)].append(cluster)
    rows = []
    for (variant, source, top_n), items in sorted(grouped.items()):
        row = summarize_probability(items)
        row.update({"variant": variant, "source": source, "top_n": top_n})
        rows.append(row)
    rows.sort(key=lambda row: (row["source"], row["top_n"], row["variant"]))
    return rows


def render_probability_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| variant | source | top_n | n | hit | avg_prob | brier | logloss | avg ask | avg edge |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {variant} | {source} | {top_n} | {n} | {hit_rate} | {avg_probability} | {brier} | {log_loss} | {avg_ask_sum} | {avg_edge} |".format(
                **{
                    **row,
                    "hit_rate": fmt(row["hit_rate"]),
                    "avg_probability": fmt(row["avg_probability"]),
                    "brier": fmt(row["brier"]),
                    "log_loss": fmt(row["log_loss"]),
                    "avg_ask_sum": fmt(row["avg_ask_sum"]),
                    "avg_edge": fmt(row["avg_edge"]),
                }
            )
        )
    return lines


def sort_key(cluster: ClusterExample) -> tuple[str, str, str, str, str, int]:
    return (cluster.market_date, cluster.timestamp, cluster.station, cluster.source, cluster.obs_delay_bucket, cluster.top_n)


def logit(probability: float) -> float:
    probability = clip_probability(probability)
    return math.log(probability / (1.0 - probability))


def clip_probability(probability: float) -> float:
    return min(0.999, max(0.001, float(probability)))


def binary_log_loss(probability: float, target: bool) -> float:
    probability = clip_probability(probability)
    return -math.log(probability if target else 1.0 - probability)


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


if __name__ == "__main__":
    main()
