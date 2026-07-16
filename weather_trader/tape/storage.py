from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterator

from weather_trader.tape.contracts import MarketTapeEvent, contract_to_dict, market_tape_event_from_dict


class SegmentCorruptionError(ValueError):
    """Raised when an append-only segment cannot be replayed safely."""


@dataclass(frozen=True)
class SegmentStats:
    path: Path
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
            events=self._events,
            bytes_written=self.path.stat().st_size,
            first_event_id=self._first_event_id,
            last_event_id=self._last_event_id,
        )

    def __enter__(self) -> RawSegmentWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def iter_segment(path: Path) -> Iterator[MarketTapeEvent]:
    with path.open("rb") as handle:
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


def _encode_event(event: MarketTapeEvent) -> bytes:
    event_data = contract_to_dict(event)
    canonical = _canonical_json(event_data)
    record = {"event": event_data, "sha256": hashlib.sha256(canonical).hexdigest()}
    return _canonical_json(record) + b"\n"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
