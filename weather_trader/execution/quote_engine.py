from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from weather_trader.execution.contracts import (
    BookSnapshot,
    LiveOrderMode,
    LivePriceSheet,
    LiveQuoteIntent,
    LiveQuoteState,
    utc_now_iso,
)
from weather_trader.execution.liquidity import quantize_price, quantize_shares, quantize_usdc


PHASE2_QUOTE_ENGINE_VERSION = "phase2_post_only_shadow_v1"


def build_post_only_quote_intent(
    *,
    sheet: LivePriceSheet,
    book: BookSnapshot | None,
    as_of_utc: datetime,
    min_quote_notional_usd: float = 1.0,
) -> LiveQuoteIntent:
    quote_id = stable_quote_id(sheet.live_candidate_id, sheet.version, sheet.fair_valid_until)
    batch_group_id = stable_quote_id("batch", sheet.strategy_name, sheet.market_date, sheet.station, sheet.selected_bucket, sheet.selected_side)
    max_quote = _float_or_none(sheet.max_quote_price)
    best_ask = book.best_ask if book is not None else None
    quote_price = _post_only_price(max_quote, best_ask)
    skip_reason = _skip_reason(sheet, book, quote_price, min_quote_notional_usd)
    state = LiveQuoteState.SHADOW_SKIPPED if skip_reason else LiveQuoteState.SHADOW_POSTABLE
    quote_size = quantize_usdc(sheet.quote_size_cap if skip_reason is None else 0.0)
    quote_shares = quantize_shares(quote_size / quote_price) if quote_price and quote_price > 0.0 and quote_size > 0.0 else 0.0
    raw = {
        "version": PHASE2_QUOTE_ENGINE_VERSION,
        "source": "phase1_price_sheet",
        "price_sheet": {
            "version": sheet.version,
            "raw_model_fair": sheet.raw_model_fair,
            "calibrated_fair": sheet.calibrated_fair,
            "uncertainty_haircut": sheet.uncertainty_haircut,
            "adverse_selection_haircut": sheet.adverse_selection_haircut,
            "min_required_edge": sheet.min_required_edge,
            "max_quote_price": sheet.max_quote_price,
            "quote_size_cap": sheet.quote_size_cap,
            "fair_valid_until": sheet.fair_valid_until,
            "cancel_triggers": list(sheet.cancel_triggers),
        },
        "book": _book_payload(book),
        "post_only": {
            "tick_size": 0.01,
            "formula": "min(max_quote_price, best_ask - 0.01)",
            "price_clamped_below_ask": bool(best_ask is not None and max_quote is not None and quote_price is not None and quote_price < max_quote),
            "best_ask": best_ask,
            "max_quote_price": max_quote,
            "quote_price": quote_price,
            "skip_reason": skip_reason,
        },
        "decision_time_utc": as_of_utc.astimezone(timezone.utc).isoformat(),
    }
    return LiveQuoteIntent(
        timestamp=utc_now_iso(),
        quote_id=quote_id,
        live_candidate_id=sheet.live_candidate_id,
        price_sheet_version=sheet.version,
        strategy_name=sheet.strategy_name,
        station=sheet.station,
        market_date=sheet.market_date,
        market_family=sheet.market_family,
        selected_market_id=sheet.selected_market_id,
        selected_token_id=sheet.selected_token_id,
        selected_side=sheet.selected_side,
        selected_bucket=sheet.selected_bucket,
        order_mode=LiveOrderMode.GTD,
        quote_price=quote_price,
        quote_size_usd=quote_size,
        quote_shares=quote_shares,
        post_only=True,
        batch_group_id=batch_group_id,
        gtd_expiry=sheet.fair_valid_until,
        state=state,
        skip_reason=skip_reason,
        raw_json=raw,
    )


def shadow_cancel_reason(quote: dict[str, Any], book: BookSnapshot | None, as_of_utc: datetime) -> str | None:
    expiry = _parse_time(quote.get("gtd_expiry"))
    if expiry is not None and as_of_utc.astimezone(timezone.utc) >= expiry:
        return "fair_valid_until"
    if book is None:
        return "feed_stale_or_disconnected"
    quote_price = _float_or_none(quote.get("quote_price"))
    if quote_price is None:
        return "missing_quote_price"
    if book.best_ask is not None and quote_price >= float(book.best_ask) - 1e-9:
        return "book_crosses_max_quote_price"
    return None


def stable_quote_id(*parts: Any) -> str:
    payload = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":"))
    return f"quote_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _post_only_price(max_quote: float | None, best_ask: float | None) -> float | None:
    if max_quote is None:
        return None
    if best_ask is None:
        return quantize_price(max_quote)
    return _floor_cent(min(max_quote, float(best_ask) - 0.01))


def _skip_reason(
    sheet: LivePriceSheet,
    book: BookSnapshot | None,
    quote_price: float | None,
    min_quote_notional_usd: float,
) -> str | None:
    if not sheet.eligible:
        return sheet.reject_reason or "PRICE_SHEET_INELIGIBLE"
    if sheet.selected_token_id is None:
        return "MISSING_TOKEN"
    if book is None or book.best_ask is None:
        return "MISSING_BOOK_OR_ASK"
    if quote_price is None or quote_price <= 0.0:
        return "NO_POST_ONLY_PRICE"
    if quote_price >= float(book.best_ask) - 1e-9:
        return "WOULD_CROSS_SPREAD"
    if sheet.quote_size_cap < min_quote_notional_usd:
        return "QUOTE_SIZE_BELOW_MIN"
    return None


def _book_payload(book: BookSnapshot | None) -> dict[str, Any] | None:
    if book is None:
        return None
    return {
        "token_id": book.token_id,
        "timestamp": book.timestamp,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "spread": book.spread,
    }


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


def _floor_cent(value: float | None) -> float | None:
    if value is None:
        return None
    return quantize_price(max(0.0, int((value + 1e-12) * 100.0) / 100.0))


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
