#!/usr/bin/env python3
"""Detailed RoboWeather status report.

Usage:
    python scripts/status_report.py

Environment:
    ROBOWEATHER_STATUS_DB=/path/to/research.sqlite
    DB=/path/to/research.sqlite  # fallback alias used by run_research.sh

The report is intentionally terminal-first and deterministic. Historical
performance is resolved per policy position. Today's open/live status is marked
to the latest available orderbook bid for the selected YES/NO token.
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.environ.get(
    "ROBOWEATHER_STATUS_DB",
    os.environ.get("DB", str(REPO_ROOT / "data/paper/research_2026-05-08_multimodel.sqlite")),
)
TODAY = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
ALL_STATIONS = {
    "KATL",
    "KDAL",
    "KHOU",
    "KLAX",
    "KLGA",
    "KMIA",
    "KORD",
    "KSEA",
    "KSFO",
    "KBKF",
    "KPDX",
    "KDEN",
}
STATION_REGIME_LABELS = {
    "KBOS": "coastal",
    "KDCA": "coastal",
    "KHOU": "coastal",
    "KLAX": "coastal",
    "KLGA": "coastal",
    "KMIA": "coastal",
    "KSEA": "coastal",
    "KSFO": "coastal",
    "KATL": "inland",
    "KDEN": "inland",
    "KDFW": "inland",
    "KORD": "inland",
    "KBKF": "manual",
    "KDAL": "manual",
    "KPDX": "manual",
}
LOW_N_STATION_THRESHOLD = 20


def is_active_policy(name: str) -> bool:
    return name.startswith("pm_us12_") or name.startswith("max_so_far_")


def parse_bucket(bucket: str | None) -> tuple[int | None, int | None]:
    if not bucket:
        return None, None
    value = bucket.replace("F", "")
    if value.startswith("<="):
        return None, int(value[2:])
    if value.startswith(">="):
        return int(value[2:]), None
    low, high = value.split("-")
    return int(low), int(high)


def bucket_type(bucket: str | None) -> str:
    if not bucket:
        return "missing"
    if bucket.startswith("<=") or bucket.startswith(">="):
        return "tail"
    return "range"


def entry_band(entry: float) -> str:
    if entry < 0.05:
        return "0.00-0.05"
    if entry < 0.10:
        return "0.05-0.10"
    if entry < 0.25:
        return "0.10-0.25"
    if entry < 0.50:
        return "0.25-0.50"
    if entry < 0.75:
        return "0.50-0.75"
    return "0.75-1.00"


def station_regime(station: str) -> str:
    return STATION_REGIME_LABELS.get(station, "manual")


def opportunity_key(row: sqlite3.Row) -> tuple[str, str, str, str, str]:
    return (
        row["market_date"],
        row["station"],
        row["selected_side"],
        row["selected_bucket"] or "",
        row["obs_delay_bucket"] or "",
    )


def in_bucket(temp: float | None, bucket: str | None) -> bool:
    if temp is None or not bucket:
        return False
    low, high = parse_bucket(bucket)
    if high is None:
        return low is not None and temp >= low
    if low is None:
        return temp <= high
    return low <= temp <= high


def fmt_money(value: float | None, width: int = 8) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"${value:>+{width - 1}.2f}"


def fmt_pct(value: float | None, width: int = 6) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{value:>{width - 1}.0f}%"


def fmt_num(value: float | None, width: int = 6, decimals: int = 2) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{value:>{width}.{decimals}f}"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_minutes(value: str | None) -> float | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0


def position_sharpe(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((pnl - mean_pnl) ** 2 for pnl in pnls) / len(pnls)
    std_pnl = math.sqrt(variance)
    if std_pnl == 0:
        return None
    return mean_pnl / std_pnl


def daily_sharpe(daily_pnls: dict[str, float]) -> tuple[float | None, float | None, float | None]:
    values = list(daily_pnls.values())
    if not values:
        return None, None, None
    mean_day = sum(values) / len(values)
    variance = sum((value - mean_day) ** 2 for value in values) / len(values)
    std_day = math.sqrt(variance)
    if std_day == 0:
        return mean_day, std_day, None
    return mean_day, std_day, mean_day / std_day


def mark_reason(row: sqlite3.Row, high: float | None, hrrr: float | None) -> str:
    bid = row["current_bid"]
    side = row["selected_side"]
    bucket = row["selected_bucket"]
    if bid is None:
        return "NO_BOOK_BID"
    if bid >= 0.95:
        return "MARKED_WIN"
    if bid <= 0.05:
        return "MARKED_LOSS"
    if side == "BUY_NO" and in_bucket(high, bucket):
        return "TEMP_IN_NO_BUCKET"
    if side == "BUY_NO" and in_bucket(hrrr, bucket):
        return "HRRR_THREATENS_NO"
    if side == "BUY_YES":
        low, _ = parse_bucket(bucket)
        if low is not None and high is not None and hrrr is not None and high < low <= hrrr:
            return "HRRR_CAN_REACH_YES"
    return "OPEN"


def score_policy_outcome(row: sqlite3.Row) -> tuple[bool | None, float | None]:
    final_high = row["final_high_tmpf"]
    if final_high is None:
        return None, None
    yes_won = in_bucket(float(final_high), row["selected_bucket"])
    correct = yes_won if row["selected_side"] == "BUY_YES" else not yes_won
    entry = float(row["entry_price"])
    realized_return = 1.0 - entry if correct else -entry
    return correct, realized_return


def print_table(title: str, header: str, rows: list[str], bar_width: int = 140) -> None:
    print(f"\n{title}")
    print("-" * bar_width)
    print(header)
    print("-" * bar_width)
    if rows:
        for row in rows:
            print(row)
    else:
        print("(none)")


def mark_pct(marked: int, total: int) -> float | None:
    if total <= 0:
        return None
    return marked / total * 100.0


def return_risk(return_value: float | None, risk: float | None) -> float | None:
    if return_value is None or risk is None or risk <= 0:
        return None
    return return_value / risk * 100.0


def research_status(
    *,
    active: bool,
    resolved: int,
    total: int,
    today_positions: int,
    rr: float | None,
    psharpe: float | None,
    mark_coverage: float | None,
    live_rr: float | None,
) -> str:
    if not active:
        return "LEGACY"
    if resolved < 10:
        return "TOO_EARLY"
    if today_positions == 0:
        return "STALE"
    if mark_coverage is not None and mark_coverage < 50:
        return "BOOK_GAPS"
    if psharpe is not None and rr is not None and psharpe > 0.25 and rr > 20:
        return "PROMISING"
    if psharpe is not None and psharpe < 0 and rr is not None and rr < 0:
        return "WEAK"
    if live_rr is not None and live_rr < -30:
        return "LIVE_STRESS"
    if total - resolved > resolved:
        return "PENDING_HEAVY"
    return "WATCH"


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

coverage = conn.execute(
    """
    select
        (select count(*) from prediction_snapshots) as snapshots,
        (select count(*) from prediction_snapshots where date(timestamp) = ?) as snapshots_today,
        (select count(*) from research_policy_positions) as policy_positions,
        (select count(*) from research_policy_positions where date(timestamp) = ?) as policy_positions_today,
        (select count(distinct policy_name) from research_policy_positions) as policies,
        (select count(*) from book_snapshots) as book_snapshots,
        (select max(timestamp) from book_snapshots) as latest_book_ts
    """,
    (TODAY, TODAY),
).fetchone()

station_temps: dict[str, dict[str, Any]] = {}
for row in conn.execute(
    """
    select
        station,
        count(*) as snapshots,
        min(timestamp) as first_ts,
        max(timestamp) as last_ts,
        max(current_temp) as current_temp,
        max(high_so_far) as high,
        max(hrrr_remaining_max) as hrrr,
        max(obs_age_minutes) as max_obs_age
    from prediction_snapshots
    where date(timestamp) = ?
    group by station
    """,
    (TODAY,),
):
    station_temps[row["station"]] = dict(row)

policy_inventory_rows = conn.execute(
    """
    select
        policy_name,
        count(*) as positions,
        count(distinct station) as stations,
        sum(case when date(timestamp) = ? then 1 else 0 end) as today_positions,
        min(timestamp) as first_ts,
        max(timestamp) as last_ts
    from research_policy_positions
    group by policy_name
    order by
        case when policy_name like 'pm_us12_%' or policy_name like 'max_so_far_%' then 0 else 1 end,
        policy_name
    """,
    (TODAY,),
).fetchall()

policy_position_rows = conn.execute(
    """
    select
        rpp.id,
        rpp.timestamp,
        rpp.policy_name,
        rpp.station,
        rpp.market_date,
        rpp.scope_key,
        rpp.strategy_bucket,
        rpp.obs_delay_bucket,
        rpp.selected_side,
        rpp.selected_bucket,
        rpp.entry_price,
        rpp.entry_edge,
        rpp.entry_fair,
        sdo.final_high_tmpf,
        rpp.raw_json
    from research_policy_positions rpp
    left join station_date_outcomes sdo
      on sdo.station = rpp.station
     and sdo.market_date = rpp.market_date
    order by rpp.policy_name, rpp.station, rpp.market_date, rpp.id
    """
).fetchall()

resolved_rows = conn.execute(
    """
    select
        rpp.id,
        rpp.policy_name,
        rpp.station,
        date(rpp.timestamp) as day,
        rpp.entry_price,
        avg(pr.paper_pnl) as paper_pnl,
        max(pr.correct) as correct
    from research_policy_positions rpp
    join json_each(rpp.source_prediction_snapshot_ids) je
    join prediction_results pr on pr.prediction_snapshot_id = cast(je.value as integer)
    where pr.paper_pnl is not null
    group by rpp.id
    """,
).fetchall()

policy_stats: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "resolved": 0,
        "won": 0,
        "lost": 0,
        "risk": 0.0,
        "pnl": 0.0,
        "pnls": [],
        "daily": defaultdict(float),
        "stations": set(),
    }
)
station_resolved: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "resolved": 0,
        "won": 0,
        "lost": 0,
        "risk": 0.0,
        "pnl": 0.0,
        "pnls": [],
        "policies": set(),
        "days": set(),
    }
)
policy_opportunities: dict[str, set[tuple[str, str, str, str, str]]] = defaultdict(set)
policy_entry_bands: dict[str, set[str]] = defaultdict(set)
policy_bucket_types: dict[str, set[str]] = defaultdict(set)
opportunity_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
opportunity_policies: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
station_exposure: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"positions": 0, "opportunities": set(), "policies": set(), "entry_risk": 0.0, "pending": 0}
)
expected_policy_stats: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "resolved": 0,
        "won": 0,
        "risk": 0.0,
        "realized": 0.0,
        "expected": 0.0,
        "fair_sum": 0.0,
        "fair_n": 0,
        "edge_sum": 0.0,
        "edge_n": 0,
    }
)
expected_entry_band_stats: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "resolved": 0,
        "won": 0,
        "risk": 0.0,
        "realized": 0.0,
        "expected": 0.0,
        "fair_sum": 0.0,
        "fair_n": 0,
        "edge_sum": 0.0,
        "edge_n": 0,
        "policies": set(),
    }
)

for row in policy_position_rows:
    key = opportunity_key(row)
    opportunity_counts[key] += 1
    opportunity_policies[key].add(row["policy_name"])
    policy_opportunities[row["policy_name"]].add(key)
    policy_entry_bands[row["policy_name"]].add(entry_band(float(row["entry_price"])))
    policy_bucket_types[row["policy_name"]].add(bucket_type(row["selected_bucket"]))

    station = station_exposure[row["station"]]
    station["positions"] += 1
    station["opportunities"].add(key)
    station["policies"].add(row["policy_name"])
    station["entry_risk"] += float(row["entry_price"])

    correct, realized_return = score_policy_outcome(row)
    fair = row["entry_fair"]
    if correct is not None and realized_return is not None and fair is not None:
        entry = float(row["entry_price"])
        fair_value = float(fair)
        edge_value = row["entry_edge"]
        expected_return = fair_value - entry
        band = entry_band(entry)
        for stats in (expected_policy_stats[row["policy_name"]], expected_entry_band_stats[band]):
            stats["resolved"] += 1
            stats["won"] += int(correct)
            stats["risk"] += entry
            stats["realized"] += realized_return
            stats["expected"] += expected_return
            stats["fair_sum"] += fair_value
            stats["fair_n"] += 1
            if edge_value is not None:
                stats["edge_sum"] += float(edge_value)
                stats["edge_n"] += 1
        expected_entry_band_stats[band]["policies"].add(row["policy_name"])

total_positions_seen = len(policy_position_rows)
unique_opportunities = len(opportunity_counts)
duplicate_opportunities = max(0, total_positions_seen - unique_opportunities)
active_position_rows = [row for row in policy_position_rows if is_active_policy(row["policy_name"])]
active_opportunities = {opportunity_key(row) for row in active_position_rows}
active_duplicates = max(0, len(active_position_rows) - len(active_opportunities))

for row in resolved_rows:
    stats = policy_stats[row["policy_name"]]
    pnl = float(row["paper_pnl"])
    entry = float(row["entry_price"])
    stats["resolved"] += 1
    stats["risk"] += entry
    stats["pnl"] += pnl
    stats["pnls"].append(pnl)
    stats["daily"][row["day"]] += pnl
    stats["stations"].add(row["station"])
    if row["correct"] == 1:
        stats["won"] += 1
    elif row["correct"] == 0:
        stats["lost"] += 1

    station = station_resolved[row["station"]]
    station["resolved"] += 1
    station["risk"] += entry
    station["pnl"] += pnl
    station["pnls"].append(pnl)
    station["policies"].add(row["policy_name"])
    station["days"].add(row["day"])
    if row["correct"] == 1:
        station["won"] += 1
    elif row["correct"] == 0:
        station["lost"] += 1

live_rows = conn.execute(
    """
    with latest_books as (
        select bs.token_id, bs.best_bid, bs.best_ask, bs.timestamp
        from book_snapshots bs
        join (
            select token_id, max(id) as id
            from book_snapshots
            group by token_id
        ) latest on latest.id = bs.id
    )
    select
        rpp.id,
        rpp.timestamp,
        rpp.policy_name,
        rpp.station,
        rpp.market_date,
        rpp.scope_key,
        rpp.model_group,
        rpp.strategy_bucket,
        rpp.obs_delay_bucket,
        rpp.selected_market_id,
        rpp.selected_side,
        rpp.selected_bucket,
        rpp.entry_price,
        rpp.entry_edge,
        rpp.entry_fair,
        m.yes_token_id,
        m.no_token_id,
        case when rpp.selected_side = 'BUY_YES' then m.yes_token_id else m.no_token_id end as selected_token_id,
        lb.best_bid as current_bid,
        lb.best_ask as current_ask,
        lb.timestamp as current_book_time,
        case when lb.best_bid is null then null else lb.best_bid - rpp.entry_price end as mtm_pnl
    from research_policy_positions rpp
    join markets m on m.market_id = rpp.selected_market_id
    left join latest_books lb on lb.token_id = case
        when rpp.selected_side = 'BUY_YES' then m.yes_token_id
        else m.no_token_id
    end
    where date(rpp.timestamp) = ?
    order by rpp.station, rpp.policy_name, rpp.id
    """,
    (TODAY,),
).fetchall()

today_policy: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"positions": 0, "risk": 0.0, "mtm": 0.0, "marked": 0, "missing": 0, "stations": set(), "wins95": 0, "loss05": 0}
)
today_station: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"positions": 0, "risk": 0.0, "mtm": 0.0, "marked": 0, "missing": 0, "policies": set(), "rows": [], "alerts": []}
)

for row in live_rows:
    bid = row["current_bid"]
    mtm = row["mtm_pnl"]
    high = station_temps.get(row["station"], {}).get("high")
    hrrr = station_temps.get(row["station"], {}).get("hrrr")
    reason = mark_reason(row, high, hrrr)

    policy = today_policy[row["policy_name"]]
    policy["positions"] += 1
    policy["risk"] += float(row["entry_price"])
    policy["stations"].add(row["station"])
    if mtm is None:
        policy["missing"] += 1
    else:
        policy["marked"] += 1
        policy["mtm"] += float(mtm)
    if bid is not None and bid >= 0.95:
        policy["wins95"] += 1
    if bid is not None and bid <= 0.05:
        policy["loss05"] += 1

    station = today_station[row["station"]]
    station["positions"] += 1
    station["risk"] += float(row["entry_price"])
    station["policies"].add(row["policy_name"])
    if mtm is None:
        station["missing"] += 1
    else:
        station["marked"] += 1
        station["mtm"] += float(mtm)
    if reason != "OPEN" and reason not in station["alerts"]:
        station["alerts"].append(reason)
    detail = dict(row)
    detail["reason"] = reason
    station["rows"].append(detail)

conn.close()

now = datetime.now(timezone.utc)
latest_book_age = age_minutes(coverage["latest_book_ts"])
book_age_text = "n/a" if latest_book_age is None else f"{latest_book_age:.1f}m old"
active_today = sum(1 for row in live_rows if is_active_policy(row["policy_name"]))
marked_today = sum(1 for row in live_rows if row["mtm_pnl"] is not None)
today_mtm = sum(float(row["mtm_pnl"]) for row in live_rows if row["mtm_pnl"] is not None)
today_risk = sum(float(row["entry_price"]) for row in live_rows)
today_live_rr = return_risk(today_mtm, today_risk)
today_mark_coverage = mark_pct(marked_today, len(live_rows))
station_coverage = mark_pct(len(station_temps), len(ALL_STATIONS))

print(f"ROBOWEATHER RESEARCH MONITOR - {TODAY} {now.strftime('%H:%M')} UTC")
print(f"DB: {DB_PATH}")
print(
    f"Data: {coverage['snapshots_today']} snapshots today / {coverage['snapshots']} all-time | "
    f"{coverage['policy_positions_today']} policy positions today / {coverage['policy_positions']} all-time | "
    f"{coverage['policies']} policies"
)
print(
    f"Freshness: {coverage['book_snapshots']} orderbook snapshots | "
    f"latest book {coverage['latest_book_ts']} ({book_age_text})"
)
print(
    f"Live book health: {marked_today}/{len(live_rows)} positions marked "
    f"({fmt_pct(today_mark_coverage, 5)}), live mark R/R {fmt_pct(today_live_rr, 6)}, "
    f"risk at work ${today_risk:.2f}, active-policy rows {active_today}"
)
print(
    f"Station coverage: {len(station_temps)}/{len(ALL_STATIONS)} stations "
    f"({fmt_pct(station_coverage, 5)})"
)
print(
    "Metrics: R/R = return over entry risk; pSharp = per-position Sharpe; "
    "dSharp = daily-return Sharpe; LiveR/R = current bid mark over entry risk."
)
print("Statuses are research triage labels, not deployment recommendations.")

inventory_lines: list[str] = []
for row in policy_inventory_rows:
    stats = policy_stats.get(row["policy_name"], {})
    resolved = int(stats.get("resolved", 0))
    won = int(stats.get("won", 0))
    lost = int(stats.get("lost", 0))
    wr = (won / (won + lost) * 100.0) if won + lost else None
    pnl = float(stats.get("pnl", 0.0)) if resolved else None
    risk = float(stats.get("risk", 0.0)) if resolved else None
    ror = return_risk(pnl, risk)
    psharpe = position_sharpe(list(stats.get("pnls", [])))
    _, _, dsharpe = daily_sharpe(dict(stats.get("daily", {})))
    live = today_policy.get(row["policy_name"], {})
    live_positions = int(live.get("positions", 0))
    live_marked = int(live.get("marked", 0))
    live_risk = float(live.get("risk", 0.0))
    live_mtm = float(live.get("mtm", 0.0))
    live_rr = return_risk(live_mtm, live_risk)
    live_mark_pct = mark_pct(live_marked, live_positions)
    active_flag = is_active_policy(row["policy_name"])
    status = research_status(
        active=active_flag,
        resolved=resolved,
        total=int(row["positions"]),
        today_positions=int(row["today_positions"]),
        rr=ror,
        psharpe=psharpe,
        mark_coverage=live_mark_pct,
        live_rr=live_rr,
    )
    active = "Y" if active_flag else "N"
    inventory_lines.append(
        f"{row['policy_name']:<38} {active:>3} {row['positions']:>5} {len(policy_opportunities[row['policy_name']]):>5} {resolved:>5} "
        f"{row['positions'] - resolved:>5} {row['today_positions']:>5} {row['stations']:>5} "
        f"{fmt_pct(wr, 5)} {fmt_pct(ror, 6)} {fmt_num(psharpe, 7, 3)} {fmt_num(dsharpe, 7, 3)} "
        f"{live_positions:>4} {fmt_pct(live_mark_pct, 5)} {fmt_pct(live_rr, 6)} "
        f"{live.get('wins95', 0):>5} {live.get('loss05', 0):>5} {status:<13}"
    )

print_table(
    "POLICY RESEARCH BREAKDOWN - no dollar PnL; performance normalized by risk",
    f"{'POLICY':<38} {'Act':>3} {'Total':>5} {'Opp':>5} {'Res':>5} {'Pend':>5} {'Live':>5} {'Stns':>5} "
    f"{'WR':>5} {'R/R':>6} {'pSharp':>7} {'dSharp':>7} {'Now':>4} {'Mark%':>5} "
    f"{'LiveR/R':>6} {'Bid95':>5} {'Bid05':>5} {'Status':<13}",
    inventory_lines,
)

expected_policy_lines: list[str] = []
for policy_name, stats in sorted(
    expected_policy_stats.items(),
    key=lambda item: (
        not is_active_policy(item[0]),
        -(return_risk(float(item[1]["realized"]), float(item[1]["risk"])) or -999.0),
        item[0],
    ),
):
    resolved = int(stats["resolved"])
    risk = float(stats["risk"])
    hit_rate = stats["won"] / resolved * 100.0 if resolved else None
    avg_fair = stats["fair_sum"] / stats["fair_n"] * 100.0 if stats["fair_n"] else None
    avg_edge = stats["edge_sum"] / stats["edge_n"] if stats["edge_n"] else None
    expected_rr = return_risk(float(stats["expected"]), risk)
    realized_rr = return_risk(float(stats["realized"]), risk)
    edge_capture = None if expected_rr is None or realized_rr is None else realized_rr - expected_rr
    gate = "LOW_N" if resolved < 30 else "OK"
    expected_policy_lines.append(
        f"{policy_name:<38} {'Y' if is_active_policy(policy_name) else 'N':>3} {resolved:>5} "
        f"{fmt_num(risk / resolved if resolved else None, 6, 3)} {fmt_pct(avg_fair, 8)} "
        f"{fmt_num(avg_edge, 8, 3)} {fmt_pct(hit_rate, 7)} {fmt_pct(expected_rr, 8)} "
        f"{fmt_pct(realized_rr, 8)} {fmt_pct(edge_capture, 8)} {gate:<5}"
    )

print_table(
    "EXPECTED EDGE VALIDATION - ex-ante fair vs resolved weather outcome",
    f"{'POLICY':<38} {'Act':>3} {'Res':>5} {'AvgPx':>6} {'AvgFair':>8} {'AvgEdge':>8} "
    f"{'Hit%':>7} {'ExpR/R':>8} {'RealR/R':>8} {'Real-Exp':>8} {'Gate':<5}",
    expected_policy_lines[:30],
)

expected_band_lines: list[str] = []
for band, stats in sorted(expected_entry_band_stats.items()):
    resolved = int(stats["resolved"])
    risk = float(stats["risk"])
    hit_rate = stats["won"] / resolved * 100.0 if resolved else None
    avg_fair = stats["fair_sum"] / stats["fair_n"] * 100.0 if stats["fair_n"] else None
    avg_edge = stats["edge_sum"] / stats["edge_n"] if stats["edge_n"] else None
    expected_rr = return_risk(float(stats["expected"]), risk)
    realized_rr = return_risk(float(stats["realized"]), risk)
    edge_capture = None if expected_rr is None or realized_rr is None else realized_rr - expected_rr
    expected_band_lines.append(
        f"{band:<11} {resolved:>5} {len(stats['policies']):>4} {fmt_num(risk / resolved if resolved else None, 6, 3)} "
        f"{fmt_pct(avg_fair, 8)} {fmt_num(avg_edge, 8, 3)} {fmt_pct(hit_rate, 7)} "
        f"{fmt_pct(expected_rr, 8)} {fmt_pct(realized_rr, 8)} {fmt_pct(edge_capture, 8)}"
    )

print_table(
    "ENTRY BAND EDGE VALIDATION - pooled resolved policy positions",
    f"{'Band':<11} {'Res':>5} {'Pol':>4} {'AvgPx':>6} {'AvgFair':>8} {'AvgEdge':>8} "
    f"{'Hit%':>7} {'ExpR/R':>8} {'RealR/R':>8} {'Real-Exp':>8}",
    expected_band_lines,
)

exposure_lines: list[str] = []
for row in policy_inventory_rows:
    policy_name = row["policy_name"]
    total = int(row["positions"])
    opportunities = len(policy_opportunities[policy_name])
    duplicate_count = max(0, total - opportunities)
    entry_bands = ",".join(sorted(policy_entry_bands[policy_name])) or "n/a"
    bucket_types = ",".join(sorted(policy_bucket_types[policy_name])) or "n/a"
    exposure_lines.append(
        f"{policy_name:<38} {'Y' if is_active_policy(policy_name) else 'N':>3} {total:>5} {opportunities:>5} "
        f"{duplicate_count:>5} {fmt_pct(mark_pct(duplicate_count, total), 6)} {entry_bands:<23} {bucket_types:<13}"
    )

print(
    f"\nRESEARCH EXPOSURE SUMMARY: {unique_opportunities} unique opportunities / "
    f"{total_positions_seen} raw policy positions ({duplicate_opportunities} duplicate exposures). "
    f"Active layer: {len(active_opportunities)} unique / {len(active_position_rows)} raw "
    f"({active_duplicates} duplicate exposures)."
)
print_table(
    "POLICY EXPOSURE DIAGNOSTICS - station/date/side/bucket/obs-delay opportunities",
    f"{'POLICY':<38} {'Act':>3} {'Raw':>5} {'Opp':>5} {'Dup':>5} {'Dup%':>6} {'Entry bands':<23} {'Bucket types':<13}",
    exposure_lines,
)

overlap_rows: list[tuple[float, int, str, str]] = []
policy_names = sorted(policy_opportunities)
for idx, left in enumerate(policy_names):
    for right in policy_names[idx + 1 :]:
        left_keys = policy_opportunities[left]
        right_keys = policy_opportunities[right]
        if not left_keys or not right_keys:
            continue
        shared = len(left_keys & right_keys)
        if shared == 0:
            continue
        base = min(len(left_keys), len(right_keys))
        overlap_rows.append((shared / base if base else 0.0, shared, left, right))
overlap_rows.sort(key=lambda row: (-row[0], -row[1], row[2], row[3]))
overlap_lines = [
    f"{left:<38} {right:<38} {shared:>6} {fmt_pct(overlap * 100.0, 8)}"
    for overlap, shared, left, right in overlap_rows
]
print_table(
    "TOP POLICY OVERLAPS - shared station/date/side/bucket/obs-delay",
    f"{'Policy A':<38} {'Policy B':<38} {'Shared':>6} {'Overlap':>8}",
    overlap_lines[:20],
    bar_width=100,
)

today_policy_lines: list[str] = []
for name, row in sorted(today_policy.items(), key=lambda item: return_risk(item[1]["mtm"], item[1]["risk"]) or -999.0, reverse=True):
    stats = policy_stats.get(name, {})
    resolved = int(stats.get("resolved", 0))
    wr = None
    rr = None
    psharpe = None
    dsharpe = None
    if stats:
        won = int(stats.get("won", 0))
        lost = int(stats.get("lost", 0))
        wr = (won / (won + lost) * 100.0) if won + lost else None
        rr = return_risk(float(stats.get("pnl", 0.0)), float(stats.get("risk", 0.0)))
        psharpe = position_sharpe(list(stats.get("pnls", [])))
        _, _, dsharpe = daily_sharpe(dict(stats.get("daily", {})))
    live_rr = return_risk(float(row["mtm"]), float(row["risk"]))
    live_mark_pct = mark_pct(int(row["marked"]), int(row["positions"]))
    today_policy_lines.append(
        f"{name:<38} {row['positions']:>4} {row['marked']:>4} {row['missing']:>4} "
        f"{fmt_pct(live_mark_pct, 5)} {fmt_pct(live_rr, 7)} ${row['risk']:>7.2f} "
        f"{row['wins95']:>5} {row['loss05']:>5} {len(row['stations']):>5} "
        f"{resolved:>5} {fmt_pct(wr, 5)} {fmt_pct(rr, 6)} {fmt_num(psharpe, 7, 3)} {fmt_num(dsharpe, 7, 3)}"
    )

print_table(
    f"LIVE POLICY MONITOR - {TODAY}; current mark is orderbook bid normalized by entry risk",
    f"{'POLICY':<38} {'Pos':>4} {'Mark':>4} {'Miss':>4} {'M%':>5} {'LiveR/R':>7} {'Risk':>8} "
    f"{'Bid95':>5} {'Bid05':>5} {'Stns':>5} {'Res':>5} {'WR':>5} {'R/R':>6} {'pSharp':>7} {'dSharp':>7}",
    today_policy_lines,
)

station_research_lines: list[str] = []
station_names = sorted(set(station_resolved.keys()) | set(today_station.keys()) | set(station_temps.keys()))
for station in station_names:
    resolved = station_resolved.get(station, {})
    live = today_station.get(station, {})
    temps = station_temps.get(station, {})
    exposure = station_exposure.get(station, {})
    resolved_count = int(resolved.get("resolved", 0))
    won = int(resolved.get("won", 0))
    lost = int(resolved.get("lost", 0))
    wr = (won / (won + lost) * 100.0) if won + lost else None
    rr = return_risk(float(resolved.get("pnl", 0.0)), float(resolved.get("risk", 0.0)))
    psharpe = position_sharpe(list(resolved.get("pnls", [])))
    live_positions = int(live.get("positions", 0))
    live_marked = int(live.get("marked", 0))
    live_rr = return_risk(float(live.get("mtm", 0.0)), float(live.get("risk", 0.0)))
    live_mark_pct = mark_pct(live_marked, live_positions)
    status = "NO_LIVE"
    if live_positions:
        if live_mark_pct is not None and live_mark_pct < 50:
            status = "BOOK_GAPS"
        elif live_rr is not None and live_rr < -40:
            status = "LIVE_STRESS"
        elif live_rr is not None and live_rr > 20:
            status = "LIVE_STRONG"
        else:
            status = "WATCH"
    if resolved_count < 20:
        status = "LOW_SAMPLE" if status == "NO_LIVE" else f"{status}/LOW_N"
    gate = "LOW_N" if resolved_count < LOW_N_STATION_THRESHOLD else "OK"
    station_research_lines.append(
        f"{station:<6} {station_regime(station):<8} {int(exposure.get('positions', 0)):>5} "
        f"{len(exposure.get('opportunities', set())):>5} {resolved_count:>5} {live_positions:>5} "
        f"{len(resolved.get('days', set())):>4} {len(resolved.get('policies', set())):>4} {fmt_pct(wr, 5)} {fmt_pct(rr, 6)} "
        f"{fmt_num(psharpe, 7, 3)} {fmt_pct(live_mark_pct, 5)} {fmt_pct(live_rr, 7)} "
        f"{fmt_num(temps.get('high'), 5, 0)} {fmt_num(temps.get('hrrr'), 6, 1)} {gate:<5} {status}"
    )

print_table(
    "STATION RESEARCH BREAKDOWN - station cohort quality and live stress",
    f"{'STN':<6} {'Regime':<8} {'Raw':>5} {'Opp':>5} {'Res':>5} {'Live':>5} {'Days':>4} {'Pol':>4} {'WR':>5} {'R/R':>6} "
    f"{'pSharp':>7} {'M%':>5} {'LiveR/R':>7} {'High':>5} {'HRRR':>6} {'Gate':<5} Status",
    station_research_lines,
)

station_lines: list[str] = []
for station, row in sorted(today_station.items(), key=lambda item: return_risk(item[1]["mtm"], item[1]["risk"]) or -999.0, reverse=True):
    temps = station_temps.get(station, {})
    resolved = station_resolved.get(station, {})
    station_wr = None
    station_rr = None
    if resolved:
        won = int(resolved.get("won", 0))
        lost = int(resolved.get("lost", 0))
        station_wr = (won / (won + lost) * 100.0) if won + lost else None
        station_rr = return_risk(float(resolved.get("pnl", 0.0)), float(resolved.get("risk", 0.0)))
    live_rr = return_risk(float(row["mtm"]), float(row["risk"]))
    live_mark_pct = mark_pct(int(row["marked"]), int(row["positions"]))
    alerts = ",".join(row["alerts"]) if row["alerts"] else "OPEN"
    station_lines.append(
        f"{station:<6} {temps.get('snapshots', 0):>5} {fmt_num(temps.get('high'), 5, 0)} "
        f"{fmt_num(temps.get('hrrr'), 6, 1)} {row['positions']:>4} {len(row['policies']):>4} "
        f"{row['marked']:>4} {row['missing']:>4} {fmt_pct(live_mark_pct, 5)} {fmt_pct(live_rr, 7)} "
        f"${row['risk']:>7.2f} {resolved.get('resolved', 0):>5} {fmt_pct(station_wr, 5)} {fmt_pct(station_rr, 6)} {alerts}"
    )

print_table(
    "STATION COVERAGE AND LIVE MARKS - no dollar PnL; live return normalized by risk",
    f"{'STN':<6} {'Snaps':>5} {'High':>5} {'HRRR':>6} {'Pos':>4} {'Pol':>4} "
    f"{'Mark':>4} {'Miss':>4} {'M%':>5} {'LiveR/R':>7} {'Risk':>8} {'Res':>5} {'WR':>5} {'R/R':>6} Alerts",
    station_lines,
)

missing = sorted(ALL_STATIONS - set(station_temps))
if missing:
    print(f"\nMISSING STATIONS TODAY: {', '.join(missing)}")

print("\nPOLICY LIVE DRILLDOWN - selected token bid marks normalized by entry risk")
for policy_name, policy in sorted(today_policy.items(), key=lambda item: item[0]):
    details = [dict(row) for row in live_rows if row["policy_name"] == policy_name]
    live_rr = return_risk(float(policy["mtm"]), float(policy["risk"]))
    print(
        f"\n{policy_name} positions={policy['positions']} marked={policy['marked']} "
        f"missing={policy['missing']} mark%={fmt_pct(mark_pct(policy['marked'], policy['positions'])).strip()} "
        f"live R/R={fmt_pct(live_rr).strip()} risk=${policy['risk']:.2f}"
    )
    print(
        f"  {'STN':<6} {'Side':<7} {'Bucket':<8} {'Entry':>6} {'Bid':>6} "
        f"{'Ask':>6} {'MarkR/R':>8} {'Edge':>7} {'Age':>6} {'High':>5} {'HRRR':>5} Reason"
    )
    print("  " + "-" * 128)
    detail_rows = sorted(details, key=lambda item: (item["mtm_pnl"] is None, item["station"], item["selected_bucket"] or ""))
    for detail in detail_rows:
        book_age = age_minutes(detail["current_book_time"])
        age_text = "n/a" if book_age is None else f"{book_age:.0f}m"
        high = station_temps.get(detail["station"], {}).get("high")
        hrrr = station_temps.get(detail["station"], {}).get("hrrr")
        reason = mark_reason(detail, high, hrrr)
        detail_rr = return_risk(detail["mtm_pnl"], detail["entry_price"])
        print(
            f"  {detail['station']:<6} {detail['selected_side']:<7} "
            f"{(detail['selected_bucket'] or ''):<8} {detail['entry_price']:>6.3f} "
            f"{fmt_num(detail['current_bid'], 6, 3)} {fmt_num(detail['current_ask'], 6, 3)} "
            f"{fmt_pct(detail_rr, 8)} {fmt_num(detail['entry_edge'], 7, 3)} "
            f"{age_text:>6} {fmt_num(high, 5, 0)} {fmt_num(hrrr, 5, 1)} {reason}"
        )

print(f"\nGenerated: {now.isoformat()}")
