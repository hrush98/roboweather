from __future__ import annotations

from dataclasses import dataclass
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
PHASE2_SHADOW_SPEC_GRID_VERSION = "phase2_shadow_spec_grid_v1"


@dataclass(frozen=True)
class ShadowQuoteSpec:
    quote_spec_id: str
    grid_version: str
    fair_source: str
    haircut_rule: str
    edge_rule: str
    quote_rule: str
    ttl_seconds: int
    cancel_rule: str
    quote_size_usd: float
    side: str
    post_only: bool
    crossing_behavior: str
    ask_offset_cents: int


def stable_spec_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"qspec_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def phase2_shadow_quote_specs() -> tuple[ShadowQuoteSpec, ...]:
    specs: list[ShadowQuoteSpec] = []
    for ttl_seconds in (60, 180):
        for quote_size_usd in (50.0, 100.0):
            for ask_offset_cents in (1, 2, 3, 5, 8, 12):
                payload = {
                    "grid_version": PHASE2_SHADOW_SPEC_GRID_VERSION,
                    "fair_source": "phase1_capped_haircut_fair",
                    "haircut_rule": "phase1_uncertainty_plus_adverse_selection",
                    "edge_rule": "phase1_min_required_edge",
                    "quote_rule": f"min(max_quote_price,best_ask-{ask_offset_cents}c)",
                    "ttl_seconds": ttl_seconds,
                    "cancel_rule": "ttl_or_fair_book_cross_or_stale_feed",
                    "quote_size_usd": quote_size_usd,
                    "side": "BUY_NO",
                    "post_only": True,
                    "crossing_behavior": "skip_if_crossing_or_missing_book",
                    "ask_offset_cents": ask_offset_cents,
                }
                specs.append(ShadowQuoteSpec(quote_spec_id=stable_spec_id(payload), **payload))
    return tuple(specs)


DEFAULT_PHASE2_SHADOW_QUOTE_SPEC = phase2_shadow_quote_specs()[0]


def build_shadow_quote_intents(
    *,
    sheet: LivePriceSheet,
    book: BookSnapshot | None,
    as_of_utc: datetime,
    specs: tuple[ShadowQuoteSpec, ...] | None = None,
    min_quote_notional_usd: float = 1.0,
) -> list[LiveQuoteIntent]:
    return [
        build_post_only_quote_intent(
            sheet=sheet,
            book=book,
            as_of_utc=as_of_utc,
            spec=spec,
            min_quote_notional_usd=min_quote_notional_usd,
        )
        for spec in (specs or phase2_shadow_quote_specs())
    ]


def build_post_only_quote_intent(
    *,
    sheet: LivePriceSheet,
    book: BookSnapshot | None,
    as_of_utc: datetime,
    spec: ShadowQuoteSpec | None = None,
    min_quote_notional_usd: float = 1.0,
) -> LiveQuoteIntent:
    spec = spec or DEFAULT_PHASE2_SHADOW_QUOTE_SPEC
    quote_id = stable_quote_id(sheet.live_candidate_id, sheet.version, sheet.fair_valid_until, spec.quote_spec_id)
    batch_group_id = stable_quote_id("batch", sheet.strategy_name, sheet.market_date, sheet.station, sheet.selected_bucket, sheet.selected_side, sheet.live_candidate_id)
    max_quote = _float_or_none(sheet.max_quote_price)
    best_ask = book.best_ask if book is not None else None
    quote_price = _post_only_price(max_quote, best_ask, spec.ask_offset_cents)
    intended_size = quantize_usdc(float(spec.quote_size_usd))
    skip_reason = _skip_reason(sheet, book, quote_price, intended_size, min_quote_notional_usd)
    state = LiveQuoteState.SHADOW_SKIPPED if skip_reason else LiveQuoteState.SHADOW_POSTABLE
    quote_size = intended_size
    quote_shares = quantize_shares(quote_size / quote_price) if quote_price and quote_price > 0.0 and quote_size > 0.0 else 0.0
    raw = {
        "version": PHASE2_QUOTE_ENGINE_VERSION,
        "spec_grid_version": PHASE2_SHADOW_SPEC_GRID_VERSION,
        "source": "phase1_price_sheet",
        "quote_spec": {
            "quote_spec_id": spec.quote_spec_id,
            "grid_version": spec.grid_version,
            "fair_source": spec.fair_source,
            "haircut_rule": spec.haircut_rule,
            "edge_rule": spec.edge_rule,
            "quote_rule": spec.quote_rule,
            "ttl_seconds": spec.ttl_seconds,
            "cancel_rule": spec.cancel_rule,
            "quote_size_usd": spec.quote_size_usd,
            "side": spec.side,
            "post_only": spec.post_only,
            "crossing_behavior": spec.crossing_behavior,
            "ask_offset_cents": spec.ask_offset_cents,
        },
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
        "initial_depth_context": _depth_context(book, quote_price=quote_price, quote_size_usd=intended_size),
        "post_only": {
            "tick_size": 0.01,
            "formula": spec.quote_rule,
            "price_clamped_below_ask": bool(best_ask is not None and max_quote is not None and quote_price is not None and quote_price < max_quote),
            "best_ask": best_ask,
            "max_quote_price": max_quote,
            "quote_price": quote_price,
            "skip_reason": skip_reason,
            "would_post": skip_reason is None,
        },
        "markout_hooks": {
            "status": "pending_batch",
            "token_id": sheet.selected_token_id,
            "quote_time_utc": as_of_utc.astimezone(timezone.utc).isoformat(),
            "event_source": "clob_feed_events",
            "windows": ["10s", "30s", "2m", "10m", "next_weather_update", "close", "settlement"],
            "useful_size_notional_usd": intended_size,
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
        quote_spec_id=spec.quote_spec_id,
        fair_source=spec.fair_source,
        quote_rule=spec.quote_rule,
        cancel_rule=spec.cancel_rule,
        would_post=skip_reason is None,
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


def _post_only_price(max_quote: float | None, best_ask: float | None, ask_offset_cents: int = 1) -> float | None:
    if max_quote is None:
        return None
    if best_ask is None:
        return None
    return _floor_cent(min(max_quote, float(best_ask) - max(1, ask_offset_cents) / 100.0))


def _skip_reason(
    sheet: LivePriceSheet,
    book: BookSnapshot | None,
    quote_price: float | None,
    quote_size: float,
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
    if quote_size < min_quote_notional_usd:
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


def _depth_context(book: BookSnapshot | None, *, quote_price: float | None, quote_size_usd: float) -> dict[str, Any]:
    if book is None or quote_price is None or quote_price <= 0.0:
        return {
            "quote_price": quote_price,
            "quote_size_usd": quote_size_usd,
            "queue_ahead_shares": None,
            "queue_ahead_usd": None,
            "same_price_bid_shares": None,
            "same_price_bid_usd": None,
            "better_price_bid_shares": None,
            "better_price_bid_usd": None,
            "plausible_50_usd_depth": False,
            "plausible_100_usd_depth": False,
        }
    better = [level for level in book.bids if level.price > quote_price + 1e-9]
    same = [level for level in book.bids if abs(level.price - quote_price) <= 1e-9]
    ahead = better + same
    better_usd = sum(level.price * level.size for level in better)
    same_usd = sum(level.price * level.size for level in same)
    queue_usd = better_usd + same_usd
    queue_shares = sum(level.size for level in ahead)
    same_shares = sum(level.size for level in same)
    better_shares = sum(level.size for level in better)
    return {
        "quote_price": quote_price,
        "quote_size_usd": quote_size_usd,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "spread": book.spread,
        "queue_ahead_shares": quantize_shares(queue_shares),
        "queue_ahead_usd": quantize_usdc(queue_usd),
        "same_price_bid_shares": quantize_shares(same_shares),
        "same_price_bid_usd": quantize_usdc(same_usd),
        "better_price_bid_shares": quantize_shares(better_shares),
        "better_price_bid_usd": quantize_usdc(better_usd),
        "plausible_50_usd_depth": quote_size_usd >= 50.0,
        "plausible_100_usd_depth": quote_size_usd >= 100.0,
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
