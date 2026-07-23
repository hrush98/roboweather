from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from weather_trader.tape.contracts import MarketTapeEvent, contract_to_dict, market_tape_event_from_dict


class SegmentCorruptionError(ValueError):
    """Raised when an append-only segment cannot be replayed safely."""


@dataclass(frozen=True)
class SegmentStats:
    path: Path
    partition_id: str
    events: int
    bytes_written: int
    first_event_id: str | None
    last_event_id: str | None


class RawSegmentWriter:
    """Append newline-delimited event envelopes to one active partition.

    Offsets are byte offsets, making event identifiers stable across parser
    rebuilds. An active segment is flushed for each append; callers may request
    fsync where crash durability is more important than throughput.
    """

    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        partition_id: str,
        fsync_on_append: bool = False,
    ) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.partition_id = partition_id
        self.path = directory / f"{session_id}__{partition_id}.jsonl"
        self._handle: BinaryIO = self.path.open("ab")
        self._fsync_on_append = fsync_on_append
        self._events = 0
        self._first_event_id: str | None = None
        self._last_event_id: str | None = None

    def append(self, event: MarketTapeEvent) -> MarketTapeEvent:
        if event.collector_session_id != self.session_id:
            raise ValueError("event collector session does not match segment session")
        if event.partition_id is not None or event.append_offset is not None or event.stable_event_id is not None:
            raise ValueError("event already has storage identity")
        offset = self._handle.tell()
        event_id = f"{self.session_id}:{self.partition_id}:{offset}"
        stored = replace(
            event,
            partition_id=self.partition_id,
            append_offset=offset,
            stable_event_id=event_id,
        )
        encoded = _encode_event(stored)
        self._handle.write(encoded)
        self._handle.flush()
        if self._fsync_on_append:
            os.fsync(self._handle.fileno())
        self._events += 1
        self._first_event_id = self._first_event_id or event_id
        self._last_event_id = event_id
        return stored

    def close(self) -> SegmentStats:
        if not self._handle.closed:
            self._handle.close()
        return SegmentStats(
            path=self.path,
            partition_id=self.partition_id,
            events=self._events,
            bytes_written=self.path.stat().st_size,
            first_event_id=self._first_event_id,
            last_event_id=self._last_event_id,
        )

    def __enter__(self) -> RawSegmentWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class RotatingRawSegmentWriter:
    """Rotate raw segments on UTC boundaries derived from event receipt time."""

    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        rotation_seconds: int = 3600,
        fsync_on_append: bool = False,
        compress_closed: bool = False,
    ) -> None:
        if rotation_seconds < 1:
            raise ValueError("rotation_seconds must be positive")
        self.directory = directory
        self.session_id = session_id
        self.rotation_seconds = rotation_seconds
        self.fsync_on_append = fsync_on_append
        self.compress_closed = compress_closed
        self._writer: RawSegmentWriter | None = None
        self._closed: list[SegmentStats] = []

    @property
    def current_path(self) -> Path | None:
        return self._writer.path if self._writer is not None else None

    @property
    def bytes_written(self) -> int:
        closed = sum(item.bytes_written for item in self._closed)
        current = self._writer.path.stat().st_size if self._writer is not None else 0
        return closed + current

    def append(self, event: MarketTapeEvent) -> tuple[MarketTapeEvent, SegmentStats | None]:
        partition_id = _partition_id(event.received_at_utc, self.rotation_seconds)
        rotated: SegmentStats | None = None
        if self._writer is None or self._writer.partition_id != partition_id:
            if self._writer is not None:
                rotated = _finalize_segment(
                    self._writer.close(),
                    compress=self.compress_closed,
                )
                self._closed.append(rotated)
            self._writer = RawSegmentWriter(
                self.directory,
                session_id=self.session_id,
                partition_id=partition_id,
                fsync_on_append=self.fsync_on_append,
            )
        return self._writer.append(event), rotated

    def close(self) -> tuple[SegmentStats, ...]:
        if self._writer is not None:
            self._closed.append(
                _finalize_segment(
                    self._writer.close(),
                    compress=self.compress_closed,
                )
            )
            self._writer = None
        return tuple(self._closed)


def iter_segment(path: Path) -> Iterator[MarketTapeEvent]:
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                return
            if not line.endswith(b"\n"):
                raise SegmentCorruptionError(f"truncated record at byte offset {offset}")
            try:
                record = json.loads(line)
                event_data = record["event"]
                checksum = record["sha256"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise SegmentCorruptionError(f"invalid record at byte offset {offset}") from exc
            canonical = _canonical_json(event_data)
            if hashlib.sha256(canonical).hexdigest() != checksum:
                raise SegmentCorruptionError(f"checksum mismatch at byte offset {offset}")
            event = market_tape_event_from_dict(event_data)
            if event.append_offset != offset:
                raise SegmentCorruptionError(f"stored offset mismatch at byte offset {offset}")
            yield event


def _finalize_segment(stats: SegmentStats, *, compress: bool) -> SegmentStats:
    if not compress or stats.events == 0:
        return stats
    compressed_path = stats.path.with_suffix(f"{stats.path.suffix}.gz")
    temporary_path = compressed_path.with_suffix(f"{compressed_path.suffix}.tmp")
    try:
        with stats.path.open("rb") as source:
            with gzip.open(temporary_path, "wb", compresslevel=6) as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        os.replace(temporary_path, compressed_path)
        stats.path.unlink()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return replace(
        stats,
        path=compressed_path,
        bytes_written=compressed_path.stat().st_size,
    )


def _encode_event(event: MarketTapeEvent) -> bytes:
    event_data = contract_to_dict(event)
    canonical = _canonical_json(event_data)
    record = {"event": event_data, "sha256": hashlib.sha256(canonical).hexdigest()}
    return _canonical_json(record) + b"\n"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _partition_id(received_at_utc: str, rotation_seconds: int) -> str:
    parsed = datetime.fromisoformat(received_at_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("received_at_utc must be timezone-aware")
    epoch_bucket = int(parsed.timestamp()) // rotation_seconds * rotation_seconds
    bucket = datetime.fromtimestamp(epoch_bucket, tz=timezone.utc)
    return bucket.strftime("%Y%m%dT%H%M%SZ")
