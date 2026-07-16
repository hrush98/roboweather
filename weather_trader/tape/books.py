from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from weather_trader.tape.contracts import BookCheckpoint, CoverageState, MarketTapeEvent
from weather_trader.tape.storage import iter_segment


@dataclass(frozen=True)
class ReconstructedBook:
    token_id: str
    event_id: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    reconstruction_hash: str
    valid: bool
    invalid_reason: str | None


class BookReconstructor:
    """Deterministic L2 reconstruction that never applies deltas across a gap."""

    def __init__(self) -> None:
        self._books: dict[str, tuple[dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
        self._valid: set[str] = set()
        self._reason: dict[str, str] = {}

    def apply(self, event: MarketTapeEvent) -> ReconstructedBook:
        token_id = event.token_id
        if event.event_type == "book":
            if not _is_full_book(event.raw_payload):
                self.invalidate(token_id, "malformed_full_book")
                return self.snapshot(token_id, event.stable_event_id or f"receipt:{event.receipt_sequence}")
            bids = _levels(event.raw_payload, "bids")
            asks = _levels(event.raw_payload, "asks")
            self._books[token_id] = (bids, asks)
            self._valid.add(token_id)
            self._reason.pop(token_id, None)
        elif event.coverage_state is not CoverageState.VALID:
            self.invalidate(token_id, f"coverage_{event.coverage_state.value.lower()}")
        elif event.event_type == "price_change":
            if token_id not in self._valid or token_id not in self._books:
                self.invalidate(token_id, "delta_before_full_book")
            else:
                self._apply_change(event)
        return self.snapshot(token_id, event.stable_event_id or f"receipt:{event.receipt_sequence}")

    def invalidate(self, token_id: str, reason: str) -> None:
        self._valid.discard(token_id)
        self._reason[token_id] = reason

    def snapshot(self, token_id: str, event_id: str) -> ReconstructedBook:
        bids, asks = self._books.get(token_id, ({}, {}))
        bid_rows = tuple((float(price), float(size)) for price, size in sorted(bids.items(), reverse=True))
        ask_rows = tuple((float(price), float(size)) for price, size in sorted(asks.items()))
        digest = _book_hash(bids, asks)
        return ReconstructedBook(
            token_id=token_id,
            event_id=event_id,
            bids=bid_rows,
            asks=ask_rows,
            reconstruction_hash=digest,
            valid=token_id in self._valid,
            invalid_reason=self._reason.get(token_id),
        )

    def checkpoint(self, event: MarketTapeEvent) -> BookCheckpoint:
        if event.stable_event_id is None or event.append_offset is None:
            raise ValueError("checkpoint event must have storage identity")
        book = self.snapshot(event.token_id, event.stable_event_id)
        return BookCheckpoint(
            checkpoint_id=f"{event.token_id}:{event.stable_event_id}",
            session_id=event.collector_session_id,
            token_id=event.token_id,
            event_id=event.stable_event_id,
            event_offset=event.append_offset,
            captured_at_utc=event.received_at_utc,
            bids=book.bids,
            asks=book.asks,
            reconstruction_hash=book.reconstruction_hash,
            coverage_state=CoverageState.VALID if book.valid else event.coverage_state,
        )

    def _apply_change(self, event: MarketTapeEvent) -> None:
        payload = event.raw_payload
        change = payload.get("price_change") if isinstance(payload, dict) else None
        source = change if isinstance(change, dict) else payload
        if not isinstance(source, dict):
            self.invalidate(event.token_id, "malformed_price_change")
            return
        try:
            price = Decimal(str(source["price"]))
            size = Decimal(str(source["size"]))
            side = str(source["side"]).upper()
        except (KeyError, InvalidOperation):
            self.invalidate(event.token_id, "malformed_price_change")
            return
        bids, asks = self._books[event.token_id]
        levels = bids if side == "BUY" else asks if side == "SELL" else None
        if levels is None:
            self.invalidate(event.token_id, "unknown_price_change_side")
        elif size == 0:
            levels.pop(price, None)
        elif size > 0:
            levels[price] = size
        else:
            self.invalidate(event.token_id, "negative_level_size")


def reconstruct_segment(path: Path) -> dict[str, ReconstructedBook]:
    reconstructor = BookReconstructor()
    latest: dict[str, ReconstructedBook] = {}
    for event in iter_segment(path):
        latest[event.token_id] = reconstructor.apply(event)
    return latest


def _levels(payload: object, key: str) -> dict[Decimal, Decimal]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        return {}
    result: dict[Decimal, Decimal] = {}
    for row in payload[key]:
        try:
            price = Decimal(str(row["price"]))
            size = Decimal(str(row["size"]))
        except (KeyError, TypeError, InvalidOperation):
            continue
        if size > 0:
            result[price] = size
    return result


def _is_full_book(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("bids"), list)
        and isinstance(payload.get("asks"), list)
    )


def _book_hash(bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal]) -> str:
    payload = {
        "asks": [[str(price), str(size)] for price, size in sorted(asks.items())],
        "bids": [[str(price), str(size)] for price, size in sorted(bids.items(), reverse=True)],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
