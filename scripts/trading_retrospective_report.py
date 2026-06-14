#!/usr/bin/env python3
"""Weekly live-trading retrospective from SQLite.

The report is intentionally standalone: it reads the live SQLite ledger,
optionally compares the period to the research portfolio replay, and emits
Markdown or JSON for manual Sunday/Monday review.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.portfolio_promotion_report import (  # noqa: E402
    RiskCaps,
    build_portfolio_report,
    default_replay_specs,
    load_replay_rows,
)

DEFAULT_LIVE_DB = Path.home() / ".local/state/roboweather/live_trading.sqlite"
DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"


@dataclass(frozen=True)
class Thresholds:
    min_resolved_positions: int = 3
    review_realized_rr: float = 0.0
    kill_realized_rr: float = -0.25
    review_fill_rate: float = 0.50
    review_terminal_reject_rate: float = 0.10
    promotion_min_fills: int = 20
    promotion_min_rr: float = 0.25
    size_up_min_rr: float = 0.50


@dataclass
class PolicySummary:
    policy: str
    positions: int = 0
    resolved_positions: int = 0
    intended_notional_usd: float = 0.0
    filled_notional_usd: float = 0.0
    live_expected_ev_usd: float = 0.0
    live_filled_expected_ev_usd: float = 0.0
    live_realized_pnl_usd: float = 0.0
    attempts: int = 0
    rejected_attempts: int = 0
    terminal_reject_attempts: int = 0
    child_fak_miss_then_resting_attempts: int = 0
    resting_ttl_expired_attempts: int = 0
    order_construction_error_attempts: int = 0
    partial_fill_attempts: int = 0

    @property
    def fill_rate(self) -> float | None:
        return self.filled_notional_usd / self.intended_notional_usd if self.intended_notional_usd else None

    @property
    def realized_rr(self) -> float | None:
        return self.live_realized_pnl_usd / self.filled_notional_usd if self.filled_notional_usd else None

    @property
    def reject_rate(self) -> float | None:
        return self.rejected_attempts / self.attempts if self.attempts else None

    @property
    def terminal_reject_rate(self) -> float | None:
        return self.terminal_reject_attempts / self.attempts if self.attempts else None

    @property
    def missed_expected_ev_usd(self) -> float:
        return self.live_expected_ev_usd - self.live_filled_expected_ev_usd


@dataclass(frozen=True)
class Period:
    start_date: str
    end_date: str
    start_timestamp: str | None = None
    end_timestamp: str | None = None

    def sql_clause(self, column: str) -> tuple[str, tuple[str, ...]]:
        if self.start_timestamp or self.end_timestamp:
            clauses: list[str] = []
            params: list[str] = []
            if self.start_timestamp:
                clauses.append(f"{column} >= ?")
                params.append(self.start_timestamp)
            if self.end_timestamp:
                clauses.append(f"{column} <= ?")
                params.append(self.end_timestamp)
            return " and ".join(clauses), tuple(params)
        return f"substr({column}, 1, 10) between ? and ?", (self.start_date, self.end_date)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def default_period(today: date | None = None) -> tuple[str, str]:
    """Return the previous 7 resolved calendar days, ending yesterday."""
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


def money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"${value:.2f}"


def fmt(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}"


def pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{100.0 * value:.1f}%"


def expected_ev_usd(row: sqlite3.Row, notional: float | None = None) -> float:
    edge = row["entry_edge"] if "entry_edge" in row.keys() else None
    entry = row["entry_price"] if "entry_price" in row.keys() else None
    if notional is None:
        notional = row["target_notional_usd"] if "target_notional_usd" in row.keys() else None
    if edge is None or entry is None or notional is None or float(entry) <= 0:
        return 0.0
    return float(notional) * (float(edge) / float(entry))


def load_policy_summaries(conn: sqlite3.Connection, period: Period) -> dict[str, PolicySummary]:
    if not table_exists(conn, "live_policy_positions"):
        return {}
    conn.row_factory = sqlite3.Row
    cols = columns(conn, "live_policy_positions")
    required = {"timestamp", "strategy_name", "target_notional_usd", "cost_usd"}
    if not required.issubset(cols):
        return {}

    where_clause, params = period.sql_clause("timestamp")
    query = f"""
        select *
        from live_policy_positions
        where {where_clause}
        order by timestamp, id
    """
    summaries: dict[str, PolicySummary] = {}
    for row in conn.execute(query, params):
        policy = str(row["strategy_name"])
        summary = summaries.setdefault(policy, PolicySummary(policy=policy))
        filled_notional = float(row["cost_usd"] or 0.0)
        summary.positions += 1
        summary.intended_notional_usd += float(row["target_notional_usd"] or 0.0)
        summary.filled_notional_usd += filled_notional
        summary.live_expected_ev_usd += expected_ev_usd(row)
        summary.live_filled_expected_ev_usd += expected_ev_usd(row, filled_notional)
        summary.live_realized_pnl_usd += float(row["realized_pnl"] or 0.0) if "realized_pnl" in row.keys() else 0.0
        state = str(row["state"] if "state" in row.keys() else "")
        if "realized_pnl" in row.keys() and row["realized_pnl"] is not None:
            summary.resolved_positions += 1
        elif state.upper() in {"RESOLVED", "SETTLED", "CLOSED"}:
            summary.resolved_positions += 1
    return summaries


def is_rejectish(state: str, reason: str) -> bool:
    upper_state = state.upper()
    upper_reason = reason.upper()
    return upper_state in {"REJECTED", "FAILED", "ERROR"} or "REJECT" in upper_reason or "ERROR" in upper_reason


def classify_attempt(row: sqlite3.Row, has_resting_child: bool) -> str | None:
    state = str(row["final_state"] or "")
    reason = str(row["final_reason"] or "")
    order_mode = str(row["order_mode"] or "")
    parent_state = str(row["parent_state"] or "")
    cost_usd = float(row["cost_usd"] or 0.0)
    target_notional = float(row["target_notional_usd"] or 0.0)
    upper_reason = reason.upper()
    upper_state = state.upper()
    upper_mode = order_mode.upper()

    if cost_usd > 0.0 and target_notional > 0.0 and cost_usd < target_notional - 1e-6:
        return "partial_fill"
    if "RESTING_TTL_EXPIRED" in upper_reason or (upper_mode == "GTC" and upper_state == "CANCELLED"):
        return "resting_ttl_expired"
    if "TICK" in upper_reason or "ALLOWANCE" in upper_reason or "KILL_SWITCH" in upper_reason or "INVALID" in upper_reason:
        return "order_construction_error"
    if upper_mode == "FAK" and "NO ORDERS FOUND" in upper_reason and has_resting_child:
        return "child_fak_miss_then_resting"
    if is_rejectish(state, reason) and parent_state.upper() == "REJECTED":
        return "terminal_reject"
    if is_rejectish(state, reason):
        return "nonterminal_reject"
    return None


def attach_attempt_summaries(conn: sqlite3.Connection, summaries: dict[str, PolicySummary], period: Period) -> list[dict[str, Any]]:
    if not table_exists(conn, "live_order_attempts") or not table_exists(conn, "live_policy_positions"):
        return []
    attempt_cols = columns(conn, "live_order_attempts")
    if not {"timestamp", "live_position_id", "final_state", "final_reason", "order_mode"}.issubset(attempt_cols):
        return []

    has_resting = {
        int(row[0]): bool(row[1])
        for row in conn.execute(
            """
            select live_position_id, max(case when upper(order_mode) = 'GTC' then 1 else 0 end) has_resting
            from live_order_attempts
            group by live_position_id
            """
        )
    }
    where_clause, params = period.sql_clause("a.timestamp")
    query = f"""
        select a.*, p.strategy_name, p.state parent_state
        from live_order_attempts a
        join live_policy_positions p on p.id = a.live_position_id
        where {where_clause}
        order by a.timestamp, a.id
    """
    category_counts: dict[tuple[str, str], dict[str, Any]] = {}
    for row in conn.execute(query, params):
        policy = str(row["strategy_name"])
        summary = summaries.setdefault(policy, PolicySummary(policy=policy))
        summary.attempts += 1
        if is_rejectish(str(row["final_state"] or ""), str(row["final_reason"] or "")):
            summary.rejected_attempts += 1
        category = classify_attempt(row, has_resting.get(int(row["live_position_id"]), False))
        if category == "terminal_reject":
            summary.terminal_reject_attempts += 1
        elif category == "child_fak_miss_then_resting":
            summary.child_fak_miss_then_resting_attempts += 1
        elif category == "resting_ttl_expired":
            summary.resting_ttl_expired_attempts += 1
        elif category == "order_construction_error":
            summary.order_construction_error_attempts += 1
        elif category == "partial_fill":
            summary.partial_fill_attempts += 1
        if category is not None:
            key = (category, str(row["final_reason"] or ""))
            record = category_counts.setdefault(
                key,
                {
                    "category": category,
                    "reason": str(row["final_reason"] or ""),
                    "attempts": 0,
                    "intended_notional_usd": 0.0,
                    "filled_notional_usd": 0.0,
                },
            )
            record["attempts"] += 1
            record["intended_notional_usd"] += float(row["target_notional_usd"] or 0.0)
            record["filled_notional_usd"] += float(row["cost_usd"] or 0.0)
    return sorted(category_counts.values(), key=lambda item: (-int(item["attempts"]), str(item["category"]), str(item["reason"])))


def load_reject_reasons(conn: sqlite3.Connection, period: Period) -> list[dict[str, Any]]:
    if not table_exists(conn, "live_order_attempts"):
        return []
    attempt_cols = columns(conn, "live_order_attempts")
    if not {"timestamp", "final_state", "final_reason"}.issubset(attempt_cols):
        return []
    where_clause, params = period.sql_clause("timestamp")
    query = f"""
        select final_reason,
               final_state,
               count(*) attempts,
               sum(target_notional_usd) intended_notional_usd,
               sum(cost_usd) filled_notional_usd
        from live_order_attempts
        where {where_clause}
          and (
              upper(final_state) in ('REJECTED', 'FAILED', 'ERROR')
              or upper(final_reason) like '%REJECT%'
              or upper(final_reason) like '%ERROR%'
          )
        group by final_reason, final_state
        order by attempts desc, final_reason
    """
    return [
        {
            "reason": row[0],
            "state": row[1],
            "attempts": int(row[2] or 0),
            "intended_notional_usd": float(row[3] or 0.0),
            "filled_notional_usd": float(row[4] or 0.0),
        }
        for row in conn.execute(query, params)
    ]


def load_event_counts(conn: sqlite3.Connection, period: Period) -> list[dict[str, Any]]:
    if not table_exists(conn, "live_trade_events"):
        return []
    event_cols = columns(conn, "live_trade_events")
    if not {"timestamp", "event_type"}.issubset(event_cols):
        return []
    where_clause, params = period.sql_clause("timestamp")
    query = f"""
        select event_type, count(*) events
        from live_trade_events
        where {where_clause}
        group by event_type
        order by events desc, event_type
    """
    return [{"event_type": row[0], "events": int(row[1] or 0)} for row in conn.execute(query, params)]


def build_replay_summary(research_db: Path | None, start_date: str, end_date: str) -> dict[str, Any] | None:
    if research_db is None or not research_db.exists():
        return None
    rows, metadata = load_replay_rows(research_db, start_date=start_date, end_date=end_date)
    report = build_portfolio_report(rows, default_replay_specs(include_hrrr_candidate=False), caps=RiskCaps(), use_depth=True)
    sleeves = []
    total_risk = 0.0
    total_pnl = 0.0
    for row in report:
        total_risk += row.stats.risk_usd
        total_pnl += row.stats.pnl_usd
        sleeves.append(
            {
                "sleeve": row.label,
                "risk_usd": row.stats.risk_usd,
                "resolved_replay_pnl_usd": row.stats.pnl_usd,
                "rr": row.stats.rr,
                "fills": row.stats.allocated_rows,
            }
        )
    return {
        "metadata": metadata,
        "total_risk_usd": total_risk,
        "resolved_replay_pnl_usd": total_pnl,
        "rr": total_pnl / total_risk if total_risk else None,
        "sleeves": sleeves,
    }



def promotion_status(role: str, fills: int, pnl: float, rr: float | None, thresholds: Thresholds) -> tuple[str, str]:
    if fills <= 0:
        return "NO_SAMPLE", "no resolved replay fills in this window"
    if role == "candidate":
        if fills < thresholds.promotion_min_fills:
            if pnl > 0.0 and rr is not None and rr > 0.0:
                return "WATCH_LOW_SAMPLE", "positive but below promotion sample gate"
            return "REJECT_OR_WAIT", "below sample gate and not clearly positive"
        if pnl > 0.0 and rr is not None and rr >= thresholds.promotion_min_rr:
            return "PROMOTE_REVIEW", "meets empirical R/R gate; still needs operator review"
        if rr is not None and rr <= 0.0:
            return "REJECT_REVIEW", "non-positive empirical R/R"
        return "WATCH", "positive but below promotion R/R gate"

    if fills < thresholds.min_resolved_positions:
        return "WATCH_LOW_SAMPLE", "live sleeve has too few resolved replay fills"
    if rr is not None and rr <= 0.0:
        return "DEACTIVATE_REVIEW", "live sleeve has non-positive empirical R/R"
    if rr is not None and rr >= thresholds.size_up_min_rr:
        return "SIZE_UP_REVIEW", "live sleeve clears size-up R/R screen"
    return "KEEP_WATCH", "positive but below size-up screen"


def build_promotion_review(research_db: Path | None, start_date: str, end_date: str, thresholds: Thresholds) -> dict[str, Any] | None:
    if research_db is None or not research_db.exists():
        return None
    rows, metadata = load_replay_rows(research_db, start_date=start_date, end_date=end_date)
    report = build_portfolio_report(rows, default_replay_specs(include_hrrr_candidate=True), caps=RiskCaps(), use_depth=True)
    sleeves: list[dict[str, Any]] = []
    for row in report:
        stats = row.stats
        status, reason = promotion_status(row.role, stats.allocated_rows, stats.pnl_usd, stats.rr, thresholds)
        sleeves.append(
            {
                "role": row.role,
                "sleeve": row.label,
                "fills": stats.allocated_rows,
                "candidates": stats.candidate_rows,
                "risk_usd": stats.risk_usd,
                "empirical_pnl_usd": stats.pnl_usd,
                "rr": stats.rr,
                "win_rate": stats.win_rate,
                "skipped_capacity_rows": stats.skipped_capacity_rows,
                "skipped_depth_rows": stats.skipped_depth_rows,
                "status": status,
                "reason": reason,
            }
        )
    return {"metadata": metadata, "sleeves": sleeves}


def threshold_flags(summary: PolicySummary, thresholds: Thresholds) -> list[str]:
    flags: list[str] = []
    if summary.resolved_positions >= thresholds.min_resolved_positions:
        rr = summary.realized_rr
        if rr is not None and rr <= thresholds.kill_realized_rr:
            flags.append("KILL_REVIEW_REALIZED_RR")
        elif rr is not None and rr <= thresholds.review_realized_rr:
            flags.append("REVIEW_REALIZED_RR")
    fill_rate = summary.fill_rate
    if fill_rate is not None and summary.positions > 0 and fill_rate < thresholds.review_fill_rate:
        flags.append("REVIEW_FILL_RATE")
    terminal_reject_rate = summary.terminal_reject_rate
    if terminal_reject_rate is not None and summary.attempts > 0 and terminal_reject_rate >= thresholds.review_terminal_reject_rate:
        flags.append("REVIEW_TERMINAL_REJECT_RATE")
    if summary.order_construction_error_attempts > 0:
        flags.append("REVIEW_ORDER_CONSTRUCTION_ERRORS")
    return flags


def build_report(
    live_db: Path,
    research_db: Path | None,
    start_date: str,
    end_date: str,
    thresholds: Thresholds,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> dict[str, Any]:
    period = Period(start_date, end_date, start_timestamp=start_timestamp, end_timestamp=end_timestamp)
    conn = sqlite3.connect(live_db)
    conn.row_factory = sqlite3.Row
    try:
        summaries = load_policy_summaries(conn, period)
        execution_categories = attach_attempt_summaries(conn, summaries, period)
        reject_reasons = load_reject_reasons(conn, period)
        event_counts = load_event_counts(conn, period)
    finally:
        conn.close()

    policy_rows = []
    for summary in sorted(summaries.values(), key=lambda item: item.policy):
        row = asdict(summary)
        row["fill_rate"] = summary.fill_rate
        row["realized_rr"] = summary.realized_rr
        row["reject_rate"] = summary.reject_rate
        row["terminal_reject_rate"] = summary.terminal_reject_rate
        row["missed_expected_ev_usd"] = summary.missed_expected_ev_usd
        row["flags"] = threshold_flags(summary, thresholds)
        policy_rows.append(row)

    totals = {
        "positions": sum(item.positions for item in summaries.values()),
        "resolved_positions": sum(item.resolved_positions for item in summaries.values()),
        "intended_notional_usd": sum(item.intended_notional_usd for item in summaries.values()),
        "filled_notional_usd": sum(item.filled_notional_usd for item in summaries.values()),
        "live_expected_ev_usd": sum(item.live_expected_ev_usd for item in summaries.values()),
        "live_filled_expected_ev_usd": sum(item.live_filled_expected_ev_usd for item in summaries.values()),
        "live_realized_pnl_usd": sum(item.live_realized_pnl_usd for item in summaries.values()),
        "attempts": sum(item.attempts for item in summaries.values()),
        "rejected_attempts": sum(item.rejected_attempts for item in summaries.values()),
        "terminal_reject_attempts": sum(item.terminal_reject_attempts for item in summaries.values()),
        "child_fak_miss_then_resting_attempts": sum(item.child_fak_miss_then_resting_attempts for item in summaries.values()),
        "resting_ttl_expired_attempts": sum(item.resting_ttl_expired_attempts for item in summaries.values()),
        "order_construction_error_attempts": sum(item.order_construction_error_attempts for item in summaries.values()),
        "partial_fill_attempts": sum(item.partial_fill_attempts for item in summaries.values()),
    }
    totals["missed_expected_ev_usd"] = totals["live_expected_ev_usd"] - totals["live_filled_expected_ev_usd"]
    totals["fill_rate"] = totals["filled_notional_usd"] / totals["intended_notional_usd"] if totals["intended_notional_usd"] else None
    totals["realized_rr"] = totals["live_realized_pnl_usd"] / totals["filled_notional_usd"] if totals["filled_notional_usd"] else None
    totals["reject_rate"] = totals["rejected_attempts"] / totals["attempts"] if totals["attempts"] else None
    totals["terminal_reject_rate"] = totals["terminal_reject_attempts"] / totals["attempts"] if totals["attempts"] else None

    return {
        "period": asdict(period),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "live_db": str(live_db),
        "research_db": str(research_db) if research_db else None,
        "thresholds": asdict(thresholds),
        "totals": totals,
        "policies": policy_rows,
        "execution_categories": execution_categories,
        "reject_reasons": reject_reasons,
        "event_counts": event_counts,
        "portfolio_replay": build_replay_summary(research_db, start_date, end_date),
        "promotion_review": build_promotion_review(research_db, start_date, end_date, thresholds),
    }


def render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    replay = report["portfolio_replay"]
    promotion = report.get("promotion_review")
    period = report["period"]
    period_text = f"{period['start_date']} to {period['end_date']}"
    if period.get("start_timestamp") or period.get("end_timestamp"):
        period_text += f"; timestamps {period.get('start_timestamp') or '-inf'} to {period.get('end_timestamp') or '+inf'}"
    lines = [
        "# Weekly Trading Retrospective",
        "",
        f"- period: {period_text}",
        f"- generated_at: {report['generated_at']}",
        f"- live_db: {report['live_db']}",
        f"- research_db: {report['research_db'] or 'n/a'}",
        "",
        "## Executive Summary",
        "",
        f"- model-implied EV from intended exposure, uncalibrated: {money(totals['live_expected_ev_usd'])}",
        f"- model-implied EV on filled exposure, uncalibrated: {money(totals['live_filled_expected_ev_usd'])}",
        f"- missed model-implied EV from unfilled exposure: {money(totals['missed_expected_ev_usd'])}",
        f"- live realized PnL: {money(totals['live_realized_pnl_usd'])}",
        f"- intended notional: {money(totals['intended_notional_usd'])}",
        f"- filled notional/cost: {money(totals['filled_notional_usd'])}",
        f"- fill rate: {pct(totals['fill_rate'])}",
        f"- order attempts: {totals['attempts']}",
        f"- raw rejected attempts: {totals['rejected_attempts']} ({pct(totals['reject_rate'])})",
        f"- terminal reject attempts: {totals['terminal_reject_attempts']} ({pct(totals['terminal_reject_rate'])})",
        f"- child FAK misses followed by resting: {totals['child_fak_miss_then_resting_attempts']}",
        f"- resting TTL expirations: {totals['resting_ttl_expired_attempts']}",
        f"- order construction errors: {totals['order_construction_error_attempts']}",
        f"- partial-fill attempts: {totals['partial_fill_attempts']}",
        f"- resolved positions: {totals['resolved_positions']} / {totals['positions']}",
    ]
    if replay:
        lines.extend(
            [
                f"- empirical replay EV/PnL proxy, resolved outcomes: {money(replay['resolved_replay_pnl_usd'])}",
                f"- portfolio replay risk: {money(replay['total_risk_usd'])}",
                f"- portfolio replay R/R: {fmt(replay['rr'])}",
            ]
        )
        if promotion:
            actionable = [row for row in promotion["sleeves"] if str(row["status"]).endswith("REVIEW")]
            watched = [row for row in promotion["sleeves"] if str(row["status"]).startswith("WATCH")]
            lines.append(f"- promotion review: {len(actionable)} review items, {len(watched)} watch items")
    else:
        lines.append("- portfolio replay: n/a")

    lines.extend(
        [
            "",
            "## Policy Performance",
            "",
            "| policy | pos | resolved | intended | filled | fill | model EV intended | model EV filled | model EV missed | realized PnL | realized RR | terminal reject | flags |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["policies"]:
        lines.append(
            "| {policy} | {positions} | {resolved_positions} | {intended} | {filled} | {fill_rate} | {ev} | {filled_ev} | {missed_ev} | {pnl} | {rr} | {terminal_reject_rate} | {flags} |".format(
                policy=row["policy"],
                positions=row["positions"],
                resolved_positions=row["resolved_positions"],
                intended=money(row["intended_notional_usd"]),
                filled=money(row["filled_notional_usd"]),
                fill_rate=pct(row["fill_rate"]),
                ev=money(row["live_expected_ev_usd"]),
                filled_ev=money(row["live_filled_expected_ev_usd"]),
                missed_ev=money(row["missed_expected_ev_usd"]),
                pnl=money(row["live_realized_pnl_usd"]),
                rr=fmt(row["realized_rr"]),
                terminal_reject_rate=pct(row["terminal_reject_rate"]),
                flags=", ".join(row["flags"]) if row["flags"] else "",
            )
        )

    lines.extend(["", "## Execution Categories", ""])
    if report["execution_categories"]:
        lines.extend(["| category | reason | attempts | intended | filled |", "|---|---|---:|---:|---:|"])
        for row in report["execution_categories"]:
            lines.append(
                f"| {row['category']} | {row['reason']} | {row['attempts']} | {money(row['intended_notional_usd'])} | {money(row['filled_notional_usd'])} |"
            )
    else:
        lines.append("No notable execution categories found for the period.")

    lines.extend(["", "## Raw Rejects By Reason", ""])
    if report["reject_reasons"]:
        lines.extend(["| reason | state | attempts | intended | filled |", "|---|---|---:|---:|---:|"])
        for row in report["reject_reasons"]:
            lines.append(
                f"| {row['reason']} | {row['state']} | {row['attempts']} | {money(row['intended_notional_usd'])} | {money(row['filled_notional_usd'])} |"
            )
    else:
        lines.append("No rejected/failed/error attempts found for the period.")

    lines.extend(["", "## Portfolio Replay", ""])
    if replay:
        lines.extend(["| sleeve | fills | replay risk | replay PnL | replay RR |", "|---|---:|---:|---:|---:|"])
        for row in replay["sleeves"]:
            lines.append(
                f"| {row['sleeve']} | {row['fills']} | {money(row['risk_usd'])} | {money(row['resolved_replay_pnl_usd'])} | {fmt(row['rr'])} |"
            )
    else:
        lines.append("No research replay was run. Pass `--research-db` to include the empirical current-stack replay EV/PnL proxy.")


    lines.extend(["", "## Promotion / Candidate Review", ""])
    if promotion:
        lines.extend(
            [
                "| role | sleeve | fills | candidates | risk | empirical PnL | R/R | win | cap skip | depth skip | status | reason |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        for row in promotion["sleeves"]:
            lines.append(
                "| {role} | {sleeve} | {fills} | {candidates} | {risk} | {pnl} | {rr} | {win} | {cap} | {depth} | {status} | {reason} |".format(
                    role=row["role"],
                    sleeve=row["sleeve"],
                    fills=row["fills"],
                    candidates=row["candidates"],
                    risk=money(row["risk_usd"]),
                    pnl=money(row["empirical_pnl_usd"]),
                    rr=fmt(row["rr"]),
                    win=fmt(row["win_rate"]),
                    cap=row["skipped_capacity_rows"],
                    depth=row["skipped_depth_rows"],
                    status=row["status"],
                    reason=row["reason"],
                )
            )
    else:
        lines.append("No promotion review was run. Pass `--research-db` to include candidate replay review.")

    lines.extend(["", "## Event Counts", ""])
    if report["event_counts"]:
        lines.extend(["| event_type | events |", "|---|---:|"])
        for row in report["event_counts"]:
            lines.append(f"| {row['event_type']} | {row['events']} |")
    else:
        lines.append("No live trade events found for the period.")

    return chr(10).join(lines)


def parse_args() -> argparse.Namespace:
    start_default, end_default = default_period()
    parser = argparse.ArgumentParser(description="Emit a weekly live-trading retrospective from SQLite.")
    parser.add_argument("--live-db", default=str(DEFAULT_LIVE_DB))
    parser.add_argument("--research-db", default=str(DEFAULT_RESEARCH_DB), help="Set to '' to disable portfolio replay.")
    parser.add_argument("--start-date", default=start_default)
    parser.add_argument("--end-date", default=end_default)
    parser.add_argument("--start-timestamp", help="Optional inclusive UTC/local ISO timestamp filter for live-ledger rows.")
    parser.add_argument("--end-timestamp", help="Optional inclusive UTC/local ISO timestamp filter for live-ledger rows.")
    parser.add_argument("--out", help="Optional output path. Without this, the report is printed to stdout.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--min-resolved-positions", type=int, default=3)
    parser.add_argument("--review-realized-rr", type=float, default=0.0)
    parser.add_argument("--kill-realized-rr", type=float, default=-0.25)
    parser.add_argument("--review-fill-rate", type=float, default=0.50)
    parser.add_argument("--review-terminal-reject-rate", type=float, default=0.10)
    parser.add_argument("--promotion-min-fills", type=int, default=20)
    parser.add_argument("--promotion-min-rr", type=float, default=0.25)
    parser.add_argument("--size-up-min-rr", type=float, default=0.50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    research_db = Path(args.research_db).expanduser() if args.research_db else None
    thresholds = Thresholds(
        min_resolved_positions=args.min_resolved_positions,
        review_realized_rr=args.review_realized_rr,
        kill_realized_rr=args.kill_realized_rr,
        review_fill_rate=args.review_fill_rate,
        review_terminal_reject_rate=args.review_terminal_reject_rate,
        promotion_min_fills=args.promotion_min_fills,
        promotion_min_rr=args.promotion_min_rr,
        size_up_min_rr=args.size_up_min_rr,
    )
    report = build_report(
        Path(args.live_db).expanduser(),
        research_db,
        args.start_date,
        args.end_date,
        thresholds,
        start_timestamp=args.start_timestamp,
        end_timestamp=args.end_timestamp,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else render_markdown(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + chr(10), encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
