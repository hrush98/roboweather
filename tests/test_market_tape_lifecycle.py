from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import (
    CollectorMetric,
    CollectorSession,
    CoverageInterval,
    CoverageState,
)
from weather_trader.tape.discovery import _tokens_from_markets
from weather_trader.tape.lifecycle import evaluate_tape_lifecycle
from weather_trader.tape.storage import SegmentStats


def test_lifecycle_report_passes_complete_market_and_resource_gates(tmp_path: Path) -> None:
    catalog = _complete_catalog(tmp_path)

    report = evaluate_tape_lifecycle(catalog)

    assert report.passed is True
    assert report.recorded_hours == 24.033333
    assert report.eligible_closed_markets == 1
    assert report.cohort_markets == 1
    assert report.incomplete_cohort_markets == 0
    assert report.discovery_refreshes == 2
    assert report.selected_validation_run_id == "validation-1"
    assert report.complete_markets == 1
    assert report.required_station_families == ("KATL:HIGH_TEMP",)
    assert report.required_station_families == report.complete_station_families
    assert report.projected_daily_raw_bytes is not None
    assert report.projected_daily_raw_bytes < report.max_daily_raw_bytes
    catalog.close()


def test_lifecycle_report_rejects_fallback_listing_short_probe_and_gap(tmp_path: Path) -> None:
    catalog = _complete_catalog(tmp_path, listing_source="discovery_fallback", coverage_end="2026-07-16T18:00:00+00:00")

    report = evaluate_tape_lifecycle(catalog, min_recorded_hours=25.0)

    assert report.passed is False
    assert "recorded_duration_below_gate" in report.failures
    assert "no_authoritative_listing_timestamps" in report.failures
    assert "no_eligible_closed_markets" in report.failures
    evidence = report.markets[0]
    assert "listing_timestamp_not_authoritative" in evidence.failures
    assert "token_coverage_not_continuous_to_end" in evidence.failures
    catalog.close()


def test_lifecycle_report_validation_start_excludes_historical_error_session(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    old_session = CollectorSession(
        session_id="historical-error",
        started_at_utc="2026-07-01T00:00:00+00:00",
        started_monotonic_ns=1,
        collector_version="test",
        hostname="host",
    )
    catalog.start_session(old_session)
    catalog.finish_session(
        old_session.session_id,
        finished_at_utc="2026-07-01T01:00:00+00:00",
        reason="error",
    )

    unscoped = evaluate_tape_lifecycle(catalog)
    scoped = evaluate_tape_lifecycle(
        catalog,
        validation_start_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert "collector_sessions_finished_with_error" in unscoped.failures
    assert scoped.passed is True
    assert scoped.selected_session_ids == ("session-1",)
    assert scoped.validation_start_at_utc == "2026-07-16T00:00:00+00:00"
    catalog.close()


def test_lifecycle_report_session_selector_keeps_selected_error_fail_closed(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    catalog.connection.execute(
        """
        update tape_collector_sessions
        set finished_at_utc = ?, finish_reason = ?
        where session_id = ?
        """,
        ("2026-07-17T12:01:00+00:00", "error", "session-1"),
    )
    catalog.connection.commit()

    report = evaluate_tape_lifecycle(
        catalog,
        validation_session_ids=("session-1",),
    )

    assert report.passed is False
    assert report.selected_session_ids == ("session-1",)
    assert "collector_sessions_finished_with_error" in report.failures
    catalog.close()


def test_lifecycle_report_validation_end_excludes_later_session_metrics_and_errors(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    catalog.connection.execute(
        """
        insert into tape_reconstruction_errors (
            session_id, token_id, event_id, receipt_sequence,
            captured_at_utc, reason
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            "session-1",
            "yes-token",
            "late-event",
            1,
            "2026-07-18T00:00:00+00:00",
            "late failure",
        ),
    )
    catalog.connection.execute(
        """
        insert into tape_collector_metrics (
            session_id, captured_at_utc, messages, events, queue_depth,
            queue_capacity, queue_high_water, rss_bytes, raw_disk_bytes,
            receipt_lag_ms, reconnect_attempt, raw_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "session-1",
            "2026-07-18T00:00:00+00:00",
            2000,
            4000,
            0,
            10_000,
            10_000,
            2 * 1024**3,
            2 * 1024**3,
            20_000.0,
            1,
            "{}",
        ),
    )
    catalog.connection.commit()

    report = evaluate_tape_lifecycle(
        catalog,
        validation_end_at=datetime(2026, 7, 17, 13, tzinfo=timezone.utc),
    )

    assert report.passed is True
    assert report.events == 2000
    assert report.queue_high_water == 500
    assert report.reconstruction_errors == 0
    catalog.close()


def test_lifecycle_report_rejects_invalid_validation_window(tmp_path: Path) -> None:
    catalog = _complete_catalog(tmp_path)

    try:
        evaluate_tape_lifecycle(
            catalog,
            validation_start_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            validation_end_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
    except ValueError as error:
        assert str(error) == "validation_end_at must be after validation_start_at"
    else:
        raise AssertionError("expected invalid validation window to fail")
    catalog.close()


def test_lifecycle_report_rejects_unknown_selected_session(tmp_path: Path) -> None:
    catalog = _complete_catalog(tmp_path)

    try:
        evaluate_tape_lifecycle(
            catalog,
            validation_session_ids=("mistyped-session",),
        )
    except ValueError as error:
        assert str(error) == "validation_session_ids not found: mistyped-session"
    else:
        raise AssertionError("expected unknown validation session to fail")
    catalog.close()


def test_lifecycle_report_passes_with_good_matured_and_right_censored_market(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    pending = MarketSnapshot(
        market_id="pending-market",
        condition_id="pending-condition",
        question="Highest temperature in Atlanta?",
        slug="atlanta-pending",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 7, 17),
        lower_f=91.0,
        upper_f=92.0,
        yes_token_id="pending-yes",
        no_token_id="pending-no",
        end_date="2026-07-18T12:00:00+00:00",
        resolution_source="Weather Underground",
        discovered_at="2026-07-16T12:03:00+00:00",
        market_family=MarketFamily.HIGH_TEMP,
        listed_at="2026-07-16T12:02:00+00:00",
    )
    tokens = _tokens_from_markets([pending])
    catalog.upsert_tokens(tokens)
    catalog.record_discovery_refresh(
        session_id="session-1",
        attempted_at_utc="2026-07-16T12:03:00+00:00",
        completed_at_utc="2026-07-16T12:03:00+00:00",
        complete=True,
        token_ids_and_markets=tuple(
            (token.token_id, token.market_id) for token in tokens
        ),
    )

    report = evaluate_tape_lifecycle(catalog)

    assert report.passed is True
    assert report.listed_markets == 2
    assert report.cohort_markets == 1
    assert report.right_censored_markets == 1
    assert report.incomplete_cohort_markets == 0
    evidence = next(
        market for market in report.markets if market.market_id == "pending-market"
    )
    assert evidence.cohort_status == "RIGHT_CENSORED"
    assert evidence.complete is False
    assert evidence.failures == ()
    catalog.close()


def test_lifecycle_report_fails_for_late_and_fallback_matured_markets(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    late = MarketSnapshot(
        market_id="late-market",
        condition_id="late-condition",
        question="Highest temperature in Atlanta?",
        slug="atlanta-late",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 7, 16),
        lower_f=91.0,
        upper_f=92.0,
        yes_token_id="late-yes",
        no_token_id="late-no",
        end_date="2026-07-17T12:00:00+00:00",
        resolution_source="Weather Underground",
        discovered_at="2026-07-16T12:10:01+00:00",
        market_family=MarketFamily.HIGH_TEMP,
        listed_at="2026-07-16T12:02:00+00:00",
    )
    fallback = MarketSnapshot(
        market_id="fallback-market",
        condition_id="fallback-condition",
        question="Highest temperature in Atlanta?",
        slug="atlanta-fallback",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 7, 17),
        lower_f=92.0,
        upper_f=93.0,
        yes_token_id="fallback-yes",
        no_token_id="fallback-no",
        end_date="2026-07-17T12:00:00+00:00",
        resolution_source="Weather Underground",
        discovered_at="2026-07-16T12:03:00+00:00",
        market_family=MarketFamily.HIGH_TEMP,
        listed_at=None,
    )
    tokens = _tokens_from_markets([late, fallback])
    catalog.upsert_tokens(tokens)
    catalog.record_discovery_refresh(
        session_id="session-1",
        attempted_at_utc="2026-07-16T12:10:01+00:00",
        completed_at_utc="2026-07-16T12:10:01+00:00",
        complete=True,
        token_ids_and_markets=tuple(
            (token.token_id, token.market_id) for token in tokens
        ),
    )

    report = evaluate_tape_lifecycle(catalog)

    assert report.listed_markets == 3
    assert report.cohort_markets == 3
    assert report.right_censored_markets == 0
    assert report.incomplete_cohort_markets == 2
    assert "incomplete_validation_cohort_markets" in report.failures
    failures_by_market = {
        market.market_id: market.failures for market in report.markets
    }
    assert "discovery_lag_over_budget" in failures_by_market["late-market"]
    assert (
        "listing_timestamp_not_authoritative"
        in failures_by_market["fallback-market"]
    )
    assert report.markets[0].cohort_status == "MATURED"
    catalog.close()


def test_lifecycle_report_fails_on_incomplete_refresh_and_mixed_build(
    tmp_path: Path,
) -> None:
    catalog = _complete_catalog(tmp_path)
    catalog.record_discovery_refresh(
        session_id="session-1",
        attempted_at_utc="2026-07-16T12:06:00+00:00",
        completed_at_utc="2026-07-16T12:06:01+00:00",
        complete=False,
        warnings=("broad_market_discovery_empty",),
    )
    second = CollectorSession(
        session_id="session-2",
        started_at_utc="2026-07-16T12:30:00+00:00",
        started_monotonic_ns=2,
        collector_version="test",
        hostname="host",
        validation_run_id="validation-1",
        build_fingerprint="build-2",
        config_fingerprint="config-1",
    )
    catalog.start_session(second)
    catalog.finish_session(
        second.session_id,
        finished_at_utc="2026-07-16T13:00:00+00:00",
        reason="max_seconds",
    )

    report = evaluate_tape_lifecycle(
        catalog,
        validation_run_id="validation-1",
    )

    assert report.incomplete_discovery_refreshes == 1
    assert "incomplete_discovery_refreshes" in report.failures
    assert "build_fingerprint_missing_or_mixed" in report.failures
    assert report.build_fingerprints == ("build-1", "build-2")
    catalog.close()


def _complete_catalog(
    tmp_path: Path,
    *,
    listing_source: str = "gamma_created_at",
    coverage_end: str = "2026-07-17T12:00:00+00:00",
) -> TapeCatalog:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    session = CollectorSession(
        session_id="session-1",
        started_at_utc="2026-07-16T11:59:00+00:00",
        started_monotonic_ns=1,
        collector_version="test",
        hostname="host",
        validation_run_id="validation-1",
        build_fingerprint="build-1",
        config_fingerprint="config-1",
    )
    catalog.start_session(session)
    market = MarketSnapshot(
        market_id="market-1",
        condition_id="condition-1",
        question="Highest temperature in Atlanta?",
        slug="atlanta-high",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 7, 16),
        lower_f=90.0,
        upper_f=91.0,
        yes_token_id="yes-token",
        no_token_id="no-token",
        end_date="2026-07-17T12:00:00+00:00",
        resolution_source="Weather Underground",
        discovered_at="2026-07-16T12:01:00+00:00",
        market_family=MarketFamily.HIGH_TEMP,
        listed_at="2026-07-16T12:00:00+00:00" if listing_source == "gamma_created_at" else None,
    )
    tokens = _tokens_from_markets([market])
    if listing_source == "discovery_fallback":
        assert all(token.listing_timestamp_source == listing_source for token in tokens)
    catalog.upsert_tokens(tokens)
    for refreshed_at in (
        "2026-07-16T12:01:00+00:00",
        "2026-07-16T12:06:00+00:00",
    ):
        catalog.record_discovery_refresh(
            session_id="session-1",
            attempted_at_utc=refreshed_at,
            completed_at_utc=refreshed_at,
            complete=True,
            token_ids_and_markets=tuple(
                (token.token_id, token.market_id) for token in tokens
            ),
        )
    for token in tokens:
        catalog.transition_coverage(
            CoverageInterval(
                session_id="session-1",
                token_id=token.token_id,
                state=CoverageState.VALID,
                started_at_utc="2026-07-16T12:01:02+00:00",
                ended_at_utc=coverage_end,
                subscription_generation=1,
            )
        )
    catalog.record_metric(
        CollectorMetric(
            session_id="session-1",
            captured_at_utc="2026-07-17T12:01:00+00:00",
            messages=1000,
            events=2000,
            queue_depth=0,
            queue_capacity=10_000,
            queue_high_water=500,
            rss_bytes=128 * 1024**2,
            raw_disk_bytes=1024**3,
            receipt_lag_ms=500.0,
            reconnect_attempt=0,
        )
    )
    segment = tmp_path / "segment.jsonl"
    segment.touch()
    catalog.record_partition(
        "session-1",
        SegmentStats(
            path=segment,
            partition_id="20260716T120000Z",
            events=2000,
            bytes_written=1024**3,
            first_event_id="first",
            last_event_id="last",
        ),
        closed_at_utc="2026-07-17T12:01:00+00:00",
    )
    catalog.finish_session(
        "session-1",
        finished_at_utc="2026-07-17T12:01:00+00:00",
        reason="max_seconds",
    )
    return catalog
