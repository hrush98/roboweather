from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from weather_trader.tape.books import BookReconstructor, ReconstructedBook
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import DecisionTapeJoin, DecisionTiming, MarketTapeEvent
from weather_trader.tape.storage import iter_segment


def join_decision_to_tape(
    catalog: TapeCatalog,
    paths: list[Path] | tuple[Path, ...],
    decision: DecisionTiming,
    *,
    pre_signal_seconds: float = 60.0,
    persist: bool = True,
) -> DecisionTapeJoin:
    """Join one immutable decision to causal book state and its coverage interval.

    ``reconstruction_hash`` is the book at or before quote readiness. The first
    event received after quote readiness remains an audit reference, but is not
    included in that hash. When a termination boundary is supplied, validity
    requires both raw-tape observation and one continuous catalog ``VALID``
    interval through that boundary.
    """
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
    termination = (
        _utc(decision.quote_termination_at_utc)
        if decision.quote_termination_at_utc is not None
        else None
    )
    if termination is not None and termination < quote_ready:
        raise ValueError("quote termination predates quote readiness")

    scan = _scan_tape(paths, token_id=decision.token_id, quote_ready=quote_ready, termination=termination)
    required_end = termination
    if required_end is None and scan.first_visible_event is not None:
        required_end = _utc(scan.first_visible_event.received_at_utc)
    window_start = quote_ready - timedelta(seconds=pre_signal_seconds)
    interval = (
        _coverage_interval(
            catalog,
            session_id=scan.session_id,
            token_id=decision.token_id,
            window_start=window_start,
        )
        if scan.session_id is not None
        else None
    )
    reason = _invalid_reason(
        scan=scan,
        interval=interval,
        required_end=required_end,
        termination=termination,
    )
    valid = reason is None
    result = DecisionTapeJoin(
        decision_id=decision.decision_id,
        hypothesis_version=decision.hypothesis_version,
        token_id=decision.token_id,
        session_id=scan.session_id or "unknown",
        quote_ready_at_utc=quote_ready.isoformat(),
        first_visible_event_id=(
            scan.first_visible_event.stable_event_id if scan.first_visible_event is not None else None
        ),
        first_visible_event_at_utc=(
            scan.first_visible_event.received_at_utc if scan.first_visible_event is not None else None
        ),
        coverage_valid=valid,
        invalid_reason=reason,
        pre_signal_seconds=pre_signal_seconds,
        reconstruction_hash=(
            scan.quote_ready_book.reconstruction_hash if scan.quote_ready_book is not None else None
        ),
        quote_termination_at_utc=termination.isoformat() if termination is not None else None,
        termination_event_id=(
            scan.termination_event.stable_event_id if scan.termination_event is not None else None
        ),
        termination_event_at_utc=(
            scan.termination_event.received_at_utc if scan.termination_event is not None else None
        ),
        termination_reconstruction_hash=(
            scan.termination_book.reconstruction_hash if scan.termination_book is not None else None
        ),
        tape_observed_through_at_utc=(
            scan.tape_observed_through.isoformat() if scan.tape_observed_through is not None else None
        ),
        coverage_interval_id=int(interval["id"]) if interval is not None else None,
        coverage_started_at_utc=str(interval["started_at_utc"]) if interval is not None else None,
        coverage_ended_at_utc=(
            str(interval["ended_at_utc"])
            if interval is not None and interval["ended_at_utc"] is not None
            else None
        ),
        source_type=decision.source_type,
        source_ref=decision.source_ref,
    )
    if persist:
        catalog.record_decision_join(result)
    return result


class _TapeScan:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.quote_ready_book: ReconstructedBook | None = None
        self.first_visible_event: MarketTapeEvent | None = None
        self.termination_book: ReconstructedBook | None = None
        self.termination_event: MarketTapeEvent | None = None
        self.tape_observed_through: datetime | None = None


def _scan_tape(
    paths: list[Path] | tuple[Path, ...],
    *,
    token_id: str,
    quote_ready: datetime,
    termination: datetime | None,
) -> _TapeScan:
    result = _TapeScan()
    reconstructor = BookReconstructor()
    prior_sequence = 0
    for path in sorted(paths):
        for event in iter_segment(path):
            if result.session_id is None:
                result.session_id = event.collector_session_id
            elif event.collector_session_id != result.session_id:
                raise ValueError("cross-session decision joins are not supported")
            if event.receipt_sequence <= prior_sequence:
                raise ValueError("segments are not in causal receipt-sequence order")
            prior_sequence = event.receipt_sequence
            event_time = _utc(event.received_at_utc)
            if result.tape_observed_through is None or event_time > result.tape_observed_through:
                result.tape_observed_through = event_time
            if event.token_id != token_id:
                continue
            if result.first_visible_event is None and event_time >= quote_ready:
                result.first_visible_event = event
            if termination is not None and event_time > termination:
                continue
            if termination is None and event_time > quote_ready:
                continue
            book = reconstructor.apply(event)
            if event_time <= quote_ready:
                result.quote_ready_book = book
            if termination is not None:
                result.termination_book = book
                result.termination_event = event
        if termination is None and result.first_visible_event is not None:
            break
    return result


def _coverage_interval(
    catalog: TapeCatalog,
    *,
    session_id: str,
    token_id: str,
    window_start: datetime,
) -> sqlite3.Row | None:
    rows = catalog.connection.execute(
        """
        select id, started_at_utc, ended_at_utc
        from tape_coverage_intervals
        where session_id = ? and token_id = ? and state = 'VALID'
        order by started_at_utc desc, id desc
        """,
        (session_id, token_id),
    ).fetchall()
    return next((row for row in rows if _utc(str(row["started_at_utc"])) <= window_start), None)


def _invalid_reason(
    *,
    scan: _TapeScan,
    interval: sqlite3.Row | None,
    required_end: datetime | None,
    termination: datetime | None,
) -> str | None:
    if scan.session_id is None:
        return "no_tape_events"
    if scan.quote_ready_book is None:
        return "no_reconstructable_book_at_quote_ready"
    if not scan.quote_ready_book.valid:
        return "invalid_book_at_quote_ready"
    if termination is None and scan.first_visible_event is None:
        return "no_visible_event_after_quote_ready"
    if termination is not None:
        if scan.tape_observed_through is None or scan.tape_observed_through < termination:
            return "quote_termination_not_observed"
        if scan.termination_book is None or not scan.termination_book.valid:
            return "invalid_book_at_quote_termination"
    if interval is None or required_end is None:
        return "insufficient_continuous_valid_coverage"
    ended_at = _utc(str(interval["ended_at_utc"])) if interval["ended_at_utc"] is not None else None
    if ended_at is not None and ended_at < required_end:
        return "insufficient_continuous_valid_coverage"
    return None


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
