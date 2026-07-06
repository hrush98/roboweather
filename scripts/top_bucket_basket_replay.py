#!/usr/bin/env python3
"""Replay top-N YES bucket baskets from raw prediction distributions.

This tests a dutched basket: buy YES on the N most likely mutually exclusive
temperature buckets with equal payout per bucket. With payout normalized to $1,
cost/risk is the sum of YES asks, and profit is either ``1 - ask_sum`` when the
final temperature lands in the basket or ``-ask_sum`` otherwise.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import bucket_won, return_risk, sharpe  # noqa: E402

DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
GLOBAL_LOW_STATIONS = frozenset({"EGLC", "LFPB", "RJTT", "RKSI", "VHHH", "ZSPD"})
GLOBAL_LOW_MODELS = (
    "dynamic_bucket_international_celsius_low_obs_2022_2025",
    "mvp_international_celsius_low_obs_2022_2025",
)
US_LOW_MODELS = (
    "low_dynamic_bucket_obs_2022_2025",
    "low_mvp_obs_2022_2025",
)
CONSENSUS_GROUPS = {
    "global_low_dynamic_mvp_distribution": GLOBAL_LOW_MODELS,
    "us_low_dynamic_mvp_distribution": US_LOW_MODELS,
}


@dataclass(frozen=True)
class BasketOpportunity:
    source: str
    station: str
    market_date: str
    timestamp: str
    decision_time_utc: str
    obs_delay_bucket: str
    top_n: int
    buckets: tuple[str, ...]
    ask_sum: float
    fair_mass: float
    edge: float
    hit: bool

    @property
    def pnl(self) -> float:
        return 1.0 - self.ask_sum if self.hit else -self.ask_sum


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay top-N YES bucket baskets from raw candidate distributions.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Research SQLite database.")
    parser.add_argument("--market-family", default="LOW_TEMP")
    parser.add_argument("--strategy-bucket", default="HIGH_CONVICTION")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--global-low-only", action="store_true", help="Restrict to the current global low station set.")
    parser.add_argument("--include-us-low", action="store_true", help="Include US low models in addition to global low models.")
    parser.add_argument("--allow-dead-buckets", action="store_true", help="Do not filter buckets already impossible from high/low so far.")
    parser.add_argument("--top-n", type=int, action="append", help="Top-N basket size. May be repeated.")
    parser.add_argument("--max-ask-sum", type=float, action="append", help="Ask-sum cap. May be repeated.")
    parser.add_argument("--min-edge", type=float, action="append", help="Minimum fair_mass - ask_sum. May be repeated.")
    parser.add_argument("--selector", choices=("first_eligible", "best_edge_intraday", "cheapest_intraday"), action="append")
    parser.add_argument("--min-n", type=int, default=6)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--examples", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db).expanduser()
    rows = load_snapshot_rows(
        db_path,
        market_family=args.market_family,
        strategy_bucket=args.strategy_bucket,
        start_date=args.start_date,
        end_date=args.end_date,
        global_low_only=args.global_low_only,
        include_us_low=args.include_us_low,
    )
    top_ns = args.top_n or [1, 2, 3, 4]
    max_ask_sums = args.max_ask_sum or [0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00]
    min_edges = args.min_edge or [0.00, 0.05, 0.10, 0.15]
    selectors = args.selector or ["first_eligible", "best_edge_intraday", "cheapest_intraday"]

    opportunities = build_model_opportunities(rows, top_ns=top_ns, require_alive=not args.allow_dead_buckets)
    opportunities.extend(build_consensus_opportunities(rows, top_ns=top_ns, require_alive=not args.allow_dead_buckets))
    summaries: list[dict[str, Any]] = []
    selected_by_summary: dict[tuple[str, str, int, float, float], list[BasketOpportunity]] = {}
    for selector in selectors:
        for max_ask_sum in max_ask_sums:
            for min_edge in min_edges:
                filtered = [
                    opp
                    for opp in opportunities
                    if opp.ask_sum <= max_ask_sum and opp.edge >= min_edge
                ]
                selected = select_opportunities(filtered, selector)
                grouped: dict[tuple[str, int], list[BasketOpportunity]] = defaultdict(list)
                for opp in selected:
                    grouped[(opp.source, opp.top_n)].append(opp)
                for (source, top_n), items in grouped.items():
                    summary = summarize(items)
                    if summary["n"] < args.min_n:
                        continue
                    summary.update(
                        {
                            "selector": selector,
                            "source": source,
                            "top_n": top_n,
                            "max_ask_sum": max_ask_sum,
                            "min_edge": min_edge,
                        }
                    )
                    summaries.append(summary)
                    selected_by_summary[(selector, source, top_n, max_ask_sum, min_edge)] = items

    summaries.sort(key=lambda row: (row["rr"] is None, -(row["rr"] or -999), -row["n"]))
    print(render_report(args, rows, opportunities, summaries[: args.top]))
    if summaries and args.examples:
        best = summaries[0]
        key = (
            str(best["selector"]),
            str(best["source"]),
            int(best["top_n"]),
            float(best["max_ask_sum"]),
            float(best["min_edge"]),
        )
        print(render_examples(selected_by_summary[key], limit=args.examples))


def load_snapshot_rows(
    db_path: Path,
    *,
    market_family: str,
    strategy_bucket: str,
    start_date: str | None,
    end_date: str | None,
    global_low_only: bool,
    include_us_low: bool,
) -> list[dict[str, Any]]:
    models = set(GLOBAL_LOW_MODELS)
    if include_us_low:
        models.update(US_LOW_MODELS)
    where = [
        "coalesce(ps.market_family, 'HIGH_TEMP') = ?",
        "ps.strategy_bucket = ?",
        "ps.raw_json like '%candidate_distribution%'",
    ]
    params: list[Any] = [market_family, strategy_bucket]
    if start_date:
        where.append("ps.market_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("ps.market_date <= ?")
        params.append(end_date)
    if models:
        where.append(f"ps.model_name in ({','.join('?' for _ in sorted(models))})")
        params.extend(sorted(models))
    if global_low_only:
        where.append(f"ps.station in ({','.join('?' for _ in sorted(GLOBAL_LOW_STATIONS))})")
        params.extend(sorted(GLOBAL_LOW_STATIONS))

    sql = f"""
        select
            ps.id,
            ps.timestamp,
            ps.station,
            ps.market_date,
            ps.decision_time_utc,
            ps.obs_delay_bucket,
            ps.strategy_bucket,
            ps.model_name,
            ps.raw_json,
            ps.low_so_far,
            ps.high_so_far,
            sdo.final_low_tmpf,
            sdo.final_high_tmpf
        from prediction_snapshots ps
        left join station_date_outcomes sdo
          on sdo.station = ps.station
         and sdo.market_date = ps.market_date
        where {" and ".join(where)}
          and sdo.final_low_tmpf is not null
        order by ps.timestamp, ps.id
    """
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(sql, params)]
    finally:
        db.close()


def build_model_opportunities(
    rows: Iterable[dict[str, Any]],
    *,
    top_ns: Iterable[int],
    require_alive: bool,
) -> list[BasketOpportunity]:
    opportunities: list[BasketOpportunity] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        source = str(row["model_name"])
        for top_n in top_ns:
            opp = opportunity_from_candidates(source, row, candidate_distribution(row), top_n=top_n, require_alive=require_alive)
            if opp is None:
                continue
            key = (opp.source, opp.station, opp.market_date, opp.timestamp, opp.obs_delay_bucket, opp.top_n)
            if key in seen:
                continue
            seen.add(key)
            opportunities.append(opp)
    return opportunities


def build_consensus_opportunities(
    rows: Iterable[dict[str, Any]],
    *,
    top_ns: Iterable[int],
    require_alive: bool,
) -> list[BasketOpportunity]:
    by_key: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        model_name = str(row["model_name"])
        for group_name, models in CONSENSUS_GROUPS.items():
            if model_name in models:
                key = (
                    group_name,
                    str(row["station"]),
                    str(row["market_date"]),
                    str(row["decision_time_utc"]),
                    str(row["obs_delay_bucket"]),
                )
                by_key[key][model_name] = row

    opportunities: list[BasketOpportunity] = []
    for key, by_model in by_key.items():
        group_name = key[0]
        required = CONSENSUS_GROUPS[group_name]
        if any(model_name not in by_model for model_name in required):
            continue
        rows_for_group = [by_model[model_name] for model_name in required]
        candidates = consensus_candidates(rows_for_group)
        if not candidates:
            continue
        base = max(rows_for_group, key=lambda row: str(row["timestamp"]))
        for top_n in top_ns:
            opp = opportunity_from_candidates(group_name, base, candidates, top_n=top_n, require_alive=require_alive)
            if opp is not None:
                opportunities.append(opp)
    return opportunities


def candidate_distribution(row: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(row.get("raw_json") or "{}"))
    except json.JSONDecodeError:
        return []
    candidates = payload.get("candidate_distribution")
    return candidates if isinstance(candidates, list) else []


def consensus_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for candidate in candidate_distribution(row):
            bucket = candidate.get("bucket")
            if bucket is not None:
                by_bucket[str(bucket)].append(candidate)
    output: list[dict[str, Any]] = []
    required_n = len(rows)
    for bucket, candidates in by_bucket.items():
        if len(candidates) != required_n:
            continue
        fairs = [float_or_none(candidate.get("fair_yes")) for candidate in candidates]
        asks = [float_or_none(candidate.get("yes_ask")) for candidate in candidates]
        if any(value is None for value in fairs) or any(value is None for value in asks):
            continue
        newest = candidates[-1]
        item = dict(newest)
        item["bucket"] = bucket
        item["fair_yes"] = sum(float(value) for value in fairs if value is not None) / required_n
        item["yes_ask"] = asks[-1]
        output.append(item)
    return output


def opportunity_from_candidates(
    source: str,
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    top_n: int,
    require_alive: bool,
) -> BasketOpportunity | None:
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
    top = sorted(tradable, key=lambda item: (-item[1], item[2], item[0]))[:top_n]
    buckets = tuple(item[0] for item in top)
    ask_sum = sum(item[2] for item in top)
    fair_mass = sum(item[1] for item in top)
    final_low = float_or_none(row.get("final_low_tmpf"))
    if final_low is None:
        return None
    hit = any(bucket_won(final_low, bucket) for bucket in buckets)
    return BasketOpportunity(
        source=source,
        station=str(row["station"]),
        market_date=str(row["market_date"]),
        timestamp=str(row["timestamp"]),
        decision_time_utc=str(row["decision_time_utc"]),
        obs_delay_bucket=str(row["obs_delay_bucket"]),
        top_n=top_n,
        buckets=buckets,
        ask_sum=ask_sum,
        fair_mass=fair_mass,
        edge=fair_mass - ask_sum,
        hit=hit,
    )


def select_opportunities(opportunities: Iterable[BasketOpportunity], selector: str) -> list[BasketOpportunity]:
    grouped: dict[tuple[str, str, str, int], list[BasketOpportunity]] = defaultdict(list)
    for opp in opportunities:
        grouped[(opp.source, opp.station, opp.market_date, opp.top_n)].append(opp)
    selected = []
    for items in grouped.values():
        if selector == "first_eligible":
            selected.append(min(items, key=lambda opp: (opp.timestamp, opp.decision_time_utc)))
        elif selector == "best_edge_intraday":
            selected.append(max(items, key=lambda opp: (opp.edge, -opp.ask_sum, opp.timestamp)))
        elif selector == "cheapest_intraday":
            selected.append(min(items, key=lambda opp: (opp.ask_sum, -opp.edge, opp.timestamp)))
        else:
            raise ValueError(f"unsupported selector: {selector}")
    return selected


def summarize(items: list[BasketOpportunity]) -> dict[str, Any]:
    pnls = [item.pnl for item in items]
    risk = sum(item.ask_sum for item in items)
    pnl = sum(pnls)
    return {
        "n": len(items),
        "hit_rate": sum(1 for item in items if item.hit) / len(items) if items else None,
        "risk": risk,
        "pnl": pnl,
        "rr": return_risk(pnl, risk),
        "sharpe": sharpe(pnls),
        "avg_ask_sum": mean(item.ask_sum for item in items),
        "avg_fair_mass": mean(item.fair_mass for item in items),
        "avg_edge": mean(item.edge for item in items),
    }


def render_report(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    opportunities: list[BasketOpportunity],
    summaries: list[dict[str, Any]],
) -> str:
    lines = [
        "# Top Bucket Basket Replay",
        "",
        f"- db: {Path(args.db).expanduser()}",
        f"- market_family: {args.market_family}",
        f"- strategy_bucket snapshots: {args.strategy_bucket}",
        f"- snapshot rows: {len(rows)}",
        f"- basket opportunities: {len(opportunities)}",
        f"- start_date: {args.start_date}",
        f"- end_date: {args.end_date}",
        f"- global_low_only: {args.global_low_only}",
        f"- include_us_low: {args.include_us_low}",
        f"- require_alive_buckets: {not args.allow_dead_buckets}",
        "",
        "| selector | source | top_n | max_ask | min_edge | n | hit | rr | sharpe | pnl | risk | avg ask | avg fair | avg edge |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {selector} | {source} | {top_n} | {max_ask_sum:.2f} | {min_edge:.2f} | {n} | {hit_rate} | {rr} | {sharpe} | {pnl:.3f} | {risk:.3f} | {avg_ask_sum} | {avg_fair_mass} | {avg_edge} |".format(
                **{**row, "hit_rate": fmt(row["hit_rate"]), "rr": fmt(row["rr"]), "sharpe": fmt(row["sharpe"]), "avg_ask_sum": fmt(row["avg_ask_sum"]), "avg_fair_mass": fmt(row["avg_fair_mass"]), "avg_edge": fmt(row["avg_edge"])}
            )
        )
    if not summaries:
        lines.append("")
        lines.append("No basket variants met the sample threshold.")
    return "\n".join(lines)


def render_examples(items: list[BasketOpportunity], *, limit: int) -> str:
    examples = sorted(items, key=lambda item: item.pnl)[:limit]
    lines = [
        "",
        "## Worst Examples For Top Variant",
        "",
        "| date | station | buckets | ask_sum | fair_mass | edge | hit | pnl |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in examples:
        lines.append(
            f"| {item.market_date} | {item.station} | {','.join(item.buckets)} | {item.ask_sum:.3f} | {item.fair_mass:.3f} | {item.edge:.3f} | {str(item.hit)} | {item.pnl:.3f} |"
        )
    return "\n".join(lines)


def float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def bucket_alive(row: dict[str, Any], bucket: str) -> bool:
    lower, upper = parse_bucket(bucket)
    market_family = str(row.get("market_family") or "LOW_TEMP")
    if market_family == "HIGH_TEMP":
        high_so_far = float_or_none(row.get("high_so_far"))
        if high_so_far is None:
            return True
        return upper is None or high_so_far <= upper

    low_so_far = float_or_none(row.get("low_so_far"))
    if low_so_far is None:
        return True
    return lower is None or low_so_far >= lower


def parse_bucket(bucket: str) -> tuple[float | None, float | None]:
    text = bucket.removesuffix("F")
    if text.startswith("<="):
        return None, float(text.removeprefix("<="))
    if text.startswith(">="):
        return float(text.removeprefix(">=")), None
    if "-" in text:
        lower, upper = text.split("-", 1)
        return float(lower), float(upper)
    return None, None


def mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    if not materialized:
        return None
    return sum(materialized) / len(materialized)


def fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


if __name__ == "__main__":
    main()
