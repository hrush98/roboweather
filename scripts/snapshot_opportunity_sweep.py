#!/usr/bin/env python3
"""Explore prediction snapshot opportunities without writing policy rows."""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import bucket_type, edge_band, entry_band, probability_band, return_risk, sharpe
from weather_trader.research.policies import CONSENSUS_GROUPS, CONSENSUS_GROUPS_BY_MODEL

ACTIVE_LOCAL_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"

ENTRY_FIELDS = (
    "selected_yes_ask",
    "selected_no_ask",
    "entry_price",
)

LIQUIDITY_FIELDS = (
    "selected_best_bid",
    "selected_best_ask",
    "selected_spread",
    "selected_depth_at_ask",
    "selected_depth_ask_plus_0_01",
    "selected_depth_ask_plus_0_03",
    "selected_depth_ask_plus_0_05",
    "selected_book_timestamp",
    "selected_book_age_seconds",
    "selected_sweep_price_cap",
    "selected_sweep_depth_to_cap",
    "selected_sweep_fillable_25_usd",
    "selected_sweep_fillable_50_usd",
    "selected_sweep_fillable_100_usd",
    "selected_sweep_vwap_25",
    "selected_sweep_vwap_50",
    "selected_sweep_vwap_100",
)

MODE_ORDER = (
    "all_snapshots",
    "first_opportunity",
    "station_date_first",
    "edge_improve_50",
    "best_edge",
    "best_liquidity",
    "hindsight_best",
)


@dataclass(frozen=True)
class ModeResult:
    name: str
    rows: list[dict[str, Any]]
    hindsight_only: bool = False


def default_db_path() -> Path:
    explicit = os.environ.get("ROBOWEATHER_STATUS_DB") or os.environ.get("DB")
    if explicit:
        return Path(explicit).expanduser()
    if ACTIVE_LOCAL_DB.exists():
        return ACTIVE_LOCAL_DB
    return REPO_ROOT / "data/paper/research_2026-05-08_multimodel.sqlite"


def load_snapshot_rows(
    db: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    market_family: str | None = None,
) -> list[dict[str, Any]]:
    where = ["ps.selected_side != 'SKIP'", "ps.selected_market_id is not null"]
    params: list[Any] = []
    if start_date:
        where.append("ps.market_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("ps.market_date <= ?")
        params.append(end_date)
    if market_family:
        where.append("coalesce(ps.market_family, 'HIGH_TEMP') = ?")
        params.append(market_family)

    sql = f"""
        select
            ps.id,
            ps.timestamp,
            ps.station,
            ps.market_date,
            ps.decision_time_utc,
            ps.decision_time_local,
            ps.latest_obs_time_utc,
            ps.latest_obs_time_local,
            ps.obs_age_minutes,
            ps.obs_delay_bucket,
            ps.strategy_bucket,
            ps.selected_market_id,
            ps.selected_bucket,
            ps.selected_side,
            coalesce(pr.edge, ps.selected_edge) as selected_edge,
            ps.selected_fair_yes,
            ps.selected_fair_no,
            ps.selected_yes_ask,
            ps.selected_no_ask,
            ps.model_name,
            coalesce(ps.market_family, 'HIGH_TEMP') as market_family,
            ps.selected_best_bid,
            ps.selected_best_ask,
            ps.selected_spread,
            ps.selected_depth_at_ask,
            ps.selected_depth_ask_plus_0_01,
            ps.selected_depth_ask_plus_0_03,
            ps.selected_depth_ask_plus_0_05,
            ps.selected_book_timestamp,
            ps.selected_book_age_seconds,
            ps.selected_sweep_price_cap,
            ps.selected_sweep_depth_to_cap,
            ps.selected_sweep_fillable_25_usd,
            ps.selected_sweep_fillable_50_usd,
            ps.selected_sweep_fillable_100_usd,
            ps.selected_sweep_vwap_25,
            ps.selected_sweep_vwap_50,
            ps.selected_sweep_vwap_100,
            pr.correct,
            pr.entry_price,
            pr.paper_pnl,
            pr.resolved_at
        from prediction_snapshots ps
        left join prediction_results pr on pr.prediction_snapshot_id = ps.id
        where {" and ".join(where)}
        order by ps.timestamp, ps.id
    """
    rows = []
    for row in db.execute(sql, params).fetchall():
        item = dict(row)
        item["source"] = str(item.get("model_name") or "missing_model")
        item["selected_fair"] = selected_fair(item)
        item["entry_price"] = entry_price(item)
        rows.append(item)
    return rows


def build_consensus_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for item in rows:
        model_name = str(item.get("model_name") or "")
        group_names = CONSENSUS_GROUPS_BY_MODEL.get(model_name)
        if not group_names:
            continue
        for group_name in group_names:
            key = consensus_key(group_name, item)
            by_key.setdefault(key, {})[model_name] = item

    consensus: list[dict[str, Any]] = []
    for key, by_model in by_key.items():
        group_name = str(key[0])
        required_models = CONSENSUS_GROUPS[group_name]
        participants = [by_model.get(model_name) for model_name in required_models]
        if any(item is None for item in participants):
            continue
        agreed = [item for item in participants if item is not None]
        newest = max(agreed, key=sort_key)
        base = min(agreed, key=lambda item: int(item["id"]))
        item = dict(base)
        for field in (*ENTRY_FIELDS, *LIQUIDITY_FIELDS):
            item[field] = newest.get(field)
        item["id"] = min(int(row["id"]) for row in agreed)
        item["timestamp"] = max(str(row.get("timestamp") or "") for row in agreed)
        item["model_name"] = group_name
        item["source"] = f"consensus:{group_name}"
        item["selected_edge"] = mean(float_or_none(row.get("selected_edge")) for row in agreed)
        item["selected_fair"] = mean(selected_fair(row) for row in agreed)
        item["paper_pnl"] = paper_pnl_from_entry(item.get("correct"), entry_price(item))
        item["source_prediction_snapshot_ids"] = [int(row["id"]) for row in agreed]
        item["consensus_models"] = list(required_models)
        consensus.append(item)
    return sorted(consensus, key=sort_key)


def consensus_key(group_name: str, item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        group_name,
        item.get("market_family") or "HIGH_TEMP",
        item.get("station"),
        item.get("market_date"),
        item.get("obs_delay_bucket"),
        item.get("strategy_bucket"),
        item.get("selected_side"),
        item.get("selected_market_id"),
        item.get("selected_bucket"),
    )


def selected_fair(item: dict[str, Any]) -> float | None:
    consensus_fair = float_or_none(item.get("selected_fair"))
    if consensus_fair is not None and str(item.get("source") or "").startswith("consensus:"):
        return consensus_fair
    if item.get("selected_side") == "BUY_YES":
        return float_or_none(item.get("selected_fair_yes"))
    if item.get("selected_side") == "BUY_NO":
        return float_or_none(item.get("selected_fair_no"))
    return float_or_none(item.get("selected_fair"))


def entry_price(item: dict[str, Any]) -> float | None:
    result_entry = float_or_none(item.get("entry_price"))
    if result_entry is not None:
        return result_entry
    if item.get("selected_side") == "BUY_YES":
        return float_or_none(item.get("selected_yes_ask"))
    if item.get("selected_side") == "BUY_NO":
        return float_or_none(item.get("selected_no_ask"))
    return None


def paper_pnl_from_entry(correct: Any, entry: float | None) -> float | None:
    if correct is None or entry is None:
        return None
    return (1.0 - entry) if int(correct) else -entry


def apply_modes(rows: list[dict[str, Any]]) -> dict[str, ModeResult]:
    return {
        "all_snapshots": ModeResult("all_snapshots", sorted(rows, key=sort_key)),
        "first_opportunity": ModeResult("first_opportunity", first_by_key(rows, opportunity_key)),
        "station_date_first": ModeResult("station_date_first", first_by_key(rows, station_date_key)),
        "edge_improve_50": ModeResult("edge_improve_50", edge_improve_rows(rows)),
        "best_edge": ModeResult("best_edge", best_by_key(rows, opportunity_key, best_edge_key)),
        "best_liquidity": ModeResult("best_liquidity", best_by_key(rows, opportunity_key, best_liquidity_key)),
        "hindsight_best": ModeResult("hindsight_best", best_by_key(rows, opportunity_key, hindsight_key), True),
    }


def opportunity_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("source"),
        item.get("strategy_bucket"),
        item.get("station"),
        item.get("market_date"),
        item.get("market_family") or "HIGH_TEMP",
        item.get("selected_bucket"),
        item.get("selected_side"),
        item.get("obs_delay_bucket"),
    )


def station_date_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("source"),
        item.get("strategy_bucket"),
        item.get("station"),
        item.get("market_date"),
        item.get("market_family") or "HIGH_TEMP",
    )


def sort_key(item: dict[str, Any]) -> tuple[str, int]:
    return (str(item.get("timestamp") or ""), int(item.get("id") or 0))


def first_by_key(rows: list[dict[str, Any]], key_func: Callable[[dict[str, Any]], tuple[Any, ...]]) -> list[dict[str, Any]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=sort_key):
        selected.setdefault(key_func(row), row)
    return list(selected.values())


def edge_improve_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    last_positive_edge: dict[tuple[Any, ...], float | None] = {}
    for row in sorted(rows, key=sort_key):
        key = opportunity_key(row)
        if key not in last_positive_edge:
            selected.append(row)
            edge = float_or_none(row.get("selected_edge"))
            last_positive_edge[key] = edge if edge is not None and edge > 0 else None
            continue
        edge = float_or_none(row.get("selected_edge"))
        if edge is None or edge <= 0:
            continue
        baseline = last_positive_edge.get(key)
        if baseline is None or edge >= baseline * 1.5:
            selected.append(row)
            last_positive_edge[key] = edge
    return selected


def best_by_key(
    rows: list[dict[str, Any]],
    key_func: Callable[[dict[str, Any]], tuple[Any, ...]],
    score_func: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key_func(row)].append(row)
    return [sorted(group, key=score_func)[0] for group in grouped.values()]


def best_edge_key(item: dict[str, Any]) -> tuple[float, str, int]:
    edge = float_or_none(item.get("selected_edge"))
    return (-(edge if edge is not None else -math.inf), *sort_key(item))


def best_liquidity_key(item: dict[str, Any]) -> tuple[float, float, str, int]:
    liquidity = float_or_none(item.get("selected_sweep_fillable_50_usd"))
    return (-(liquidity if liquidity is not None else -math.inf), entry_price(item) or math.inf, *sort_key(item))


def hindsight_key(item: dict[str, Any]) -> tuple[float, str, int]:
    pnl = float_or_none(item.get("paper_pnl"))
    return (-(pnl if pnl is not None else -math.inf), *sort_key(item))


def summarize_slice(
    label: str,
    rows: list[dict[str, Any]],
    *,
    min_n: int,
    hindsight_only: bool = False,
) -> dict[str, Any]:
    resolved_rows = [row for row in rows if row.get("paper_pnl") is not None]
    pending = len(rows) - len(resolved_rows)
    pnls = [float(row["paper_pnl"]) for row in resolved_rows]
    entries = [entry for row in rows if (entry := entry_price(row)) is not None]
    resolved_entries = [entry for row in resolved_rows if (entry := entry_price(row)) is not None]
    fair_values = [fair for row in rows if (fair := selected_fair(row)) is not None]
    edge_values = [edge for row in rows if (edge := float_or_none(row.get("selected_edge"))) is not None]
    liquidity_values = [
        value for row in rows if (value := float_or_none(row.get("selected_sweep_fillable_50_usd"))) is not None
    ]
    book_ages = [value for row in rows if (value := float_or_none(row.get("selected_book_age_seconds"))) is not None]
    risk = sum(resolved_entries)
    pnl = sum(pnls)
    rr = return_risk(pnl, risk)
    flags = []
    if len(resolved_rows) < min_n:
        flags.append("LOW_N")
    if pending > len(resolved_rows):
        flags.append("PENDING_HEAVY")
    if hindsight_only:
        flags.append("HINDSIGHT_ONLY")
    avg_liquidity = mean(liquidity_values)
    if avg_liquidity is not None and avg_liquidity < 50.0:
        flags.append("EXECUTION_WEAK")
    return {
        "label": label,
        "n": len(rows),
        "resolved": len(resolved_rows),
        "pending": pending,
        "win_rate": mean(1.0 if int(row["correct"]) else 0.0 for row in resolved_rows if row.get("correct") is not None),
        "pnl": pnl if resolved_rows else None,
        "risk": risk if resolved_rows else None,
        "rr": rr,
        "sharpe": sharpe(pnls),
        "avg_entry": mean(entries),
        "avg_edge": mean(edge_values),
        "avg_fair": mean(fair_values),
        "avg_sweep_50": avg_liquidity,
        "avg_book_age": mean(book_ages),
        "flags": ",".join(flags) if flags else "OK",
    }


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = sum(1 for row in rows if row.get("paper_pnl") is not None)
    return {
        "rows": len(rows),
        "resolved": resolved,
        "pending": len(rows) - resolved,
        "unique_opportunities": len({opportunity_key(row) for row in rows}),
        "stations": len({row.get("station") for row in rows}),
        "market_dates": len({row.get("market_date") for row in rows}),
        "sources": len({row.get("source") for row in rows}),
        "strategies": len({row.get("strategy_bucket") for row in rows}),
    }


def slice_tables(rows: list[dict[str, Any]], *, min_n: int, hindsight_only: bool) -> dict[str, list[dict[str, Any]]]:
    specs: tuple[tuple[str, Callable[[dict[str, Any]], tuple[Any, ...] | str]], ...] = (
        ("By Source", lambda row: str(row.get("source") or "missing")),
        ("By Source Strategy", lambda row: (row.get("source"), row.get("strategy_bucket"))),
        ("By Source Strategy Side", lambda row: (row.get("source"), row.get("strategy_bucket"), row.get("selected_side"))),
        (
            "By Source Strategy Obs Delay",
            lambda row: (row.get("source"), row.get("strategy_bucket"), row.get("obs_delay_bucket") or "missing"),
        ),
        (
            "By Source Strategy Entry Side",
            lambda row: (row.get("source"), row.get("strategy_bucket"), entry_band_value(row), row.get("selected_side")),
        ),
        (
            "By Source Strategy Edge Side",
            lambda row: (row.get("source"), row.get("strategy_bucket"), edge_band(float_or_none(row.get("selected_edge"))), row.get("selected_side")),
        ),
        ("By Station", lambda row: str(row.get("station") or "missing")),
        ("By Bucket Type", lambda row: bucket_type(row.get("selected_bucket"))),
        ("By Decision Hour", lambda row: decision_hour_band(row.get("decision_time_local"))),
        ("By Liquidity", lambda row: liquidity_band(float_or_none(row.get("selected_sweep_fillable_50_usd")))),
    )
    tables = {}
    for name, key_func in specs:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = key_func(row)
            label = " + ".join(str(part) for part in key) if isinstance(key, tuple) else str(key)
            grouped[label].append(row)
        stats = [summarize_slice(label, group, min_n=min_n, hindsight_only=hindsight_only) for label, group in grouped.items()]
        tables[name] = sorted(stats, key=rank_key)
    return tables


def calibration_tables(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    specs = {
        "Fair Calibration": lambda row: probability_band(selected_fair(row)),
        "Edge Calibration": lambda row: edge_band(float_or_none(row.get("selected_edge"))),
        "Entry Calibration": lambda row: entry_band_value(row),
        "Side Calibration": lambda row: str(row.get("selected_side") or "missing"),
        "Obs Delay Calibration": lambda row: str(row.get("obs_delay_bucket") or "missing"),
    }
    output = {}
    for name, key_func in specs.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[key_func(row)].append(row)
        output[name] = sorted([calibration_row(label, group) for label, group in grouped.items()], key=calibration_rank_key)
    return output


def calibration_row(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("correct") is not None]
    fair_values = [fair for row in rows if (fair := selected_fair(row)) is not None]
    resolved_fair_values = [fair for row in resolved if (fair := selected_fair(row)) is not None]
    hit_rate = mean(1.0 if int(row["correct"]) else 0.0 for row in resolved)
    avg_resolved_fair = mean(resolved_fair_values)
    return {
        "band": label,
        "n": len(rows),
        "resolved": len(resolved),
        "pending": len(rows) - len(resolved),
        "avg_fair": mean(fair_values),
        "resolved_avg_fair": avg_resolved_fair,
        "hit_rate": hit_rate,
        "cal_error": None if avg_resolved_fair is None or hit_rate is None else hit_rate - avg_resolved_fair,
    }


def promotion_candidates(mode_tables: dict[str, dict[str, list[dict[str, Any]]]], *, min_n: int, top_n: int) -> list[dict[str, Any]]:
    rows = []
    for mode_name, tables in mode_tables.items():
        if mode_name == "hindsight_best":
            continue
        for table_name, table_rows in tables.items():
            for row in table_rows:
                flags = set(str(row["flags"]).split(",")) if row["flags"] != "OK" else set()
                if row["resolved"] < min_n or "EXECUTION_WEAK" in flags:
                    continue
                if (row["rr"] or 0) > 0 and (row["sharpe"] or 0) > 0:
                    rows.append({"mode": mode_name, "table": table_name, **row})
    return sorted(rows, key=rank_key)[:top_n]


def render_report(mode_results: dict[str, ModeResult], *, min_n: int, top_n: int) -> str:
    lines = ["# Snapshot Opportunity Sweep", ""]
    all_mode_tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for mode_name in MODE_ORDER:
        mode = mode_results[mode_name]
        lines.extend([f"## {mode.name}", ""])
        if mode.hindsight_only:
            lines.extend(["Exploratory upper bound: selected by realized paper PnL. Do not use for promotion rules.", ""])
        coverage = coverage_summary(mode.rows)
        lines.extend(render_kv_table(coverage))
        tables = slice_tables(mode.rows, min_n=min_n, hindsight_only=mode.hindsight_only)
        all_mode_tables[mode_name] = tables
        for table_name, rows in tables.items():
            lines.extend(["", f"### {table_name}", ""])
            lines.extend(render_metric_table(rows[:top_n]))
        for table_name, rows in calibration_tables(mode.rows).items():
            lines.extend(["", f"### {table_name}", ""])
            lines.extend(render_calibration_table(rows[:top_n]))
        lines.append("")

    lines.extend(["## Promotion Candidates", ""])
    candidates = promotion_candidates(all_mode_tables, min_n=min_n, top_n=top_n)
    if candidates:
        lines.extend(render_promotion_table(candidates))
    else:
        lines.append("No non-hindsight slices met the promotion screen.")
    return "\n".join(lines).rstrip() + "\n"


def render_kv_table(values: dict[str, Any]) -> list[str]:
    lines = ["| metric | value |", "|---|---:|"]
    for key, value in values.items():
        lines.append(f"| {key} | {value} |")
    return lines


def render_metric_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("label", "n", "resolved", "pending", "win_rate", "pnl", "risk", "rr", "sharpe", "avg_entry", "avg_edge", "avg_fair", "avg_sweep_50", "avg_book_age", "flags")
    return render_rows(columns, rows)


def render_calibration_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("band", "n", "resolved", "pending", "avg_fair", "resolved_avg_fair", "hit_rate", "cal_error")
    return render_rows(columns, rows)


def render_promotion_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("mode", "table", "label", "resolved", "rr", "sharpe", "pnl", "avg_entry", "avg_edge", "avg_sweep_50")
    return render_rows(columns, rows)


def render_rows(columns: Iterable[str], rows: list[dict[str, Any]]) -> list[str]:
    cols = list(columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(format_value(row.get(col)) for col in cols) + " |")
    return lines


def rank_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    rr = row.get("rr")
    pnl = row.get("pnl")
    return (-int(row.get("resolved") or 0), -(rr if rr is not None else -math.inf), -(pnl if pnl is not None else -math.inf), str(row.get("label") or ""))


def calibration_rank_key(row: dict[str, Any]) -> tuple[int, str]:
    return (-int(row.get("resolved") or 0), str(row.get("band") or ""))


def entry_band_value(row: dict[str, Any]) -> str:
    entry = entry_price(row)
    return "missing" if entry is None else entry_band(entry)


def decision_hour_band(value: Any) -> str:
    if value is None:
        return "missing"
    try:
        hour = int(str(value).split("T", 1)[1][:2])
    except (IndexError, ValueError):
        return "missing"
    if hour < 8:
        return "00-08"
    if hour < 12:
        return "08-12"
    if hour < 16:
        return "12-16"
    if hour < 20:
        return "16-20"
    return "20-24"


def liquidity_band(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 25:
        return "0-25"
    if value < 50:
        return "25-50"
    if value < 100:
        return "50-100"
    return ">=100"


def mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--market-family")
    parser.add_argument("--min-n", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-consensus", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = sqlite3.connect(str(args.db))
    db.row_factory = sqlite3.Row
    try:
        rows = load_snapshot_rows(db, start_date=args.start_date, end_date=args.end_date, market_family=args.market_family)
    finally:
        db.close()
    if args.include_consensus:
        rows = [*rows, *build_consensus_rows(rows)]
    report = render_report(apply_modes(rows), min_n=args.min_n, top_n=args.top_n)
    if args.output:
        args.output.write_text(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
