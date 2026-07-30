from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.storage import SegmentCorruptionError, iter_segment


@dataclass(frozen=True)
class TapeHealthReport:
    healthy: bool
    session_id: str | None
    finish_reason: str | None
    messages: int
    events: int
    subscription_generation: int | None
    subscribed_tokens: int
    valid_subscribed_tokens: int
    partitions: int
    raw_disk_bytes: int
    queue_high_water: int
    queue_capacity: int
    rss_bytes: int
    receipt_lag_ms: float | None
    reconstruction_errors: int
    discovery_refreshes: int
    latest_discovery_status: str | None
    unhealthy_discovery_refreshes: int
    coverage_counts: dict[str, int]
    failures: tuple[str, ...]


def evaluate_tape_health(
    catalog: TapeCatalog,
    *,
    stale_after_seconds: float = 120.0,
    max_receipt_lag_ms: float = 10_000.0,
    max_rss_bytes: int = 1_073_741_824,
    verify_segments: bool = True,
    now: datetime | None = None,
) -> TapeHealthReport:
    session = catalog.connection.execute(
        "select * from tape_collector_sessions order by started_at_utc desc limit 1"
    ).fetchone()
    if session is None:
        return TapeHealthReport(
            healthy=False,
            session_id=None,
            finish_reason=None,
            messages=0,
            events=0,
            subscription_generation=None,
            subscribed_tokens=0,
            valid_subscribed_tokens=0,
            partitions=0,
            raw_disk_bytes=0,
            queue_high_water=0,
            queue_capacity=0,
            rss_bytes=0,
            receipt_lag_ms=None,
            reconstruction_errors=0,
            discovery_refreshes=0,
            latest_discovery_status=None,
            unhealthy_discovery_refreshes=0,
            coverage_counts={},
            failures=("no_sessions",),
        )

    session_id = str(session["session_id"])
    subscription_rows = catalog.connection.execute(
        """
        select g.generation, g.effective_at_utc, m.token_id
        from tape_subscription_generations g
        left join tape_subscription_members m
          on m.session_id = g.session_id and m.generation = g.generation
        where g.session_id = ?
        order by g.generation, m.token_id
        """,
        (session_id,),
    ).fetchall()
    subscription_generation, subscribed_tokens, valid_subscribed_tokens = (
        _latest_subscription_validity(
            catalog,
            session_id=session_id,
            subscription_rows=subscription_rows,
            active=session["finished_at_utc"] is None,
        )
    )
    metric = catalog.connection.execute(
        "select * from tape_collector_metrics where session_id = ? order by id desc limit 1",
        (session_id,),
    ).fetchone()
    partitions = catalog.connection.execute(
        "select * from tape_raw_partitions where session_id = ? order by partition_id",
        (session_id,),
    ).fetchall()
    coverage_rows = catalog.connection.execute(
        """
        select state, count(*) count from tape_coverage_intervals
        where session_id = ? group by state order by state
        """,
        (session_id,),
    ).fetchall()
    coverage_counts = {str(row["state"]): int(row["count"]) for row in coverage_rows}
    reconstruction_errors = int(
        catalog.connection.execute(
            "select count(*) from tape_reconstruction_errors where session_id = ?", (session_id,)
        ).fetchone()[0]
    )
    discovery_rows = catalog.connection.execute(
        """
        select status from tape_discovery_refreshes
        where session_id = ? order by id
        """,
        (session_id,),
    ).fetchall()
    latest_discovery_status = (
        str(discovery_rows[-1]["status"]) if discovery_rows else None
    )
    unhealthy_discovery_refreshes = sum(
        str(row["status"]) != "COMPLETE" for row in discovery_rows
    )
    failures: list[str] = []
    finish_reason = session["finish_reason"]
    if finish_reason == "error":
        failures.append("session_finished_with_error")
    if reconstruction_errors:
        failures.append("reconstruction_errors")
    if not discovery_rows:
        failures.append("missing_discovery_refresh_health")
    elif latest_discovery_status != "COMPLETE":
        failures.append("latest_discovery_refresh_unhealthy")
    if metric is None:
        failures.append("missing_telemetry")
        messages = events = raw_disk_bytes = queue_high_water = queue_capacity = rss_bytes = 0
        receipt_lag_ms = None
    else:
        messages = int(metric["messages"])
        events = int(metric["events"])
        raw_disk_bytes = int(metric["raw_disk_bytes"])
        queue_high_water = int(metric["queue_high_water"])
        queue_capacity = int(metric["queue_capacity"])
        rss_bytes = int(metric["rss_bytes"])
        receipt_lag_ms = metric["receipt_lag_ms"]
        if queue_high_water >= queue_capacity:
            failures.append("queue_capacity_reached")
        if receipt_lag_ms is not None and float(receipt_lag_ms) > max_receipt_lag_ms:
            failures.append("receipt_lag_over_budget")
        if rss_bytes > max_rss_bytes:
            failures.append("rss_over_budget")
        if session["finished_at_utc"] is None:
            captured = _parse_utc(str(metric["captured_at_utc"]))
            age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
            if age > stale_after_seconds:
                failures.append("telemetry_stale")
    if messages <= 0:
        failures.append("zero_messages")
    if events <= 0:
        failures.append("zero_events")
    if subscription_generation is None:
        failures.append("missing_subscription_generation")
    elif subscribed_tokens <= 0:
        failures.append("empty_latest_subscription")
    elif valid_subscribed_tokens < subscribed_tokens:
        failures.append("subscribed_tokens_without_valid_full_book")
    # The active writer catalogs a segment atomically only when it rotates or
    # closes. Fresh telemetry and the current raw byte count are sufficient for
    # operational health before that first rotation; terminal sessions must
    # always have cataloged partitions.
    if events > 0 and not partitions and session["finished_at_utc"] is not None:
        failures.append("missing_partitions")
    if verify_segments:
        for partition in partitions:
            path = Path(str(partition["path"]))
            if not path.is_file():
                failures.append(f"missing_segment:{path}")
                continue
            try:
                count = sum(1 for _ in iter_segment(path))
            except SegmentCorruptionError:
                failures.append(f"corrupt_segment:{path}")
                continue
            if count != int(partition["events"]):
                failures.append(f"segment_event_count_mismatch:{path}")
    return TapeHealthReport(
        healthy=not failures,
        session_id=session_id,
        finish_reason=finish_reason,
        messages=messages,
        events=events,
        subscription_generation=subscription_generation,
        subscribed_tokens=subscribed_tokens,
        valid_subscribed_tokens=valid_subscribed_tokens,
        partitions=len(partitions),
        raw_disk_bytes=raw_disk_bytes,
        queue_high_water=queue_high_water,
        queue_capacity=queue_capacity,
        rss_bytes=rss_bytes,
        receipt_lag_ms=float(receipt_lag_ms) if receipt_lag_ms is not None else None,
        reconstruction_errors=reconstruction_errors,
        discovery_refreshes=len(discovery_rows),
        latest_discovery_status=latest_discovery_status,
        unhealthy_discovery_refreshes=unhealthy_discovery_refreshes,
        coverage_counts=coverage_counts,
        failures=tuple(failures),
    )


def _latest_subscription_validity(
    catalog: TapeCatalog,
    *,
    session_id: str,
    subscription_rows: list,
    active: bool,
) -> tuple[int | None, int, int]:
    """Count latest members with valid coverage in their current membership run.

    A token retained across a dynamic generation keeps its earlier full-book
    transition. A removed and later re-added token must receive a new full book.
    Active sessions additionally require the token's current state to remain
    VALID; terminal sessions may have closed otherwise-valid coverage.
    """
    if not subscription_rows:
        return None, 0, 0
    generations: dict[int, tuple[str, set[str]]] = {}
    for row in subscription_rows:
        generation = int(row["generation"])
        effective_at, members = generations.setdefault(
            generation, (str(row["effective_at_utc"]), set())
        )
        if row["token_id"] is not None:
            members.add(str(row["token_id"]))
    latest_generation = max(generations)
    latest_members = generations[latest_generation][1]
    coverage_rows = catalog.connection.execute(
        """
        select token_id, state, started_at_utc
        from tape_coverage_intervals
        where session_id = ?
        order by id
        """,
        (session_id,),
    ).fetchall()
    valid_starts: dict[str, list[str]] = {}
    latest_states: dict[str, str] = {}
    for row in coverage_rows:
        token_id = str(row["token_id"])
        latest_states[token_id] = str(row["state"])
        if str(row["state"]) == "VALID":
            valid_starts.setdefault(token_id, []).append(str(row["started_at_utc"]))
    valid = 0
    for token_id in latest_members:
        membership_started_at = generations[latest_generation][0]
        for generation in range(latest_generation - 1, 0, -1):
            previous = generations.get(generation)
            if previous is None or token_id not in previous[1]:
                break
            membership_started_at = previous[0]
        if not any(
            started_at >= membership_started_at
            for started_at in valid_starts.get(token_id, ())
        ):
            continue
        if active and latest_states.get(token_id) != "VALID":
            continue
        valid += 1
    return latest_generation, len(latest_members), valid


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
