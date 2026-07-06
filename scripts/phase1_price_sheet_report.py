#!/usr/bin/env python3
"""Replay Phase 1 price-maker sheets for the scoped US consensus no-tiny sleeve."""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.snapshot_opportunity_sweep import (
    POLICY_SEARCH_CONSENSUS_GROUPS,
    build_consensus_rows,
    default_db_path,
    entry_price,
    load_snapshot_rows,
    selected_fair,
)
from weather_trader.execution.contracts import MarketFamily, TradeAction
from weather_trader.execution.price_maker import (
    PHASE1_CONSENSUS_NO_TINY_POLICY,
    build_phase1_price_sheet,
)
from weather_trader.live.execution import CATBOOST_MODEL, DYNAMIC_TUNED_MODEL, LIVE_MODEL_GROUP, PM_ACTIVE_US12_STATIONS


@dataclass(frozen=True)
class ReportRow:
    row: dict[str, Any]
    quote_price: float | None
    quote_size_cap: float
    calibrated_fair: float | None
    raw_model_fair: float | None
    uncertainty_haircut: float
    adverse_selection_haircut: float
    min_required_edge: float
    eligible: bool
    reject_reason: str | None
    resolved: bool
    correct: bool | None
    pnl_usd: float | None
    risk_usd: float | None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db_path(), help="Research SQLite DB path.")
    parser.add_argument("--start-date", help="Optional market_date lower bound.")
    parser.add_argument("--end-date", help="Optional market_date upper bound.")
    parser.add_argument("--current-window-days", type=int, default=30)
    parser.add_argument("--target-notional-usd", type=float, default=50.0)
    args = parser.parse_args()

    rows = build_report_rows(
        args.db,
        start_date=args.start_date,
        end_date=args.end_date,
        target_notional_usd=args.target_notional_usd,
    )
    print(render_report(rows, db=args.db, current_window_days=args.current_window_days))
    return 0


def build_report_rows(
    db_path: Path,
    *,
    start_date: str | None,
    end_date: str | None,
    target_notional_usd: float,
) -> list[ReportRow]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        base_rows = load_snapshot_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            market_family=str(MarketFamily.HIGH_TEMP),
            us_high_temp_only=True,
        )
    consensus = build_consensus_rows(base_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS)
    scoped = first_by_scope([row for row in consensus if row_matches_phase1_scope(row)])
    result: list[ReportRow] = []
    for row in scoped:
        sheet = build_phase1_price_sheet(
            live_candidate_id=f"replay-{row.get('id')}",
            strategy_name=PHASE1_CONSENSUS_NO_TINY_POLICY,
            policy_name=PHASE1_CONSENSUS_NO_TINY_POLICY,
            source=source_from_row(row),
            selected_token_id=None,
            quote_features=quote_features_from_row(row),
            as_of_utc=decision_time(row),
            target_notional_usd=target_notional_usd,
        )
        correct = _correct(row)
        pnl_usd = quote_pnl(correct, sheet.max_quote_price, sheet.quote_size_cap) if sheet.eligible else None
        result.append(
            ReportRow(
                row=row,
                quote_price=sheet.max_quote_price,
                quote_size_cap=sheet.quote_size_cap,
                calibrated_fair=sheet.calibrated_fair,
                raw_model_fair=sheet.raw_model_fair,
                uncertainty_haircut=sheet.uncertainty_haircut,
                adverse_selection_haircut=sheet.adverse_selection_haircut,
                min_required_edge=sheet.min_required_edge,
                eligible=sheet.eligible,
                reject_reason=sheet.reject_reason,
                resolved=correct is not None,
                correct=correct,
                pnl_usd=pnl_usd,
                risk_usd=sheet.quote_size_cap if sheet.eligible and correct is not None else None,
            )
        )
    return result


def row_matches_phase1_scope(row: dict[str, Any]) -> bool:
    row_entry = entry_price(row)
    if row.get("model_name") != LIVE_MODEL_GROUP:
        return False
    if row.get("strategy_bucket") != "HIGH_CONVICTION":
        return False
    if row.get("selected_side") != str(TradeAction.BUY_NO):
        return False
    if row.get("station") not in PM_ACTIVE_US12_STATIONS:
        return False
    if row_entry is None or row_entry < 0.05 or row_entry > 0.50:
        return False
    if not _local_time_in_window(str(row.get("decision_time_local") or ""), "12:00", "15:00"):
        return False
    models = set(row.get("consensus_models") or [])
    return {DYNAMIC_TUNED_MODEL, CATBOOST_MODEL}.issubset(models)


def first_by_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (str(item.get("timestamp")), int(item.get("id") or 0))):
        key = (
            row.get("station"),
            row.get("market_date"),
            row.get("selected_bucket"),
            row.get("selected_side"),
            row.get("obs_delay_bucket"),
        )
        by_key.setdefault(key, row)
    return list(by_key.values())


def source_from_row(row: dict[str, Any]) -> SimpleNamespace:
    fair = selected_fair(row)
    row_entry = entry_price(row)
    return SimpleNamespace(
        station=row.get("station"),
        market_date=row.get("market_date"),
        market_family=row.get("market_family") or str(MarketFamily.HIGH_TEMP),
        selected_market_id=row.get("selected_market_id"),
        selected_side=row.get("selected_side"),
        selected_bucket=row.get("selected_bucket"),
        selected_best_bid=row.get("selected_best_bid"),
        selected_best_ask=row.get("selected_best_ask"),
        selected_spread=row.get("selected_spread"),
        entry_price=row_entry,
        entry_fair=fair,
        entry_edge=(fair - row_entry) if fair is not None and row_entry is not None else row.get("selected_edge"),
        raw_policy={
            "model_group": LIVE_MODEL_GROUP,
            "model_names": [DYNAMIC_TUNED_MODEL, CATBOOST_MODEL],
            "model_fairs": row.get("model_fairs") or {},
        },
    )


def quote_features_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "best_bid": row.get("selected_best_bid"),
        "best_ask": row.get("selected_best_ask"),
        "spread": row.get("selected_spread"),
        "top_level_cancel_count_5m": 0,
        "recent_trade_count_5m": 0,
        "selected_ask_just_depleted": False,
    }


def render_report(rows: list[ReportRow], *, db: Path, current_window_days: int) -> str:
    latest_date = max((_date_or_none(row.row.get("market_date")) for row in rows), default=None)
    current_start = None
    if latest_date is not None:
        current_start = latest_date.toordinal() - max(0, current_window_days - 1)
    current_rows = [
        row
        for row in rows
        if current_start is not None and _date_or_none(row.row.get("market_date")) is not None and _date_or_none(row.row.get("market_date")).toordinal() >= current_start
    ]
    lines = [
        "# Phase 1 Price-Maker Sheet Replay",
        "",
        f"- DB: `{db}`",
        f"- Scope: `{PHASE1_CONSENSUS_NO_TINY_POLICY}` BUY_NO, US high-temperature, late no-tiny, <= $0.50 entry",
        "",
        "| Window | Rows | Eligible | Resolved | Win rate | Risk | PnL | R/R | Avg quote | Avg raw fair | Avg capped fair |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        summary_line("all", rows),
        summary_line(f"last_{current_window_days}d", current_rows),
        "",
        "Gate notes:",
        "- Quote prices are generated from capped fair values after uncertainty and adverse-selection haircuts.",
        "- This is theoretical quote replay only; passive fill probability is Phase 3 and should not be inferred from this report.",
    ]
    return "\n".join(lines)


def summary_line(label: str, rows: list[ReportRow]) -> str:
    eligible = [row for row in rows if row.eligible and row.quote_price is not None]
    resolved = [row for row in eligible if row.resolved and row.pnl_usd is not None and row.risk_usd is not None]
    wins = sum(1 for row in resolved if row.correct)
    risk = sum(float(row.risk_usd or 0.0) for row in resolved)
    pnl = sum(float(row.pnl_usd or 0.0) for row in resolved)
    rr = pnl / risk if risk > 0 else None
    return (
        f"| {label} | {len(rows)} | {len(eligible)} | {len(resolved)} | "
        f"{_fmt_pct(wins / len(resolved) if resolved else None)} | {_fmt_money(risk)} | {_fmt_money(pnl)} | "
        f"{_fmt_num(rr)} | {_fmt_num(_mean(row.quote_price for row in eligible))} | "
        f"{_fmt_num(_mean(row.raw_model_fair for row in eligible))} | {_fmt_num(_mean(row.calibrated_fair for row in eligible))} |"
    )


def quote_pnl(correct: bool | None, quote_price: float | None, notional_usd: float) -> float | None:
    if correct is None or quote_price is None or quote_price <= 0.0:
        return None
    if correct:
        return notional_usd * ((1.0 - quote_price) / quote_price)
    return -notional_usd


def decision_time(row: dict[str, Any]) -> datetime:
    value = str(row.get("decision_time_utc") or row.get("timestamp") or "")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _correct(row: dict[str, Any]) -> bool | None:
    value = row.get("correct")
    if value is None:
        return None
    return bool(int(value))


def _local_time_in_window(value: str, start: str, end: str) -> bool:
    if not value:
        return False
    try:
        local_time = datetime.fromisoformat(value.replace("Z", "+00:00")).time()
    except ValueError:
        return False
    start_time = datetime.strptime(start, "%H:%M").time()
    end_time = datetime.strptime(end, "%H:%M").time()
    return start_time <= local_time <= end_time


def _date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _mean(values: Any) -> float | None:
    parsed = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not parsed:
        return None
    return sum(parsed) / len(parsed)


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


if __name__ == "__main__":
    raise SystemExit(main())
