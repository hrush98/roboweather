from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from weather_trader.tape.books import BookReconstructor
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import (
    CollectorSession,
    CoverageInterval,
    CoverageState,
    MarketTapeEvent,
)
from weather_trader.tape.decision_sources import decision_timing_from_execution_quote
from weather_trader.tape.joins import join_decision_to_tape
from weather_trader.tape.storage import RawSegmentWriter


def _execution_fixture(path: Path, *, cancellation_at: str | None = None) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table prediction_snapshots (
            id integer primary key,
            timestamp text not null,
            latest_obs_time_utc text not null
        );
        create table live_candidate_snapshots (
            candidate_id text primary key,
            local_receipt_timestamp text not null,
            prediction_snapshot_id integer,
            source_prediction_snapshot_ids text not null
        );
        create table live_price_sheets (
            id integer primary key,
            live_candidate_id text not null,
            version text not null,
            raw_json text not null
        );
        create table live_quote_intents (
            id integer primary key,
            timestamp text not null,
            quote_id text not null,
            live_candidate_id text not null,
            price_sheet_version text not null,
            selected_token_id text,
            gtd_expiry text not null,
            quote_spec_id text,
            would_post integer,
            raw_json text not null
        );
        """
    )
    connection.execute(
        "insert into prediction_snapshots values (?, ?, ?)",
        (7, "2026-07-17T11:59:00+00:00", "2026-07-17T11:58:00+00:00"),
    )
    connection.execute(
        "insert into live_candidate_snapshots values (?, ?, ?, ?)",
        ("candidate-1", "2026-07-17T12:00:00+00:00", None, "[7]"),
    )
    connection.execute(
        "insert into live_price_sheets values (?, ?, ?, ?)",
        (
            1,
            "candidate-1",
            "price_sheet_v2",
            json.dumps(
                {
                    "signal_spec": {
                        "signal_spec_id": "signal-v2",
                        "activation_timestamp": "2026-07-17T00:00:00+00:00",
                    }
                }
            ),
        ),
    )
    raw = {}
    if cancellation_at is not None:
        raw["shadow_cancel"] = {"checked_at": cancellation_at, "reason": "book_cross"}
    connection.execute(
        "insert into live_quote_intents values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "2026-07-17T12:00:30+00:00",
            "quote-1",
            "candidate-1",
            "price_sheet_v2",
            "token-1",
            "2026-07-17T12:03:00+00:00",
            "quote-arm-1",
            1,
            json.dumps(raw),
        ),
    )
    connection.commit()
    connection.close()


def _event(
    sequence: int,
    timestamp: str,
    *,
    token_id: str = "token-1",
    full: bool = False,
    size: str = "10",
) -> MarketTapeEvent:
    payload = (
        {"event_type": "book", "bids": [{"price": "0.4", "size": size}], "asks": []}
        if full
        else {"price_change": {"side": "BUY", "price": "0.4", "size": size}}
    )
    return MarketTapeEvent(
        collector_session_id="session-1",
        token_id=token_id,
        market_id="market-1",
        event_type="book" if full else "price_change",
        raw_payload=payload,
        received_at_utc=timestamp,
        received_monotonic_ns=sequence,
        receipt_sequence=sequence,
        subscription_generation=1,
        coverage_state=CoverageState.RESYNCING if full else CoverageState.VALID,
    )


def test_execution_quote_exports_and_joins_through_termination(tmp_path: Path) -> None:
    execution_db = tmp_path / "execution.sqlite"
    _execution_fixture(execution_db)
    decision = decision_timing_from_execution_quote(execution_db, latency_ms=30_000)

    assert decision.decision_id == "quote-1"
    assert decision.hypothesis_version == "price_sheet_v2:signal-v2:quote-arm-1"
    assert decision.quote_termination_at_utc == "2026-07-17T12:03:00+00:00"
    assert decision.source_type == "execution_quote_intent"

    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(
        CollectorSession("session-1", "2026-07-17T11:50:00+00:00", 1, "test", "host")
    )
    coverage_id = catalog.insert_coverage_interval(
        CoverageInterval(
            "session-1",
            "token-1",
            CoverageState.VALID,
            "2026-07-17T11:58:00+00:00",
            "2026-07-17T12:03:10+00:00",
            1,
        )
    )
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    initial = writer.append(_event(1, "2026-07-17T12:00:15+00:00", full=True))
    first_visible = writer.append(_event(2, "2026-07-17T12:01:30+00:00", size="12"))
    termination_event = writer.append(_event(3, "2026-07-17T12:02:30+00:00", size="14"))
    writer.append(
        _event(4, "2026-07-17T12:03:00+00:00", token_id="other-token", full=True)
    )
    path = writer.close().path

    initial_book = BookReconstructor().apply(initial)
    joined = join_decision_to_tape(catalog, [path], decision, pre_signal_seconds=60)

    assert joined.coverage_valid is True
    assert joined.first_visible_event_id == first_visible.stable_event_id
    assert joined.reconstruction_hash == initial_book.reconstruction_hash
    assert joined.termination_event_id == termination_event.stable_event_id
    assert joined.termination_reconstruction_hash != joined.reconstruction_hash
    assert joined.tape_observed_through_at_utc == "2026-07-17T12:03:00+00:00"
    assert joined.coverage_interval_id == coverage_id
    row = catalog.connection.execute(
        "select * from tape_decision_joins where decision_id = 'quote-1'"
    ).fetchone()
    assert row["quote_termination_at_utc"] == "2026-07-17T12:03:00+00:00"
    assert row["source_type"] == "execution_quote_intent"
    catalog.close()


def test_execution_quote_uses_observed_cancel_before_gtd(tmp_path: Path) -> None:
    execution_db = tmp_path / "execution.sqlite"
    _execution_fixture(execution_db, cancellation_at="2026-07-17T12:02:00+00:00")

    decision = decision_timing_from_execution_quote(execution_db)

    assert decision.quote_termination_at_utc == "2026-07-17T12:02:00+00:00"
