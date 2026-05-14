from __future__ import annotations

from collections import defaultdict
from typing import Any

from rich.text import Text


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


def _status_text(value: Any) -> Text:
    text = str(value or "")
    if text in {"DONE", "ITM", "LIVE_STRONG", "PROMISING"}:
        return Text(text, style="green")
    if text in {"LIVE", "MIXED", "WATCH", "TOO_EARLY"}:
        return Text(text, style="yellow")
    if text in {"BOOK_GAPS", "LIVE_STRESS", "WEAK"}:
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


def _build_live_policy_view(live_rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        }
    )
    station_unique: dict[str, dict[str, Any]] = defaultdict(lambda: {"unique_count": 0, "unique_mtm": 0.0, "done": 0})
    rows_by_policy: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    raw_count = 0
    buy_yes = 0
    buy_no = 0
    in_money = 0
    done = 0
    raw_mtm = 0.0
    latest_position_time = ""

    for row in live_rows:
        raw_count += 1
        station = str(row.get("station", ""))
        side = str(row.get("selected_side", ""))
        bucket = _bucket_from_row(row)
        market_date = str(row.get("market_date", ""))
        key = (station, market_date, f"{side}|{bucket}")
        pnl = _safe_float(row.get("unrealized_pnl"))
        current_bid = row.get("current_bid")
        entry_price = _safe_float(row.get("entry_price"))
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
                "mark": 0.0,
                "pnl": 0.0,
                "max_bid": float("-inf"),
                "status": "",
            },
        )
        group["rows"] += 1
        group["entry"] += entry_price
        group["mark"] += _safe_float(current_bid)
        group["pnl"] += pnl
        if current_bid is not None:
            group["max_bid"] = max(group["max_bid"], _safe_float(current_bid))
        status = str(row.get("state", "") or row.get("effective_status", "") or "")
        if not group["status"]:
            group["status"] = status
        elif status and group["status"] != status:
            group["status"] = "MIXED"

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
                "wins95": 0,
                "loss05": 0,
            },
        )
        policy_group["open_positions"] += 1
        policy_group["risk"] += entry_price
        policy_group["mtm"] += pnl
        if current_bid is None:
            policy_group["missing"] += 1
        else:
            policy_group["marked"] += 1
        if pnl > 0:
            policy_group["wins"] += 1
        if current_bid is not None and _safe_float(current_bid) >= 0.95:
            policy_group["done"] += 1
            policy_group["wins95"] += 1
        if current_bid is not None and _safe_float(current_bid) <= 0.05:
            policy_group["loss05"] += 1

    exposure_rows: list[dict[str, Any]] = []
    for group in exposure_index.values():
        rows = group["rows"] or 1
        entry = group["entry"] / rows
        mark = group["mark"] / rows
        pnl_pct = group["pnl"] / group["entry"] if group["entry"] else None
        status = group["status"] or "LIVE"
        if group["max_bid"] >= 0.95:
            status = "DONE"
        elif group["pnl"] > 0:
            status = "ITM"
        exposure_rows.append({**group, "entry": entry, "mark": mark, "pnl_pct": pnl_pct, "status": status})
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
            }
        )
        station_rows[-1]["status"] = _live_status(station_rows[-1]["mark_pct"], station_rows[-1]["live_rr"])
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
        row["mark_pct"] = _ratio(row["marked"], row["open_positions"])
        row["live_rr"] = _ratio(row["mtm"], row["risk"])
        row["status"] = _live_status(row["mark_pct"], row["live_rr"])

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
        "rows": policy_rows,
    }
