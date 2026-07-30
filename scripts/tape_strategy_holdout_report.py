#!/usr/bin/env python3
"""Replay a frozen snapshot-selected portfolio against causal market tape.

This report is the execution holdout half of strategy discovery. Candidate
families must be chosen using data no later than ``--discovery-cutoff``. The
report then selects their first eligible post-activation decisions, applies
portfolio-order deduplication, reconstructs the tape at quote-ready time, and
simulates an immediate ask sweep without writing either database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.live_policy_promotion_report import PolicySpec, select_policy_rows
from scripts.snapshot_opportunity_sweep import (
    PM_ACTIVE_DYNAMIC_TUNED_MODEL,
    PM_ACTIVE_MVP_MODEL,
    load_snapshot_rows,
)
from weather_trader.tape.contracts import CoverageState
from weather_trader.tape.storage import iter_segment

DEFAULT_RESEARCH_DB = (
    Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
)
DEFAULT_TAPE_CATALOG = Path.home() / ".local/state/roboweather/market_tape/catalog.sqlite"
HIGH_REGRESSION_MODEL = "high_regression_pm_active_us12_obs_2022_2025"

DEFAULT_SPECS = (
    PolicySpec(
        name="pm_high_regression_10m_late",
        source="model",
        identifier=HIGH_REGRESSION_MODEL,
        label="PM high regression HC 10m late",
        obs_delay_bucket="10m",
        entry_price_min=0.05,
        entry_price_max=0.50,
        local_decision_start="12:00",
        local_decision_end="15:00",
        scope="station_date",
    ),
    PolicySpec(
        name="pm_mvp_late",
        source="model",
        identifier=PM_ACTIVE_MVP_MODEL,
        label="PM MVP HC late",
        entry_price_min=0.05,
        entry_price_max=0.50,
        local_decision_start="12:00",
        local_decision_end="15:00",
        scope="station_date",
    ),
    PolicySpec(
        name="pm_dynamic_tuned_10m_late",
        source="model",
        identifier=PM_ACTIVE_DYNAMIC_TUNED_MODEL,
        label="PM dynamic tuned HC 10m late",
        obs_delay_bucket="10m",
        entry_price_min=0.05,
        entry_price_max=0.50,
        local_decision_start="12:00",
        local_decision_end="15:00",
        scope="station_date",
    ),
)
SPECS_BY_NAME = {spec.name: spec for spec in DEFAULT_SPECS}


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def apply_event(
    bids: dict[float, float],
    asks: dict[float, float],
    event: Any,
) -> bool:
    """Apply one normalized tape event, failing closed on malformed state."""
    payload = event.raw_payload
    if event.event_type == "book":
        if not isinstance(payload, dict):
            return False
        replacement: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
        try:
            for key in ("bids", "asks"):
                for level in payload.get(key) or []:
                    price = float(level["price"])
                    size = float(level["size"])
                    if price <= 0 or size < 0:
                        return False
                    if size > 0:
                        replacement[key][price] = size
        except (KeyError, TypeError, ValueError):
            return False
        bids.clear()
        asks.clear()
        bids.update(replacement["bids"])
        asks.update(replacement["asks"])
        return True

    if event.coverage_state is not CoverageState.VALID:
        return False
    if event.event_type != "price_change":
        return True
    source = payload.get("price_change") if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        source = payload
    if not isinstance(source, dict):
        return False
    try:
        price = float(source["price"])
        size = float(source["size"])
        side = str(source["side"]).upper()
    except (KeyError, TypeError, ValueError):
        return False
    levels = bids if side == "BUY" else asks if side == "SELL" else None
    if levels is None or price <= 0 or size < 0:
        return False
    if size == 0:
        levels.pop(price, None)
    else:
        levels[price] = size
    return True


def reconstruct_book(
    tape: sqlite3.Connection,
    token_id: str,
    ready: datetime,
    *,
    pre_signal_seconds: int,
    target_tokens: set[str],
    event_cache: dict[Path, list[Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    window_start = ready - timedelta(seconds=pre_signal_seconds)
    interval = tape.execute(
        """
        select id, session_id, started_at_utc, ended_at_utc
        from tape_coverage_intervals
        where token_id=? and state='VALID'
          and started_at_utc<=?
          and (ended_at_utc is null or ended_at_utc>=?)
        order by started_at_utc desc limit 1
        """,
        (token_id, window_start.isoformat(), ready.isoformat()),
    ).fetchone()
    if interval is None:
        return None, "no_continuous_valid_interval"

    checkpoint = tape.execute(
        """
        select event_id, captured_at_utc, raw_json
        from tape_book_checkpoints
        where token_id=? and session_id=? and coverage_state='VALID'
          and captured_at_utc<=? and captured_at_utc>=?
        order by captured_at_utc desc limit 1
        """,
        (
            token_id,
            interval["session_id"],
            ready.isoformat(),
            interval["started_at_utc"],
        ),
    ).fetchone()
    if checkpoint is None:
        return None, "no_seed_checkpoint"

    try:
        seed = json.loads(checkpoint["raw_json"])
        bids = {float(price): float(size) for price, size in seed.get("bids") or []}
        asks = {float(price): float(size) for price, size in seed.get("asks") or []}
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "invalid_seed_checkpoint"

    checkpoint_time = utc(checkpoint["captured_at_utc"])
    partitions = tape.execute(
        """
        select path, closed_at_utc
        from tape_raw_partitions
        where session_id=? and closed_at_utc>=? and path is not null
        order by partition_id
        """,
        (interval["session_id"], checkpoint["captured_at_utc"]),
    ).fetchall()
    reached_ready = checkpoint_time >= ready
    for partition in partitions:
        path = Path(partition["path"])
        if not path.exists():
            return None, "missing_raw_partition"
        if path not in event_cache:
            event_cache[path] = [
                event for event in iter_segment(path) if event.token_id in target_tokens
            ]
        for event in event_cache[path]:
            event_time = utc(event.received_at_utc)
            if event_time <= checkpoint_time:
                continue
            if event_time > ready:
                reached_ready = True
                break
            if event.token_id == token_id and not apply_event(bids, asks, event):
                return None, "invalid_event_after_checkpoint"
        if utc(partition["closed_at_utc"]) >= ready:
            reached_ready = True
        if reached_ready:
            break
    if not reached_ready:
        return None, "raw_tape_does_not_reach_quote_ready"
    return {
        "bids": bids,
        "asks": asks,
        "checkpoint_age_s": (ready - checkpoint_time).total_seconds(),
    }, None


def sweep_asks(
    asks: dict[float, float],
    *,
    price_cap: float,
    target_cost: float,
) -> tuple[float, float, float | None]:
    """Take available asks in price order, allowing a useful partial fill."""
    remaining = target_cost
    cost = 0.0
    shares = 0.0
    for price, size in sorted(asks.items()):
        if price > price_cap or remaining <= 1e-9:
            break
        if price <= 0 or size <= 0:
            continue
        take_cost = min(remaining, price * size)
        cost += take_cost
        shares += take_cost / price
        remaining -= take_cost
    return cost, shares, (cost / shares if shares else None)


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    selected = list(rows)
    cost = sum(row["cost"] for row in selected)
    pnl = sum(row["pnl"] for row in selected)
    return {
        "executions": len(selected),
        "cost": round(cost, 2),
        "pnl": round(pnl, 2),
        "rr": round(pnl / cost, 3) if cost else None,
        "wins": sum(bool(row["won"]) for row in selected),
        "avg_vwap": (
            round(sum(row["vwap"] for row in selected) / len(selected), 3)
            if selected
            else None
        ),
        "avg_checkpoint_age_s": (
            round(
                sum(row["checkpoint_age_s"] for row in selected) / len(selected),
                2,
            )
            if selected
            else None
        ),
    }


def freeze_portfolio(
    rows: list[dict[str, Any]],
    specs: Iterable[PolicySpec],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, int]]:
    """Apply sleeve priority and one-position-per-station/date capacity."""
    frozen: list[tuple[str, dict[str, Any]]] = []
    raw_counts: dict[str, int] = {}
    seen_station_dates: set[tuple[str, str]] = set()
    for spec in specs:
        selected = select_policy_rows(rows, spec)
        raw_counts[spec.name] = len(selected)
        for row in selected:
            key = (str(row["station"]), str(row["market_date"]))
            if key in seen_station_dates:
                continue
            seen_station_dates.add(key)
            frozen.append((spec.name, row))
    return frozen, raw_counts


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    specs = tuple(SPECS_BY_NAME[name] for name in args.sleeve)
    with sqlite3.connect(args.research_db) as research:
        research.row_factory = sqlite3.Row
        snapshot_rows = load_snapshot_rows(
            research,
            start_date=args.holdout_start,
            end_date=args.end_date,
            market_family="HIGH_TEMP",
            us_high_temp_only=True,
        )
    resolved = [row for row in snapshot_rows if row.get("paper_pnl") is not None]
    frozen, raw_counts = freeze_portfolio(resolved, specs)

    with sqlite3.connect(args.tape_catalog) as tape:
        tape.row_factory = sqlite3.Row
        rejected: Counter[str] = Counter()
        mapped: list[tuple[str, dict[str, Any], str]] = []
        for sleeve, row in frozen:
            outcome = "YES" if row["selected_side"] == "BUY_YES" else "NO"
            token = tape.execute(
                "select token_id from tape_tokens where market_id=? and outcome=? limit 1",
                (str(row["selected_market_id"]), outcome),
            ).fetchone()
            if token is None:
                rejected["token_not_cataloged"] += 1
                continue
            mapped.append((sleeve, row, str(token["token_id"])))

        target_tokens = {token_id for _, _, token_id in mapped}
        event_cache: dict[Path, list[Any]] = {}
        executed: list[dict[str, Any]] = []
        for sleeve, row, token_id in mapped:
            ready = utc(str(row["timestamp"])) + timedelta(milliseconds=args.latency_ms)
            book, reason = reconstruct_book(
                tape,
                token_id,
                ready,
                pre_signal_seconds=args.pre_signal_seconds,
                target_tokens=target_tokens,
                event_cache=event_cache,
            )
            if reason is not None or book is None:
                rejected[reason or "unknown_reconstruction_error"] += 1
                continue
            cost, shares, vwap = sweep_asks(
                book["asks"],
                price_cap=args.price_cap,
                target_cost=args.target_cost_usd,
            )
            if cost <= 0 or vwap is None:
                rejected["no_asks_at_or_below_cap"] += 1
                continue
            won = float(row["paper_pnl"]) > 0
            executed.append(
                {
                    "sleeve": sleeve,
                    "station": row["station"],
                    "market_date": row["market_date"],
                    "side": row["selected_side"],
                    "bucket": row["selected_bucket"],
                    "token_id": token_id,
                    "snapshot_timestamp": row["timestamp"],
                    "quote_ready": ready.isoformat(),
                    "snapshot_entry": float(
                        row["selected_yes_ask"]
                        if row["selected_side"] == "BUY_YES"
                        else row["selected_no_ask"]
                    ),
                    "cost": cost,
                    "shares": shares,
                    "vwap": vwap,
                    "won": won,
                    "pnl": shares - cost if won else -cost,
                    "checkpoint_age_s": book["checkpoint_age_s"],
                }
            )

    return {
        "method": {
            "discovery_cutoff": args.discovery_cutoff,
            "holdout_start": args.holdout_start,
            "holdout_end": args.end_date,
            "sleeve_priority": [spec.name for spec in specs],
            "latency_ms": args.latency_ms,
            "pre_signal_seconds": args.pre_signal_seconds,
            "target_cost_usd": args.target_cost_usd,
            "price_cap": args.price_cap,
            "execution": "immediate_taker_ask_sweep_partial_fills_allowed",
            "truth": "weather_outcome_not_venue_settlement",
        },
        "data": {
            "research_db": str(Path(args.research_db).expanduser()),
            "tape_catalog": str(Path(args.tape_catalog).expanduser()),
            "resolved_market_dates": sorted(
                {str(row["market_date"]) for row in resolved}
            ),
            "resolved_snapshot_rows": len(resolved),
        },
        "raw_selected_counts": raw_counts,
        "deduplicated_signals": len(frozen),
        "rejected": dict(sorted(rejected.items())),
        "portfolio": summarize(executed),
        "by_sleeve": {
            spec.name: summarize(
                row for row in executed if row["sleeve"] == spec.name
            )
            for spec in specs
        },
        "trades": executed,
        "limitations": [
            "Sleeves were selected separately on pre-cutoff snapshot evidence.",
            "Public L2 replay supports immediate taker fills only; it does not infer passive queue fills.",
            "PnL uses resolved weather outcomes rather than authoritative venue settlement.",
            "A positive small holdout is hypothesis evidence, not funded promotion evidence.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    method = report["method"]
    portfolio = report["portfolio"]
    lines = [
        "# Tape Strategy Holdout Report",
        "",
        f"- Discovery cutoff: {method['discovery_cutoff']}",
        f"- Holdout: {method['holdout_start']} through {method['holdout_end'] or 'latest resolved'}",
        f"- Resolved market dates: {len(report['data']['resolved_market_dates'])}",
        f"- Deduplicated signals: {report['deduplicated_signals']}",
        f"- Tape executions: {portfolio['executions']}",
        f"- Cost / PnL / R/R: ${portfolio['cost']:.2f} / ${portfolio['pnl']:.2f} / {portfolio['rr']}",
        "",
        "| sleeve | executions | wins | cost | pnl | R/R | avg VWAP |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for sleeve, stats in report["by_sleeve"].items():
        lines.append(
            f"| {sleeve} | {stats['executions']} | {stats['wins']} | "
            f"${stats['cost']:.2f} | ${stats['pnl']:.2f} | "
            f"{stats['rr']} | {stats['avg_vwap']} |"
        )
    lines.extend(["", "Rejected:"])
    if report["rejected"]:
        lines.extend(
            f"- {reason}: {count}" for reason, count in report["rejected"].items()
        )
    else:
        lines.append("- none")
    lines.extend(["", "Limitations:"])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a frozen US high-temperature portfolio on valid market tape."
    )
    parser.add_argument("--research-db", default=str(DEFAULT_RESEARCH_DB))
    parser.add_argument("--tape-catalog", default=str(DEFAULT_TAPE_CATALOG))
    parser.add_argument("--discovery-cutoff", default="2026-07-22")
    parser.add_argument("--holdout-start", default="2026-07-23")
    parser.add_argument("--end-date")
    parser.add_argument(
        "--sleeve",
        action="append",
        choices=tuple(SPECS_BY_NAME),
        help="Repeat to set the frozen sleeve priority; defaults to all built-in sleeves.",
    )
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--pre-signal-seconds", type=int, default=60)
    parser.add_argument("--target-cost-usd", type=float, default=25.0)
    parser.add_argument("--price-cap", type=float, default=0.50)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.sleeve is None:
        args.sleeve = list(SPECS_BY_NAME)
    if date.fromisoformat(args.discovery_cutoff) >= date.fromisoformat(args.holdout_start):
        parser.error("--discovery-cutoff must be earlier than --holdout-start")
    if args.latency_ms < 0 or args.pre_signal_seconds < 0:
        parser.error("latency and pre-signal coverage must be nonnegative")
    if args.target_cost_usd <= 0 or not 0 < args.price_cap <= 1:
        parser.error("target cost must be positive and price cap must be in (0, 1]")
    return args


def main() -> None:
    args = parse_args()
    report = run_report(args)
    output = (
        json.dumps(report, indent=2, sort_keys=True)
        if args.format == "json"
        else render_markdown(report)
    )
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
