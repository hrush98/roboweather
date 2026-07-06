from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


MARKOUT_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("10s", timedelta(seconds=10)),
    ("30s", timedelta(seconds=30)),
    ("2m", timedelta(minutes=2)),
    ("10m", timedelta(minutes=10)),
)


def label_shadow_quote_outcome(
    quote: dict[str, Any],
    *,
    feed_events: list[dict[str, Any]],
    book_snapshots: list[dict[str, Any]],
    labeled_at: str,
) -> dict[str, Any]:
    quote_price = _float_or_none(quote.get("quote_price"))
    intended_notional = _float_or_none(quote.get("quote_size_usd")) or 0.0
    quote_time = _parse_time(quote.get("timestamp"))
    expiry = _parse_time(quote.get("gtd_expiry"))
    raw = _json(quote.get("raw_json"))
    depth = raw.get("initial_depth_context") if isinstance(raw.get("initial_depth_context"), dict) else {}
    queue_ahead_usd = _float_or_none(depth.get("queue_ahead_usd")) or 0.0
    events = _events_in_window(feed_events, start=quote_time, end=expiry)
    books = _events_in_window(book_snapshots, start=quote_time, end=expiry)
    sell_flow_usd = _sell_flow_at_or_through_quote(events, quote_price)
    touched_quote = _book_touched_quote(events, books, quote_price)
    conservative_threshold = queue_ahead_usd + intended_notional
    base_threshold = queue_ahead_usd + intended_notional * 0.5
    conservative_fill = sell_flow_usd >= conservative_threshold and intended_notional > 0.0
    base_fill = sell_flow_usd >= base_threshold and intended_notional > 0.0
    optimistic_fill = touched_quote or sell_flow_usd > 0.0
    markouts = _markouts(quote_price, quote_time, feed_events, book_snapshots)
    adverse_book_move = _adverse_book_move(quote_price, events, books)
    cancel_trigger = quote.get("cancel_reason")
    payload = {
        "labeled_at": labeled_at,
        "quote_id": quote.get("quote_id"),
        "live_candidate_id": quote.get("live_candidate_id"),
        "quote_spec_id": quote.get("quote_spec_id"),
        "intended_notional_usd": intended_notional,
        "quote_price": quote_price,
        "postable": str(quote.get("state")) != "SHADOW_SKIPPED" and quote_price is not None,
        "queue_ahead_usd": queue_ahead_usd,
        "observed_sell_flow_usd": sell_flow_usd,
        "conservative_fill": conservative_fill,
        "base_fill": base_fill,
        "optimistic_fill": optimistic_fill,
        "adverse_book_move": adverse_book_move,
        "cancel_trigger": cancel_trigger,
        "markouts": markouts,
        "feed_event_count": len(events),
        "book_snapshot_count": len(books),
        "raw_json": {
            "label_version": "shadow_outcome_labels_v1",
            "quote_state": quote.get("state"),
            "skip_reason": quote.get("skip_reason"),
            "cancel_reason": cancel_trigger,
            "depth_context": depth,
            "thresholds": {
                "conservative_usd": conservative_threshold,
                "base_usd": base_threshold,
                "optimistic": "any sell flow at-or-through quote or book touch",
            },
            "observations": {
                "feed_event_count": len(events),
                "book_snapshot_count": len(books),
                "touched_quote": touched_quote,
                "observed_sell_flow_usd": sell_flow_usd,
            },
        },
    }
    return payload


def _sell_flow_at_or_through_quote(events: list[dict[str, Any]], quote_price: float | None) -> float:
    if quote_price is None:
        return 0.0
    total = 0.0
    for event in events:
        price = _float_or_none(event.get("price"))
        size = _float_or_none(event.get("size"))
        if price is None or size is None or price > quote_price + 1e-9:
            continue
        event_type = str(event.get("event_type") or "")
        side = str(event.get("side") or "").upper()
        if event_type in {"last_trade_price", "trade", "match"} or side in {"SELL", "ASK"}:
            total += abs(price * size)
    return round(total, 6)


def _book_touched_quote(events: list[dict[str, Any]], books: list[dict[str, Any]], quote_price: float | None) -> bool:
    if quote_price is None:
        return False
    for row in [*events, *books]:
        best_bid = _float_or_none(row.get("best_bid"))
        best_ask = _float_or_none(row.get("best_ask"))
        if best_bid is not None and best_bid >= quote_price - 1e-9:
            return True
        if best_ask is not None and best_ask <= quote_price + 1e-9:
            return True
    return False


def _adverse_book_move(quote_price: float | None, events: list[dict[str, Any]], books: list[dict[str, Any]]) -> bool:
    if quote_price is None:
        return False
    for row in [*events, *books]:
        best_bid = _float_or_none(row.get("best_bid"))
        if best_bid is not None and best_bid <= quote_price - 0.02:
            return True
    return False


def _markouts(
    quote_price: float | None,
    quote_time: datetime | None,
    feed_events: list[dict[str, Any]],
    book_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = sorted([*feed_events, *book_snapshots], key=lambda row: _parse_time(row.get("received_at") or row.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc))
    result: dict[str, Any] = {}
    for label, delta in MARKOUT_WINDOWS:
        target = quote_time + delta if quote_time is not None else None
        row = _first_at_or_after(rows, target)
        best_bid = _float_or_none(row.get("best_bid")) if row else None
        best_ask = _float_or_none(row.get("best_ask")) if row else None
        mid = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None
        result[label] = {
            "status": "available" if row is not None else "pending",
            "received_at": row.get("received_at") if row else None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "bid_markout_vs_quote": (best_bid - quote_price) if best_bid is not None and quote_price is not None else None,
            "mid_markout_vs_quote": (mid - quote_price) if mid is not None and quote_price is not None else None,
        }
    result["next_weather_update"] = {"status": "pending_batch"}
    result["close"] = {"status": "pending_batch"}
    result["settlement"] = {"status": "pending_batch"}
    return result


def _first_at_or_after(rows: list[dict[str, Any]], target: datetime | None) -> dict[str, Any] | None:
    if target is None:
        return None
    for row in rows:
        timestamp = _parse_time(row.get("received_at") or row.get("timestamp"))
        if timestamp is not None and timestamp >= target:
            return row
    return None


def _events_in_window(rows: list[dict[str, Any]], *, start: datetime | None, end: datetime | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        timestamp = _parse_time(row.get("received_at") or row.get("timestamp"))
        if timestamp is None:
            continue
        if start is not None and timestamp < start:
            continue
        if end is not None and timestamp > end:
            continue
        result.append(row)
    return result


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        import json

        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
