from __future__ import annotations

import asyncio
from concurrent.futures import Future
import hashlib
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.books import BookReconstructor
from weather_trader.tape.contracts import (
    CollectorMetric,
    CollectorSession,
    CoverageInterval,
    CoverageState,
    MarketTapeEvent,
    SubscriptionGeneration,
)
from weather_trader.tape.discovery import TapeDiscoveryResult, TapeDiscoveryService
from weather_trader.tape.storage import RotatingRawSegmentWriter
from weather_trader.tape.subscriptions import SubscriptionRegistry


POLYMARKET_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
COLLECTOR_VERSION = "market_tape_collector_v1"


class MarketTapeTransport(Protocol):
    def stream(
        self,
        token_ids: tuple[str, ...],
        *,
        subscription_updates: asyncio.Queue[SubscriptionUpdate] | None = None,
    ) -> AsyncIterator[dict[str, Any] | list[Any]]:
        ...


@dataclass(frozen=True)
class SubscriptionUpdate:
    added_token_ids: tuple[str, ...]
    removed_token_ids: tuple[str, ...]


class PolymarketTapeTransport:
    def __init__(
        self,
        url: str = POLYMARKET_MARKET_WS_URL,
        *,
        max_message_bytes: int = 8 * 1024 * 1024,
        subscription_batch_size: int = 500,
    ) -> None:
        if subscription_batch_size < 1:
            raise ValueError("subscription_batch_size must be positive")
        self.url = url
        self.max_message_bytes = max_message_bytes
        self.subscription_batch_size = subscription_batch_size

    async def _send_initial_subscription(
        self,
        websocket: Any,
        token_ids: tuple[str, ...],
    ) -> None:
        batches = tuple(_batched(token_ids, self.subscription_batch_size))
        first, *remaining = batches or ((),)
        await websocket.send(
            json.dumps(
                {
                    "type": "market",
                    "assets_ids": list(first),
                }
            )
        )
        for batch in remaining:
            await self._send_subscription_operation(
                websocket,
                batch,
                operation="subscribe",
            )

    async def _send_subscription_operation(
        self,
        websocket: Any,
        token_ids: tuple[str, ...],
        *,
        operation: str,
    ) -> None:
        for batch in _batched(token_ids, self.subscription_batch_size):
            await websocket.send(
                json.dumps(
                    {
                        "assets_ids": list(batch),
                        "operation": operation,
                    }
                )
            )

    async def stream(
        self,
        token_ids: tuple[str, ...],
        *,
        subscription_updates: asyncio.Queue[SubscriptionUpdate] | None = None,
    ) -> AsyncIterator[dict[str, Any] | list[Any]]:
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
            await self._send_initial_subscription(websocket, token_ids)
            receive_task: asyncio.Task[str | bytes] | None = None
            update_task: asyncio.Task[SubscriptionUpdate] | None = None
            try:
                while True:
                    receive_task = receive_task or asyncio.create_task(websocket.recv())
                    if subscription_updates is not None:
                        update_task = update_task or asyncio.create_task(subscription_updates.get())
                    waiting = {receive_task}
                    if update_task is not None:
                        waiting.add(update_task)
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    if update_task is not None and update_task in done:
                        update = update_task.result()
                        update_task = None
                        if update.added_token_ids:
                            await self._send_subscription_operation(
                                websocket,
                                update.added_token_ids,
                                operation="subscribe",
                            )
                        if update.removed_token_ids:
                            await self._send_subscription_operation(
                                websocket,
                                update.removed_token_ids,
                                operation="unsubscribe",
                            )
                        subscription_updates.task_done()
                    if receive_task not in done:
                        continue
                    raw = receive_task.result()
                    receive_task = None
                    if raw == "PONG":
                        continue
                    try:
                        yield json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
            finally:
                pending = [
                    task
                    for task in (receive_task, update_task)
                    if task is not None and not task.done()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)


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
    max_receipt_lag_seconds: float | None = 10.0,
    subscription_batch_size: int = 500,
    checkpoint_every: int = 1000,
    validation_run_id: str | None = None,
    build_fingerprint: str | None = None,
) -> TapeCollectionStats:
    if queue_size < 1:
        raise ValueError("queue_size must be positive")
    started_at = _utc_now()
    session_id = f"tape-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    config_fingerprint = _fingerprint(
        {
            "checkpoint_every": checkpoint_every,
            "collector_version": COLLECTOR_VERSION,
            "market_limit": market_limit,
            "max_receipt_lag_seconds": max_receipt_lag_seconds,
            "max_reconnect_attempts": max_reconnect_attempts,
            "queue_size": queue_size,
            "reconnect_initial_seconds": reconnect_initial_seconds,
            "reconnect_max_seconds": reconnect_max_seconds,
            "refresh_seconds": refresh_seconds,
            "rotation_seconds": rotation_seconds,
            "subscription_batch_size": subscription_batch_size,
            "telemetry_seconds": telemetry_seconds,
        }
    )
    session = CollectorSession(
        session_id=session_id,
        started_at_utc=started_at,
        started_monotonic_ns=time.monotonic_ns(),
        collector_version=COLLECTOR_VERSION,
        hostname=socket.gethostname(),
        validation_run_id=validation_run_id or session_id,
        build_fingerprint=build_fingerprint or _fingerprint(
            {"collector_version": COLLECTOR_VERSION}
        ),
        config_fingerprint=config_fingerprint,
    )
    catalog.start_session(session)
    registry = SubscriptionRegistry(catalog, discovery or TapeDiscoveryService())
    transport = transport or PolymarketTapeTransport(
        subscription_batch_size=subscription_batch_size,
    )
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    if max_reconnect_attempts < 0:
        raise ValueError("max_reconnect_attempts must be non-negative")
    if max_receipt_lag_seconds is not None and max_receipt_lag_seconds <= 0:
        raise ValueError("max_receipt_lag_seconds must be positive when provided")
    if subscription_batch_size < 1:
        raise ValueError("subscription_batch_size must be positive")
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be positive")
    writer = RotatingRawSegmentWriter(
        raw_directory,
        session_id=session_id,
        rotation_seconds=rotation_seconds,
        compress_closed=True,
    )
    messages = events = generations = high_water = 0
    reconnects = 0
    consecutive_reconnect_attempts = 0
    subscribed: tuple[str, ...] = ()
    latest_generation_number = 0
    receipt_sequence = 0
    coverage: dict[str, CoverageState] = {}
    book_reconstructor = BookReconstructor()
    token_event_counts: dict[str, int] = {}
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
                    registry,
                    token_to_market,
                    coverage,
                    refresh_seconds=refresh_seconds,
                    telemetry_seconds=telemetry_seconds,
                    queue_size=queue_size,
                    market_limit=market_limit,
                    deadline=deadline,
                    max_messages=remaining_messages,
                    initial_messages=messages,
                    initial_receipt_sequence=receipt_sequence,
                    reconnect_attempt=consecutive_reconnect_attempts,
                    book_reconstructor=book_reconstructor,
                    token_event_counts=token_event_counts,
                    checkpoint_every=checkpoint_every,
                    max_receipt_lag_seconds=max_receipt_lag_seconds,
                )
            except Exception:
                # Errors raised outside the transport producer (for example,
                # catalog or storage failures) are not safe to recover from.
                raise
            messages += collected.messages
            events += collected.events
            high_water = max(high_water, collected.queue_high_water)
            receipt_sequence += collected.events
            generations += collected.subscription_generations
            subscribed = collected.subscribed_token_ids
            latest_generation_number = collected.latest_generation
            if collected.events:
                # The retry budget protects against a feed which cannot
                # reconnect at all. A connection that delivered data was
                # healthy, so a later network interruption starts a fresh
                # consecutive retry budget.
                consecutive_reconnect_attempts = 0
            if collected.termination == "max_messages":
                finish_reason = "max_messages"
                break
            if collected.termination == "deadline":
                finish_reason = "max_seconds"
                break
            gap_reason = (
                "transport_error"
                if collected.termination == "transport_error"
                else collected.termination
            )
            _transition_tokens(
                catalog,
                session_id,
                subscribed,
                coverage,
                latest_generation_number,
                CoverageState.GAPPED,
                reason=gap_reason,
                gap_id=uuid.uuid4().hex,
            )
            if consecutive_reconnect_attempts >= max_reconnect_attempts:
                if collected.error is not None:
                    raise collected.error
                raise RuntimeError(
                    "market tape stream ended and reconnect budget was exhausted"
                )
            consecutive_reconnect_attempts += 1
            reconnects += 1
            await _reconnect_delay(
                consecutive_reconnect_attempts,
                initial_seconds=reconnect_initial_seconds,
                maximum_seconds=reconnect_max_seconds,
                deadline=deadline,
            )
            if deadline is not None and time.monotonic() >= deadline:
                finish_reason = "max_seconds"
                break
            _transition_tokens(
                catalog,
                session_id,
                subscribed,
                coverage,
                latest_generation_number,
                CoverageState.RECONNECTING,
                reason=f"{gap_reason}_retry_{consecutive_reconnect_attempts}",
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
    subscription_generations: int
    subscribed_token_ids: tuple[str, ...]
    latest_generation: int
    error: Exception | None = None


async def _collect_generation(
    transport: MarketTapeTransport,
    generation: SubscriptionGeneration,
    writer: RotatingRawSegmentWriter,
    catalog: TapeCatalog,
    registry: SubscriptionRegistry,
    token_to_market: dict[str, str],
    coverage: dict[str, CoverageState],
    *,
    refresh_seconds: float,
    telemetry_seconds: float,
    queue_size: int,
    market_limit: int,
    deadline: float | None,
    max_messages: int | None,
    initial_messages: int,
    initial_receipt_sequence: int,
    reconnect_attempt: int,
    book_reconstructor: BookReconstructor,
    token_event_counts: dict[str, int],
    checkpoint_every: int,
    max_receipt_lag_seconds: float | None,
) -> _GenerationStats:
    queue: asyncio.Queue[tuple[dict[str, Any] | list[Any], str, int]] = asyncio.Queue(maxsize=queue_size)
    subscription_updates: asyncio.Queue[SubscriptionUpdate] = asyncio.Queue()
    current_generation = generation
    current_subscribed = generation.token_ids
    stream = transport.stream(
        current_subscribed,
        subscription_updates=subscription_updates,
    )
    producer_done = asyncio.Event()

    async def produce() -> None:
        try:
            async for message in stream:
                await queue.put((message, _utc_now(), time.monotonic_ns()))
        finally:
            producer_done.set()

    producer = asyncio.create_task(produce())
    messages = events = high_water = 0
    next_refresh = time.monotonic() + refresh_seconds
    refresh_task: Future[TapeDiscoveryResult] | None = None
    refresh_attempted_at: str | None = None
    subscription_generations = 0
    termination = "stream_end"
    transport_error: Exception | None = None
    last_receipt_lag_ms: float | None = None
    next_telemetry = time.monotonic() + telemetry_seconds

    def record_telemetry() -> None:
        nonlocal next_telemetry
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

    async def apply_refresh_if_ready() -> None:
        nonlocal refresh_task, next_refresh, current_generation
        nonlocal current_subscribed, subscription_generations
        nonlocal refresh_attempted_at
        if refresh_task is None:
            if time.monotonic() >= next_refresh:
                if deadline is not None and time.monotonic() + 0.01 >= deadline:
                    return
                refresh_attempted_at = _utc_now()
                refresh_task = _start_discovery(
                    registry.discovery,
                    market_limit=market_limit,
                )
            return
        if not refresh_task.done():
            return
        try:
            discovery_result = refresh_task.result()
        except Exception as exc:
            # A discovery outage must not tear down otherwise valid tape
            # coverage. The next bounded refresh retries it.
            completed_at = _utc_now()
            catalog.record_discovery_refresh(
                session_id=generation.session_id,
                attempted_at_utc=refresh_attempted_at or completed_at,
                completed_at_utc=completed_at,
                complete=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            refresh_task = None
            refresh_attempted_at = None
            next_refresh = time.monotonic() + refresh_seconds
            return
        completed_at = _utc_now()
        refreshed = registry.apply_discovery(
            discovery_result,
            session_id=generation.session_id,
            effective_at_utc=completed_at,
            attempted_at_utc=refresh_attempted_at,
        )
        refresh_task = None
        refresh_attempted_at = None
        next_refresh = time.monotonic() + refresh_seconds
        if refreshed.subscription is None:
            return
        previous = set(current_subscribed)
        current_generation = refreshed.subscription
        current_subscribed = current_generation.token_ids
        current = set(current_subscribed)
        added = tuple(sorted(current - previous))
        removed = tuple(sorted(previous - current))
        token_to_market.clear()
        token_to_market.update(
            {entry.token_id: entry.market_id for entry in catalog.active_tokens()}
        )
        changed_at = _utc_now()
        if removed:
            _transition_tokens(
                catalog,
                generation.session_id,
                removed,
                coverage,
                current_generation.generation,
                CoverageState.CLOSED,
                reason="subscription_removed",
            )
        for token_id in added:
            coverage[token_id] = CoverageState.RESYNCING
            catalog.transition_coverage(
                CoverageInterval(
                    session_id=generation.session_id,
                    token_id=token_id,
                    state=CoverageState.RESYNCING,
                    started_at_utc=changed_at,
                    ended_at_utc=None,
                    subscription_generation=current_generation.generation,
                    reason="dynamic_subscription_added",
                )
            )
        await subscription_updates.put(
            SubscriptionUpdate(
                added_token_ids=added,
                removed_token_ids=removed,
            )
        )
        subscription_generations += 1

    try:
        while True:
            if max_messages is not None and messages >= max_messages:
                termination = "max_messages"
                break
            if deadline is not None and time.monotonic() >= deadline:
                termination = "deadline"
                break
            await apply_refresh_if_ready()
            remaining = (
                deadline - time.monotonic()
                if deadline is not None
                else float("inf")
            )
            if remaining <= 0:
                termination = "deadline"
                break
            if producer_done.is_set() and queue.empty():
                try:
                    await producer
                except Exception as exc:
                    transport_error = exc
                    termination = "transport_error"
                else:
                    termination = "stream_end"
                break
            wait_timeout = min(remaining, 1.0)
            if telemetry_seconds > 0:
                wait_timeout = min(
                    wait_timeout,
                    max(0.001, next_telemetry - time.monotonic()),
                )
            if refresh_task is None:
                wait_timeout = min(
                    wait_timeout,
                    max(0.001, next_refresh - time.monotonic()),
                )
            else:
                wait_timeout = min(wait_timeout, 0.1)
            try:
                message, received_at, monotonic_ns = await asyncio.wait_for(
                    queue.get(), timeout=wait_timeout
                )
            except asyncio.TimeoutError:
                if producer.done():
                    try:
                        await producer
                    except Exception as exc:
                        transport_error = exc
                        termination = "transport_error"
                    else:
                        termination = "stream_end"
                    break
                if telemetry_seconds <= 0 or time.monotonic() >= next_telemetry:
                    record_telemetry()
                if deadline is not None and time.monotonic() >= deadline:
                    termination = "deadline"
                    break
                continue
            messages += 1
            high_water = max(high_water, 1, queue.qsize())
            receipt_lag_exceeded = False
            for unit in _event_units(message):
                token_id = _token_id(unit)
                if not token_id or token_id not in token_to_market:
                    continue
                event_type = _event_type(unit)
                event_receipt_lag_ms = None
                if _uses_live_event_timestamp(event_type):
                    event_receipt_lag_ms = _receipt_lag_ms(
                        _feed_timestamp(unit),
                        received_at,
                    )
                    last_receipt_lag_ms = event_receipt_lag_ms
                if (
                    max_receipt_lag_seconds is not None
                    and event_receipt_lag_ms is not None
                    and event_receipt_lag_ms > max_receipt_lag_seconds * 1000.0
                ):
                    # A stale frame means the socket may be draining delayed
                    # data after an outage. Do not persist it under VALID
                    # coverage; reconnect and require a fresh full book.
                    termination = "receipt_lag"
                    transport_error = RuntimeError(
                        "market tape receipt lag exceeded "
                        f"{max_receipt_lag_seconds:g}s"
                    )
                    receipt_lag_exceeded = True
                    break
                state = coverage.get(token_id, CoverageState.RESYNCING)
                events += 1
                stored, rotated = writer.append(
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
                        subscription_generation=current_generation.generation,
                        coverage_state=state,
                    )
                )
                if rotated is not None:
                    catalog.record_partition(generation.session_id, rotated, closed_at_utc=received_at)
                token_event_counts[token_id] = token_event_counts.get(token_id, 0) + 1
                book = book_reconstructor.apply(stored)
                if not book.valid:
                    # Incremental events can race ahead of their initial full
                    # book after a subscription. They remain raw-marked
                    # RESYNCING and unusable for replay, but are not malformed
                    # reconstruction evidence. A bad full book, or any
                    # failure after VALID coverage, is still an error.
                    if event_type == "book" or state is CoverageState.VALID:
                        catalog.record_reconstruction_error(
                            session_id=generation.session_id,
                            token_id=token_id,
                            event_id=stored.stable_event_id,
                            receipt_sequence=stored.receipt_sequence,
                            captured_at_utc=received_at,
                            reason=book.invalid_reason or "unknown_reconstruction_error",
                        )
                    if state is CoverageState.VALID:
                        coverage[token_id] = CoverageState.GAPPED
                        catalog.transition_coverage(
                            CoverageInterval(
                                session_id=generation.session_id,
                                token_id=token_id,
                                state=CoverageState.GAPPED,
                                started_at_utc=received_at,
                                ended_at_utc=None,
                                subscription_generation=current_generation.generation,
                                reason=book.invalid_reason,
                                gap_id=uuid.uuid4().hex,
                            )
                        )
                elif event_type == "book" or token_event_counts[token_id] % checkpoint_every == 0:
                    catalog.record_checkpoint(book_reconstructor.checkpoint(stored))
                if event_type == "book" and book.valid and state is not CoverageState.VALID:
                    coverage[token_id] = CoverageState.VALID
                    catalog.transition_coverage(
                        CoverageInterval(
                            session_id=generation.session_id,
                            token_id=token_id,
                            state=CoverageState.VALID,
                            started_at_utc=received_at,
                            ended_at_utc=None,
                            subscription_generation=current_generation.generation,
                            reason="initial_full_book_received",
                        )
                    )
            queue.task_done()
            if receipt_lag_exceeded:
                record_telemetry()
                break
            if telemetry_seconds <= 0 or time.monotonic() >= next_telemetry:
                record_telemetry()
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        record_telemetry()
    return _GenerationStats(
        messages,
        events,
        high_water,
        termination,
        subscription_generations,
        current_subscribed,
        current_generation.generation,
        transport_error,
    )


def _start_discovery(
    discovery: TapeDiscoveryService,
    *,
    market_limit: int,
) -> Future[TapeDiscoveryResult]:
    """Run blocking Gamma discovery without pausing WebSocket consumption."""
    future: Future[TapeDiscoveryResult] = Future()

    def run() -> None:
        try:
            future.set_result(discovery.discover(market_limit=market_limit))
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(
        target=run,
        name="market-tape-discovery",
        daemon=True,
    ).start()
    return future


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


def _batched(
    values: tuple[str, ...],
    batch_size: int,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        values[start : start + batch_size]
        for start in range(0, len(values), batch_size)
    )


def _token_id(message: dict[str, Any]) -> str | None:
    change = message.get("price_change")
    source = change if isinstance(change, dict) else message
    value = source.get("asset_id") or source.get("token_id")
    return str(value) if value else None


def _event_type(message: dict[str, Any]) -> str:
    parent = message.get("parent")
    source = parent if isinstance(parent, dict) else message
    return str(source.get("event_type") or source.get("type") or "unknown")


def _uses_live_event_timestamp(event_type: str) -> bool:
    """Whether an event timestamp measures live feed delivery latency.

    Full-book timestamps describe the age of the represented book state, so
    they cannot be compared with local receipt time as transport latency.
    """
    return event_type.lower() in {
        "best_bid_ask",
        "last_trade_price",
        "price_change",
        "tick_size_change",
    }


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


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
