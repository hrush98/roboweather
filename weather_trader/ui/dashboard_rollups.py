from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from rich.text import Text

from weather_trader.execution.contracts import TradeAction
from weather_trader.execution.positions import effective_status_for_position

_PRELIM_CUTOFF_LOCAL = time(19, 0)
_STATION_TIMEZONES = {
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _fmt(value) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError, OverflowError):
        return ""


def _fmt_money(value) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError, OverflowError):
        return ""


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError, OverflowError):
        return ""


def _money_text(value: Any) -> Text:
    amount = _safe_float(value, 0.0)
    if amount > 0:
        return Text(f"${amount:.2f}", style="green")
    if amount < 0:
        return Text(f"${amount:.2f}", style="red")
    return Text("$0.00", style="dim")


def _pct_text(value: Any, positive_threshold: float = 0.5) -> Text:
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return Text("", style="dim")
    style = "green" if amount >= positive_threshold else "red" if amount < positive_threshold else ""
    return Text(f"{amount * 100:.1f}%", style=style)


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _price_rr(price: float | None, entry: float) -> float | None:
    if price is None or entry == 0:
        return None
    return (price - entry) / entry


def _live_status(mark_pct: float | None, live_rr: float | None, resolved: int | None = None) -> str:
    if resolved is not None and resolved < 10:
        return "TOO_EARLY"
    if mark_pct is not None and mark_pct < 0.5:
        return "BOOK_GAPS"
    if live_rr is not None and live_rr < -0.4:
        return "LIVE_STRESS"
    if live_rr is not None and live_rr > 0.2:
        return "LIVE_STRONG"
    return "WATCH"


def _book_status(mark_pct: float | None) -> str:
    if mark_pct is None:
        return "NO_BOOK_MARK"
    if mark_pct < 0.5:
        return "NO_BOOK_MARK"
    return "MARKED"


def _status_text(value: Any) -> Text:
    text = str(value or "")
    if text in {"DONE", "ITM", "LIVE_STRONG", "PROMISING", "PRELIM_WIN", "OFFICIAL_WIN", "MARKED", "RUNNING", "EXITED"}:
        return Text(text, style="green")
    if text in {"LIVE", "MIXED", "WATCH", "TOO_EARLY"}:
        return Text(text, style="yellow")
    if text in {"BOOK_GAPS", "NO_BOOK_MARK", "LIVE_STRESS", "WEAK", "PRELIM_LOSS", "OFFICIAL_LOSS", "FAILED"}:
        return Text(text, style="red")
    return Text(text)


def _bucket_label(lower_f, upper_f) -> str:
    if lower_f is not None and upper_f is not None:
        return f"{float(lower_f):g}-{float(upper_f):g}F"
    if lower_f is not None:
        return f">={float(lower_f):g}F"
    if upper_f is not None:
        return f"<={float(upper_f):g}F"
    return "unknown"


def _bucket_from_row(row: dict[str, Any]) -> str:
    bucket = row.get("selected_bucket")
    if bucket:
        return str(bucket)
    return _bucket_label(row.get("lower_f"), row.get("upper_f"))


def _parse_bucket(bucket: str | None) -> tuple[float | None, float | None]:
    if not bucket:
        return None, None
    value = bucket.replace("F", "")
    try:
        if value.startswith("<="):
            return None, float(value[2:])
        if value.startswith(">="):
            return float(value[2:]), None
        low, high = value.split("-")
        return float(low), float(high)
    except (TypeError, ValueError):
        return None, None


def _in_bucket(temp: float | None, bucket: str | None) -> bool:
    if temp is None or not bucket:
        return False
    low, high = _parse_bucket(bucket)
    if high is None:
        return low is not None and temp >= low
    if low is None:
        return temp <= high
    return low <= temp <= high


def _market_date(row: dict[str, Any]) -> date | None:
    value = row.get("market_date")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _prelim_ready(row: dict[str, Any], as_of_utc: datetime) -> bool:
    market_date = _market_date(row)
    timezone_name = _STATION_TIMEZONES.get(str(row.get("station", "")))
    if market_date is None or timezone_name is None:
        return False
    local_now = as_of_utc.astimezone(ZoneInfo(timezone_name))
    if local_now.date() > market_date:
        return True
    return local_now.date() == market_date and local_now.time() >= _PRELIM_CUTOFF_LOCAL


def _weather_outcome(row: dict[str, Any], as_of_utc: datetime | None = None) -> dict[str, Any]:
    as_of = as_of_utc or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    entry = _safe_float(row.get("entry_price"))
    final_high = row.get("final_high_tmpf")
    high_so_far = row.get("high_so_far")
    side = TradeAction(str(row.get("selected_side", TradeAction.SKIP)))
    lower_f, upper_f = _parse_bucket(_bucket_from_row(row))
    high = final_high
    if high is None and _prelim_ready(row, as_of):
        high = high_so_far
    if high is None and high_so_far is not None:
        effective = effective_status_for_position(
            side=side,
            lower_f=lower_f,
            upper_f=upper_f,
            high_so_far=_safe_float(high_so_far, default=None),
        )
        if str(effective) in {"EFFECTIVELY_WON", "EFFECTIVELY_LOST"}:
            high = high_so_far
    if high is None:
        return {"weather_status": "LIVE", "weather_correct": None, "weather_pnl": None, "weather_high": high_so_far}
    yes_won = _in_bucket(_safe_float(high), _bucket_from_row(row))
    correct = yes_won if side == "BUY_YES" else not yes_won
    prefix = "OFFICIAL" if final_high is not None else "PRELIM"
    return {
        "weather_status": f"{prefix}_{'WIN' if correct else 'LOSS'}",
        "weather_correct": correct,
        "weather_pnl": 1.0 - entry if correct else -entry,
        "weather_high": _safe_float(high),
    }


def _normalized_policy_model_group(row: dict[str, Any]) -> str:
    policy_name = str(row.get("policy_name", ""))
    raw_policy = row.get("raw_policy") or {}
    policy_meta = raw_policy.get("policy") if isinstance(raw_policy, dict) else {}
    if not isinstance(policy_meta, dict):
        policy_meta = {}
    source = str(policy_meta.get("source") or row.get("policy_source") or "")
    if policy_name.startswith("max_so_far") or source == "max_so_far":
        return "max_so_far"
    model_group = str(row.get("model_group", ""))
    if model_group:
        return model_group
    return str(policy_meta.get("model_name") or source or "")


def _build_position_view(open_positions: list[dict[str, Any]]) -> dict[str, Any]:
    exposure_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    station_raw: dict[str, dict[str, Any]] = defaultdict(lambda: {"raw_count": 0, "buy_yes": 0, "buy_no": 0, "raw_mtm": 0.0, "done": 0})
    station_unique: dict[str, dict[str, Any]] = defaultdict(lambda: {"unique_count": 0, "unique_mtm": 0.0, "done": 0})
    raw_count = 0
    buy_yes = 0
    buy_no = 0
    in_money = 0
    done = 0
    raw_mtm = 0.0
    latest_position_time = ""

    for row in open_positions:
        raw_count += 1
        station = str(row.get("station", ""))
        side = str(row.get("side", ""))
        bucket = _bucket_from_row(row)
        market_date = str(row.get("market_date", ""))
        key = (station, market_date, f"{side}|{bucket}")
        cost = _safe_float(row.get("cost"))
        pnl = _safe_float(row.get("unrealized_pnl"))
        shares = _safe_float(row.get("shares"), 0.0)
        mark_value = _safe_float(row.get("mark_value"))
        current_bid = row.get("current_bid")
        timestamp = str(row.get("timestamp", ""))
        if timestamp and timestamp > latest_position_time:
            latest_position_time = timestamp
        if side == "BUY_YES":
            buy_yes += 1
        elif side == "BUY_NO":
            buy_no += 1
        if pnl > 0:
            in_money += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            done += 1
        raw_mtm += pnl
        station_raw[station]["raw_count"] += 1
        station_raw[station]["raw_mtm"] += pnl
        if side == "BUY_YES":
            station_raw[station]["buy_yes"] += 1
        elif side == "BUY_NO":
            station_raw[station]["buy_no"] += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            station_raw[station]["done"] += 1

        group = exposure_index.setdefault(
            key,
            {
                "station": station,
                "market_date": market_date,
                "side": side,
                "bucket": bucket,
                "rows": 0,
                "cost": 0.0,
                "shares": 0.0,
                "mark_value": 0.0,
                "pnl": 0.0,
                "max_bid": float("-inf"),
                "status": "",
            },
        )
        group["rows"] += 1
        group["cost"] += cost
        group["shares"] += shares
        group["mark_value"] += mark_value
        group["pnl"] += pnl
        if current_bid is not None:
            group["max_bid"] = max(group["max_bid"], _safe_float(current_bid))
        status = str(row.get("state", "") or row.get("effective_status", "") or "")
        if not group["status"]:
            group["status"] = status
        elif status and group["status"] != status:
            group["status"] = "MIXED"

    exposure_rows: list[dict[str, Any]] = []
    for group in exposure_index.values():
        share_count = group["shares"] or 0.0
        entry = group["cost"] / share_count if share_count else 0.0
        mark = group["mark_value"] / share_count if share_count else 0.0
        pnl_pct = group["pnl"] / group["cost"] if group["cost"] else None
        status = group["status"] or "LIVE"
        if group["max_bid"] >= 0.95:
            status = "DONE"
        elif group["pnl"] > 0:
            status = "ITM"
        exposure_rows.append(
            {
                **group,
                "entry": entry,
                "mark": mark,
                "pnl_pct": pnl_pct,
                "status": status,
            }
        )
        station_unique[group["station"]]["unique_count"] += 1
        station_unique[group["station"]]["unique_mtm"] += group["pnl"]
        if group["max_bid"] >= 0.95:
            station_unique[group["station"]]["done"] += 1

    exposure_rows.sort(key=lambda row: (row["pnl"], row["station"], row["market_date"], row["bucket"]), reverse=True)
    station_rows: list[dict[str, Any]] = []
    for station in sorted(set(list(station_raw.keys()) + list(station_unique.keys()))):
        raw = station_raw[station]
        unique = station_unique[station]
        station_rows.append(
            {
                "station": station,
                "raw_count": raw["raw_count"],
                "unique_count": unique["unique_count"],
                "buy_yes": raw["buy_yes"],
                "buy_no": raw["buy_no"],
                "raw_mtm": raw["raw_mtm"],
                "unique_mtm": unique["unique_mtm"],
                "done": raw["done"],
            }
        )
    station_rows.sort(key=lambda row: (row["raw_mtm"], row["station"]), reverse=True)

    return {
        "raw_count": raw_count,
        "unique_count": len(exposure_rows),
        "buy_yes": buy_yes,
        "buy_no": buy_no,
        "in_money": in_money,
        "done": done,
        "raw_mtm": raw_mtm,
        "unique_mtm": sum(row["pnl"] for row in exposure_rows),
        "stations": len(station_rows),
        "latest_position_time": latest_position_time[11:19] if len(latest_position_time) >= 19 else latest_position_time,
        "exposure_index": exposure_index,
        "exposure_rows": exposure_rows,
        "station_rows": station_rows,
    }


def _build_policy_view(
    policy_rows: list[dict[str, Any]],
    exposure_index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    rows_by_policy: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    latest_time = ""
    for row in policy_rows:
        policy = str(row.get("policy_name", ""))
        model_group = _normalized_policy_model_group(row)
        strategy_bucket = str(row.get("strategy_bucket", ""))
        obs_delay_bucket = str(row.get("obs_delay_bucket", ""))
        key = (
            str(row.get("station", "")),
            str(row.get("market_date", "")),
            f"{str(row.get('selected_side', ''))}|{_bucket_from_row(row)}",
        )
        exposure = exposure_index.get(key)
        if exposure is None:
            continue
        if row.get("timestamp") and str(row.get("timestamp")) > latest_time:
            latest_time = str(row.get("timestamp"))
        pnl = _safe_float(exposure["pnl"])
        group_key = (policy, model_group, strategy_bucket, obs_delay_bucket)
        group = rows_by_policy.setdefault(
            group_key,
            {
                "policy": policy,
                "model_group": model_group,
                "strategy_bucket": strategy_bucket,
                "obs_delay_bucket": obs_delay_bucket,
                "rows": 0,
                "station_days": 0,
                "wins": 0,
                "done": 0,
                "mtm": 0.0,
                "entry_sum": 0.0,
            },
        )
        group["rows"] += 1
        group["station_days"] += 1
        group["mtm"] += pnl
        group["entry_sum"] += _safe_float(row.get("entry_price"))
        if pnl > 0:
            group["wins"] += 1
        if exposure["max_bid"] >= 0.95:
            group["done"] += 1
    rows = list(rows_by_policy.values())
    rows.sort(
        key=lambda row: (
            row["mtm"],
            row["wins"],
            row["station_days"],
            row["policy"],
            row["model_group"],
            row["strategy_bucket"],
            row["obs_delay_bucket"],
        ),
        reverse=True,
    )
    for row in rows:
        row["win_rate"] = row["wins"] / row["rows"] if row["rows"] else 0.0
        row["avg_pnl"] = row["mtm"] / row["rows"] if row["rows"] else 0.0
    return {"rows": rows, "latest_time": latest_time[11:19] if len(latest_time) >= 19 else latest_time}


def _build_live_policy_view(live_rows: list[dict[str, Any]], as_of_utc: datetime | None = None) -> dict[str, Any]:
    as_of = as_of_utc or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    exposure_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    station_raw: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "raw_count": 0,
            "buy_yes": 0,
            "buy_no": 0,
            "raw_mtm": 0.0,
            "done": 0,
            "marked": 0,
            "missing": 0,
            "risk": 0.0,
            "wins95": 0,
            "loss05": 0,
            "weather_scored": 0,
            "weather_wins": 0,
            "weather_losses": 0,
            "weather_pnl": 0.0,
            "weather_risk": 0.0,
        }
    )
    station_unique: dict[str, dict[str, Any]] = defaultdict(lambda: {"unique_count": 0, "unique_mtm": 0.0, "done": 0})
    rows_by_policy: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    position_rows: list[dict[str, Any]] = []
    raw_count = 0
    buy_yes = 0
    buy_no = 0
    in_money = 0
    done = 0
    raw_mtm = 0.0
    latest_position_time = ""

    for row in live_rows:
        outcome = _weather_outcome(row, as_of)
        raw_count += 1
        station = str(row.get("station", ""))
        side = str(row.get("selected_side", ""))
        bucket = _bucket_from_row(row)
        market_date = str(row.get("market_date", ""))
        key = (station, market_date, f"{side}|{bucket}")
        pnl = _safe_float(row.get("unrealized_pnl"))
        current_bid = row.get("current_bid")
        entry_price = _safe_float(row.get("entry_price"))
        entry_fair = row.get("entry_fair")
        entry_fair_float = _safe_float(entry_fair)
        entry_edge = row.get("entry_edge")
        entry_edge_float = _safe_float(entry_edge)
        exp_rr = _price_rr(entry_fair_float, entry_price) if entry_fair is not None else None
        live_rr = _price_rr(_safe_float(current_bid), entry_price) if current_bid is not None else None
        live_minus_exp = live_rr - exp_rr if live_rr is not None and exp_rr is not None else None
        timestamp = str(row.get("timestamp", ""))
        if timestamp and timestamp > latest_position_time:
            latest_position_time = timestamp

        if side == "BUY_YES":
            buy_yes += 1
        elif side == "BUY_NO":
            buy_no += 1
        if pnl > 0:
            in_money += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            done += 1
        raw_mtm += pnl

        station_raw[station]["raw_count"] += 1
        station_raw[station]["raw_mtm"] += pnl
        station_raw[station]["risk"] += entry_price
        if current_bid is None:
            station_raw[station]["missing"] += 1
        else:
            station_raw[station]["marked"] += 1
            if _safe_float(current_bid) >= 0.95:
                station_raw[station]["wins95"] += 1
            if _safe_float(current_bid) <= 0.05:
                station_raw[station]["loss05"] += 1
        if side == "BUY_YES":
            station_raw[station]["buy_yes"] += 1
        elif side == "BUY_NO":
            station_raw[station]["buy_no"] += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            station_raw[station]["done"] += 1

        group = exposure_index.setdefault(
            key,
            {
                "station": station,
                "market_date": market_date,
                "side": side,
                "bucket": bucket,
                "rows": 0,
                "entry": 0.0,
                "fair": 0.0,
                "edge": 0.0,
                "mark": 0.0,
                "pnl": 0.0,
                "fair_count": 0,
                "edge_count": 0,
                "max_bid": float("-inf"),
                "status": "",
                "book_marked": 0,
                "book_missing": 0,
                "weather_scored": 0,
                "weather_wins": 0,
                "weather_losses": 0,
                "weather_pnl": 0.0,
                "weather_risk": 0.0,
                "weather_status": "LIVE",
                "weather_high": outcome["weather_high"],
            },
        )
        group["rows"] += 1
        group["entry"] += entry_price
        if entry_fair is not None:
            group["fair"] += entry_fair_float
            group["fair_count"] += 1
        if entry_edge is not None:
            group["edge"] += entry_edge_float
            group["edge_count"] += 1
        group["mark"] += _safe_float(current_bid)
        group["pnl"] += pnl
        if current_bid is not None:
            group["max_bid"] = max(group["max_bid"], _safe_float(current_bid))
            group["book_marked"] += 1
        else:
            group["book_missing"] += 1
        status = str(row.get("state", "") or row.get("effective_status", "") or "")
        if not group["status"]:
            group["status"] = status
        elif status and group["status"] != status:
            group["status"] = "MIXED"
        if outcome["weather_correct"] is not None:
            group["weather_scored"] += 1
            group["weather_risk"] += entry_price
            group["weather_pnl"] += _safe_float(outcome["weather_pnl"])
            group["weather_high"] = outcome["weather_high"]
            if outcome["weather_correct"]:
                group["weather_wins"] += 1
            else:
                group["weather_losses"] += 1
            if group["weather_status"] == "LIVE":
                group["weather_status"] = str(outcome["weather_status"])
            elif group["weather_status"] != outcome["weather_status"]:
                group["weather_status"] = "MIXED"

        if outcome["weather_correct"] is not None:
            station_raw[station]["weather_scored"] += 1
            station_raw[station]["weather_risk"] += entry_price
            station_raw[station]["weather_pnl"] += _safe_float(outcome["weather_pnl"])
            if outcome["weather_correct"]:
                station_raw[station]["weather_wins"] += 1
            else:
                station_raw[station]["weather_losses"] += 1

        policy = str(row.get("policy_name", ""))
        model_group = _normalized_policy_model_group(row)
        strategy_bucket = str(row.get("strategy_bucket", ""))
        obs_delay_bucket = str(row.get("obs_delay_bucket", ""))
        policy_key = (policy, model_group, strategy_bucket, obs_delay_bucket)
        policy_group = rows_by_policy.setdefault(
            policy_key,
            {
                "policy": policy,
                "model_group": model_group,
                "strategy_bucket": strategy_bucket,
                "obs_delay_bucket": obs_delay_bucket,
                "open_positions": 0,
                "wins": 0,
                "done": 0,
                "marked": 0,
                "missing": 0,
                "risk": 0.0,
                "mtm": 0.0,
                "entry_sum": 0.0,
                "fair_sum": 0.0,
                "edge_sum": 0.0,
                "bid_sum": 0.0,
                "fair_count": 0,
                "edge_count": 0,
                "wins95": 0,
                "loss05": 0,
                "weather_scored": 0,
                "weather_wins": 0,
                "weather_losses": 0,
                "weather_pnl": 0.0,
                "weather_risk": 0.0,
                "weather_status": "LIVE",
            },
        )
        policy_group["open_positions"] += 1
        policy_group["risk"] += entry_price
        policy_group["mtm"] += pnl
        policy_group["entry_sum"] += entry_price
        if entry_fair is not None:
            policy_group["fair_sum"] += entry_fair_float
            policy_group["fair_count"] += 1
        if entry_edge is not None:
            policy_group["edge_sum"] += entry_edge_float
            policy_group["edge_count"] += 1
        if current_bid is None:
            policy_group["missing"] += 1
        else:
            policy_group["marked"] += 1
            policy_group["bid_sum"] += _safe_float(current_bid)
        if pnl > 0:
            policy_group["wins"] += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            policy_group["done"] += 1
            policy_group["wins95"] += 1
        if current_bid is not None and _safe_float(current_bid) <= 0.05:
            policy_group["loss05"] += 1
        if outcome["weather_correct"] is not None:
            policy_group["weather_scored"] += 1
            policy_group["weather_risk"] += entry_price
            policy_group["weather_pnl"] += _safe_float(outcome["weather_pnl"])
            if outcome["weather_correct"]:
                policy_group["weather_wins"] += 1
            else:
                policy_group["weather_losses"] += 1
            if policy_group["weather_status"] == "LIVE":
                policy_group["weather_status"] = str(outcome["weather_status"])
            elif policy_group["weather_status"] != outcome["weather_status"]:
                policy_group["weather_status"] = "MIXED"

        position_rows.append(
            {
                "time": timestamp[11:19] if len(timestamp) >= 19 else timestamp,
                "policy": policy,
                "model_group": model_group,
                "strategy_bucket": strategy_bucket,
                "obs_delay_bucket": obs_delay_bucket,
                "station": station,
                "market_date": market_date,
                "side": side,
                "bucket": bucket,
                "entry": entry_price,
                "fair": entry_fair_float if entry_fair is not None else None,
                "edge": entry_edge_float if entry_edge is not None else None,
                "bid": _safe_float(current_bid) if current_bid is not None else None,
                "exp_rr": exp_rr,
                "live_rr": live_rr,
                "live_minus_exp": live_minus_exp,
                "risk": entry_price,
                "high": outcome["weather_high"],
                "weather_status": outcome["weather_status"],
                "book_status": _book_status(1.0 if current_bid is not None else None),
            }
        )

    exposure_rows: list[dict[str, Any]] = []
    for group in exposure_index.values():
        rows = group["rows"] or 1
        entry = group["entry"] / rows
        mark = group["mark"] / group["book_marked"] if group["book_marked"] else None
        fair = group["fair"] / group["fair_count"] if group["fair_count"] else None
        edge = group["edge"] / group["edge_count"] if group["edge_count"] else None
        pnl_pct = group["pnl"] / group["entry"] if group["entry"] else None
        status = group["status"] or "LIVE"
        if group["max_bid"] >= 0.95:
            status = "DONE"
        elif group["pnl"] > 0:
            status = "ITM"
        book_pct = _ratio(group["book_marked"], group["rows"])
        exposure_rows.append(
            {
                **group,
                "entry": entry,
                "fair": fair,
                "edge": edge,
                "mark": mark,
                "pnl_pct": pnl_pct,
                "expected_rr": _price_rr(fair, entry),
                "status": status,
                "book_status": _book_status(book_pct),
                "book_mark_pct": book_pct,
                "live_minus_exp": pnl_pct - _price_rr(fair, entry) if pnl_pct is not None and fair is not None else None,
                "weather_rr": _ratio(group["weather_pnl"], group["weather_risk"]),
            }
        )
        station_unique[group["station"]]["unique_count"] += 1
        station_unique[group["station"]]["unique_mtm"] += group["pnl"]
        if group["max_bid"] >= 0.95:
            station_unique[group["station"]]["done"] += 1

    exposure_rows.sort(key=lambda row: (row["pnl"], row["station"], row["market_date"], row["bucket"]), reverse=True)
    station_rows: list[dict[str, Any]] = []
    for station in sorted(set(list(station_raw.keys()) + list(station_unique.keys()))):
        raw = station_raw[station]
        unique = station_unique[station]
        station_rows.append(
            {
                "station": station,
                "raw_count": raw["raw_count"],
                "unique_count": unique["unique_count"],
                "buy_yes": raw["buy_yes"],
                "buy_no": raw["buy_no"],
                "raw_mtm": raw["raw_mtm"],
                "unique_mtm": unique["unique_mtm"],
                "done": raw["done"],
                "marked": raw["marked"],
                "missing": raw["missing"],
                "risk": raw["risk"],
                "live_rr": _ratio(raw["raw_mtm"], raw["risk"]),
                "mark_pct": _ratio(raw["marked"], raw["raw_count"]),
                "wins95": raw["wins95"],
                "loss05": raw["loss05"],
                "weather_scored": raw["weather_scored"],
                "weather_wins": raw["weather_wins"],
                "weather_losses": raw["weather_losses"],
                "weather_pnl": raw["weather_pnl"],
                "weather_risk": raw["weather_risk"],
                "weather_rr": _ratio(raw["weather_pnl"], raw["weather_risk"]),
            }
        )
        station_rows[-1]["status"] = _live_status(station_rows[-1]["mark_pct"], station_rows[-1]["live_rr"])
        station_rows[-1]["book_status"] = _book_status(station_rows[-1]["mark_pct"])
    station_rows.sort(key=lambda row: (row["raw_mtm"], row["station"]), reverse=True)

    policy_station_index: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in live_rows:
        policy = str(row.get("policy_name", ""))
        model_group = _normalized_policy_model_group(row)
        strategy_bucket = str(row.get("strategy_bucket", ""))
        obs_delay_bucket = str(row.get("obs_delay_bucket", ""))
        station = str(row.get("station", ""))
        key = (policy, model_group, strategy_bucket, obs_delay_bucket, station)
        station_group = policy_station_index.setdefault(
            key,
            {
                "policy": policy,
                "model_group": model_group,
                "strategy_bucket": strategy_bucket,
                "obs_delay_bucket": obs_delay_bucket,
                "station": station,
                "open_positions": 0,
                "wins": 0,
                "done": 0,
                "mtm": 0.0,
            },
        )
        station_group["open_positions"] += 1
        station_group["mtm"] += _safe_float(row.get("unrealized_pnl"))
        if _safe_float(row.get("unrealized_pnl")) > 0:
            station_group["wins"] += 1
        if row.get("current_bid") is not None and _safe_float(row.get("current_bid")) >= 0.95:
            station_group["done"] += 1

    policy_station_rows = list(policy_station_index.values())
    policy_station_rows.sort(
        key=lambda row: (
            row["mtm"],
            row["wins"],
            row["open_positions"],
            row["policy"],
            row["station"],
        ),
        reverse=True,
    )
    for row in policy_station_rows:
        row["win_rate"] = row["wins"] / row["open_positions"] if row["open_positions"] else 0.0

    policy_rows = list(rows_by_policy.values())
    policy_rows.sort(
        key=lambda row: (
            row["mtm"],
            row["wins"],
            row["open_positions"],
            row["policy"],
            row["model_group"],
            row["strategy_bucket"],
            row["obs_delay_bucket"],
        ),
        reverse=True,
    )
    for row in policy_rows:
        row["win_rate"] = row["wins"] / row["open_positions"] if row["open_positions"] else 0.0
        row["avg_pnl"] = row["mtm"] / row["open_positions"] if row["open_positions"] else 0.0
        row["avg_entry"] = row["entry_sum"] / row["open_positions"] if row["open_positions"] else None
        row["avg_fair"] = row["fair_sum"] / row["fair_count"] if row["fair_count"] else None
        row["avg_edge"] = row["edge_sum"] / row["edge_count"] if row["edge_count"] else None
        row["avg_bid"] = row["bid_sum"] / row["marked"] if row["marked"] else None
        row["mark_pct"] = _ratio(row["marked"], row["open_positions"])
        row["live_rr"] = _ratio(row["mtm"], row["risk"])
        row["expected_rr"] = _price_rr(row["avg_fair"], row["avg_entry"] or 0.0)
        row["live_minus_exp"] = (
            row["live_rr"] - row["expected_rr"]
            if row["live_rr"] is not None and row["expected_rr"] is not None
            else None
        )
        row["status"] = _live_status(row["mark_pct"], row["live_rr"])
        row["book_status"] = _book_status(row["mark_pct"])
        row["weather_rr"] = _ratio(row["weather_pnl"], row["weather_risk"])

    position_rows.sort(
        key=lambda row: (
            row["live_minus_exp"] if row["live_minus_exp"] is not None else 999.0,
            -row["risk"],
            row["policy"],
            row["station"],
            row["bucket"],
        )
    )

    return {
        "raw_count": raw_count,
        "unique_count": len(exposure_rows),
        "buy_yes": buy_yes,
        "buy_no": buy_no,
        "in_money": in_money,
        "done": done,
        "raw_mtm": raw_mtm,
        "unique_mtm": sum(row["pnl"] for row in exposure_rows),
        "stations": len(station_rows),
        "latest_position_time": latest_position_time[11:19] if len(latest_position_time) >= 19 else latest_position_time,
        "exposure_rows": exposure_rows,
        "station_rows": station_rows,
        "policy_station_rows": policy_station_rows,
        "policy_rows": policy_rows,
        "position_rows": position_rows,
        "rows": policy_rows,
    }
