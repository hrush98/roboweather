#!/usr/bin/env python3
"""Cap-aware portfolio replay from raw prediction snapshots.

This is the promotion gate for live sizing decisions. It replays the current
live stack in plan order, applies live-style caps and recorded ask-sweep depth,
and reports only the incremental contribution each later sleeve adds after
previous sleeves have consumed capacity.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.snapshot_opportunity_sweep import (  # noqa: E402
    HRRR_RICH_DYNAMIC_TUNED_MODEL,
    METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
    PM_ACTIVE_DYNAMIC_TUNED_MODEL,
    POLICY_SEARCH_CONSENSUS_GROUPS,
    build_consensus_rows,
    entry_price,
    load_snapshot_rows,
    selected_fair,
    sort_key,
)

DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_SCOPE = "station_date_bucket_side_obs_delay"
GLOBAL_LOW_STATIONS = frozenset({"EGLC", "LFPB", "RJTT", "RKSI", "VHHH", "ZSPD"})
HRRR_INLAND_STATIONS = frozenset({"KATL", "KDAL", "KORD"})


@dataclass(frozen=True)
class RiskCaps:
    max_order_usd: float = 100.0
    station_date_usd: float = 300.0
    station_date_side_usd: float = 200.0
    exact_bucket_side_usd: float = 100.0
    daily_new_risk_usd: float = 750.0
    min_order_usd: float = 0.0


@dataclass(frozen=True)
class ReplaySpec:
    name: str
    label: str
    source: str
    identifier: str
    target_notional_usd: float
    strategy_bucket: str = "HIGH_CONVICTION"
    market_family: str | None = None
    selected_side: str | None = None
    station_allow_set: frozenset[str] | None = None
    obs_delay_bucket: str | None = None
    entry_price_min: float | None = None
    entry_price_max: float | None = None
    edge_min: float | None = None
    local_decision_start: str | None = None
    local_decision_end: str | None = None
    scope: str = DEFAULT_SCOPE
    hrrr_disagreement_min: float | None = None
    obs_edge_max: float | None = None
    obs_core_identifier: str = "obs_bucket_consensus"
    role: str = "live_current"


@dataclass
class ReplayStats:
    candidate_rows: int = 0
    allocated_rows: int = 0
    skipped_capacity_rows: int = 0
    skipped_depth_rows: int = 0
    risk_usd: float = 0.0
    pnl_usd: float = 0.0
    wins: int = 0
    avg_entry: float | None = None
    avg_depth_fillable_usd: float | None = None
    examples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rr(self) -> float | None:
        return self.pnl_usd / self.risk_usd if self.risk_usd else None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.allocated_rows if self.allocated_rows else None


@dataclass
class ReplayReportRow:
    name: str
    label: str
    role: str
    stats: ReplayStats


@dataclass
class ExposureState:
    caps: RiskCaps
    daily: dict[tuple[Any, ...], float] = field(default_factory=dict)
    station_date: dict[tuple[Any, ...], float] = field(default_factory=dict)
    station_date_side: dict[tuple[Any, ...], float] = field(default_factory=dict)
    exact_bucket_side: dict[tuple[Any, ...], float] = field(default_factory=dict)

    def available(self, row: dict[str, Any]) -> float:
        keys = exposure_keys(row)
        values = [
            self.caps.max_order_usd,
            self.caps.daily_new_risk_usd - self.daily.get(keys["daily"], 0.0),
            self.caps.station_date_usd - self.station_date.get(keys["station_date"], 0.0),
            self.caps.station_date_side_usd - self.station_date_side.get(keys["station_date_side"], 0.0),
            self.caps.exact_bucket_side_usd - self.exact_bucket_side.get(keys["exact_bucket_side"], 0.0),
        ]
        return max(0.0, min(values))

    def reserve(self, row: dict[str, Any], notional_usd: float) -> None:
        keys = exposure_keys(row)
        self.daily[keys["daily"]] = self.daily.get(keys["daily"], 0.0) + notional_usd
        self.station_date[keys["station_date"]] = self.station_date.get(keys["station_date"], 0.0) + notional_usd
        self.station_date_side[keys["station_date_side"]] = self.station_date_side.get(keys["station_date_side"], 0.0) + notional_usd
        self.exact_bucket_side[keys["exact_bucket_side"]] = self.exact_bucket_side.get(keys["exact_bucket_side"], 0.0) + notional_usd


def default_replay_specs(include_hrrr_candidate: bool = True) -> list[ReplaySpec]:
    specs = [
        ReplaySpec(
            name="live_consensus_no_tiny",
            label="LIVE US consensus no-tiny",
            source="consensus",
            identifier="obs_bucket_consensus",
            target_notional_usd=100.0,
            market_family="HIGH_TEMP",
            entry_price_min=0.05,
            entry_price_max=0.50,
            local_decision_start="12:00",
            local_decision_end="15:00",
        ),
        ReplaySpec(
            name="live_moonshot",
            label="LIVE moonshot",
            source="model",
            identifier=PM_ACTIVE_DYNAMIC_TUNED_MODEL,
            target_notional_usd=2.0,
            market_family="HIGH_TEMP",
            selected_side="BUY_NO",
            entry_price_min=0.05,
            entry_price_max=0.10,
            edge_min=0.90,
            local_decision_start="12:00",
            local_decision_end="15:00",
        ),
        ReplaySpec(
            name="global_low_tiny_tail",
            label="LIVE global low tiny tail",
            source="consensus",
            identifier="global_low_dynamic_mvp",
            target_notional_usd=5.0,
            strategy_bucket="TAIL",
            market_family="LOW_TEMP",
            selected_side="BUY_NO",
            station_allow_set=GLOBAL_LOW_STATIONS,
            entry_price_min=0.0,
            entry_price_max=0.05,
            local_decision_start="00:30",
            local_decision_end="05:00",
        ),
        ReplaySpec(
            name="global_low_canary",
            label="LIVE global low canary",
            source="consensus",
            identifier="global_low_dynamic_mvp",
            target_notional_usd=100.0,
            market_family="LOW_TEMP",
            selected_side="BUY_NO",
            station_allow_set=GLOBAL_LOW_STATIONS,
            entry_price_min=0.05,
            entry_price_max=0.75,
            local_decision_start="00:30",
            local_decision_end="05:00",
        ),
        ReplaySpec(
            name="global_low_mvp_addon",
            label="LIVE global low MVP add-on",
            source="model",
            identifier="mvp_international_celsius_low_obs_2022_2025",
            target_notional_usd=50.0,
            market_family="LOW_TEMP",
            selected_side="BUY_NO",
            station_allow_set=GLOBAL_LOW_STATIONS,
            entry_price_min=0.05,
            entry_price_max=0.50,
        ),
    ]
    if include_hrrr_candidate:
        specs.append(
            ReplaySpec(
                name="hrrr_dynamic_tuned_inland_late_disagreement",
                label="CANDIDATE HRRR inland late disagreement",
                source="model",
                identifier=HRRR_RICH_DYNAMIC_TUNED_MODEL,
                target_notional_usd=25.0,
                market_family="HIGH_TEMP",
                station_allow_set=HRRR_INLAND_STATIONS,
                entry_price_min=0.0,
                entry_price_max=0.50,
                edge_min=0.25,
                local_decision_start="12:00",
                local_decision_end="15:00",
                hrrr_disagreement_min=0.15,
                obs_edge_max=0.10,
                role="candidate",
            )
        )
        specs.append(
            ReplaySpec(
                name="metar_hrrr_dynamic_tuned_inland_late_disagreement",
                label="CANDIDATE METAR+HRRR inland late disagreement",
                source="model",
                identifier=METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
                target_notional_usd=25.0,
                market_family="HIGH_TEMP",
                station_allow_set=HRRR_INLAND_STATIONS,
                entry_price_min=0.0,
                entry_price_max=0.50,
                edge_min=0.25,
                local_decision_start="12:00",
                local_decision_end="15:00",
                hrrr_disagreement_min=0.15,
                obs_edge_max=0.10,
                role="candidate",
            )
        )
    return specs


def load_replay_rows(db_path: Path, *, start_date: str | None = None, end_date: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        high_rows = load_snapshot_rows(db, start_date=start_date, end_date=end_date, market_family="HIGH_TEMP", us_high_temp_only=False)
        low_rows = load_snapshot_rows(db, start_date=start_date, end_date=end_date, market_family="LOW_TEMP", us_high_temp_only=False)
    finally:
        db.close()
    base_rows = [*high_rows, *low_rows]
    rows = [*base_rows, *build_consensus_rows(base_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS)]
    metadata = {
        "db": str(db_path),
        "base_snapshot_rows": len(base_rows),
        "replay_rows_with_consensus": len(rows),
        "max_snapshot_timestamp": max((str(row.get("timestamp")) for row in base_rows), default=None),
        "resolved_through": max((str(row.get("market_date")) for row in base_rows if row.get("paper_pnl") is not None), default=None),
    }
    return rows, metadata


def build_portfolio_report(
    rows: list[dict[str, Any]],
    specs: Iterable[ReplaySpec],
    *,
    caps: RiskCaps = RiskCaps(),
    use_depth: bool = True,
    example_limit: int = 5,
) -> list[ReplayReportRow]:
    context = build_context(rows)
    exposure = ExposureState(caps)
    report: list[ReplayReportRow] = []
    for spec in specs:
        selected = select_spec_rows(rows, spec, context)
        stats = allocate_rows(selected, spec, exposure, use_depth=use_depth, example_limit=example_limit)
        stats.candidate_rows = len(selected)
        report.append(ReplayReportRow(spec.name, spec.label, spec.role, stats))
    return report


def build_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    obs_by_key = {
        opportunity_key(row): row
        for row in rows
        if row.get("source") == "consensus:obs_bucket_consensus" or row.get("model_name") == "obs_bucket_consensus"
    }
    return {"obs_by_key": obs_by_key}


def select_spec_rows(rows: list[dict[str, Any]], spec: ReplaySpec, context: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in sorted(rows, key=sort_key):
        if not row_matches_spec(row, spec, context):
            continue
        key = uniqueness_key(row, spec.scope)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def row_matches_spec(row: dict[str, Any], spec: ReplaySpec, context: dict[str, Any]) -> bool:
    if row.get("paper_pnl") is None:
        return False
    if spec.source == "model":
        if row.get("model_name") != spec.identifier:
            return False
    elif spec.source == "consensus":
        if row.get("source") != f"consensus:{spec.identifier}":
            return False
    else:
        return False
    if spec.market_family is not None and (row.get("market_family") or "HIGH_TEMP") != spec.market_family:
        return False
    if spec.station_allow_set is not None and row.get("station") not in spec.station_allow_set:
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
    if spec.hrrr_disagreement_min is not None:
        if not passes_hrrr_disagreement(row, spec, context):
            return False
    return True


def passes_hrrr_disagreement(row: dict[str, Any], spec: ReplaySpec, context: dict[str, Any]) -> bool:
    entry = entry_price(row)
    fair = selected_fair(row)
    if entry is None or fair is None:
        return False
    obs = context["obs_by_key"].get(opportunity_key(row))
    if obs is None:
        disagreement = float(fair) - float(entry)
        return disagreement >= float(spec.hrrr_disagreement_min or 0.0)
    obs_edge = obs.get("selected_edge")
    if spec.obs_edge_max is not None and obs_edge is not None and float(obs_edge) >= spec.obs_edge_max:
        return False
    obs_fair = selected_fair(obs)
    if obs_fair is None:
        return False
    return float(fair) - float(obs_fair) >= float(spec.hrrr_disagreement_min or 0.0)


def allocate_rows(
    rows: list[dict[str, Any]],
    spec: ReplaySpec,
    exposure: ExposureState,
    *,
    use_depth: bool,
    example_limit: int,
) -> ReplayStats:
    stats = ReplayStats()
    entries: list[float] = []
    depths: list[float] = []
    for row in rows:
        entry = entry_price(row)
        pnl_per_contract = row.get("paper_pnl")
        if entry is None or pnl_per_contract is None or float(entry) <= 0:
            continue
        cap_available = exposure.available(row)
        target = min(spec.target_notional_usd, exposure.caps.max_order_usd)
        depth_available = fillable_notional(row, target) if use_depth else target
        notional = min(target, cap_available, depth_available)
        if notional < exposure.caps.min_order_usd or notional <= 0:
            if cap_available <= 0:
                stats.skipped_capacity_rows += 1
            elif depth_available <= 0:
                stats.skipped_depth_rows += 1
            else:
                stats.skipped_capacity_rows += 1
            continue
        exposure.reserve(row, notional)
        pnl_usd = (float(pnl_per_contract) / float(entry)) * notional
        stats.allocated_rows += 1
        stats.risk_usd += notional
        stats.pnl_usd += pnl_usd
        stats.wins += 1 if pnl_usd > 0 else 0
        entries.append(float(entry))
        depths.append(depth_available)
        if len(stats.examples) < example_limit:
            stats.examples.append(
                {
                    "market_date": row.get("market_date"),
                    "station": row.get("station"),
                    "family": row.get("market_family") or "HIGH_TEMP",
                    "bucket": row.get("selected_bucket"),
                    "side": row.get("selected_side"),
                    "entry": float(entry),
                    "risk_usd": round(notional, 2),
                    "pnl_usd": round(pnl_usd, 2),
                }
            )
    stats.avg_entry = sum(entries) / len(entries) if entries else None
    stats.avg_depth_fillable_usd = sum(depths) / len(depths) if depths else None
    return stats


def fillable_notional(row: dict[str, Any], target: float) -> float:
    fields = [
        (25.0, "selected_sweep_fillable_25_usd"),
        (50.0, "selected_sweep_fillable_50_usd"),
        (100.0, "selected_sweep_fillable_100_usd"),
    ]
    for threshold, field_name in fields:
        value = row.get(field_name)
        if target <= threshold and value is not None:
            return max(0.0, float(value))
    for _, field_name in reversed(fields):
        value = row.get(field_name)
        if value is not None:
            return max(0.0, float(value))
    return target


def exposure_keys(row: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    family = row.get("market_family") or "HIGH_TEMP"
    station = row.get("station")
    date = row.get("market_date")
    side = row.get("selected_side")
    bucket = row.get("selected_bucket")
    return {
        "daily": (date,),
        "station_date": (family, station, date),
        "station_date_side": (family, station, date, side),
        "exact_bucket_side": (family, station, date, bucket, side),
    }


def opportunity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("market_family") or "HIGH_TEMP",
        row.get("station"),
        row.get("market_date"),
        row.get("obs_delay_bucket"),
        row.get("strategy_bucket"),
        row.get("selected_side"),
        row.get("selected_market_id"),
        row.get("selected_bucket"),
    )


def uniqueness_key(row: dict[str, Any], scope: str) -> tuple[Any, ...]:
    key: list[Any] = [row.get("station"), row.get("market_date"), row.get("market_family") or "HIGH_TEMP"]
    if scope == "station_date_bucket_side_obs_delay":
        key.extend([row.get("selected_bucket"), row.get("selected_side"), row.get("obs_delay_bucket")])
    elif scope == "station_date_bucket_side":
        key.extend([row.get("selected_bucket"), row.get("selected_side")])
    elif scope != "station_date":
        raise ValueError(f"unsupported scope: {scope}")
    return tuple(key)


def local_hhmm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 and "T" in text else ""


def render_markdown(report: list[ReplayReportRow], metadata: dict[str, Any], caps: RiskCaps) -> str:
    lines = ["# Portfolio Promotion Report", ""]
    for key, value in metadata.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            f"- caps: max_order={caps.max_order_usd}, station_date={caps.station_date_usd}, station_date_side={caps.station_date_side_usd}, exact_bucket_side={caps.exact_bucket_side_usd}, daily_new={caps.daily_new_risk_usd}",
            "- source: raw prediction_snapshots plus in-memory consensus; research_policy_positions unused",
            "",
            "| order | role | sleeve | candidates | fills | skipped cap | skipped depth | risk | pnl | rr | win | avg entry | avg depth |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    total_risk = 0.0
    total_pnl = 0.0
    for index, row in enumerate(report, start=1):
        s = row.stats
        total_risk += s.risk_usd
        total_pnl += s.pnl_usd
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                index,
                row.role,
                row.label,
                s.candidate_rows,
                s.allocated_rows,
                s.skipped_capacity_rows,
                s.skipped_depth_rows,
                money(s.risk_usd),
                money(s.pnl_usd),
                fmt(s.rr),
                fmt(s.win_rate),
                fmt(s.avg_entry),
                money(s.avg_depth_fillable_usd),
            )
        )
    lines.extend(["", f"Portfolio incremental risk: {money(total_risk)}", f"Portfolio incremental PnL: {money(total_pnl)}", f"Portfolio R/R: {fmt(total_pnl / total_risk if total_risk else None)}"])
    lines.extend(["", "## Examples", ""])
    for row in report:
        if not row.stats.examples:
            continue
        lines.append(f"### {row.label}")
        lines.append("")
        lines.append("| date | station | family | bucket | side | entry | risk | pnl |")
        lines.append("|---|---|---|---|---|---:|---:|---:|")
        for ex in row.stats.examples:
            lines.append(
                "| {market_date} | {station} | {family} | {bucket} | {side} | {entry:.3f} | ${risk_usd:.2f} | ${pnl_usd:.2f} |".format(**ex)
            )
        lines.append("")
    return "\n".join(lines)


def report_to_json(report: list[ReplayReportRow], metadata: dict[str, Any], caps: RiskCaps) -> dict[str, Any]:
    return {"metadata": metadata, "caps": asdict(caps), "sleeves": [asdict(row) for row in report]}


def fmt(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"${value:.2f}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cap-aware portfolio replay for live policy promotion.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--no-depth", action="store_true", help="Ignore recorded ask-sweep depth and allocate up to caps/target.")
    parser.add_argument("--no-hrrr-candidates", action="store_true")
    parser.add_argument("--max-order-usd", type=float, default=100.0)
    parser.add_argument("--station-date-usd", type=float, default=300.0)
    parser.add_argument("--station-date-side-usd", type=float, default=200.0)
    parser.add_argument("--exact-bucket-side-usd", type=float, default=100.0)
    parser.add_argument("--daily-new-risk-usd", type=float, default=750.0)
    parser.add_argument("--min-order-usd", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    caps = RiskCaps(
        max_order_usd=args.max_order_usd,
        station_date_usd=args.station_date_usd,
        station_date_side_usd=args.station_date_side_usd,
        exact_bucket_side_usd=args.exact_bucket_side_usd,
        daily_new_risk_usd=args.daily_new_risk_usd,
        min_order_usd=args.min_order_usd,
    )
    rows, metadata = load_replay_rows(Path(args.db).expanduser(), start_date=args.start_date, end_date=args.end_date)
    metadata["start_date"] = args.start_date
    metadata["end_date"] = args.end_date
    metadata["depth_mode"] = "ignored" if args.no_depth else "recorded ask-sweep fillable notional"
    report = build_portfolio_report(
        rows,
        default_replay_specs(include_hrrr_candidate=not args.no_hrrr_candidates),
        caps=caps,
        use_depth=not args.no_depth,
    )
    if args.format == "json":
        print(json.dumps(report_to_json(report, metadata, caps), indent=2, sort_keys=True))
    else:
        print(render_markdown(report, metadata, caps))


if __name__ == "__main__":
    main()
