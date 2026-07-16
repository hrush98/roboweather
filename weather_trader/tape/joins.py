from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from weather_trader.tape.books import BookReconstructor
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import DecisionTapeJoin, DecisionTiming
from weather_trader.tape.storage import iter_segment


def join_decision_to_tape(
    catalog: TapeCatalog,
    paths: list[Path] | tuple[Path, ...],
    decision: DecisionTiming,
    *,
    pre_signal_seconds: float = 60.0,
    persist: bool = True,
) -> DecisionTapeJoin:
    if pre_signal_seconds < 0:
        raise ValueError("pre_signal_seconds must be non-negative")
    source = _utc(decision.observation_source_timestamp)
    received = _utc(decision.observation_received_at_utc)
    started = _utc(decision.decision_started_at_utc)
    finished = _utc(decision.decision_finished_at_utc)
    activated = _utc(decision.activation_timestamp)
    if not (source <= received <= started <= finished):
        raise ValueError("decision timestamps are not causally ordered")
    if started < activated:
        raise ValueError("decision predates hypothesis activation")
    quote_ready = finished + timedelta(milliseconds=decision.latency_ms)
    reconstructor = BookReconstructor()
    first = None
    book = None
    session_id: str | None = None
    prior_sequence = 0
    for path in sorted(paths):
        for event in iter_segment(path):
            if session_id is None:
                session_id = event.collector_session_id
            elif event.collector_session_id != session_id:
                raise ValueError("cross-session decision joins are not supported")
            if event.receipt_sequence <= prior_sequence:
                raise ValueError("segments are not in causal receipt-sequence order")
            prior_sequence = event.receipt_sequence
            if event.token_id != decision.token_id:
                continue
            book = reconstructor.apply(event)
            if _utc(event.received_at_utc) >= quote_ready:
                first = event
                break
        if first is not None:
            break
    if first is None or session_id is None:
        result = DecisionTapeJoin(
            decision.decision_id, decision.hypothesis_version, decision.token_id,
            session_id or "unknown", quote_ready.isoformat(), None, None, False,
            "no_visible_event_after_quote_ready", pre_signal_seconds, None,
        )
    else:
        window_start = quote_ready - timedelta(seconds=pre_signal_seconds)
        interval = catalog.connection.execute(
            """
            select state from tape_coverage_intervals
            where session_id = ? and token_id = ? and state = 'VALID'
              and started_at_utc <= ?
              and (ended_at_utc is null or ended_at_utc >= ?)
            order by started_at_utc desc limit 1
            """,
            (session_id, decision.token_id, window_start.isoformat(), first.received_at_utc),
        ).fetchone()
        valid = interval is not None and book is not None and book.valid
        reason = None if valid else "insufficient_continuous_valid_coverage"
        result = DecisionTapeJoin(
            decision.decision_id, decision.hypothesis_version, decision.token_id,
            session_id, quote_ready.isoformat(), first.stable_event_id,
            first.received_at_utc, valid, reason, pre_signal_seconds,
            book.reconstruction_hash if book is not None else None,
        )
    if persist:
        catalog.record_decision_join(result)
    return result


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
