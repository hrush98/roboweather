from __future__ import annotations

from datetime import date
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
