#!/usr/bin/env python3
"""Quant-style daily policy leaderboard scored from weather outcomes.

This report is for research monitoring, not accounting. It scores each research
policy position against station_date_outcomes.final_high_tmpf when available,
then falls back to post-local-cutoff high_so_far snapshots. That makes the daily
report useful once the observed station high is practically known even if
Polymarket has not fully settled. Positions without an official or preliminary
weather outcome are kept pending.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_db_path() -> Path:
    explicit = os.environ.get("ROBOWEATHER_STATUS_DB") or os.environ.get("DB")
    if explicit:
        return Path(explicit).expanduser()
    local_state = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
    if local_state.exists():
        return local_state
    return REPO_ROOT / "data/paper/research_2026-05-08_multimodel.sqlite"


DB_PATH = _default_db_path()

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
}
LOW_N_STATION_THRESHOLD = 20
PRELIM_CUTOFF_LOCAL = time(19, 0)
STATION_TIMEZONES = {
    "KATL": "America/New_York",
    "KBKF": "America/Denver",
    "KDAL": "America/Chicago",
    "KDEN": "America/Denver",
    "KHOU": "America/Chicago",
    "KLAX": "America/Los_Angeles",
    "KLGA": "America/New_York",
    "KMIA": "America/New_York",
    "KORD": "America/Chicago",
    "KSEA": "America/Los_Angeles",
    "KSFO": "America/Los_Angeles",
}


def is_active_policy(name: str) -> bool:
    return name.startswith("broad_")


def parse_bucket(bucket: str | None) -> tuple[float | None, float | None]:
    if not bucket:
        return None, None
    text = bucket.removesuffix("F")
    if text.startswith("<="):
        return None, float(text.removeprefix("<="))
    if text.startswith(">="):
        return float(text.removeprefix(">=")), None
    if "-" in text:
        low, high = text.split("-", 1)
        return float(low), float(high)
    return None, None


def bucket_won(final_temp: float, bucket: str | None) -> bool:
    low, high = parse_bucket(bucket)
    if low is None and high is None:
        return False
    if low is None:
        return final_temp <= float(high)
    if high is None:
        return final_temp >= float(low)
    return float(low) <= final_temp <= float(high)


def prelim_ready(row: sqlite3.Row, as_of_utc: datetime) -> bool:
    timezone_name = STATION_TIMEZONES.get(row["station"])
    if timezone_name is None:
        return False
    market_date = date.fromisoformat(row["market_date"])
    local_now = as_of_utc.astimezone(ZoneInfo(timezone_name))
    if local_now.date() > market_date:
        return True
    return local_now.date() == market_date and local_now.time() >= PRELIM_CUTOFF_LOCAL


def score_position(row: sqlite3.Row, as_of_utc: datetime) -> dict[str, Any]:
    market_family = row["market_family"] if "market_family" in row.keys() else "HIGH_TEMP"
    if market_family == "LOW_TEMP":
        final_temp = row["final_low_tmpf"]
        progress_temp = row["low_so_far"]
        label = "low"
    else:
        final_temp = row["final_high_tmpf"]
        progress_temp = row["high_so_far"]
        label = "high"
    temp = final_temp
    source = "official" if final_temp is not None else None
    if temp is None and progress_temp is not None and prelim_ready(row, as_of_utc):
        temp = progress_temp
        source = f"prelim_{label}_so_far"
    if temp is None:
        return {"resolved": False, "correct": None, "ret": None, "temp": None, "source": None}
    yes_won = bucket_won(float(temp), row["selected_bucket"])
    correct = yes_won if row["selected_side"] == "BUY_YES" else not yes_won
    entry = float(row["entry_price"])
    ret = (1.0 - entry) if correct else -entry
    return {"resolved": True, "correct": bool(correct), "ret": ret, "temp": float(temp), "source": source}


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def sharpe(values: list[float]) -> float | None:
    if not values:
        return None
    avg = sum(values) / len(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return avg / std


def return_risk(total_return: float | None, total_risk: float | None) -> float | None:
    if total_return is None or not total_risk:
        return None
    return total_return / total_risk


def fmt_pct(value: float | None, width: int = 7) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{value * 100:>{width - 1}.0f}%"


def fmt_num(value: float | None, width: int = 7, decimals: int = 3) -> str:
    if value is None:
        return f"{'n/a':>{width}}"
    return f"{value:>{width}.{decimals}f}"


def fmt_date(value: date) -> str:
    return value.isoformat()


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


def bucket_type(bucket: str | None) -> str:
    if not bucket:
        return "missing"
    if bucket.startswith("<=") or bucket.startswith(">="):
        return "tail"
    return "range"


def probability_band(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 0.25:
        return "0.00-0.25"
    if value < 0.50:
        return "0.25-0.50"
    if value < 0.75:
        return "0.50-0.75"
    return "0.75-1.00"


def edge_band(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 0.10:
        return "<0.10"
    if value < 0.20:
        return "0.10-0.20"
    if value < 0.35:
        return "0.20-0.35"
    return ">=0.35"


def fair_for_side(row: sqlite3.Row) -> float | None:
    fair = row["entry_fair"]
    if fair is None:
        return None
    return float(fair)


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


def research_status(resolved: int, pending: int, rr: float | None, p_sharpe: float | None, breadth: int) -> str:
    if resolved == 0 and pending:
        return "PENDING"
    if resolved < 5:
        return "TINY_N"
    if breadth < 3:
        return "CONCENTRATED"
    if p_sharpe is not None and rr is not None and p_sharpe > 0.25 and rr > 0.20:
        return "PROMISING"
    if p_sharpe is not None and p_sharpe < 0 and rr is not None and rr < 0:
        return "WEAK"
    if pending > resolved:
        return "PENDING_HEAVY"
    return "WATCH"


def rows_for_range(db: sqlite3.Connection, start_date: str, end_date: str) -> list[sqlite3.Row]:
    return db.execute(
        """
        with station_progress as (
            select
                station,
                market_date,
                coalesce(market_family, 'HIGH_TEMP') as market_family,
                max(high_so_far) as high_so_far,
                min(low_so_far) as low_so_far,
                max(timestamp) as latest_snapshot_at
            from prediction_snapshots
            group by station, market_date, coalesce(market_family, 'HIGH_TEMP')
        )
        select
            rpp.id,
            rpp.timestamp,
            rpp.policy_name,
            rpp.station,
            rpp.market_date,
            rpp.strategy_bucket,
            rpp.obs_delay_bucket,
            coalesce(rpp.market_family, 'HIGH_TEMP') as market_family,
            rpp.selected_side,
            rpp.selected_bucket,
            rpp.entry_price,
            rpp.entry_edge,
            rpp.entry_fair,
            sdo.final_high_tmpf,
            sdo.final_low_tmpf,
            sdo.source as outcome_source,
            sdo.resolved_at,
            highs.high_so_far,
            highs.low_so_far,
            highs.latest_snapshot_at
        from research_policy_positions rpp
        left join station_date_outcomes sdo
          on sdo.station = rpp.station
         and sdo.market_date = rpp.market_date
        left join station_progress highs
          on highs.station = rpp.station
         and highs.market_date = rpp.market_date
         and highs.market_family = coalesce(rpp.market_family, 'HIGH_TEMP')
        where rpp.market_date between ? and ?
        order by rpp.policy_name, rpp.station, rpp.id
        """,
        (start_date, end_date),
    ).fetchall()


def rows_for_date(db: sqlite3.Connection, target_date: str) -> list[sqlite3.Row]:
    return rows_for_range(db, target_date, target_date)


def window_start(target_date: str, days: int) -> str:
    end = date.fromisoformat(target_date)
    return fmt_date(end - timedelta(days=days - 1))


def compute_leaderboard(
    target_date: str,
    active_only: bool = False,
    windows: tuple[int, ...] = (1,),
    as_of_utc: datetime | None = None,
) -> dict[str, Any]:
    as_of = as_of_utc or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    all_start = db.execute(
        "select min(market_date) as first_date from research_policy_positions"
    ).fetchone()["first_date"] or target_date
    if 9999 in windows:
        start_date = all_start
    else:
        max_window = max(day for day in windows if day < 9999)
        start_date = window_start(target_date, max_window)
    rows = rows_for_range(db, start_date, target_date)
    db.close()

    by_policy: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "resolved": 0,
            "pending": 0,
            "won": 0,
            "lost": 0,
            "risk": 0.0,
            "ret": 0.0,
            "rets": [],
            "entries": [],
            "edges": [],
            "fairs": [],
            "resolved_fairs": [],
            "resolved_edges": [],
            "expected_risk": 0.0,
            "expected_ret": 0.0,
            "stations": set(),
            "buckets": set(),
            "bets": [],
        }
    )
    by_policy_day: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"risk": 0.0, "ret": 0.0, "resolved": 0}
    )
    by_station: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "resolved": 0,
            "pending": 0,
            "won": 0,
            "lost": 0,
            "risk": 0.0,
            "ret": 0.0,
            "rets": [],
            "policies": set(),
            "fair_sum": 0.0,
            "fair_n": 0,
        }
    )
    split_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "resolved": 0, "pending": 0, "won": 0, "risk": 0.0, "ret": 0.0, "rets": [], "policies": set()}
    )
    calibration_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "resolved": 0, "wins": 0, "fair_sum": 0.0, "fair_n": 0, "edge_sum": 0.0, "edge_n": 0, "brier_sum": 0.0, "brier_n": 0}
    )
    overlap_keys: dict[str, set[tuple[str, str, str, str, str]]] = defaultdict(set)
    filtered_rows = [row for row in rows if not active_only or is_active_policy(row["policy_name"])]
    opportunity_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in filtered_rows:
        opportunity_counts[opportunity_key(row)] += 1

    outcome_stations = set()
    pending_stations = set()
    for row in filtered_rows:
        policy_name = row["policy_name"]
        score = score_position(row, as_of)
        entry = float(row["entry_price"])
        edge = row["entry_edge"]
        fair = fair_for_side(row)
        opp_key = opportunity_key(row)
        exposure_weight = 1.0 / opportunity_counts[opp_key]

        policy = by_policy[policy_name]
        policy["total"] += 1
        policy.setdefault("opportunities", set()).add(opp_key)
        policy.setdefault("adj_risk", 0.0)
        policy.setdefault("adj_ret", 0.0)
        policy.setdefault("adj_rets", [])
        policy["entries"].append(entry)
        if edge is not None:
            policy["edges"].append(float(edge))
        if fair is not None:
            policy["fairs"].append(fair)
        policy["stations"].add(row["station"])
        policy["buckets"].add(row["selected_bucket"])
        overlap_keys[policy_name].add(opp_key)

        station = by_station[row["station"]]
        station["total"] += 1
        station["policies"].add(policy_name)
        if fair is not None:
            station["fair_sum"] += fair
            station["fair_n"] += 1

        if score["resolved"]:
            ret = float(score["ret"])
            correct = bool(score["correct"])
            outcome_stations.add(row["station"])
            policy["resolved"] += 1
            policy["risk"] += entry
            policy["ret"] += ret
            policy["rets"].append(ret)
            if fair is not None:
                policy["expected_risk"] += entry
                policy["expected_ret"] += fair - entry
                policy["resolved_fairs"].append(fair)
            if edge is not None:
                policy["resolved_edges"].append(float(edge))
            policy["adj_risk"] += entry * exposure_weight
            policy["adj_ret"] += ret * exposure_weight
            policy["adj_rets"].append(ret * exposure_weight)
            if correct:
                policy["won"] += 1
            else:
                policy["lost"] += 1
            day_key = (policy_name, row["market_date"])
            by_policy_day[day_key]["risk"] += entry
            by_policy_day[day_key]["ret"] += ret
            by_policy_day[day_key]["resolved"] += 1

            station["resolved"] += 1
            station["risk"] += entry
            station["ret"] += ret
            station["rets"].append(ret)
            if correct:
                station["won"] += 1
            else:
                station["lost"] += 1
        else:
            pending_stations.add(row["station"])
            policy["pending"] += 1
            station["pending"] += 1

        policy["bets"].append(
            {
                "station": row["station"],
                "side": row["selected_side"],
                "bucket": row["selected_bucket"],
                "entry": entry,
                "edge": edge,
                "final_high": score["temp"],
                "outcome_source": score["source"],
                "correct": score["correct"],
            }
        )

        split_values = {
            "station": row["station"],
            "side": row["selected_side"],
            "bucket_type": bucket_type(row["selected_bucket"]),
            "entry_band": entry_band(entry),
            "obs_delay": row["obs_delay_bucket"],
            "strategy": row["strategy_bucket"],
        }
        for split_name, split_value in split_values.items():
            split = split_stats[(split_name, str(split_value))]
            split["total"] += 1
            split["policies"].add(policy_name)
            if score["resolved"]:
                split["resolved"] += 1
                split["risk"] += entry
                split["ret"] += float(score["ret"])
                split["rets"].append(float(score["ret"]))
                if score["correct"]:
                    split["won"] += 1
            else:
                split["pending"] += 1

        for cal_name, cal_value in {
            "fair": probability_band(fair),
            "edge": edge_band(float(edge) if edge is not None else None),
            "entry": entry_band(entry),
            "side": row["selected_side"],
            "station": row["station"],
            "obs_delay": row["obs_delay_bucket"] or "missing",
        }.items():
            cal = calibration_stats[(cal_name, cal_value)]
            cal["n"] += 1
            if fair is not None:
                cal["fair_sum"] += fair
                cal["fair_n"] += 1
            if edge is not None:
                cal["edge_sum"] += float(edge)
                cal["edge_n"] += 1
            if score["resolved"]:
                correct_float = 1.0 if score["correct"] else 0.0
                cal["resolved"] += 1
                cal["wins"] += int(correct_float)
                if fair is not None:
                    cal["brier_sum"] += (fair - correct_float) ** 2
                    cal["brier_n"] += 1

    total_abs_policy_return = sum(abs(float(policy["ret"])) for policy in by_policy.values())
    leaderboard = []
    for policy_name, policy in by_policy.items():
        resolved = int(policy["resolved"])
        rr = return_risk(float(policy["ret"]), float(policy["risk"]))
        p_sharpe = sharpe(policy["rets"])
        daily_rrs = []
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for (day_policy, _day), day_stats in sorted(by_policy_day.items()):
            if day_policy != policy_name:
                continue
            day_rr = return_risk(float(day_stats["ret"]), float(day_stats["risk"]))
            if day_rr is not None:
                daily_rrs.append(day_rr)
                cumulative += day_rr
                peak = max(peak, cumulative)
                max_drawdown = min(max_drawdown, cumulative - peak)
        win_rate = policy["won"] / resolved if resolved else None
        avg_entry = mean(policy["entries"])
        avg_fair = mean(policy["fairs"])
        avg_edge = mean(policy["edges"])
        avg_resolved_fair = mean(policy["resolved_fairs"])
        avg_resolved_edge = mean(policy["resolved_edges"])
        expected_rr = return_risk(float(policy["expected_ret"]), float(policy["expected_risk"]))
        avg_win = mean([ret for ret in policy["rets"] if ret > 0])
        avg_loss = mean([ret for ret in policy["rets"] if ret < 0])
        payoff = abs(avg_win / avg_loss) if avg_win is not None and avg_loss not in (None, 0) else None
        breadth = len(policy["stations"])
        status = research_status(resolved, int(policy["pending"]), rr, p_sharpe, breadth)
        adj_rr = return_risk(float(policy["adj_ret"]), float(policy["adj_risk"]))
        leaderboard.append(
            {
                "policy": policy_name,
                "active": is_active_policy(policy_name),
                "total": policy["total"],
                "opportunities": len(policy["opportunities"]),
                "resolved": resolved,
                "pending": policy["pending"],
                "won": policy["won"],
                "lost": policy["lost"],
                "win_rate": win_rate,
                "rr": rr,
                "sharpe": p_sharpe,
                "contribution": None if total_abs_policy_return == 0 else float(policy["ret"]) / total_abs_policy_return,
                "adj_rr": adj_rr,
                "adj_sharpe": sharpe(policy["adj_rets"]),
                "avg_entry": avg_entry,
                "avg_fair": avg_fair,
                "avg_edge": avg_edge,
                "avg_resolved_fair": avg_resolved_fair,
                "avg_resolved_edge": avg_resolved_edge,
                "expected_rr": expected_rr,
                "edge_capture": None if rr is None or expected_rr is None else rr - expected_rr,
                "payoff": payoff,
                "daily_vol": None if len(daily_rrs) < 2 else math.sqrt(sum((value - (sum(daily_rrs) / len(daily_rrs))) ** 2 for value in daily_rrs) / len(daily_rrs)),
                "max_drawdown": max_drawdown if daily_rrs else None,
                "stations": breadth,
                "buckets": len(policy["buckets"]),
                "status": status,
                "bets": policy["bets"],
            }
        )

    leaderboard.sort(
        key=lambda row: (
            row["status"] != "PROMISING",
            -(row["sharpe"] if row["sharpe"] is not None else -999.0),
            -(row["rr"] if row["rr"] is not None else -999.0),
            -row["resolved"],
        )
    )

    station_rows = []
    for station, stats in by_station.items():
        resolved = int(stats["resolved"])
        rr = return_risk(float(stats["ret"]), float(stats["risk"]))
        avg_fair = stats["fair_sum"] / stats["fair_n"] if stats["fair_n"] else None
        hit_rate = stats["won"] / resolved if resolved else None
        station_rows.append(
            {
                "station": station,
                "regime": station_regime(station),
                "total": stats["total"],
                "resolved": resolved,
                "pending": stats["pending"],
                "win_rate": hit_rate,
                "rr": rr,
                "sharpe": sharpe(stats["rets"]),
                "avg_fair": avg_fair,
                "cal_error": None if avg_fair is None or hit_rate is None else hit_rate - avg_fair,
                "live_stress": stats["pending"],
                "gate": "LOW_N" if resolved < LOW_N_STATION_THRESHOLD else "OK",
                "policies": len(stats["policies"]),
            }
        )
    station_rows.sort(key=lambda row: (row["regime"], row["gate"] != "LOW_N", -(row["sharpe"] if row["sharpe"] is not None else -999), -(row["rr"] if row["rr"] is not None else -999)))

    split_rows = []
    for (split_name, split_value), stats in split_stats.items():
        resolved = int(stats["resolved"])
        rr = return_risk(float(stats["ret"]), float(stats["risk"]))
        split_rows.append(
            {
                "split": split_name,
                "value": split_value,
                "total": stats["total"],
                "resolved": resolved,
                "pending": stats["pending"],
                "win_rate": stats["won"] / resolved if resolved else None,
                "rr": rr,
                "sharpe": sharpe(stats["rets"]),
                "policies": len(stats["policies"]),
            }
        )
    split_rows.sort(key=lambda row: (row["split"], -(row["resolved"]), -(row["sharpe"] if row["sharpe"] is not None else -999)))

    calibration_rows = []
    for (cal_name, cal_value), stats in calibration_stats.items():
        resolved = int(stats["resolved"])
        avg_fair = stats["fair_sum"] / stats["fair_n"] if stats["fair_n"] else None
        hit_rate = stats["wins"] / resolved if resolved else None
        avg_edge = stats["edge_sum"] / stats["edge_n"] if stats["edge_n"] else None
        calibration_rows.append(
            {
                "type": cal_name,
                "band": cal_value,
                "n": stats["n"],
                "resolved": resolved,
                "avg_fair": avg_fair,
                "avg_edge": avg_edge,
                "hit_rate": hit_rate,
                "cal_error": None if avg_fair is None or hit_rate is None else hit_rate - avg_fair,
                "brier": stats["brier_sum"] / stats["brier_n"] if stats["brier_n"] else None,
            }
        )
    calibration_rows.sort(key=lambda row: (row["type"], row["band"]))

    overlap_rows = []
    policies = sorted(overlap_keys)
    for idx, left in enumerate(policies):
        for right in policies[idx + 1 :]:
            left_keys = overlap_keys[left]
            right_keys = overlap_keys[right]
            if not left_keys or not right_keys:
                continue
            shared = len(left_keys & right_keys)
            if shared == 0:
                continue
            base = min(len(left_keys), len(right_keys))
            overlap_rows.append(
                {
                    "policy_a": left,
                    "policy_b": right,
                    "shared": shared,
                    "overlap": shared / base if base else None,
                    "a_total": len(left_keys),
                    "b_total": len(right_keys),
                }
            )
    overlap_rows.sort(key=lambda row: (-(row["overlap"] or 0.0), -row["shared"], row["policy_a"], row["policy_b"]))

    return {
        "target_date": target_date,
        "db_path": str(DB_PATH),
        "source": "station_date_outcomes.final_high_tmpf plus post-7pm prediction_snapshots.high_so_far fallback",
        "total_positions": sum(row["total"] for row in leaderboard),
        "unique_opportunities": len(opportunity_counts),
        "duplicate_opportunities": max(0, len(filtered_rows) - len(opportunity_counts)),
        "effective_independent_opportunities": len(opportunity_counts),
        "resolved_positions": sum(row["resolved"] for row in leaderboard),
        "pending_positions": sum(row["pending"] for row in leaderboard),
        "total_policies": len(leaderboard),
        "outcome_stations": sorted(outcome_stations),
        "pending_stations": sorted(pending_stations - outcome_stations),
        "leaderboard": leaderboard,
        "stations": station_rows,
        "splits": split_rows,
        "calibration": calibration_rows,
        "overlap": overlap_rows,
    }


def compute_rolling_summary(target_date: str, active_only: bool) -> list[dict[str, Any]]:
    summaries = []
    for label, days in [("1d", 1), ("3d", 3), ("7d", 7), ("all", 9999)]:
        data = compute_leaderboard(target_date, active_only=active_only, windows=(days,))
        policy_rows = data["leaderboard"]
        top = policy_rows[0] if policy_rows else None
        summaries.append(
            {
                "window": label,
                "resolved": data["resolved_positions"],
                "pending": data["pending_positions"],
                "policies": data["total_policies"],
                "top_policy": top["policy"] if top else "n/a",
                "top_status": top["status"] if top else "n/a",
                "top_rr": top["rr"] if top else None,
                "top_sharpe": top["sharpe"] if top else None,
            }
        )
    return summaries


def format_report(data: dict[str, Any], detail_limit: int = 8, rolling: list[dict[str, Any]] | None = None) -> str:
    lines = []
    lines.append(f"POLICY RESEARCH LEADERBOARD - {data['target_date']}")
    lines.append(f"DB: {data['db_path']}")
    lines.append(f"Source: {data['source']} (not PM settlement)")
    lines.append(
        f"Positions: {data['resolved_positions']} resolved / {data['total_positions']} total, "
        f"{data['pending_positions']} pending | policies: {data['total_policies']}"
    )
    lines.append(
        f"Opportunities: {data['unique_opportunities']} unique, "
        f"{data['duplicate_opportunities']} duplicate exposures, "
        f"{data['effective_independent_opportunities']} effective independent"
    )
    if data["pending_stations"]:
        lines.append(f"Pending stations: {', '.join(data['pending_stations'])}")
    lines.append("Metrics: R/R = return over entry risk; Sharpe = per-position return Sharpe; Payoff = avg win / abs(avg loss).")
    if rolling:
        lines.append("")
        lines.append("ROLLING WINDOW SUMMARY")
        lines.append(f"{'Win':<5} {'Res':>5} {'Pend':>5} {'Pol':>4} {'Top policy':<38} {'R/R':>7} {'Sharpe':>7} Status")
        lines.append("-" * 88)
        for row in rolling:
            lines.append(
                f"{row['window']:<5} {row['resolved']:>5} {row['pending']:>5} {row['policies']:>4} "
                f"{row['top_policy']:<38} {fmt_pct(row['top_rr'], 7)} {fmt_num(row['top_sharpe'], 7, 3)} {row['top_status']}"
            )
    lines.append("")
    lines.append(
        f"{'#':>3} {'POLICY':<38} {'Act':>3} {'Pos':>4} {'Opp':>4} {'Res':>4} {'Pend':>4} "
        f"{'WR':>6} {'R/R':>7} {'Sharpe':>7} {'Contr':>7} {'AdjRR':>7} {'AdjSh':>7} {'DVol':>7} {'DD':>7} "
        f"{'Payoff':>7} {'AvgE':>6} {'Edge':>7} {'St':>3} {'Bk':>3} Status"
    )
    lines.append("-" * 166)
    for idx, row in enumerate(data["leaderboard"], 1):
        lines.append(
            f"{idx:>3} {row['policy']:<38} {'Y' if row['active'] else 'N':>3} {row['total']:>4} {row['opportunities']:>4} {row['resolved']:>4} {row['pending']:>4} "
            f"{fmt_pct(row['win_rate'], 6)} {fmt_pct(row['rr'], 7)} {fmt_num(row['sharpe'], 7, 3)} "
            f"{fmt_pct(row['contribution'], 7)} "
            f"{fmt_pct(row['adj_rr'], 7)} {fmt_num(row['adj_sharpe'], 7, 3)} "
            f"{fmt_pct(row['daily_vol'], 7)} {fmt_pct(row['max_drawdown'], 7)} {fmt_num(row['payoff'], 7, 2)} "
            f"{fmt_num(row['avg_entry'], 6, 3)} {fmt_num(row['avg_edge'], 7, 3)} "
            f"{row['stations']:>3} {row['buckets']:>3} {row['status']}"
        )

    lines.append("")
    lines.append("EXPECTED EDGE VALIDATION - ex-ante fair vs realized weather outcome")
    lines.append(
        f"{'POLICY':<38} {'Res':>4} {'AvgPx':>6} {'AvgFair':>8} {'AvgEdge':>8} "
        f"{'HitRate':>8} {'ExpR/R':>8} {'RealR/R':>8} {'Real-Exp':>8} Status"
    )
    lines.append("-" * 112)
    for row in data["leaderboard"][:25]:
        lines.append(
            f"{row['policy']:<38} {row['resolved']:>4} {fmt_num(row['avg_entry'], 6, 3)} "
            f"{fmt_pct(row['avg_resolved_fair'], 8)} {fmt_num(row['avg_resolved_edge'], 8, 3)} "
            f"{fmt_pct(row['win_rate'], 8)} {fmt_pct(row['expected_rr'], 8)} "
            f"{fmt_pct(row['rr'], 8)} {fmt_pct(row['edge_capture'], 8)} {row['status']}"
        )

    lines.append("")
    lines.append("STATION REGIME DIAGNOSTICS")
    lines.append(
        f"{'STN':<6} {'Regime':<8} {'Pos':>4} {'Res':>4} {'Pend':>4} {'WR':>6} {'R/R':>7} "
        f"{'Sharpe':>7} {'AvgFair':>8} {'CalErr':>8} {'Stress':>6} {'Gate':<5} {'Pol':>3}"
    )
    lines.append("-" * 100)
    for row in data["stations"]:
        lines.append(
            f"{row['station']:<6} {row['regime']:<8} {row['total']:>4} {row['resolved']:>4} {row['pending']:>4} "
            f"{fmt_pct(row['win_rate'], 6)} {fmt_pct(row['rr'], 7)} {fmt_num(row['sharpe'], 7, 3)} "
            f"{fmt_pct(row['avg_fair'], 8)} {fmt_pct(row['cal_error'], 8)} {row['live_stress']:>6} {row['gate']:<5} {row['policies']:>3}"
        )

    lines.append("")
    lines.append("SPLIT DIAGNOSTICS")
    lines.append(f"{'Split':<12} {'Value':<16} {'Pos':>4} {'Res':>4} {'Pend':>4} {'WR':>6} {'R/R':>7} {'Sharpe':>7} {'Pol':>3}")
    lines.append("-" * 82)
    for row in data["splits"]:
        if row["resolved"] < 5 and row["pending"] == 0:
            continue
        lines.append(
            f"{row['split']:<12} {row['value']:<16} {row['total']:>4} {row['resolved']:>4} {row['pending']:>4} "
            f"{fmt_pct(row['win_rate'], 6)} {fmt_pct(row['rr'], 7)} {fmt_num(row['sharpe'], 7, 3)} {row['policies']:>3}"
        )

    lines.append("")
    lines.append("CALIBRATION DIAGNOSTICS - fair, edge, entry, side, station, obs-delay")
    lines.append(f"{'Type':<8} {'Band':<10} {'N':>4} {'Res':>4} {'AvgFair':>8} {'HitRate':>8} {'CalErr':>8} {'Brier':>8} {'AvgEdge':>8}")
    lines.append("-" * 82)
    for row in data["calibration"]:
        lines.append(
            f"{row['type']:<8} {row['band']:<10} {row['n']:>4} {row['resolved']:>4} "
            f"{fmt_pct(row['avg_fair'], 8)} {fmt_pct(row['hit_rate'], 8)} {fmt_pct(row['cal_error'], 8)} "
            f"{fmt_num(row['brier'], 8, 3)} {fmt_num(row['avg_edge'], 8, 3)}"
        )

    lines.append("")
    lines.append("POLICY OVERLAP - top shared station/date/side/bucket/obs-delay entries")
    lines.append(f"{'Policy A':<38} {'Policy B':<38} {'Shared':>6} {'Overlap':>8}")
    lines.append("-" * 94)
    for row in data["overlap"][:20]:
        lines.append(
            f"{row['policy_a']:<38} {row['policy_b']:<38} {row['shared']:>6} {fmt_pct(row['overlap'], 8)}"
        )

    lines.append("")
    lines.append(f"TOP {detail_limit} POLICY DRILLDOWN")
    for idx, row in enumerate(data["leaderboard"][:detail_limit], 1):
        lines.append("")
        lines.append(
            f"#{idx} {row['policy']} | {row['status']} | Res {row['resolved']}/{row['total']} | "
            f"WR {fmt_pct(row['win_rate']).strip()} | R/R {fmt_pct(row['rr']).strip()} | Sharpe {fmt_num(row['sharpe']).strip()}"
        )
        by_station: dict[str, list[str]] = defaultdict(list)
        for bet in row["bets"]:
            if bet["correct"] is True:
                mark = "+"
            elif bet["correct"] is False:
                mark = "-"
            else:
                mark = "?"
            high = "pending" if bet["final_high"] is None else f"{bet['final_high']:.0f}F"
            edge = "n/a" if bet["edge"] is None else f"{float(bet['edge']):+.3f}"
            by_station[bet["station"]].append(
                f"{bet['side']} {bet['bucket']} entry={bet['entry']:.3f} edge={edge} high={high} {mark}"
            )
        for station in sorted(by_station):
            lines.append(f"  {station}: " + " | ".join(by_station[station]))

    lines.append("")
    lines.append(
        "Legend: + won, - lost, ? pending. Pending means neither official outcome nor post-cutoff preliminary high is available."
    )
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(lines)


def write_insight(target_date: str, report: str, data: dict[str, Any]) -> None:
    db = sqlite3.connect(str(DB_PATH))
    best = data["leaderboard"][0] if data["leaderboard"] else {"policy": "N/A", "status": "N/A"}
    db.execute(
        """
        insert into hermes_insights (created_at, insight_type, target_date, severity, title, body, metrics_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            "policy_leaderboard",
            target_date,
            "info",
            f"Policy Leaderboard {target_date}: {best['policy']} ({best['status']})",
            report,
            json.dumps(
                {
                    "source": data["source"],
                    "top_policy": best["policy"],
                    "top_status": best["status"],
                    "resolved_positions": data["resolved_positions"],
                    "pending_positions": data["pending_positions"],
                },
                sort_keys=True,
            ),
            json.dumps(data, default=str, sort_keys=True),
        ),
    )
    db.commit()
    db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_date", nargs="?", help="Market date to score, YYYY-MM-DD. Defaults to yesterday UTC.")
    parser.add_argument("--active-only", action="store_true", help="Only include active pm_us12_* and max_so_far_* policies.")
    parser.add_argument("--no-write", action="store_true", help="Do not insert the report into hermes_insights.")
    parser.add_argument("--detail-limit", type=int, default=8, help="Number of policy drilldowns to print.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target = args.target_date or (date.today() - timedelta(days=1)).isoformat()
    data = compute_leaderboard(target, active_only=args.active_only)
    rolling = compute_rolling_summary(target, active_only=args.active_only)
    report = format_report(data, detail_limit=args.detail_limit, rolling=rolling)
    if not args.no_write:
        write_insight(target, report, data)
    print(report)


if __name__ == "__main__":
    main()
