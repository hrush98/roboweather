from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from weather_trader.execution.clob_feed import record_clob_message
from weather_trader.execution.contracts import utc_now_iso
from weather_trader.execution.store import ExecutionStore


POLYMARKET_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class MarketEventTransport(Protocol):
    def stream(self, token_ids: list[str]) -> AsyncIterator[dict[str, Any] | list[Any]]:
        ...


@dataclass(frozen=True)
class CollectionStats:
    subscribed_tokens: int
    messages: int
    events: int
    started_at: str
    finished_at: str


class PolymarketMarketWebSocketTransport:
    def __init__(self, url: str = POLYMARKET_MARKET_WS_URL) -> None:
        self.url = url

    async def stream(self, token_ids: list[str]) -> AsyncIterator[dict[str, Any] | list[Any]]:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - exercised only in live operator envs without dependency.
            raise RuntimeError("Install the `websockets` package to run the CLOB event collector.") from exc
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as websocket:
            await websocket.send(json.dumps({"type": "market", "assets_ids": token_ids}))
            async for raw_message in websocket:
                if raw_message == "PONG":
                    continue
                try:
                    payload = json.loads(raw_message)
                except (TypeError, json.JSONDecodeError):
                    continue
                yield payload


async def collect_candidate_clob_events(
    store: ExecutionStore,
    *,
    transport: MarketEventTransport | None = None,
    since_timestamp: str | None = None,
    max_messages: int | None = None,
    max_seconds: float | None = None,
) -> CollectionStats:
    subscriptions = store.shadow_candidate_token_subscriptions(since_timestamp=since_timestamp)
    token_ids = sorted({str(row["token_id"]) for row in subscriptions if row.get("token_id")})
    started_at = utc_now_iso()
    if not token_ids:
        return CollectionStats(0, 0, 0, started_at, utc_now_iso())
    transport = transport or PolymarketMarketWebSocketTransport()
    token_to_candidates = {str(row["token_id"]): list(row.get("candidate_ids") or []) for row in subscriptions}
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    messages = events = 0
    async for message in transport.stream(token_ids):
        received_at = utc_now_iso()
        token_id = _message_token_id(message)
        candidate_ids = token_to_candidates.get(token_id or "", [])
        live_candidate_id = candidate_ids[0] if len(candidate_ids) == 1 else None
        enriched = _enrich_message(message, subscribed_token_ids=token_ids, candidate_ids=candidate_ids)
        inserted = record_clob_message(
            store,
            channel="market",
            message=enriched,
            received_at=received_at,
            live_candidate_id=live_candidate_id,
        )
        messages += 1
        events += len(inserted)
        if max_messages is not None and messages >= max_messages:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
    return CollectionStats(len(token_ids), messages, events, started_at, utc_now_iso())


def collect_candidate_clob_events_sync(
    store: ExecutionStore,
    *,
    transport: MarketEventTransport | None = None,
    since_timestamp: str | None = None,
    max_messages: int | None = None,
    max_seconds: float | None = None,
) -> CollectionStats:
    return asyncio.run(
        collect_candidate_clob_events(
            store,
            transport=transport,
            since_timestamp=since_timestamp,
            max_messages=max_messages,
            max_seconds=max_seconds,
        )
    )


def _message_token_id(message: dict[str, Any] | list[Any]) -> str | None:
    if isinstance(message, list):
        for item in message:
            token_id = _message_token_id(item) if isinstance(item, dict) else None
            if token_id:
                return token_id
        return None
    if not isinstance(message, dict):
        return None
    if isinstance(message.get("price_changes"), list):
        for item in message["price_changes"]:
            if isinstance(item, dict):
                token_id = item.get("asset_id") or item.get("token_id")
                if token_id:
                    return str(token_id)
    token_id = message.get("asset_id") or message.get("token_id")
    return str(token_id) if token_id else None


def _enrich_message(
    message: dict[str, Any] | list[Any],
    *,
    subscribed_token_ids: list[str],
    candidate_ids: list[str],
) -> dict[str, Any] | list[Any]:
    if isinstance(message, list):
        return [_enrich_message(item, subscribed_token_ids=subscribed_token_ids, candidate_ids=candidate_ids) if isinstance(item, dict) else item for item in message]
    if not isinstance(message, dict):
        return message
    return {
        **message,
        "_shadow_collection": {
            "subscribed_token_ids": subscribed_token_ids,
            "candidate_ids": candidate_ids,
            "collector_version": "candidate_clob_collector_v1",
        },
    }
