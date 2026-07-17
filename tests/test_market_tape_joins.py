from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import (
    CollectorSession,
    CoverageInterval,
    CoverageState,
    DecisionTiming,
    MarketTapeEvent,
)
from weather_trader.tape.joins import join_decision_to_tape
from weather_trader.tape.storage import RawSegmentWriter


def event(sequence: int, timestamp: str, *, full: bool = False) -> MarketTapeEvent:
    payload = (
        {"event_type": "book", "bids": [{"price": "0.4", "size": "10"}], "asks": []}
        if full
        else {"price_change": {"side": "BUY", "price": "0.4", "size": "12"}}
    )
    return MarketTapeEvent(
        collector_session_id="session-1", token_id="token-1", market_id="market-1",
        event_type="book" if full else "price_change", raw_payload=payload,
        received_at_utc=timestamp, received_monotonic_ns=sequence,
        receipt_sequence=sequence, subscription_generation=1,
        coverage_state=CoverageState.RESYNCING if full else CoverageState.VALID,
    )


def decision() -> DecisionTiming:
    return DecisionTiming(
        decision_id="decision-1", hypothesis_version="hypothesis-v1",
        activation_timestamp="2026-07-16T00:00:00+00:00", token_id="token-1",
        observation_source_timestamp="2026-07-16T11:58:00+00:00",
        observation_received_at_utc="2026-07-16T11:59:00+00:00",
        decision_started_at_utc="2026-07-16T12:00:30+00:00",
        decision_finished_at_utc="2026-07-16T12:00:59+00:00", latency_ms=1000,
    )


def fixture(tmp_path: Path, *, valid_until: str | None = None):
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(CollectorSession(
        "session-1", "2026-07-16T11:50:00+00:00", 1, "test", "host"
    ))
    catalog.insert_coverage_interval(CoverageInterval(
        "session-1", "token-1", CoverageState.VALID,
        "2026-07-16T11:59:00+00:00", valid_until, 1,
    ))
    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="part")
    writer.append(event(1, "2026-07-16T12:00:00+00:00", full=True))
    visible = writer.append(event(2, "2026-07-16T12:02:00+00:00"))
    return catalog, writer.close().path, visible


def test_causal_join_applies_latency_selects_first_visible_event_and_persists(tmp_path: Path) -> None:
    catalog, path, visible = fixture(tmp_path)
    joined = join_decision_to_tape(catalog, [path], decision(), pre_signal_seconds=60)

    assert joined.quote_ready_at_utc == "2026-07-16T12:01:00+00:00"
    assert joined.first_visible_event_id == visible.stable_event_id
    assert joined.coverage_valid is True
    assert joined.reconstruction_hash is not None
    assert catalog.connection.execute("select coverage_valid from tape_decision_joins").fetchone()[0] == 1
    catalog.close()


def test_causal_join_fails_closed_when_validity_does_not_reach_visible_event(tmp_path: Path) -> None:
    catalog, path, _ = fixture(tmp_path, valid_until="2026-07-16T12:01:30+00:00")
    joined = join_decision_to_tape(catalog, [path], decision())

    assert joined.coverage_valid is False
    assert joined.invalid_reason == "insufficient_continuous_valid_coverage"
    catalog.close()


def test_causal_join_rejects_noncausal_decision_timestamps(tmp_path: Path) -> None:
    catalog, path, _ = fixture(tmp_path)
    bad = replace(decision(), observation_received_at_utc="2026-07-16T12:01:00+00:00")

    with pytest.raises(ValueError, match="causally ordered"):
        join_decision_to_tape(catalog, [path], bad)
    catalog.close()


def test_causal_join_requires_tape_and_valid_coverage_through_termination(
    tmp_path: Path,
) -> None:
    catalog, path, _ = fixture(tmp_path, valid_until="2026-07-16T12:02:30+00:00")
    bounded = replace(decision(), quote_termination_at_utc="2026-07-16T12:03:00+00:00")

    not_observed = join_decision_to_tape(catalog, [path], bounded)

    assert not_observed.coverage_valid is False
    assert not_observed.invalid_reason == "quote_termination_not_observed"

    writer = RawSegmentWriter(tmp_path, session_id="session-1", partition_id="zpart")
    writer.append(
        replace(
            event(3, "2026-07-16T12:03:00+00:00", full=True),
            token_id="other-token",
        )
    )
    second_path = writer.close().path
    coverage_break = join_decision_to_tape(catalog, [path, second_path], bounded)

    assert coverage_break.coverage_valid is False
    assert coverage_break.invalid_reason == "insufficient_continuous_valid_coverage"
    catalog.close()
