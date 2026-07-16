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
    events: int
    partitions: int
    raw_disk_bytes: int
    queue_high_water: int
    queue_capacity: int
    rss_bytes: int
    receipt_lag_ms: float | None
    reconstruction_errors: int
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
        return TapeHealthReport(False, None, None, 0, 0, 0, 0, 0, 0, None, 0, {}, ("no_sessions",))

    session_id = str(session["session_id"])
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
    failures: list[str] = []
    finish_reason = session["finish_reason"]
    if finish_reason == "error":
        failures.append("session_finished_with_error")
    if reconstruction_errors:
        failures.append("reconstruction_errors")
    if metric is None:
        failures.append("missing_telemetry")
        events = raw_disk_bytes = queue_high_water = queue_capacity = rss_bytes = 0
        receipt_lag_ms = None
    else:
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
    if events > 0 and not partitions:
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
        events=events,
        partitions=len(partitions),
        raw_disk_bytes=raw_disk_bytes,
        queue_high_water=queue_high_water,
        queue_capacity=queue_capacity,
        rss_bytes=rss_bytes,
        receipt_lag_ms=float(receipt_lag_ms) if receipt_lag_ms is not None else None,
        reconstruction_errors=reconstruction_errors,
        coverage_counts=coverage_counts,
        failures=tuple(failures),
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
