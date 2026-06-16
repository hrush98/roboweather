#!/usr/bin/env python3
"""Whole-chain live/research truth report.

Reconciles raw snapshot replay, live-selected candidates, actual fills, and
Polymarket settlement by sleeve. This is intended as the scaling gate before
promotion, sizing, or cap changes.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.calibration_table import entry_band as calibration_entry_band  # noqa: E402
from scripts.policy_leaderboard import bucket_won  # noqa: E402
from scripts.portfolio_promotion_report import (  # noqa: E402
    ReplaySpec,
    RiskCaps,
    build_portfolio_report,
    default_replay_specs,
    load_replay_rows,
)
from scripts.snapshot_opportunity_sweep import (  # noqa: E402
    PM_ACTIVE_DYNAMIC_TUNED_MODEL,
    PM_ACTIVE_NGBOOST_MODEL,
)

DEFAULT_LIVE_DB = Path.home() / ".local/state/roboweather/live_trading.sqlite"
DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"


@dataclass(frozen=True)
class SleeveConfig:
    name: str
    label: str
    live_strategies: tuple[str, ...] = ()
    replay_spec_name: str | None = None
    calibration_family: str | None = None


@dataclass
class ChainStats:
    candidates: int = 0
    resolved: int = 0
    wins: int = 0
    risk_usd: float = 0.0
    pnl_usd: float = 0.0

    @property
    def rr(self) -> float | None:
        return self.pnl_usd / self.risk_usd if self.risk_usd else None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.resolved if self.resolved else None


@dataclass
class SlippageStats:
    rows: int = 0
    filled_usd: float = 0.0
    weighted_cents: float = 0.0
    max_cents: float | None = None

    def add(self, cost_usd: float, entry_price: float, avg_fill_price: float) -> None:
        cents = (avg_fill_price - entry_price) * 100.0
        self.rows += 1
        self.filled_usd += cost_usd
        self.weighted_cents += cents * cost_usd
        self.max_cents = cents if self.max_cents is None else max(self.max_cents, cents)

    @property
    def avg_cents(self) -> float | None:
        return self.weighted_cents / self.filled_usd if self.filled_usd else None


@dataclass
class CapacityLoss:
    insufficient_depth_usd: float = 0.0
    fak_miss_usd: float = 0.0
    ttl_expired_usd: float = 0.0
    risk_cap_usd: float = 0.0
    other_reject_usd: float = 0.0


@dataclass
class CalibrationSummary:
    decisions: dict[str, int] = field(default_factory=dict)
    would_allow: int = 0
    would_canary: int = 0
    would_block: int = 0
    missing: int = 0
    unmapped: int = 0


@dataclass
class SleeveReport:
    name: str
    label: str
    raw_snapshot_replay: ChainStats = field(default_factory=ChainStats)
    live_selected_replay: ChainStats = field(default_factory=ChainStats)
    filled_entry_replay: ChainStats = field(default_factory=ChainStats)
    filled_actual_price_replay: ChainStats = field(default_factory=ChainStats)
    actual_live_pnl: ChainStats = field(default_factory=ChainStats)
    unfilled_selected_replay: ChainStats = field(default_factory=ChainStats)
    slippage: SlippageStats = field(default_factory=SlippageStats)
    capacity_loss: CapacityLoss = field(default_factory=CapacityLoss)
    calibration: CalibrationSummary = field(default_factory=CalibrationSummary)
    settlement_mismatches: int = 0
    settlement_mismatch_pnl_usd: float = 0.0
    selected_rows: int = 0
    filled_rows: int = 0


def whole_chain_replay_specs(include_candidates: bool = True) -> list[ReplaySpec]:
    specs = [
        ReplaySpec(
            name="old_dynamic_core",
            label="LEGACY old dynamic core",
            source="model",
            identifier=PM_ACTIVE_DYNAMIC_TUNED_MODEL,
            target_notional_usd=50.0,
            market_family="HIGH_TEMP",
            selected_side="BUY_NO",
            entry_price_min=0.05,
            entry_price_max=0.50,
            edge_min=0.25,
            local_decision_start="12:00",
            local_decision_end="15:00",
        ),
        ReplaySpec(
            name="ngboost_buy_yes",
            label="LEGACY NGBoost BUY_YES",
            source="model",
            identifier=PM_ACTIVE_NGBOOST_MODEL,
            target_notional_usd=10.0,
            strategy_bucket="BEST_BUCKET",
            market_family="HIGH_TEMP",
            selected_side="BUY_YES",
            entry_price_min=0.05,
            entry_price_max=0.50,
            local_decision_start="12:00",
            local_decision_end="15:00",
        ),
        *default_replay_specs(include_hrrr_candidate=include_candidates),
    ]
    if include_candidates:
        specs.append(
            ReplaySpec(
                name="global_high_research",
                label="CANDIDATE global high research",
                source="consensus",
                identifier="global_high_dynamic_mvp",
                target_notional_usd=25.0,
                market_family="HIGH_TEMP",
                entry_price_min=0.05,
                entry_price_max=0.50,
                role="candidate",
            )
        )
    return specs


SLEEVES: tuple[SleeveConfig, ...] = (
    SleeveConfig(
        name="old_dynamic_core",
        label="LEGACY old dynamic core",
        live_strategies=("pm_us12_dynamic_tuned_hc_late_buy_no_edge_025_by_bucket_side_delay_first",),
        replay_spec_name="old_dynamic_core",
        calibration_family="obs",
    ),
    SleeveConfig(
        name="ngboost_buy_yes",
        label="LEGACY NGBoost BUY_YES",
        live_strategies=("pm_us12_ngboost_best_bucket_late_buy_yes_medium_by_bucket_side_delay_first",),
        replay_spec_name="ngboost_buy_yes",
        calibration_family="obs",
    ),
    SleeveConfig(
        name="live_consensus_no_tiny",
        label="LIVE US consensus no-tiny",
        live_strategies=("pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first",),
        replay_spec_name="live_consensus_no_tiny",
        calibration_family="obs",
    ),
    SleeveConfig(
        name="live_moonshot",
        label="LIVE moonshot",
        live_strategies=("pm_us12_dynamic_tuned_hc_late_entry_05_10_buy_no_by_bucket_side_delay_first",),
        replay_spec_name="live_moonshot",
        calibration_family="obs",
    ),
    SleeveConfig(
        name="global_low_tiny_tail",
        label="LIVE global low tiny tail",
        live_strategies=("global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first",),
        replay_spec_name="global_low_tiny_tail",
        calibration_family="global_low",
    ),
    SleeveConfig(
        name="global_low_canary",
        label="LIVE global low canary",
        live_strategies=("global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first",),
        replay_spec_name="global_low_canary",
        calibration_family="global_low",
    ),
    SleeveConfig(
        name="global_low_mvp_addon",
        label="LIVE global low MVP add-on",
        live_strategies=("global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first",),
        replay_spec_name="global_low_mvp_addon",
        calibration_family="global_low",
    ),
    SleeveConfig(
        name="hrrr_dynamic_tuned_inland_late_disagreement",
        label="CANDIDATE HRRR inland late disagreement",
        replay_spec_name="hrrr_dynamic_tuned_inland_late_disagreement",
        calibration_family="hrrr_rich",
    ),
    SleeveConfig(
        name="metar_hrrr_dynamic_tuned_inland_late_disagreement",
        label="CANDIDATE METAR+HRRR inland late disagreement",
        replay_spec_name="metar_hrrr_dynamic_tuned_inland_late_disagreement",
        calibration_family="metar_hrrr_rich",
    ),
    SleeveConfig(
        name="global_high_research",
        label="CANDIDATE global high research",
        replay_spec_name="global_high_research",
        calibration_family="global_high",
    ),
)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone())


def money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"${value:.2f}"


def fmt(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def cents(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}c"


def add_chain_result(stats: ChainStats, *, risk_usd: float, pnl_usd: float) -> None:
    stats.candidates += 1
    stats.resolved += 1
    stats.risk_usd += risk_usd
    stats.pnl_usd += pnl_usd
    if pnl_usd > 0:
        stats.wins += 1


def selected_won(row: sqlite3.Row, final_temp: float) -> bool:
    yes_won = bucket_won(float(final_temp), row["selected_bucket"])
    return bool(yes_won if row["selected_side"] == "BUY_YES" else not yes_won)


def weather_winning_side(row: sqlite3.Row, final_temp: float) -> str:
    return "BUY_YES" if bucket_won(float(final_temp), row["selected_bucket"]) else "BUY_NO"


def replay_pnl_usd(*, won: bool, price: float, notional: float) -> float:
    if price <= 0.0 or notional <= 0.0:
        return 0.0
    pnl_per_contract = (1.0 - price) if won else -price
    return (pnl_per_contract / price) * notional


def load_outcomes(research_db: Path) -> dict[tuple[str, str], dict[str, float | None]]:
    if not research_db.exists():
        return {}
    conn = sqlite3.connect(f"file:{research_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "station_date_outcomes"):
            return {}
        return {
            (str(row["station"]), str(row["market_date"])): {
                "HIGH_TEMP": row["final_high_tmpf"],
                "LOW_TEMP": row["final_low_tmpf"],
            }
            for row in conn.execute(
                "select station, market_date, final_high_tmpf, final_low_tmpf from station_date_outcomes"
            )
        }
    finally:
        conn.close()


def period_clause(column: str, start_date: str | None, end_date: str | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start_date:
        clauses.append(f"substr({column}, 1, 10) >= ?")
        params.append(start_date)
    if end_date:
        clauses.append(f"substr({column}, 1, 10) <= ?")
        params.append(end_date)
    return (" and ".join(clauses) if clauses else "1=1"), params


def load_live_rows(live_db: Path, start_date: str | None, end_date: str | None) -> list[sqlite3.Row]:
    if not live_db.exists():
        return []
    conn = sqlite3.connect(live_db)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "live_policy_positions"):
            return []
        where, params = period_clause("timestamp", start_date, end_date)
        return list(conn.execute(f"select * from live_policy_positions where {where} order by timestamp, id", params))
    finally:
        conn.close()


def classify_capacity_reason(final_reason: str) -> str:
    reason = final_reason.upper()
    if "INSUFFICIENT_DEPTH" in reason:
        return "insufficient_depth_usd"
    if "NO ORDERS FOUND" in reason:
        return "fak_miss_usd"
    if "RESTING_TTL_EXPIRED" in reason:
        return "ttl_expired_usd"
    if reason.startswith("RISK_") or "_CAP" in reason:
        return "risk_cap_usd"
    return "other_reject_usd"


def attach_capacity_loss(
    live_db: Path,
    reports: dict[str, SleeveReport],
    strategy_to_sleeve: dict[str, str],
    start_date: str | None,
    end_date: str | None,
) -> None:
    if not live_db.exists():
        return
    conn = sqlite3.connect(live_db)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "live_order_attempts") or not table_exists(conn, "live_policy_positions"):
            return
        where, params = period_clause("a.timestamp", start_date, end_date)
        rows = conn.execute(
            f"""
            select a.final_state, a.final_reason, a.target_notional_usd, a.cost_usd, p.strategy_name
            from live_order_attempts a
            join live_policy_positions p on p.id = a.live_position_id
            where {where}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        state = str(row["final_state"] or "").upper()
        reason = str(row["final_reason"] or "")
        if state not in {"REJECTED", "FAILED", "ERROR", "CANCELLED"} and "TTL" not in reason.upper():
            continue
        sleeve_name = strategy_to_sleeve.get(str(row["strategy_name"]))
        if sleeve_name is None:
            continue
        lost = max(0.0, float(row["target_notional_usd"] or 0.0) - float(row["cost_usd"] or 0.0))
        attr = classify_capacity_reason(reason)
        cap = reports[sleeve_name].capacity_loss
        setattr(cap, attr, getattr(cap, attr) + lost)


def load_calibration_payload(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.expanduser().open("r", encoding="utf-8") as fh:
        return json.load(fh)


def fallback_calibration_decision(
    payload: dict[str, Any] | None,
    *,
    family: str | None,
    station: str,
    side: str,
    entry_price: float,
) -> str | None:
    if payload is None or family is None:
        return None
    band = calibration_entry_band(entry_price)
    bucket = (
        payload.get("families", {})
        .get(family, {})
        .get("buckets", {})
        .get(station, {})
        .get(side, {})
        .get(band)
    )
    if not isinstance(bucket, dict):
        return "INSUFFICIENT_DATA"
    return str(bucket.get("decision") or "INSUFFICIENT_DATA")


def recorded_calibration_decision(row: sqlite3.Row) -> str | None:
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    calibration = raw.get("calibration") if isinstance(raw, dict) else None
    if isinstance(calibration, dict) and calibration.get("decision"):
        return str(calibration["decision"])
    return None


def add_calibration(summary: CalibrationSummary, decision: str | None, mapped: bool) -> None:
    if not mapped:
        summary.unmapped += 1
        summary.decisions["UNMAPPED"] = summary.decisions.get("UNMAPPED", 0) + 1
        return
    if decision is None:
        summary.missing += 1
        summary.decisions["MISSING"] = summary.decisions.get("MISSING", 0) + 1
        return
    summary.decisions[decision] = summary.decisions.get(decision, 0) + 1
    if decision in {"TRADE", "WATCH", "DISABLED"}:
        summary.would_allow += 1
    elif decision == "CANARY":
        summary.would_canary += 1
    elif decision == "BLOCK":
        summary.would_block += 1
    else:
        summary.missing += 1


def build_raw_replay(
    research_db: Path,
    start_date: str | None,
    end_date: str | None,
    include_candidates: bool,
) -> tuple[dict[str, ChainStats], dict[str, Any]]:
    if not research_db.exists():
        return {}, {"error": f"research DB missing: {research_db}"}
    rows, metadata = load_replay_rows(research_db, start_date=start_date, end_date=end_date)
    specs = whole_chain_replay_specs(include_candidates=include_candidates)
    portfolio = build_portfolio_report(rows, specs, caps=RiskCaps(), use_depth=True)
    by_name: dict[str, ChainStats] = {}
    for row in portfolio:
        stats = ChainStats(
            candidates=row.stats.candidate_rows,
            resolved=row.stats.allocated_rows,
            wins=row.stats.wins,
            risk_usd=row.stats.risk_usd,
            pnl_usd=row.stats.pnl_usd,
        )
        by_name[row.name] = stats
    metadata["start_date"] = start_date
    metadata["end_date"] = end_date
    metadata["source"] = "raw prediction_snapshots plus in-memory consensus; current-stack caps/depth applied"
    return by_name, metadata


def build_report(
    live_db: Path,
    research_db: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    calibration_path: Path | None = None,
    include_candidates: bool = True,
) -> dict[str, Any]:
    raw_replay, replay_metadata = build_raw_replay(research_db, start_date, end_date, include_candidates)
    reports = {sleeve.name: SleeveReport(name=sleeve.name, label=sleeve.label) for sleeve in SLEEVES}
    for sleeve in SLEEVES:
        if sleeve.replay_spec_name and sleeve.replay_spec_name in raw_replay:
            reports[sleeve.name].raw_snapshot_replay = raw_replay[sleeve.replay_spec_name]

    outcomes = load_outcomes(research_db)
    live_rows = load_live_rows(live_db, start_date, end_date)
    calibration_payload = load_calibration_payload(calibration_path)
    strategy_to_sleeve = {
        strategy: sleeve.name
        for sleeve in SLEEVES
        for strategy in sleeve.live_strategies
    }
    sleeve_by_name = {sleeve.name: sleeve for sleeve in SLEEVES}

    for row in live_rows:
        sleeve_name = strategy_to_sleeve.get(str(row["strategy_name"]))
        if sleeve_name is None:
            continue
        sleeve = sleeve_by_name[sleeve_name]
        report = reports[sleeve_name]
        report.selected_rows += 1
        if float(row["cost_usd"] or 0.0) > 0:
            report.filled_rows += 1

        entry = float(row["entry_price"])
        avg_fill = float(row["avg_entry_price"] or entry)
        target_notional = float(row["target_notional_usd"] or 0.0)
        filled_notional = float(row["cost_usd"] or 0.0)
        family = str(row["market_family"] or "HIGH_TEMP")
        outcome = outcomes.get((str(row["station"]), str(row["market_date"])), {})
        final_temp = outcome.get(family)
        if final_temp is not None:
            won = selected_won(row, float(final_temp))
            add_chain_result(
                report.live_selected_replay,
                risk_usd=target_notional,
                pnl_usd=replay_pnl_usd(won=won, price=entry, notional=target_notional),
            )
            if filled_notional > 0.0:
                add_chain_result(
                    report.filled_entry_replay,
                    risk_usd=filled_notional,
                    pnl_usd=replay_pnl_usd(won=won, price=entry, notional=filled_notional),
                )
                add_chain_result(
                    report.filled_actual_price_replay,
                    risk_usd=filled_notional,
                    pnl_usd=replay_pnl_usd(won=won, price=avg_fill, notional=filled_notional),
                )
            else:
                add_chain_result(
                    report.unfilled_selected_replay,
                    risk_usd=target_notional,
                    pnl_usd=replay_pnl_usd(won=won, price=entry, notional=target_notional),
                )
            settled_side = row["winning_side"] if "winning_side" in row.keys() else None
            if settled_side is not None and str(row["state"]).upper() == "SETTLED":
                weather_side = weather_winning_side(row, float(final_temp))
                if str(settled_side) != weather_side:
                    report.settlement_mismatches += 1
                    report.settlement_mismatch_pnl_usd += float(row["realized_pnl"] or 0.0)

        realized_pnl = row["realized_pnl"] if "realized_pnl" in row.keys() else None
        mark_pnl = row["unrealized_pnl"] if "unrealized_pnl" in row.keys() else None
        actual_pnl = realized_pnl if realized_pnl is not None else mark_pnl
        if actual_pnl is not None:
            add_chain_result(
                report.actual_live_pnl,
                risk_usd=filled_notional,
                pnl_usd=float(actual_pnl or 0.0),
            )

        if filled_notional > 0.0:
            report.slippage.add(filled_notional, entry, avg_fill)

        decision = recorded_calibration_decision(row)
        if decision is None:
            decision = fallback_calibration_decision(
                calibration_payload,
                family=sleeve.calibration_family,
                station=str(row["station"]),
                side=str(row["selected_side"]),
                entry_price=entry,
            )
        add_calibration(report.calibration, decision, mapped=sleeve.calibration_family is not None)

    attach_capacity_loss(live_db, reports, strategy_to_sleeve, start_date, end_date)

    rendered_sleeves = [sleeve for sleeve in SLEEVES if include_candidates or sleeve.live_strategies]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "live_db": str(live_db),
        "research_db": str(research_db),
        "start_date": start_date,
        "end_date": end_date,
        "calibration_path": str(calibration_path) if calibration_path else None,
        "raw_replay_metadata": replay_metadata,
        "sleeves": [sleeve_report_to_dict(reports[sleeve.name]) for sleeve in rendered_sleeves],
    }


def chain_stats_to_dict(stats: ChainStats) -> dict[str, Any]:
    data = asdict(stats)
    data["rr"] = stats.rr
    data["win_rate"] = stats.win_rate
    return data


def slippage_to_dict(stats: SlippageStats) -> dict[str, Any]:
    data = asdict(stats)
    data["avg_cents"] = stats.avg_cents
    return data


def sleeve_report_to_dict(report: SleeveReport) -> dict[str, Any]:
    return {
        "name": report.name,
        "label": report.label,
        "raw_snapshot_replay": chain_stats_to_dict(report.raw_snapshot_replay),
        "live_selected_replay": chain_stats_to_dict(report.live_selected_replay),
        "filled_entry_replay": chain_stats_to_dict(report.filled_entry_replay),
        "filled_actual_price_replay": chain_stats_to_dict(report.filled_actual_price_replay),
        "actual_live_pnl": chain_stats_to_dict(report.actual_live_pnl),
        "unfilled_selected_replay": chain_stats_to_dict(report.unfilled_selected_replay),
        "slippage": slippage_to_dict(report.slippage),
        "capacity_loss": asdict(report.capacity_loss),
        "calibration": asdict(report.calibration),
        "settlement_mismatches": report.settlement_mismatches,
        "settlement_mismatch_pnl_usd": report.settlement_mismatch_pnl_usd,
        "selected_rows": report.selected_rows,
        "filled_rows": report.filled_rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Whole-Chain Truth Report",
        "",
        f"- generated_at: {report['generated_at']}",
        f"- live_db: {report['live_db']}",
        f"- research_db: {report['research_db']}",
        f"- period: {report.get('start_date') or '-inf'} to {report.get('end_date') or '+inf'}",
        f"- calibration_path: {report.get('calibration_path') or 'n/a'}",
        f"- raw replay source: {report['raw_replay_metadata'].get('source', 'n/a')}",
        f"- resolved_through: {report['raw_replay_metadata'].get('resolved_through', 'n/a')}",
        "",
        "## Sleeve Chain",
        "",
        "| sleeve | raw replay pnl/rr | live-selected pnl/rr | filled @ entry pnl/rr | filled @ actual pnl/rr | actual PnL/RR | filled vs unfilled replay | slip avg/max | settlement mismatches | calibration allow/canary/block |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["sleeves"]:
        raw = row["raw_snapshot_replay"]
        selected = row["live_selected_replay"]
        filled_entry = row["filled_entry_replay"]
        filled_actual = row["filled_actual_price_replay"]
        actual = row["actual_live_pnl"]
        unfilled = row["unfilled_selected_replay"]
        slip = row["slippage"]
        cal = row["calibration"]
        lines.append(
            "| {sleeve} | {raw_pnl} / {raw_rr} ({raw_n}) | {sel_pnl} / {sel_rr} ({sel_n}) | {fe_pnl} / {fe_rr} ({fe_n}) | {fa_pnl} / {fa_rr} ({fa_n}) | {act_pnl} / {act_rr} ({act_n}) | {filled_rr} vs {unfilled_rr} | {avg_slip} / {max_slip} | {mismatch} ({mismatch_pnl}) | {allow}/{canary}/{block} |".format(
                sleeve=row["label"],
                raw_pnl=money(raw["pnl_usd"]),
                raw_rr=fmt(raw["rr"]),
                raw_n=raw["resolved"],
                sel_pnl=money(selected["pnl_usd"]),
                sel_rr=fmt(selected["rr"]),
                sel_n=selected["resolved"],
                fe_pnl=money(filled_entry["pnl_usd"]),
                fe_rr=fmt(filled_entry["rr"]),
                fe_n=filled_entry["resolved"],
                fa_pnl=money(filled_actual["pnl_usd"]),
                fa_rr=fmt(filled_actual["rr"]),
                fa_n=filled_actual["resolved"],
                act_pnl=money(actual["pnl_usd"]),
                act_rr=fmt(actual["rr"]),
                act_n=actual["resolved"],
                filled_rr=fmt(filled_entry["rr"]),
                unfilled_rr=fmt(unfilled["rr"]),
                avg_slip=cents(slip["avg_cents"]),
                max_slip=cents(slip["max_cents"]),
                mismatch=row["settlement_mismatches"],
                mismatch_pnl=money(row["settlement_mismatch_pnl_usd"]),
                allow=cal["would_allow"],
                canary=cal["would_canary"],
                block=cal["would_block"],
            )
        )

    lines.extend(
        [
            "",
            "## Lost Capacity",
            "",
            "| sleeve | selected | filled rows | depth | FAK miss | TTL expired | risk caps | other rejects |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["sleeves"]:
        cap = row["capacity_loss"]
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                row["label"],
                row["selected_rows"],
                row["filled_rows"],
                money(cap["insufficient_depth_usd"]),
                money(cap["fak_miss_usd"]),
                money(cap["ttl_expired_usd"]),
                money(cap["risk_cap_usd"]),
                money(cap["other_reject_usd"]),
            )
        )

    lines.extend(
        [
            "",
            "## Calibration Decisions",
            "",
            "| sleeve | decisions | missing | unmapped |",
            "|---|---|---:|---:|",
        ]
    )
    for row in report["sleeves"]:
        cal = row["calibration"]
        decisions = ", ".join(f"{key}:{value}" for key, value in sorted(cal["decisions"].items())) or "n/a"
        lines.append(f"| {row['label']} | {decisions} | {cal['missing']} | {cal['unmapped']} |")

    lines.extend(
        [
            "",
            "## Interpretation Guide",
            "",
            "- raw snapshot replay tests the original hypothesis under current replay caps/depth.",
            "- live-selected replay scores exactly what the live loop reserved, whether filled or not.",
            "- filled @ entry isolates fill-selection bias from price slippage.",
            "- filled @ actual reprices the same weather-outcome replay at actual average fill price.",
            "- actual PnL is the live ledger's Polymarket settlement or mark state when available.",
            "- settlement mismatches compare official weather-outcome scoring with Polymarket winning side.",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Whole-chain truth report from research and live SQLite ledgers.")
    parser.add_argument("--live-db", default=str(DEFAULT_LIVE_DB))
    parser.add_argument("--research-db", default=str(DEFAULT_RESEARCH_DB))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--calibration-path")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out", help="Optional path to write the report instead of stdout.")
    parser.add_argument("--no-candidates", action="store_true", help="Exclude candidate-only replay sleeves.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(
        Path(args.live_db).expanduser(),
        Path(args.research_db).expanduser(),
        start_date=args.start_date,
        end_date=args.end_date,
        calibration_path=Path(args.calibration_path).expanduser() if args.calibration_path else None,
        include_candidates=not args.no_candidates,
    )
    text = json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else render_markdown(report)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
