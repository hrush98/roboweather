#!/usr/bin/env python3
"""Standardized live-policy promotion report from raw prediction snapshots.

This intentionally does not read research_policy_positions. It replays policy specs
from prediction_snapshots, scores against station_date_outcomes/prediction_results,
and emits deterministic promote/canary/deactivate decisions for live review.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import sharpe
from scripts.snapshot_opportunity_sweep import (
    HRRR_RICH_CATBOOST_MODEL,
    HRRR_RICH_DYNAMIC_TUNED_MODEL,
    HRRR_V2_CATBOOST_MODEL,
    HRRR_V2_DYNAMIC_MODEL,
    METAR_HRRR_RICH_CATBOOST_MODEL,
    METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
    PM_ACTIVE_CATBOOST_MODEL,
    PM_ACTIVE_DYNAMIC_MODEL,
    PM_ACTIVE_DYNAMIC_TUNED_MODEL,
    PM_ACTIVE_MVP_MODEL,
    PM_ACTIVE_NGBOOST_MODEL,
    POLICY_SEARCH_CONSENSUS_GROUPS,
    build_consensus_rows,
    entry_price,
    load_snapshot_rows,
    sort_key,
)

DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_SCOPE = "station_date_bucket_side_obs_delay"


@dataclass(frozen=True)
class PolicySpec:
    name: str
    source: str
    identifier: str
    label: str
    strategy_bucket: str = "HIGH_CONVICTION"
    selected_side: str | None = None
    obs_delay_bucket: str | None = None
    entry_price_min: float | None = None
    entry_price_max: float | None = 0.50
    edge_min: float | None = None
    local_decision_start: str | None = "12:00"
    local_decision_end: str | None = "15:00"
    scope: str = DEFAULT_SCOPE
    live_status: str = "candidate"


@dataclass(frozen=True)
class PromotionThresholds:
    promote_min_resolved_30: int = 25
    promote_min_rr_30: float = 0.50
    promote_min_rr_all: float = 0.25
    canary_min_resolved_30: int = 15
    canary_min_rr_30: float = 0.20
    canary_min_rr_all: float = 0.05
    max_bad_rr_7: float = -0.50


@dataclass
class WindowStats:
    resolved: int = 0
    wins: int = 0
    win_rate: float | None = None
    pnl: float = 0.0
    risk: float = 0.0
    rr: float | None = None
    avg_entry: float | None = None
    sharpe: float | None = None
    avg_sweep_50: float | None = None
    fillable_50_rate: float | None = None


@dataclass
class PolicyReportRow:
    policy_name: str
    label: str
    live_status: str
    decision: str
    reason: str
    filters: str
    scope: str
    all: WindowStats
    last30: WindowStats
    last7: WindowStats


def candidate_policy_specs(scope: str = DEFAULT_SCOPE) -> list[PolicySpec]:
    specs: list[PolicySpec] = [
        PolicySpec(
            "live_old_dynamic_core",
            "model",
            PM_ACTIVE_DYNAMIC_TUNED_MODEL,
            "LIVE old dynamic core",
            selected_side="BUY_NO",
            entry_price_min=0.05,
            edge_min=0.25,
            live_status="live_legacy",
            scope=scope,
        ),
        PolicySpec(
            "live_consensus_no_tiny",
            "consensus",
            "obs_bucket_consensus",
            "LIVE consensus no-tiny",
            entry_price_min=0.05,
            live_status="live_current",
            scope=scope,
        ),
        PolicySpec(
            "live_core_consensus_15m",
            "consensus",
            "obs_bucket_consensus",
            "LIVE core consensus 15m",
            obs_delay_bucket="15m",
            entry_price_min=0.0,
            live_status="live_current",
            scope=scope,
        ),
        PolicySpec(
            "live_ngboost_buy_yes",
            "model",
            PM_ACTIVE_NGBOOST_MODEL,
            "LIVE NGBoost BUY_YES",
            strategy_bucket="BEST_BUCKET",
            selected_side="BUY_YES",
            entry_price_min=0.05,
            live_status="live_current",
            scope=scope,
        ),
        PolicySpec(
            "live_moonshot",
            "model",
            PM_ACTIVE_DYNAMIC_TUNED_MODEL,
            "LIVE moonshot",
            selected_side="BUY_NO",
            entry_price_min=0.05,
            entry_price_max=0.10,
            edge_min=0.90,
            live_status="live_current",
            scope=scope,
        ),
    ]

    sources = [
        ("consensus", "obs_bucket_consensus", "obs bucket consensus"),
        ("consensus", "obs_dynamic_tuned_mvp", "obs dyn_tuned+mvp"),
        ("consensus", "pm_active_us12_dynamic_mvp", "obs dynamic+mvp"),
        ("model", PM_ACTIVE_DYNAMIC_MODEL, "obs dynamic"),
        ("model", PM_ACTIVE_DYNAMIC_TUNED_MODEL, "obs dynamic tuned"),
        ("model", PM_ACTIVE_CATBOOST_MODEL, "obs catboost"),
        ("model", PM_ACTIVE_MVP_MODEL, "obs mvp"),
        ("model", HRRR_V2_DYNAMIC_MODEL, "hrrr_v2 dynamic"),
        ("model", HRRR_V2_CATBOOST_MODEL, "hrrr_v2 catboost"),
        ("consensus", "hrrr_v2_bucket_consensus", "hrrr_v2 bucket consensus"),
        ("model", HRRR_RICH_DYNAMIC_TUNED_MODEL, "hrrr_rich dynamic tuned"),
        ("model", HRRR_RICH_CATBOOST_MODEL, "hrrr_rich catboost"),
        ("consensus", "hrrr_rich_bucket_consensus", "hrrr_rich bucket consensus"),
        ("model", METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL, "metar_hrrr dynamic tuned"),
        ("model", METAR_HRRR_RICH_CATBOOST_MODEL, "metar_hrrr catboost"),
        ("consensus", "metar_hrrr_rich_bucket_consensus", "metar_hrrr bucket consensus"),
    ]
    seen = {spec.name for spec in specs}
    for source, identifier, label in sources:
        for obs_delay in (None, "10m", "15m"):
            for entry_min, band in ((0.0, "00_50"), (0.05, "05_50")):
                obs_label = obs_delay or "all"
                name = slugify(f"{label}_hc_late_{obs_label}_entry_{band}")
                if name in seen:
                    continue
                seen.add(name)
                specs.append(
                    PolicySpec(
                        name=name,
                        source=source,
                        identifier=identifier,
                        label=f"{label} HC late {obs_label} entry {band}",
                        obs_delay_bucket=obs_delay,
                        entry_price_min=entry_min,
                        scope=scope,
                    )
                )
    return specs


def build_report(
    rows: list[dict[str, Any]],
    specs: Iterable[PolicySpec],
    *,
    thresholds: PromotionThresholds = PromotionThresholds(),
    last30_start: str | None = None,
    last7_start: str | None = None,
) -> list[PolicyReportRow]:
    if last30_start is None or last7_start is None:
        dates = sorted({str(row.get("market_date")) for row in rows if row.get("paper_pnl") is not None})
        if dates:
            last_date = dates[-1]
            last30_start = last30_start or offset_iso_date(last_date, 29)
            last7_start = last7_start or offset_iso_date(last_date, 6)
        else:
            last30_start = last30_start or "9999-12-31"
            last7_start = last7_start or "9999-12-31"
    report = []
    for spec in specs:
        selected = select_policy_rows(rows, spec)
        all_stats = window_stats(selected)
        stats_30 = window_stats([row for row in selected if str(row.get("market_date")) >= str(last30_start)])
        stats_7 = window_stats([row for row in selected if str(row.get("market_date")) >= str(last7_start)])
        decision, reason = classify_policy(spec, all_stats, stats_30, stats_7, thresholds)
        report.append(
            PolicyReportRow(
                policy_name=spec.name,
                label=spec.label,
                live_status=spec.live_status,
                decision=decision,
                reason=reason,
                filters=filter_label(spec),
                scope=spec.scope,
                all=all_stats,
                last30=stats_30,
                last7=stats_7,
            )
        )
    return sorted(report, key=report_sort_key)


def select_policy_rows(rows: list[dict[str, Any]], spec: PolicySpec) -> list[dict[str, Any]]:
    selected = []
    seen = set()
    for row in sorted((row for row in rows if row_matches(row, spec)), key=sort_key):
        key = uniqueness_key(row, spec.scope)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def row_matches(row: dict[str, Any], spec: PolicySpec) -> bool:
    if spec.source == "model":
        if row.get("model_name") != spec.identifier:
            return False
    elif spec.source == "consensus":
        if row.get("source") != f"consensus:{spec.identifier}":
            return False
    else:
        return False
    if row.get("strategy_bucket") != spec.strategy_bucket:
        return False
    if spec.selected_side is not None and row.get("selected_side") != spec.selected_side:
        return False
    if spec.obs_delay_bucket is not None and row.get("obs_delay_bucket") != spec.obs_delay_bucket:
        return False
    entry = entry_price(row)
    if entry is None:
        return False
    if spec.entry_price_min is not None and float(entry) < spec.entry_price_min:
        return False
    if spec.entry_price_max is not None and float(entry) > spec.entry_price_max:
        return False
    if spec.edge_min is not None:
        edge = row.get("selected_edge")
        if edge is None or float(edge) < spec.edge_min:
            return False
    local = local_hhmm(row.get("decision_time_local"))
    if spec.local_decision_start is not None and local < spec.local_decision_start:
        return False
    if spec.local_decision_end is not None and local >= spec.local_decision_end:
        return False
    return row.get("paper_pnl") is not None


def uniqueness_key(row: dict[str, Any], scope: str) -> tuple[Any, ...]:
    key: list[Any] = [row.get("station"), row.get("market_date"), row.get("market_family") or "HIGH_TEMP"]
    if scope == "station_date_bucket_side_obs_delay":
        key.extend([row.get("selected_bucket"), row.get("selected_side"), row.get("obs_delay_bucket")])
    elif scope == "station_date_bucket_side":
        key.extend([row.get("selected_bucket"), row.get("selected_side")])
    elif scope != "station_date":
        raise ValueError(f"unsupported scope: {scope}")
    return tuple(key)


def window_stats(rows: list[dict[str, Any]]) -> WindowStats:
    pnls = [float(row["paper_pnl"]) for row in rows]
    entries = [float(entry_price(row) or 0.0) for row in rows]
    risk = sum(entries)
    pnl = sum(pnls)
    resolved = len(rows)
    wins = sum(1 for pnl_value in pnls if pnl_value > 0)
    sweep_values = [float(row["selected_sweep_vwap_50"]) for row in rows if row.get("selected_sweep_vwap_50") is not None]
    fillable_values = [float(row["selected_sweep_fillable_50_usd"]) for row in rows if row.get("selected_sweep_fillable_50_usd") is not None]
    return WindowStats(
        resolved=resolved,
        wins=wins,
        win_rate=(wins / resolved) if resolved else None,
        pnl=pnl,
        risk=risk,
        rr=(pnl / risk) if risk else None,
        avg_entry=(risk / resolved) if resolved else None,
        sharpe=sharpe(pnls) if pnls else None,
        avg_sweep_50=(sum(sweep_values) / len(sweep_values)) if sweep_values else None,
        fillable_50_rate=(sum(1 for value in fillable_values if value >= 50.0) / len(fillable_values)) if fillable_values else None,
    )


def classify_policy(
    spec: PolicySpec,
    all_stats: WindowStats,
    stats_30: WindowStats,
    stats_7: WindowStats,
    thresholds: PromotionThresholds,
) -> tuple[str, str]:
    rr_all = all_stats.rr if all_stats.rr is not None else float("-inf")
    rr_30 = stats_30.rr if stats_30.rr is not None else float("-inf")
    rr_7 = stats_7.rr if stats_7.rr is not None else None
    recent_bad = rr_7 is not None and stats_7.resolved >= 5 and rr_7 <= thresholds.max_bad_rr_7

    if spec.live_status.startswith("live") and (rr_all <= 0.05 or rr_30 <= 0.05 or recent_bad):
        return "DEACTIVATE", "live policy fails standardized raw-snapshot replay"
    if (
        stats_30.resolved >= thresholds.promote_min_resolved_30
        and rr_30 >= thresholds.promote_min_rr_30
        and rr_all >= thresholds.promote_min_rr_all
        and not recent_bad
    ):
        return "PROMOTE", "passes sample, return, and execution gates"
    if (
        stats_30.resolved >= thresholds.canary_min_resolved_30
        and rr_30 >= thresholds.canary_min_rr_30
        and rr_all >= thresholds.canary_min_rr_all
        and not recent_bad
    ):
        return "CANARY", "positive but below full promotion gates"
    return "RESEARCH_ONLY", "insufficient edge, sample, or recent stability"


def load_rows(db_path: Path, *, market_family: str, us_high_temp_only: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        base_rows = load_snapshot_rows(db, market_family=market_family, us_high_temp_only=us_high_temp_only)
    finally:
        db.close()
    rows = [*base_rows, *build_consensus_rows(base_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS)]
    metadata = {
        "db": str(db_path),
        "base_snapshot_rows": len(base_rows),
        "replay_rows_with_consensus": len(rows),
        "max_snapshot_timestamp": max((str(row.get("timestamp")) for row in base_rows), default=None),
        "resolved_through": max((str(row.get("market_date")) for row in base_rows if row.get("paper_pnl") is not None), default=None),
    }
    return rows, metadata


def render_markdown(report: list[PolicyReportRow], metadata: dict[str, Any], limit: int) -> str:
    rows = report[:limit]
    lines = ["# Live Policy Promotion Report", ""]
    for key, value in metadata.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "| decision | live | policy | n30 | rr30 | n7 | rr7 | n_all | rr_all | fill50 | reason |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"])
    for row in rows:
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row.decision,
                row.live_status,
                row.label,
                row.last30.resolved,
                fmt(row.last30.rr),
                row.last7.resolved,
                fmt(row.last7.rr),
                row.all.resolved,
                fmt(row.all.rr),
                fmt(row.last30.fillable_50_rate),
                row.reason,
            )
        )
    return "\n".join(lines)


def report_sort_key(row: PolicyReportRow) -> tuple[int, float, float, int, str]:
    order = {"PROMOTE": 0, "CANARY": 1, "DEACTIVATE": 2, "RESEARCH_ONLY": 3}
    rr30 = row.last30.rr if row.last30.rr is not None else float("-inf")
    rrall = row.all.rr if row.all.rr is not None else float("-inf")
    return (order.get(row.decision, 9), -rr30, -rrall, -row.last30.resolved, row.label)


def filter_label(spec: PolicySpec) -> str:
    parts = [spec.source, spec.identifier, spec.strategy_bucket]
    if spec.selected_side:
        parts.append(f"side={spec.selected_side}")
    if spec.obs_delay_bucket:
        parts.append(f"obs={spec.obs_delay_bucket}")
    parts.append(f"entry={spec.entry_price_min if spec.entry_price_min is not None else '*'}-{spec.entry_price_max if spec.entry_price_max is not None else '*'}")
    if spec.edge_min is not None:
        parts.append(f"edge>={spec.edge_min}")
    if spec.local_decision_start or spec.local_decision_end:
        parts.append(f"local={spec.local_decision_start or '*'}-{spec.local_decision_end or '*'}")
    return ",".join(parts)


def slugify(value: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def local_hhmm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 and "T" in text else ""


def offset_iso_date(value: str, days_back: int) -> str:
    from datetime import date, timedelta

    parsed = date.fromisoformat(value)
    return (parsed - timedelta(days=days_back)).isoformat()


def fmt(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def to_jsonable(report: list[PolicyReportRow], metadata: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": metadata, "policies": [asdict(row) for row in report]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standardized raw-snapshot live policy promotion report.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--market-family", default="HIGH_TEMP")
    parser.add_argument("--scope", default=DEFAULT_SCOPE, choices=("station_date", "station_date_bucket_side", "station_date_bucket_side_obs_delay"))
    parser.add_argument("--us-high-temp-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--limit", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, metadata = load_rows(Path(args.db).expanduser(), market_family=args.market_family, us_high_temp_only=args.us_high_temp_only)
    metadata["scope"] = args.scope
    metadata["source"] = "raw prediction_snapshots plus in-memory consensus; research_policy_positions unused"
    report = build_report(rows, candidate_policy_specs(scope=args.scope))
    if args.format == "json":
        print(json.dumps(to_jsonable(report, metadata), indent=2, sort_keys=True))
    else:
        print(render_markdown(report, metadata, args.limit))


if __name__ == "__main__":
    main()
