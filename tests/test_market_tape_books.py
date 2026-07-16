from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from weather_trader.tape.books import BookReconstructor, reconstruct_segment
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import CollectorSession, CoverageState, MarketTapeEvent
from weather_trader.tape.storage import RawSegmentWriter


def make_event(sequence: int) -> MarketTapeEvent:
    return MarketTapeEvent(
        collector_session_id="session-1",
        token_id="token-1",
        market_id="market-1",
        event_type="book" if sequence in {1, 3} else "price_change",
        raw_payload={"event_type": "book", "bids": [], "asks": []},
        received_at_utc="2026-07-16T12:00:00+00:00",
        received_monotonic_ns=1000 + sequence,
        receipt_sequence=sequence,
        subscription_generation=1,
        coverage_state=CoverageState.VALID,
    )


def test_book_reconstruction_is_deterministic_and_orders_levels() -> None:
    full = replace(
        make_event(1),
        coverage_state=CoverageState.RESYNCING,
        raw_payload={
            "event_type": "book",
            "asset_id": "token-1",
            "bids": [{"price": "0.4", "size": "10"}, {"price": "0.3", "size": "5"}],
            "asks": [{"price": "0.6", "size": "8"}, {"price": "0.5", "size": "7"}],
        },
    )
    change = replace(
        make_event(2),
        raw_payload={
            "parent": {"event_type": "price_change"},
            "price_change": {"asset_id": "token-1", "side": "BUY", "price": "0.4", "size": "12"},
        },
    )

    first = BookReconstructor()
    first.apply(full)
    result = first.apply(change)
    second = BookReconstructor()
    second.apply(full)
    repeated = second.apply(change)

    assert result.valid is True
    assert result.bids == ((0.4, 12.0), (0.3, 5.0))
    assert result.asks == ((0.5, 7.0), (0.6, 8.0))
    assert result.reconstruction_hash == repeated.reconstruction_hash


def test_gap_invalidates_deltas_until_a_fresh_full_book() -> None:
    reconstructor = BookReconstructor()
    reconstructor.apply(make_event(1))
    gap_delta = replace(
        make_event(2),
        coverage_state=CoverageState.GAPPED,
        raw_payload={"price_change": {"side": "SELL", "price": "0.6", "size": "4"}},
    )
    invalid = reconstructor.apply(gap_delta)
    resynced = reconstructor.apply(replace(make_event(3), coverage_state=CoverageState.RESYNCING))

    assert invalid.valid is False
    assert invalid.invalid_reason == "coverage_gapped"
    assert resynced.valid is True


def test_malformed_full_book_fails_closed() -> None:
    result = BookReconstructor().apply(
        replace(make_event(1), raw_payload={"event_type": "book", "bids": []})
    )

    assert result.valid is False
    assert result.invalid_reason == "malformed_full_book"


def test_segment_reconstruction_and_checkpoint_persistence(tmp_path: Path) -> None:
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    stored = writer.append(make_event(1))
    path = writer.close().path
    reconstructed = reconstruct_segment(path)["token-1"]

    reconstructor = BookReconstructor()
    reconstructor.apply(stored)
    checkpoint = reconstructor.checkpoint(stored)
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(
        CollectorSession("session-1", "2026-07-16T12:00:00+00:00", 1, "test", "host")
    )
    catalog.record_checkpoint(checkpoint)
    row = catalog.connection.execute("select * from tape_book_checkpoints").fetchone()

    assert reconstructed.valid is True
    assert row["reconstruction_hash"] == reconstructed.reconstruction_hash
    assert row["event_id"] == stored.stable_event_id
    catalog.close()
