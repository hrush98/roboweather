from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from weather_trader.execution.contracts import BookLevel, BookSnapshot
from weather_trader.markets.polymarket_reader import CLOB_URL


class RestBookClient:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_books(self, token_ids: list[str]) -> dict[str, BookSnapshot]:
        token_ids = [str(token_id) for token_id in token_ids if token_id]
        if not token_ids:
            return {}
        try:
            response = requests.post(
                f"{CLOB_URL}/books",
                json=[{"token_id": token_id} for token_id in token_ids],
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return self._fetch_books_individually(token_ids)

        books: dict[str, BookSnapshot] = {}
        if isinstance(payload, list):
            for item in payload:
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                if token_id:
                    books[token_id] = parse_book_snapshot(token_id, item, source="rest_batch")

        for token_id in token_ids:
            if token_id not in books:
                book = self._fetch_book_if_available(token_id)
                if book is not None:
                    books[token_id] = book
        return books

    def fetch_book(self, token_id: str) -> BookSnapshot:
        response = requests.get(
            f"{CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return parse_book_snapshot(token_id, response.json(), source="rest")

    def _fetch_books_individually(self, token_ids: list[str]) -> dict[str, BookSnapshot]:
        books: dict[str, BookSnapshot] = {}
        for token_id in token_ids:
            book = self._fetch_book_if_available(token_id)
            if book is not None:
                books[token_id] = book
        return books

    def _fetch_book_if_available(self, token_id: str) -> BookSnapshot | None:
        try:
            return self.fetch_book(token_id)
        except requests.RequestException:
            return None


def parse_book_snapshot(token_id: str, payload: dict[str, Any], source: str = "rest") -> BookSnapshot:
    bids = sorted(_parse_levels(payload.get("bids")), key=lambda level: level.price, reverse=True)
    asks = sorted(_parse_levels(payload.get("asks")), key=lambda level: level.price)
    return BookSnapshot(
        token_id=str(token_id),
        bids=bids,
        asks=asks,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _parse_levels(levels: Any) -> list[BookLevel]:
    if not isinstance(levels, list):
        return []
    parsed: list[BookLevel] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level.get("price"))
            size = float(level.get("size"))
        except (TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        parsed.append(BookLevel(price=price, size=size))
    return parsed
