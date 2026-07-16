from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import (
    CollectorSession,
    CoverageInterval,
    CoverageState,
    MarketTapeEvent,
    SubscriptionGeneration,
)
from weather_trader.tape.discovery import TapeDiscoveryService
from weather_trader.tape.storage import RawSegmentWriter
from weather_trader.tape.subscriptions import SubscriptionRegistry


POLYMARKET_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
COLLECTOR_VERSION = "market_tape_collector_v1"


class MarketTapeTransport(Protocol):
    def stream(self, token_ids: tuple[str, ...]) -> AsyncIterator[dict[str, Any] | list[Any]]:
        ...


class PolymarketTapeTransport:
    def __init__(
        self,
        url: str = POLYMARKET_MARKET_WS_URL,
        *,
        max_message_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.url = url
        self.max_message_bytes = max_message_bytes

    async def stream(self, token_ids: tuple[str, ...]) -> AsyncIterator[dict[str, Any] | list[Any]]:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install the live extras to run the market-tape collector") from exc
        async with websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            max_size=self.max_message_bytes,
        ) as websocket:
            await websocket.send(json.dumps({"type": "market", "assets_ids": list(token_ids)}))
            async for raw in websocket:
                if raw == "PONG":
                    continue
                try:
                    yield json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue


@dataclass(frozen=True)
class TapeCollectionStats:
    session_id: str
    messages: int
    events: int
    subscription_generations: int
    subscribed_tokens: int
    queue_high_water: int
    segment_path: Path | None
    started_at_utc: str
    finished_at_utc: str


async def collect_market_tape(
    catalog: TapeCatalog,
    *,
    raw_directory: Path,
    discovery: TapeDiscoveryService | None = None,
    transport: MarketTapeTransport | None = None,
    refresh_seconds: float = 300.0,
    queue_size: int = 10000,
    market_limit: int = 50000,
    max_messages: int | None = None,
    max_seconds: float | None = None,
) -> TapeCollectionStats:
    if queue_size < 1:
        raise ValueError("queue_size must be positive")
    started_at = _utc_now()
    session_id = f"tape-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    session = CollectorSession(
        session_id=session_id,
        started_at_utc=started_at,
        started_monotonic_ns=time.monotonic_ns(),
        collector_version=COLLECTOR_VERSION,
        hostname=socket.gethostname(),
    )
    catalog.start_session(session)
    registry = SubscriptionRegistry(catalog, discovery or TapeDiscoveryService())
    transport = transport or PolymarketTapeTransport()
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    partition_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H")
    writer: RawSegmentWriter | None = None
    messages = events = generations = high_water = 0
    subscribed: tuple[str, ...] = ()
    latest_generation_number = 0
    receipt_sequence = 0
    coverage: dict[str, CoverageState] = {}
    token_to_market: dict[str, str] = {}
    finish_reason = "completed"
    try:
        while max_messages is None or messages < max_messages:
            if deadline is not None and time.monotonic() >= deadline:
                finish_reason = "max_seconds"
                break
            refreshed = registry.refresh(
                session_id=session_id,
                effective_at_utc=_utc_now(),
                market_limit=market_limit,
            )
            latest = catalog.latest_subscription(session_id)
            if latest is None:
                finish_reason = "empty_universe"
                break
            subscribed = latest.token_ids
            token_to_market = {entry.token_id: entry.market_id for entry in catalog.active_tokens()}
            if refreshed.subscription is not None:
                generations += 1
            generation = latest
            latest_generation_number = generation.generation
            now = _utc_now()
            for token_id in subscribed:
                coverage[token_id] = CoverageState.RESYNCING
                catalog.transition_coverage(
                    CoverageInterval(
                        session_id=session_id,
                        token_id=token_id,
                        state=CoverageState.RESYNCING,
                        started_at_utc=now,
                        ended_at_utc=None,
                        subscription_generation=generation.generation,
                        reason="subscription_generation_started",
                    )
                )
            if writer is None:
                writer = RawSegmentWriter(
                    raw_directory,
                    session_id=session_id,
                    partition_id=partition_id,
                )
            remaining_messages = None if max_messages is None else max_messages - messages
            collected = await _collect_generation(
                transport,
                generation,
                writer,
                catalog,
                token_to_market,
                coverage,
                refresh_seconds=refresh_seconds,
                queue_size=queue_size,
                deadline=deadline,
                max_messages=remaining_messages,
                initial_receipt_sequence=receipt_sequence,
            )
            messages += collected.messages
            events += collected.events
            high_water = max(high_water, collected.queue_high_water)
            receipt_sequence += collected.events
            if collected.termination == "max_messages":
                finish_reason = "max_messages"
                break
            if collected.termination == "deadline":
                finish_reason = "max_seconds"
                break
            reconnect_at = _utc_now()
            for token_id in subscribed:
                coverage[token_id] = CoverageState.RECONNECTING
                catalog.transition_coverage(
                    CoverageInterval(
                        session_id=session_id,
                        token_id=token_id,
                        state=CoverageState.RECONNECTING,
                        started_at_utc=reconnect_at,
                        ended_at_utc=None,
                        subscription_generation=generation.generation,
                        reason="subscription_refresh_or_stream_end",
                    )
                )
    except Exception:
        finish_reason = "error"
        raise
    finally:
        finished_at = _utc_now()
        for token_id in subscribed:
            catalog.transition_coverage(
                CoverageInterval(
                    session_id=session_id,
                    token_id=token_id,
                    state=CoverageState.CLOSED,
                    started_at_utc=finished_at,
                    ended_at_utc=finished_at,
                    subscription_generation=latest_generation_number,
                    reason=f"collector_session_{finish_reason}",
                )
            )
        catalog.finish_session(session_id, finished_at_utc=finished_at, reason=finish_reason)
        segment_path = writer.close().path if writer is not None else None
    return TapeCollectionStats(
        session_id=session_id,
        messages=messages,
        events=events,
        subscription_generations=generations,
        subscribed_tokens=len(subscribed),
        queue_high_water=high_water,
        segment_path=segment_path,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
    )


@dataclass(frozen=True)
class _GenerationStats:
    messages: int
    events: int
    queue_high_water: int
    termination: str


async def _collect_generation(
    transport: MarketTapeTransport,
    generation: SubscriptionGeneration,
    writer: RawSegmentWriter,
    catalog: TapeCatalog,
    token_to_market: dict[str, str],
    coverage: dict[str, CoverageState],
    *,
    refresh_seconds: float,
    queue_size: int,
    deadline: float | None,
    max_messages: int | None,
    initial_receipt_sequence: int,
) -> _GenerationStats:
    queue: asyncio.Queue[tuple[dict[str, Any] | list[Any], str, int]] = asyncio.Queue(maxsize=queue_size)
    stream = transport.stream(generation.token_ids)
    producer_done = asyncio.Event()

    async def produce() -> None:
        try:
            async for message in stream:
                await queue.put((message, _utc_now(), time.monotonic_ns()))
        finally:
            producer_done.set()

    producer = asyncio.create_task(produce())
    messages = events = high_water = 0
    end_at = time.monotonic() + refresh_seconds
    if deadline is not None:
        end_at = min(end_at, deadline)
    termination = "refresh"
    try:
        while True:
            if max_messages is not None and messages >= max_messages:
                termination = "max_messages"
                break
            remaining = end_at - time.monotonic()
            if remaining <= 0:
                termination = "deadline" if deadline is not None and time.monotonic() >= deadline else "refresh"
                break
            if producer_done.is_set() and queue.empty():
                await producer
                termination = "stream_end"
                break
            try:
                message, received_at, monotonic_ns = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                if producer.done():
                    await producer
                termination = "deadline" if deadline is not None and time.monotonic() >= deadline else "refresh"
                break
            messages += 1
            high_water = max(high_water, 1, queue.qsize())
            for unit in _event_units(message):
                token_id = _token_id(unit)
                if not token_id or token_id not in token_to_market:
                    continue
                event_type = _event_type(unit)
                state = coverage.get(token_id, CoverageState.RESYNCING)
                events += 1
                writer.append(
                    MarketTapeEvent(
                        collector_session_id=generation.session_id,
                        token_id=token_id,
                        market_id=token_to_market[token_id],
                        event_type=event_type,
                        raw_payload=unit,
                        feed_timestamp=_feed_timestamp(unit),
                        received_at_utc=received_at,
                        received_monotonic_ns=monotonic_ns,
                        receipt_sequence=initial_receipt_sequence + events,
                        subscription_generation=generation.generation,
                        coverage_state=state,
                    )
                )
                if event_type == "book" and state is not CoverageState.VALID:
                    coverage[token_id] = CoverageState.VALID
                    catalog.transition_coverage(
                        CoverageInterval(
                            session_id=generation.session_id,
                            token_id=token_id,
                            state=CoverageState.VALID,
                            started_at_utc=received_at,
                            ended_at_utc=None,
                            subscription_generation=generation.generation,
                            reason="initial_full_book_received",
                        )
                    )
            queue.task_done()
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
    return _GenerationStats(messages, events, high_water, termination)


def _event_units(message: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(message, list):
        return [unit for item in message if isinstance(item, dict) for unit in _event_units(item)]
    if not isinstance(message, dict):
        return []
    changes = message.get("price_changes")
    if isinstance(changes, list):
        return [
            {"parent": message, "price_change": change}
            for change in changes
            if isinstance(change, dict)
        ]
    return [message]


def _token_id(message: dict[str, Any]) -> str | None:
    change = message.get("price_change")
    source = change if isinstance(change, dict) else message
    value = source.get("asset_id") or source.get("token_id")
    return str(value) if value else None


def _event_type(message: dict[str, Any]) -> str:
    parent = message.get("parent")
    source = parent if isinstance(parent, dict) else message
    return str(source.get("event_type") or source.get("type") or "unknown")


def _feed_timestamp(message: dict[str, Any]) -> str | None:
    parent = message.get("parent")
    source = parent if isinstance(parent, dict) else message
    return _text(source.get("timestamp"))


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
