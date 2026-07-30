from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from weather_trader.tape.catalog import TapeCatalog


@dataclass(frozen=True)
class MarketLifecycleEvidence:
    market_id: str
    station: str
    market_family: str
    market_date: str
    token_count: int
    listing_at_utc: str
    listing_timestamp_source: str
    discovered_at_utc: str
    discovery_lag_seconds: float
    market_end_at_utc: str
    valid_through_end_tokens: int
    maximum_coverage_gap_seconds: float | None
    complete: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class TapeLifecycleReport:
    passed: bool
    validation_start_at_utc: str | None
    validation_end_at_utc: str | None
    selected_validation_run_id: str | None
    selected_session_ids: tuple[str, ...]
    build_fingerprints: tuple[str, ...]
    config_fingerprints: tuple[str, ...]
    sessions: int
    successful_sessions: int
    observed_start_at_utc: str | None
    observed_end_at_utc: str | None
    recorded_hours: float
    events: int
    partitions: int
    raw_disk_bytes: int
    projected_daily_raw_bytes: int | None
    max_daily_raw_bytes: int
    retention_days: int
    projected_retention_bytes: int | None
    queue_high_water: int
    queue_capacity: int
    max_rss_bytes_observed: int
    max_rss_bytes_budget: int
    max_receipt_lag_ms_observed: float | None
    max_receipt_lag_ms_budget: float
    reconstruction_errors: int
    discovery_refreshes: int
    complete_discovery_refreshes: int
    incomplete_discovery_refreshes: int
    failed_discovery_refreshes: int
    listing_source_counts: dict[str, int]
    cohort_markets: int
    incomplete_cohort_markets: int
    eligible_closed_markets: int
    complete_markets: int
    required_station_families: tuple[str, ...]
    complete_station_families: tuple[str, ...]
    markets: tuple[MarketLifecycleEvidence, ...]
    failures: tuple[str, ...]


def evaluate_tape_lifecycle(
    catalog: TapeCatalog,
    *,
    validation_start_at: datetime | None = None,
    validation_end_at: datetime | None = None,
    validation_session_ids: tuple[str, ...] | None = None,
    validation_run_id: str | None = None,
    min_recorded_hours: float = 12.0,
    max_discovery_lag_seconds: float = 300.0,
    max_coverage_gap_seconds: float = 5.0,
    max_daily_raw_bytes: int = 25 * 1024**3,
    retention_days: int = 14,
    max_receipt_lag_ms: float = 10_000.0,
    max_rss_bytes: int = 1024**3,
    now: datetime | None = None,
) -> TapeLifecycleReport:
    if min_recorded_hours <= 0:
        raise ValueError("min_recorded_hours must be positive")
    if min(max_discovery_lag_seconds, max_coverage_gap_seconds, max_daily_raw_bytes, retention_days) < 0:
        raise ValueError("lifecycle budgets must be non-negative")
    validation_start = _as_utc(validation_start_at, name="validation_start_at")
    validation_end = _as_utc(validation_end_at, name="validation_end_at")
    if validation_start is not None and validation_end is not None and validation_end <= validation_start:
        raise ValueError("validation_end_at must be after validation_start_at")
    requested_session_ids = (
        tuple(sorted({str(item) for item in validation_session_ids if str(item)}))
        if validation_session_ids is not None
        else None
    )
    if validation_session_ids is not None and not requested_session_ids:
        raise ValueError("validation_session_ids must contain at least one non-empty id")
    requested_validation_run_id = (
        str(validation_run_id).strip() if validation_run_id is not None else None
    )
    if validation_run_id is not None and not requested_validation_run_id:
        raise ValueError("validation_run_id must be non-empty")
    connection = catalog.connection
    all_sessions = connection.execute(
        "select * from tape_collector_sessions order by started_at_utc"
    ).fetchall()
    if requested_session_ids is not None:
        known_session_ids = {str(row["session_id"]) for row in all_sessions}
        unknown_session_ids = sorted(set(requested_session_ids) - known_session_ids)
        if unknown_session_ids:
            raise ValueError(
                "validation_session_ids not found: " + ", ".join(unknown_session_ids)
            )
    if requested_validation_run_id is not None:
        known_validation_run_ids = {
            str(row["validation_run_id"]) for row in all_sessions
        }
        if requested_validation_run_id not in known_validation_run_ids:
            raise ValueError(
                f"validation_run_id not found: {requested_validation_run_id}"
            )
    sessions = [
        row
        for row in all_sessions
        if (
            requested_session_ids is None
            or str(row["session_id"]) in requested_session_ids
        )
        and (
            requested_validation_run_id is None
            or str(row["validation_run_id"]) == requested_validation_run_id
        )
        and (
            validation_start is None
            or _parse_utc(str(row["started_at_utc"])) >= validation_start
        )
        and (
            validation_end is None
            or _parse_utc(str(row["started_at_utc"])) < validation_end
        )
    ]
    if not sessions:
        return _empty_report(
            validation_start=validation_start,
            validation_end=validation_end,
            max_daily_raw_bytes=max_daily_raw_bytes,
            retention_days=retention_days,
            max_receipt_lag_ms=max_receipt_lag_ms,
            max_rss_bytes=max_rss_bytes,
        )

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_time = current_time.astimezone(timezone.utc)
    selected_session_ids = tuple(str(row["session_id"]) for row in sessions)
    selected_session_id_set = set(selected_session_ids)
    validation_run_ids = {
        str(row["validation_run_id"]) for row in sessions
    }
    build_fingerprints = tuple(
        sorted({str(row["build_fingerprint"]) for row in sessions})
    )
    config_fingerprints = tuple(
        sorted({str(row["config_fingerprint"]) for row in sessions})
    )
    selected_validation_run_id = (
        next(iter(validation_run_ids)) if len(validation_run_ids) == 1 else None
    )
    metric_rows = [
        row
        for row in connection.execute(
        "select * from tape_collector_metrics order by captured_at_utc, id"
        ).fetchall()
        if str(row["session_id"]) in selected_session_id_set
        and (
            validation_end is None
            or _parse_utc(str(row["captured_at_utc"])) <= validation_end
        )
    ]
    latest_metric_by_session: dict[str, Any] = {}
    for row in metric_rows:
        latest_metric_by_session[str(row["session_id"])] = row
    observed_start = min(_parse_utc(str(row["started_at_utc"])) for row in sessions)
    if validation_start is not None:
        observed_start = max(observed_start, validation_start)
    session_ends: dict[str, datetime] = {}
    recorded_seconds = 0.0
    successful_sessions = 0
    session_errors = 0
    for session in sessions:
        session_id = str(session["session_id"])
        started = _parse_utc(str(session["started_at_utc"]))
        finished = (
            _parse_utc(str(session["finished_at_utc"]))
            if session["finished_at_utc"]
            else None
        )
        if finished is not None:
            ended = finished
        elif session_id in latest_metric_by_session:
            ended = _parse_utc(str(latest_metric_by_session[session_id]["captured_at_utc"]))
        else:
            ended = current_time
        if validation_end is not None:
            ended = min(ended, validation_end)
        session_ends[session_id] = ended
        recorded_seconds += max(0.0, (ended - started).total_seconds())
        if (
            str(session["finish_reason"] or "") == "error"
            and finished is not None
            and (validation_end is None or finished <= validation_end)
        ):
            session_errors += 1
        elif ended > started:
            successful_sessions += 1
    observed_end = max(session_ends.values())

    events = sum(int(row["events"]) for row in latest_metric_by_session.values())
    partitions_rows = [
        row
        for row in connection.execute("select * from tape_raw_partitions").fetchall()
        if str(row["session_id"]) in selected_session_id_set
        and (
            validation_end is None
            or _parse_utc(str(row["closed_at_utc"])) <= validation_end
        )
    ]
    raw_disk_bytes = sum(int(row["bytes_written"]) for row in partitions_rows)
    recorded_hours = recorded_seconds / 3600.0
    projected_daily = round(raw_disk_bytes * 24.0 / recorded_hours) if recorded_hours > 0 else None
    projected_retention = projected_daily * retention_days if projected_daily is not None else None
    queue_high_water = max((int(row["queue_high_water"]) for row in metric_rows), default=0)
    queue_capacity = min((int(row["queue_capacity"]) for row in metric_rows), default=0)
    rss_observed = max((int(row["rss_bytes"]) for row in metric_rows), default=0)
    lag_values = [float(row["receipt_lag_ms"]) for row in metric_rows if row["receipt_lag_ms"] is not None]
    receipt_lag_observed = max(lag_values) if lag_values else None
    reconstruction_errors = sum(
        1
        for row in connection.execute(
            "select session_id,captured_at_utc from tape_reconstruction_errors"
        ).fetchall()
        if str(row["session_id"]) in selected_session_id_set
        and (
            validation_end is None
            or _parse_utc(str(row["captured_at_utc"])) <= validation_end
        )
    )

    discovery_refresh_rows = [
        row
        for row in connection.execute(
            "select * from tape_discovery_refreshes order by attempted_at_utc, id"
        ).fetchall()
        if str(row["session_id"]) in selected_session_id_set
        and (
            validation_end is None
            or _parse_utc(str(row["attempted_at_utc"])) <= validation_end
        )
    ]
    selected_refresh_ids = {int(row["id"]) for row in discovery_refresh_rows}
    cohort_token_ids = {
        str(row["token_id"])
        for row in connection.execute(
            "select refresh_id, token_id from tape_discovery_refresh_members"
        ).fetchall()
        if int(row["refresh_id"]) in selected_refresh_ids
    }
    token_rows = [
        row
        for row in connection.execute(
            "select * from tape_tokens order by market_id, token_id"
        ).fetchall()
        if str(row["token_id"]) in cohort_token_ids
    ]
    by_market: dict[str, list[Any]] = defaultdict(list)
    for row in token_rows:
        by_market[str(row["market_id"])].append(row)

    coverage_by_token: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in connection.execute(
        "select token_id,started_at_utc,ended_at_utc,session_id from tape_coverage_intervals where state = 'VALID' order by token_id,started_at_utc"
    ).fetchall():
        session_id = str(row["session_id"])
        if session_id not in selected_session_id_set:
            continue
        start = max(_parse_utc(str(row["started_at_utc"])), observed_start)
        if row["ended_at_utc"]:
            end = min(_parse_utc(str(row["ended_at_utc"])), observed_end)
        else:
            end = min(session_ends.get(session_id, observed_end), observed_end)
        if end >= start:
            coverage_by_token[str(row["token_id"])].append((start, end))

    market_evidence = tuple(
        _market_evidence(
            market_id,
            rows,
            coverage_by_token,
            observed_start=observed_start,
            observed_end=observed_end,
            max_discovery_lag_seconds=max_discovery_lag_seconds,
            max_coverage_gap_seconds=max_coverage_gap_seconds,
        )
        for market_id, rows in sorted(by_market.items())
    )
    cohort_evidence = tuple(
        item
        for item in market_evidence
        if _parse_utc(item.listing_at_utc) >= observed_start
        and _parse_utc(item.listing_at_utc) <= observed_end
    )
    cohort_market_ids = {item.market_id for item in cohort_evidence}
    listing_source_counts: dict[str, int] = defaultdict(int)
    for row in token_rows:
        if str(row["market_id"]) in cohort_market_ids:
            listing_source_counts[str(row["listing_timestamp_source"])] += 1
    eligible = tuple(
        item
        for item in cohort_evidence
        if "listing_timestamp_not_authoritative" not in item.failures
        and "discovery_lag_over_budget" not in item.failures
        and "market_not_closed_in_observation_window" not in item.failures
    )
    required_families = tuple(
        sorted({f"{item.station}:{item.market_family}" for item in cohort_evidence})
    )
    complete_families = tuple(
        sorted(
            {
                f"{item.station}:{item.market_family}"
                for item in cohort_evidence
                if item.complete
            }
        )
    )

    failures: list[str] = []
    if len(validation_run_ids) != 1 or "unspecified" in validation_run_ids:
        failures.append("validation_run_identity_missing_or_mixed")
    if len(build_fingerprints) != 1 or "unspecified" in build_fingerprints:
        failures.append("build_fingerprint_missing_or_mixed")
    if len(config_fingerprints) != 1 or "unspecified" in config_fingerprints:
        failures.append("config_fingerprint_missing_or_mixed")
    if recorded_hours < min_recorded_hours:
        failures.append("recorded_duration_below_gate")
    if session_errors:
        failures.append("collector_sessions_finished_with_error")
    if not partitions_rows:
        failures.append("no_cataloged_raw_partitions")
    if projected_daily is None or projected_daily > max_daily_raw_bytes:
        failures.append("daily_raw_growth_over_budget")
    if queue_capacity <= 0 or queue_high_water >= queue_capacity:
        failures.append("queue_capacity_reached")
    if rss_observed > max_rss_bytes:
        failures.append("rss_over_budget")
    if receipt_lag_observed is None or receipt_lag_observed > max_receipt_lag_ms:
        failures.append("receipt_lag_over_budget")
    if reconstruction_errors:
        failures.append("reconstruction_errors")
    complete_refreshes = sum(
        str(row["status"]) == "COMPLETE" for row in discovery_refresh_rows
    )
    incomplete_refreshes = sum(
        str(row["status"]) == "INCOMPLETE" for row in discovery_refresh_rows
    )
    failed_refreshes = sum(
        str(row["status"]) == "ERROR" for row in discovery_refresh_rows
    )
    if not discovery_refresh_rows:
        failures.append("no_discovery_refreshes")
    elif len(discovery_refresh_rows) < 2:
        failures.append("discovery_refresh_not_repeated")
    if incomplete_refreshes:
        failures.append("incomplete_discovery_refreshes")
    if failed_refreshes:
        failures.append("failed_discovery_refreshes")
    if listing_source_counts.get("gamma_created_at", 0) == 0:
        failures.append("no_authoritative_listing_timestamps")
    if not cohort_evidence:
        failures.append("no_validation_cohort_markets")
    if any(not item.complete for item in cohort_evidence):
        failures.append("incomplete_validation_cohort_markets")
    if not eligible:
        failures.append("no_eligible_closed_markets")
    missing_families = sorted(set(required_families) - set(complete_families))
    if missing_families:
        failures.append("incomplete_station_family_lifecycles")
    if required_families and not missing_families and not any(item.complete for item in eligible):
        failures.append("no_complete_market_lifecycle")

    return TapeLifecycleReport(
        passed=not failures,
        validation_start_at_utc=_iso_utc(validation_start) if validation_start else None,
        validation_end_at_utc=_iso_utc(validation_end) if validation_end else None,
        selected_validation_run_id=selected_validation_run_id,
        selected_session_ids=selected_session_ids,
        build_fingerprints=build_fingerprints,
        config_fingerprints=config_fingerprints,
        sessions=len(sessions),
        successful_sessions=successful_sessions,
        observed_start_at_utc=_iso_utc(observed_start),
        observed_end_at_utc=_iso_utc(observed_end),
        recorded_hours=round(recorded_hours, 6),
        events=events,
        partitions=len(partitions_rows),
        raw_disk_bytes=raw_disk_bytes,
        projected_daily_raw_bytes=projected_daily,
        max_daily_raw_bytes=max_daily_raw_bytes,
        retention_days=retention_days,
        projected_retention_bytes=projected_retention,
        queue_high_water=queue_high_water,
        queue_capacity=queue_capacity,
        max_rss_bytes_observed=rss_observed,
        max_rss_bytes_budget=max_rss_bytes,
        max_receipt_lag_ms_observed=receipt_lag_observed,
        max_receipt_lag_ms_budget=max_receipt_lag_ms,
        reconstruction_errors=reconstruction_errors,
        discovery_refreshes=len(discovery_refresh_rows),
        complete_discovery_refreshes=complete_refreshes,
        incomplete_discovery_refreshes=incomplete_refreshes,
        failed_discovery_refreshes=failed_refreshes,
        listing_source_counts=dict(sorted(listing_source_counts.items())),
        cohort_markets=len(cohort_evidence),
        incomplete_cohort_markets=sum(
            not item.complete for item in cohort_evidence
        ),
        eligible_closed_markets=len(eligible),
        complete_markets=sum(item.complete for item in eligible),
        required_station_families=required_families,
        complete_station_families=complete_families,
        markets=market_evidence,
        failures=tuple(failures),
    )


def _market_evidence(
    market_id: str,
    rows: list[Any],
    coverage_by_token: dict[str, list[tuple[datetime, datetime]]],
    *,
    observed_start: datetime,
    observed_end: datetime,
    max_discovery_lag_seconds: float,
    max_coverage_gap_seconds: float,
) -> MarketLifecycleEvidence:
    first = rows[0]
    listing = _parse_utc(str(first["active_from_utc"]))
    discovered = min(_parse_utc(str(row["discovered_at_utc"])) for row in rows)
    end_text = str(first["market_end_at_utc"] or "")
    failures: list[str] = []
    if not end_text:
        end = observed_end
        failures.append("missing_market_end")
    else:
        end = _parse_utc(end_text)
    source_values = {str(row["listing_timestamp_source"]) for row in rows}
    listing_source = next(iter(source_values)) if len(source_values) == 1 else "mixed"
    if listing_source == "discovery_fallback" or listing_source == "mixed":
        failures.append("listing_timestamp_not_authoritative")
    discovery_lag = max(0.0, (discovered - listing).total_seconds())
    if discovery_lag > max_discovery_lag_seconds:
        failures.append("discovery_lag_over_budget")
    if listing < observed_start:
        failures.append("market_listed_before_observation_window")
    if end > observed_end:
        failures.append("market_not_closed_in_observation_window")

    valid_tokens = 0
    maximum_gap: float | None = None
    for row in rows:
        complete, token_gap = _coverage_reaches_end(
            coverage_by_token.get(str(row["token_id"]), []),
            required_start=discovered,
            required_end=end,
            max_gap_seconds=max_coverage_gap_seconds,
        )
        if complete:
            valid_tokens += 1
        if token_gap is not None:
            maximum_gap = token_gap if maximum_gap is None else max(maximum_gap, token_gap)
    if valid_tokens != len(rows):
        failures.append("token_coverage_not_continuous_to_end")
    complete = not failures
    return MarketLifecycleEvidence(
        market_id=market_id,
        station=str(first["station"]),
        market_family=str(first["market_family"]),
        market_date=str(first["market_date"]),
        token_count=len(rows),
        listing_at_utc=_iso_utc(listing),
        listing_timestamp_source=listing_source,
        discovered_at_utc=_iso_utc(discovered),
        discovery_lag_seconds=round(discovery_lag, 6),
        market_end_at_utc=_iso_utc(end),
        valid_through_end_tokens=valid_tokens,
        maximum_coverage_gap_seconds=round(maximum_gap, 6) if maximum_gap is not None else None,
        complete=complete,
        failures=tuple(failures),
    )


def _coverage_reaches_end(
    intervals: list[tuple[datetime, datetime]],
    *,
    required_start: datetime,
    required_end: datetime,
    max_gap_seconds: float,
) -> tuple[bool, float | None]:
    if not intervals or required_end <= required_start:
        return False, None
    ordered = sorted(intervals)
    cursor = required_start
    maximum_gap = 0.0
    for start, end in ordered:
        if end < cursor:
            continue
        gap = max(0.0, (start - cursor).total_seconds())
        maximum_gap = max(maximum_gap, gap)
        if gap > max_gap_seconds:
            return False, maximum_gap
        cursor = max(cursor, end)
        if cursor >= required_end:
            return True, maximum_gap
    return False, max(maximum_gap, max(0.0, (required_end - cursor).total_seconds()))


def _empty_report(
    *,
    validation_start: datetime | None,
    validation_end: datetime | None,
    max_daily_raw_bytes: int,
    retention_days: int,
    max_receipt_lag_ms: float,
    max_rss_bytes: int,
) -> TapeLifecycleReport:
    return TapeLifecycleReport(
        passed=False,
        validation_start_at_utc=_iso_utc(validation_start) if validation_start else None,
        validation_end_at_utc=_iso_utc(validation_end) if validation_end else None,
        selected_validation_run_id=None,
        selected_session_ids=(),
        build_fingerprints=(),
        config_fingerprints=(),
        sessions=0,
        successful_sessions=0,
        observed_start_at_utc=None,
        observed_end_at_utc=None,
        recorded_hours=0.0,
        events=0,
        partitions=0,
        raw_disk_bytes=0,
        projected_daily_raw_bytes=None,
        max_daily_raw_bytes=max_daily_raw_bytes,
        retention_days=retention_days,
        projected_retention_bytes=None,
        queue_high_water=0,
        queue_capacity=0,
        max_rss_bytes_observed=0,
        max_rss_bytes_budget=max_rss_bytes,
        max_receipt_lag_ms_observed=None,
        max_receipt_lag_ms_budget=max_receipt_lag_ms,
        reconstruction_errors=0,
        discovery_refreshes=0,
        complete_discovery_refreshes=0,
        incomplete_discovery_refreshes=0,
        failed_discovery_refreshes=0,
        listing_source_counts={},
        cohort_markets=0,
        incomplete_cohort_markets=0,
        eligible_closed_markets=0,
        complete_markets=0,
        required_station_families=(),
        complete_station_families=(),
        markets=(),
        failures=("no_sessions",),
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime | None, *, name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
