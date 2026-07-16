from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.benchmark_market_tape import benchmark
from weather_trader.tape.contracts import CoverageState, MarketTapeEvent
from weather_trader.tape.storage import RawSegmentWriter, SegmentCorruptionError, iter_segment


def make_event(sequence: int, payload: dict[str, object] | None = None) -> MarketTapeEvent:
    return MarketTapeEvent(
        collector_session_id="session-1",
        token_id="token-1",
        market_id="market-1",
        event_type="book" if sequence == 1 else "price_change",
        raw_payload=payload or {"event_type": "book", "bids": [{"price": "0.4", "size": "10"}]},
        feed_timestamp="1784200000000",
        received_at_utc="2026-07-16T12:00:00+00:00",
        received_monotonic_ns=1000 + sequence,
        receipt_sequence=sequence,
        subscription_generation=1,
        coverage_state=CoverageState.VALID,
    )


def test_raw_segment_round_trips_exact_payload_and_storage_identity(tmp_path: Path) -> None:
    payloads = [
        {"event_type": "book", "asset_id": "token-1", "asks": [], "unicode": "°F"},
        {"event_type": "price_change", "price_changes": [{"price": "0.41", "size": "7.5"}]},
    ]
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="20260716T12")
    stored = [writer.append(make_event(index, payload)) for index, payload in enumerate(payloads, start=1)]
    stats = writer.close()

    replayed = list(iter_segment(stats.path))

    assert replayed == stored
    assert [event.raw_payload for event in replayed] == payloads
    assert replayed[0].append_offset == 0
    assert replayed[0].stable_event_id == "session-1:20260716T12:0"
    assert replayed[1].append_offset and replayed[1].append_offset > 0
    assert stats.events == 2
    assert stats.bytes_written > 0


def test_segment_rejects_event_with_existing_storage_identity(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    with pytest.raises(ValueError, match="already has storage identity"):
        writer.append(replace(make_event(1), partition_id="old"))
    writer.close()


def test_segment_fails_closed_on_truncated_record(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    writer.append(make_event(1))
    path = writer.close().path
    path.write_bytes(path.read_bytes()[:-1])

    with pytest.raises(SegmentCorruptionError, match="truncated record"):
        list(iter_segment(path))


def test_segment_fails_closed_on_checksum_mismatch(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    writer.append(make_event(1))
    path = writer.close().path
    contents = path.read_text(encoding="utf-8").replace("token-1", "token-2", 1)
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(SegmentCorruptionError, match="checksum mismatch"):
        list(iter_segment(path))


def test_event_contract_rejects_noncausal_sequence_values() -> None:
    with pytest.raises(ValueError, match="receipt_sequence"):
        replace(make_event(1), receipt_sequence=0)


def test_benchmark_reports_exact_replay_and_compression(tmp_path: Path) -> None:
    payloads = [
        {"event_type": "book", "asset_id": "token-1", "market": "market-1", "bids": []},
        {"event_type": "last_trade_price", "asset_id": "token-1", "market": "market-1", "price": "0.42"},
    ]

    result = benchmark(payloads, tmp_path)

    assert result["messages"] == 2
    assert result["round_trip_exact"] is True
    assert result["raw_bytes"] > 0
    assert 0 < result["gzip_ratio"] < 1
