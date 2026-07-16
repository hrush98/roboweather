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
    CollectorMetric,
    CollectorSession,
    CoverageInterval,
    CoverageState,
    MarketTapeEvent,
    SubscriptionGeneration,
)
from weather_trader.tape.discovery import TapeDiscoveryService
from weather_trader.tape.storage import RotatingRawSegmentWriter
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
    segment_paths: tuple[Path, ...]
    reconnects: int
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
    rotation_seconds: int = 3600,
    telemetry_seconds: float = 30.0,
    max_reconnect_attempts: int = 8,
    reconnect_initial_seconds: float = 1.0,
    reconnect_max_seconds: float = 30.0,
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
    if max_reconnect_attempts < 0:
        raise ValueError("max_reconnect_attempts must be non-negative")
    writer = RotatingRawSegmentWriter(
        raw_directory,
        session_id=session_id,
        rotation_seconds=rotation_seconds,
    )
    messages = events = generations = high_water = 0
    reconnects = 0
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
            remaining_messages = None if max_messages is None else max_messages - messages
            try:
                collected = await _collect_generation(
                    transport,
                    generation,
                    writer,
                    catalog,
                    token_to_market,
                    coverage,
                    refresh_seconds=refresh_seconds,
                    telemetry_seconds=telemetry_seconds,
                    queue_size=queue_size,
                    deadline=deadline,
                    max_messages=remaining_messages,
                    initial_messages=messages,
                    initial_receipt_sequence=receipt_sequence,
                    reconnect_attempt=reconnects,
                )
            except Exception:
                _transition_tokens(
                    catalog,
                    session_id,
                    subscribed,
                    coverage,
                    generation.generation,
                    CoverageState.GAPPED,
                    reason="transport_error",
                    gap_id=uuid.uuid4().hex,
                )
                if reconnects >= max_reconnect_attempts:
                    raise
                reconnects += 1
                await _reconnect_delay(
                    reconnects,
                    initial_seconds=reconnect_initial_seconds,
                    maximum_seconds=reconnect_max_seconds,
                    deadline=deadline,
                )
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                _transition_tokens(
                    catalog,
                    session_id,
                    subscribed,
                    coverage,
                    generation.generation,
                    CoverageState.RECONNECTING,
                    reason=f"transport_retry_{reconnects}",
                )
                continue
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
            gap_reason = "scheduled_subscription_refresh" if collected.termination == "refresh" else "stream_end"
            _transition_tokens(
                catalog,
                session_id,
                subscribed,
                coverage,
                generation.generation,
                CoverageState.GAPPED,
                reason=gap_reason,
                gap_id=uuid.uuid4().hex,
            )
            if collected.termination == "stream_end":
                if reconnects >= max_reconnect_attempts:
                    raise RuntimeError("market tape stream ended and reconnect budget was exhausted")
                reconnects += 1
                await _reconnect_delay(
                    reconnects,
                    initial_seconds=reconnect_initial_seconds,
                    maximum_seconds=reconnect_max_seconds,
                    deadline=deadline,
                )
            _transition_tokens(
                catalog,
                session_id,
                subscribed,
                coverage,
                generation.generation,
                CoverageState.RECONNECTING,
                reason=f"{gap_reason}_reconnect",
            )
        if events == 0:
            raise RuntimeError("market tape session captured zero token events")
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
        segment_stats = writer.close()
        for item in segment_stats:
            catalog.record_partition(session_id, item, closed_at_utc=finished_at)
        segment_paths = tuple(item.path for item in segment_stats)
        segment_path = segment_paths[-1] if segment_paths else None
    return TapeCollectionStats(
        session_id=session_id,
        messages=messages,
        events=events,
        subscription_generations=generations,
        subscribed_tokens=len(subscribed),
        queue_high_water=high_water,
        segment_path=segment_path,
        segment_paths=segment_paths,
        reconnects=reconnects,
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
    writer: RotatingRawSegmentWriter,
    catalog: TapeCatalog,
    token_to_market: dict[str, str],
    coverage: dict[str, CoverageState],
    *,
    refresh_seconds: float,
    telemetry_seconds: float,
    queue_size: int,
    deadline: float | None,
    max_messages: int | None,
    initial_messages: int,
    initial_receipt_sequence: int,
    reconnect_attempt: int,
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
    last_receipt_lag_ms: float | None = None
    next_telemetry = time.monotonic() + telemetry_seconds
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
                message, received_at, monotonic_ns = await asyncio.wait_for(
                    queue.get(), timeout=min(remaining, 1.0)
                )
            except asyncio.TimeoutError:
                if producer.done():
                    await producer
                    termination = "stream_end"
                    break
                if time.monotonic() >= end_at:
                    termination = "deadline" if deadline is not None and time.monotonic() >= deadline else "refresh"
                    break
                continue
            messages += 1
            high_water = max(high_water, 1, queue.qsize())
            for unit in _event_units(message):
                token_id = _token_id(unit)
                if not token_id or token_id not in token_to_market:
                    continue
                event_type = _event_type(unit)
                state = coverage.get(token_id, CoverageState.RESYNCING)
                events += 1
                _, rotated = writer.append(
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
                if rotated is not None:
                    catalog.record_partition(generation.session_id, rotated, closed_at_utc=received_at)
                last_receipt_lag_ms = _receipt_lag_ms(_feed_timestamp(unit), received_at)
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
            if telemetry_seconds <= 0 or time.monotonic() >= next_telemetry:
                _record_metric(
                    catalog,
                    writer,
                    session_id=generation.session_id,
                    messages=initial_messages + messages,
                    events=initial_receipt_sequence + events,
                    queue_depth=queue.qsize(),
                    queue_capacity=queue_size,
                    queue_high_water=high_water,
                    receipt_lag_ms=last_receipt_lag_ms,
                    reconnect_attempt=reconnect_attempt,
                )
                next_telemetry = time.monotonic() + telemetry_seconds
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        _record_metric(
            catalog,
            writer,
            session_id=generation.session_id,
            messages=initial_messages + messages,
            events=initial_receipt_sequence + events,
            queue_depth=queue.qsize(),
            queue_capacity=queue_size,
            queue_high_water=high_water,
            receipt_lag_ms=last_receipt_lag_ms,
            reconnect_attempt=reconnect_attempt,
        )
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


def _transition_tokens(
    catalog: TapeCatalog,
    session_id: str,
    token_ids: tuple[str, ...],
    coverage: dict[str, CoverageState],
    generation: int,
    state: CoverageState,
    *,
    reason: str,
    gap_id: str | None = None,
) -> None:
    transitioned_at = _utc_now()
    for token_id in token_ids:
        coverage[token_id] = state
        catalog.transition_coverage(
            CoverageInterval(
                session_id=session_id,
                token_id=token_id,
                state=state,
                started_at_utc=transitioned_at,
                ended_at_utc=None,
                subscription_generation=generation,
                reason=reason,
                gap_id=gap_id,
            )
        )


async def _reconnect_delay(
    attempt: int,
    *,
    initial_seconds: float,
    maximum_seconds: float,
    deadline: float | None,
) -> None:
    delay = min(maximum_seconds, initial_seconds * (2 ** max(0, attempt - 1)))
    if deadline is not None:
        delay = min(delay, max(0.0, deadline - time.monotonic()))
    if delay > 0:
        await asyncio.sleep(delay)


def _record_metric(
    catalog: TapeCatalog,
    writer: RotatingRawSegmentWriter,
    *,
    session_id: str,
    messages: int,
    events: int,
    queue_depth: int,
    queue_capacity: int,
    queue_high_water: int,
    receipt_lag_ms: float | None,
    reconnect_attempt: int,
) -> None:
    catalog.record_metric(
        CollectorMetric(
            session_id=session_id,
            captured_at_utc=_utc_now(),
            messages=messages,
            events=events,
            queue_depth=queue_depth,
            queue_capacity=queue_capacity,
            queue_high_water=queue_high_water,
            rss_bytes=_rss_bytes(),
            raw_disk_bytes=writer.bytes_written,
            receipt_lag_ms=receipt_lag_ms,
            reconnect_attempt=reconnect_attempt,
        )
    )


def _receipt_lag_ms(feed_timestamp: str | None, received_at_utc: str) -> float | None:
    if feed_timestamp is None:
        return None
    try:
        if feed_timestamp.replace(".", "", 1).isdigit():
            value = float(feed_timestamp)
            if value > 10_000_000_000:
                value /= 1000.0
            feed = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            feed = datetime.fromisoformat(feed_timestamp.replace("Z", "+00:00"))
            if feed.tzinfo is None:
                return None
        received = datetime.fromisoformat(received_at_utc.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    return max(0.0, (received - feed).total_seconds() * 1000.0)


def _rss_bytes() -> int:
    try:
        fields = Path("/proc/self/statm").read_text().split()
        return int(fields[1]) * 4096
    except (IndexError, OSError, ValueError):
        return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
