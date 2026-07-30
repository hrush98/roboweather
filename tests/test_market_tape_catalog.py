from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import asyncio
from pathlib import Path
import sqlite3

import pytest

from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.collector import collect_market_tape
from weather_trader.tape.contracts import (
    CollectorMetric,
    CollectorSession,
    CoverageInterval,
    CoverageState,
    TokenOutcome,
)
from weather_trader.tape.discovery import TapeDiscoveryResult, TapeDiscoveryService, _tokens_from_markets
from weather_trader.tape.health import evaluate_tape_health
from weather_trader.tape.subscriptions import SubscriptionRegistry
from weather_trader.tape.storage import iter_segment


def market(*, market_id: str = "market-1", active: bool = True) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id="condition-1",
        question="Will Atlanta reach 90°F?",
        slug="atlanta-90",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 7, 16),
        lower_f=90.0,
        upper_f=91.0,
        yes_token_id=f"{market_id}-yes",
        no_token_id=f"{market_id}-no",
        end_date="2026-07-17T00:00:00Z",
        resolution_source="IEM ASOS KATL",
        discovered_at="2026-07-16T12:00:00+00:00",
        active=active,
        market_family=MarketFamily.HIGH_TEMP,
        listed_at="2026-07-16T11:58:00+00:00",
    )


def session() -> CollectorSession:
    return CollectorSession(
        session_id="session-1",
        started_at_utc="2026-07-16T12:00:00+00:00",
        started_monotonic_ns=100,
        collector_version="test",
        hostname="host",
    )


def test_market_conversion_builds_yes_no_siblings_and_excludes_inactive() -> None:
    tokens = _tokens_from_markets([market(), market(market_id="inactive", active=False)])

    assert [token.token_id for token in tokens] == ["market-1-yes", "market-1-no"]
    assert [token.outcome for token in tokens] == [TokenOutcome.YES, TokenOutcome.NO]
    assert tokens[0].sibling_token_id == "market-1-no"
    assert tokens[1].sibling_token_id == "market-1-yes"
    assert tokens[0].market_end_at_utc == "2026-07-17T00:00:00Z"
    assert tokens[0].active_from_utc == "2026-07-16T11:58:00+00:00"
    assert tokens[0].listing_timestamp_source == "gamma_created_at"


def test_tape_discovery_forces_full_scope_and_reports_completeness() -> None:
    underlying = FakeMarketDiscovery([market()], warnings=[])
    result = TapeDiscoveryService(underlying).discover(market_limit=123)

    assert len(result.tokens) == 2
    assert result.complete is True
    assert underlying.call == {
        "limit": 123,
        "validate_stations": True,
        "market_scope": "all",
        "include_future": True,
    }


def test_catalog_persists_registry_and_subscription_generations(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    tokens = _tokens_from_markets([market()])
    assert catalog.upsert_tokens(tokens) == 2

    first = catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-no", "market-1-yes"),
        effective_at_utc="2026-07-16T12:00:01+00:00",
        reason="initial",
    )
    unchanged = catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes", "market-1-no"),
        effective_at_utc="2026-07-16T12:01:00+00:00",
        reason="refresh",
    )

    assert first is not None and first.generation == 1
    assert first.token_ids == ("market-1-no", "market-1-yes")
    assert unchanged is None
    assert catalog.latest_subscription("session-1") == first
    states = catalog.connection.execute(
        "select distinct subscription_state from tape_tokens"
    ).fetchall()
    assert [row[0] for row in states] == ["SUBSCRIBED"]
    catalog.close()


def test_catalog_migrates_existing_decision_join_rows_for_termination_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table tape_decision_joins (
            decision_id text not null,
            hypothesis_version text not null,
            token_id text not null,
            session_id text not null,
            quote_ready_at_utc text not null,
            first_visible_event_id text,
            first_visible_event_at_utc text,
            coverage_valid integer not null,
            invalid_reason text,
            pre_signal_seconds real not null,
            reconstruction_hash text,
            raw_json text not null,
            primary key (decision_id, hypothesis_version)
        )
        """
    )
    connection.commit()
    connection.close()

    catalog = TapeCatalog(path)
    columns = {
        row["name"]
        for row in catalog.connection.execute("pragma table_info(tape_decision_joins)")
    }

    assert "quote_termination_at_utc" in columns
    assert "termination_reconstruction_hash" in columns
    assert "coverage_interval_id" in columns
    assert "source_ref" in columns
    catalog.close()


def test_subscription_registry_uses_catalog_universe_without_retiring_missing_tokens(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    first_discovery = FakeTapeDiscovery(_tokens_from_markets([market()]))
    registry = SubscriptionRegistry(catalog, first_discovery)

    first = registry.refresh(session_id="session-1", effective_at_utc="2026-07-16T12:00:01+00:00")
    second_discovery = FakeTapeDiscovery([], complete=False)
    registry.discovery = second_discovery
    second = registry.refresh(session_id="session-1", effective_at_utc="2026-07-16T12:05:00+00:00")

    assert first.subscription is not None
    assert second.discovery.complete is False
    assert second.subscription is None
    assert {token.token_id for token in catalog.active_tokens()} == {"market-1-yes", "market-1-no"}
    catalog.close()


def test_complete_refresh_retires_missing_tokens_and_emits_new_generation(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    registry = SubscriptionRegistry(catalog, FakeTapeDiscovery(_tokens_from_markets([market()])))
    registry.refresh(session_id="session-1", effective_at_utc="2026-07-16T12:00:01+00:00")
    registry.discovery = FakeTapeDiscovery(_tokens_from_markets([market(market_id="market-2")]))

    refreshed = registry.refresh(session_id="session-1", effective_at_utc="2026-07-16T12:05:00+00:00")

    assert refreshed.subscription is not None
    assert refreshed.subscription.generation == 2
    assert refreshed.subscription.token_ids == ("market-2-no", "market-2-yes")
    retired = catalog.connection.execute(
        "select count(*) from tape_tokens where subscription_state = 'RETIRED'"
    ).fetchone()[0]
    assert retired == 2
    catalog.close()


def test_collector_records_bounded_generation_and_requires_full_book_for_valid_coverage(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    discovery = FakeTapeDiscovery(_tokens_from_markets([market()]))
    transport = FakeTransport(
        [
            {"event_type": "book", "asset_id": "market-1-yes", "market": "market-1", "bids": [], "asks": []},
            {"event_type": "last_trade_price", "asset_id": "market-1-yes", "market": "market-1", "price": "0.4"},
        ]
    )

    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=discovery,
            transport=transport,
            refresh_seconds=60,
            queue_size=1,
            max_messages=2,
        )
    )

    events = list(iter_segment(stats.segment_path))
    assert stats.messages == 2
    assert stats.events == 2
    assert stats.queue_high_water <= 1
    assert transport.subscriptions == [("market-1-no", "market-1-yes")]
    assert events[0].coverage_state.value == "RESYNCING"
    assert events[1].coverage_state.value == "VALID"
    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals where token_id = 'market-1-yes' order by id"
        )
    ]
    assert states == ["RESYNCING", "VALID", "CLOSED"]
    assert catalog.connection.execute("select count(*) from tape_book_checkpoints").fetchone()[0] == 1
    catalog.close()


def test_collector_records_telemetry_while_feed_is_idle(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=IdleAfterBookTransport(),
            refresh_seconds=1,
            max_seconds=0.2,
            telemetry_seconds=0.05,
        )
    )

    metrics = catalog.connection.execute(
        "select * from tape_collector_metrics where session_id = ? order by id",
        (stats.session_id,),
    ).fetchall()

    assert len(metrics) >= 3
    assert all(row["events"] == 1 for row in metrics)
    catalog.close()


def test_collector_updates_subscription_without_gapping_existing_tokens(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    initial = _tokens_from_markets([market()])
    expanded = _tokens_from_markets([market(), market(market_id="market-2")])
    discovery = SequencedTapeDiscovery(initial, expanded)
    transport = DynamicTransport()

    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=discovery,
            transport=transport,
            refresh_seconds=0.05,
            max_seconds=0.25,
            telemetry_seconds=0.05,
        )
    )

    existing_states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals where token_id = 'market-1-yes' order by id"
        )
    ]
    added_states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals where token_id = 'market-2-yes' order by id"
        )
    ]

    assert stats.subscription_generations == 2
    assert transport.connections == 1
    assert transport.updates[0].added_token_ids == ("market-2-no", "market-2-yes")
    assert transport.updates[0].removed_token_ids == ()
    assert existing_states == ["RESYNCING", "VALID", "CLOSED"]
    assert added_states == ["RESYNCING", "VALID", "CLOSED"]
    catalog.close()


def test_active_health_allows_open_segment_before_first_rotation(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    captured = datetime.now(timezone.utc).isoformat()
    catalog.upsert_tokens(_tokens_from_markets([market()]))
    generation = catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes",),
        effective_at_utc=captured,
        reason="initial",
    )
    assert generation is not None
    catalog.insert_coverage_interval(
        CoverageInterval(
            session_id="session-1",
            token_id="market-1-yes",
            state=CoverageState.VALID,
            started_at_utc=captured,
            ended_at_utc=None,
            subscription_generation=1,
            reason="initial_full_book_received",
        )
    )
    catalog.record_metric(
        CollectorMetric(
            session_id="session-1",
            captured_at_utc=captured,
            messages=1,
            events=2,
            queue_depth=0,
            queue_capacity=10_000,
            queue_high_water=2,
            rss_bytes=100_000_000,
            raw_disk_bytes=1024,
            receipt_lag_ms=100.0,
            reconnect_attempt=0,
        )
    )

    report = evaluate_tape_health(catalog, now=datetime.fromisoformat(captured))

    assert report.healthy is True
    assert report.partitions == 0
    assert report.subscription_generation == 1
    assert report.subscribed_tokens == 1
    assert report.valid_subscribed_tokens == 1
    catalog.close()


def test_health_accepts_retained_valid_tokens_and_ignores_removed_members(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    catalog.upsert_tokens(_tokens_from_markets([market(), market(market_id="market-2")]))
    catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes", "market-1-no"),
        effective_at_utc="2026-07-16T12:00:01+00:00",
        reason="initial",
    )
    for token_id in ("market-1-yes", "market-1-no"):
        catalog.insert_coverage_interval(
            CoverageInterval(
                session_id="session-1",
                token_id=token_id,
                state=CoverageState.VALID,
                started_at_utc="2026-07-16T12:00:02+00:00",
                ended_at_utc=None,
                subscription_generation=1,
                reason="initial_full_book_received",
            )
        )
    catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes", "market-2-yes"),
        effective_at_utc="2026-07-16T12:01:00+00:00",
        reason="refresh",
    )
    catalog.transition_coverage(
        CoverageInterval(
            session_id="session-1",
            token_id="market-2-yes",
            state=CoverageState.VALID,
            started_at_utc="2026-07-16T12:01:01+00:00",
            ended_at_utc=None,
            subscription_generation=2,
            reason="initial_full_book_received",
        )
    )
    captured = datetime.now(timezone.utc).isoformat()
    catalog.record_metric(
        CollectorMetric(
            session_id="session-1",
            captured_at_utc=captured,
            messages=2,
            events=2,
            queue_depth=0,
            queue_capacity=10_000,
            queue_high_water=1,
            rss_bytes=1,
            raw_disk_bytes=1,
            receipt_lag_ms=None,
            reconnect_attempt=0,
        )
    )

    report = evaluate_tape_health(
        catalog, verify_segments=False, now=datetime.fromisoformat(captured)
    )

    assert report.healthy is True
    assert report.subscription_generation == 2
    assert report.subscribed_tokens == 2
    assert report.valid_subscribed_tokens == 2
    catalog.close()


def test_health_requires_new_full_book_when_token_is_removed_then_readded(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    catalog.upsert_tokens(_tokens_from_markets([market(), market(market_id="market-2")]))
    catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes",),
        effective_at_utc="2026-07-16T12:00:01+00:00",
        reason="initial",
    )
    catalog.insert_coverage_interval(
        CoverageInterval(
            session_id="session-1",
            token_id="market-1-yes",
            state=CoverageState.VALID,
            started_at_utc="2026-07-16T12:00:02+00:00",
            ended_at_utc="2026-07-16T12:01:00+00:00",
            subscription_generation=1,
            reason="initial_full_book_received",
        )
    )
    catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-2-yes",),
        effective_at_utc="2026-07-16T12:01:00+00:00",
        reason="refresh",
    )
    catalog.reconcile_subscription(
        "session-1",
        token_ids=("market-1-yes", "market-2-yes"),
        effective_at_utc="2026-07-16T12:02:00+00:00",
        reason="refresh",
    )
    catalog.insert_coverage_interval(
        CoverageInterval(
            session_id="session-1",
            token_id="market-2-yes",
            state=CoverageState.VALID,
            started_at_utc="2026-07-16T12:02:01+00:00",
            ended_at_utc=None,
            subscription_generation=3,
            reason="initial_full_book_received",
        )
    )
    captured = datetime.now(timezone.utc).isoformat()
    catalog.record_metric(
        CollectorMetric(
            session_id="session-1",
            captured_at_utc=captured,
            messages=2,
            events=2,
            queue_depth=0,
            queue_capacity=10_000,
            queue_high_water=1,
            rss_bytes=1,
            raw_disk_bytes=1,
            receipt_lag_ms=None,
            reconnect_attempt=0,
        )
    )

    report = evaluate_tape_health(
        catalog, verify_segments=False, now=datetime.fromisoformat(captured)
    )

    assert report.healthy is False
    assert report.subscription_generation == 3
    assert report.subscribed_tokens == 2
    assert report.valid_subscribed_tokens == 1
    assert "subscribed_tokens_without_valid_full_book" in report.failures
    catalog.close()


def test_terminal_health_accepts_closed_tokens_that_had_valid_full_books(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=FakeTransport(
                [
                    {
                        "event_type": "book",
                        "asset_id": token_id,
                        "market": "market-1",
                        "bids": [],
                        "asks": [],
                    }
                    for token_id in ("market-1-yes", "market-1-no")
                ]
            ),
            max_messages=2,
            telemetry_seconds=0,
        )
    )

    report = evaluate_tape_health(catalog)

    assert stats.events == 2
    assert report.finish_reason == "max_messages"
    assert report.healthy is True
    assert report.subscribed_tokens == 2
    assert report.valid_subscribed_tokens == 2
    catalog.close()


def test_health_fails_closed_on_zero_messages_events_and_generations(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    catalog.start_session(session())
    captured = datetime.now(timezone.utc).isoformat()
    catalog.record_metric(
        CollectorMetric(
            session_id="session-1",
            captured_at_utc=captured,
            messages=0,
            events=0,
            queue_depth=0,
            queue_capacity=10_000,
            queue_high_water=0,
            rss_bytes=1,
            raw_disk_bytes=0,
            receipt_lag_ms=None,
            reconnect_attempt=0,
        )
    )

    report = evaluate_tape_health(
        catalog, verify_segments=False, now=datetime.fromisoformat(captured)
    )

    assert report.healthy is False
    assert "zero_messages" in report.failures
    assert "zero_events" in report.failures
    assert "missing_subscription_generation" in report.failures
    catalog.close()


def test_collector_propagates_transport_failure_and_closes_session(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")

    with pytest.raises(RuntimeError, match="socket failed"):
        asyncio.run(
            collect_market_tape(
                catalog,
                raw_directory=tmp_path / "raw",
                discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
                transport=FailingTransport(),
                max_seconds=1,
                max_reconnect_attempts=0,
            )
        )

    row = catalog.connection.execute(
        "select finish_reason, finished_at_utc from tape_collector_sessions"
    ).fetchone()
    assert row["finish_reason"] == "error"
    assert row["finished_at_utc"] is not None
    catalog.close()


def test_collector_fails_closed_when_bounded_session_captures_no_events(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")

    with pytest.raises(RuntimeError, match="zero token events"):
        asyncio.run(
            collect_market_tape(
                catalog,
                raw_directory=tmp_path / "raw",
                discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
                transport=SilentTransport(),
                max_seconds=0.01,
                max_reconnect_attempts=0,
            )
        )

    assert catalog.connection.execute(
        "select finish_reason from tape_collector_sessions"
    ).fetchone()[0] == "error"
    catalog.close()


def test_collector_reconnects_through_explicit_gap_and_health_verifies_segments(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    transport = RecoveringTransport()
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=transport,
            max_messages=1,
            reconnect_initial_seconds=0,
            max_reconnect_attempts=1,
            telemetry_seconds=0,
        )
    )

    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals where token_id = 'market-1-yes' order by id"
        )
    ]
    report = evaluate_tape_health(catalog)

    assert stats.reconnects == 1
    assert states == ["RESYNCING", "GAPPED", "RECONNECTING", "RESYNCING", "VALID", "CLOSED"]
    assert stats.segment_paths
    assert catalog.connection.execute("select count(*) from tape_raw_partitions").fetchone()[0] == 1
    assert catalog.connection.execute("select count(*) from tape_collector_metrics").fetchone()[0] >= 1
    assert report.healthy is False
    assert report.events == 1
    assert report.partitions == 1
    assert report.subscription_generation == 1
    assert report.subscribed_tokens == 2
    assert report.valid_subscribed_tokens == 1
    assert "subscribed_tokens_without_valid_full_book" in report.failures
    catalog.close()


def test_collector_resets_retry_budget_after_a_connection_delivers_data(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=RepeatedlyInterruptedTransport(),
            max_messages=3,
            reconnect_initial_seconds=0,
            max_reconnect_attempts=1,
            telemetry_seconds=0,
        )
    )

    events = [
        event
        for path in stats.segment_paths
        for event in iter_segment(path)
    ]
    session = catalog.connection.execute(
        "select finish_reason from tape_collector_sessions where session_id = ?",
        (stats.session_id,),
    ).fetchone()

    assert stats.reconnects == 2
    assert stats.messages == 3
    assert stats.events == 3
    assert [event.receipt_sequence for event in events] == [1, 2, 3]
    assert session["finish_reason"] == "max_messages"
    catalog.close()


def test_collector_reconnects_and_resyncs_instead_of_accepting_stale_feed_data(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=StaleThenFreshTransport(),
            max_messages=2,
            reconnect_initial_seconds=0,
            max_reconnect_attempts=1,
            max_receipt_lag_seconds=10,
            telemetry_seconds=0,
        )
    )

    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals "
            "where token_id = 'market-1-yes' order by id"
        )
    ]
    events = [
        event
        for path in stats.segment_paths
        for event in iter_segment(path)
    ]
    maximum_lag = catalog.connection.execute(
        "select max(receipt_lag_ms) from tape_collector_metrics "
        "where session_id = ?",
        (stats.session_id,),
    ).fetchone()[0]

    assert stats.reconnects == 1
    assert stats.messages == 2
    assert stats.events == 1
    assert states == [
        "RESYNCING",
        "GAPPED",
        "RECONNECTING",
        "RESYNCING",
        "VALID",
        "CLOSED",
    ]
    assert len(events) == 1
    assert events[0].coverage_state.value == "RESYNCING"
    assert maximum_lag > 10_000
    catalog.close()


def test_old_full_book_timestamp_seeds_coverage_without_receipt_lag_reconnect(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=OldTimestampBookTransport(),
            max_messages=1,
            max_receipt_lag_seconds=10,
            telemetry_seconds=0,
        )
    )

    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals "
            "where token_id = 'market-1-yes' order by id"
        )
    ]
    receipt_lags = [
        row[0]
        for row in catalog.connection.execute(
            "select receipt_lag_ms from tape_collector_metrics "
            "where session_id = ?",
            (stats.session_id,),
        )
    ]

    assert stats.reconnects == 0
    assert stats.events == 1
    assert states == ["RESYNCING", "VALID", "CLOSED"]
    assert receipt_lags
    assert all(value is None for value in receipt_lags)
    catalog.close()


def test_pre_book_delta_stays_resyncing_without_reconstruction_error(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=PreBookDeltaTransport(),
            max_messages=2,
            telemetry_seconds=0,
        )
    )

    events = [
        event
        for path in stats.segment_paths
        for event in iter_segment(path)
    ]
    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals "
            "where token_id = 'market-1-yes' order by id"
        )
    ]

    assert [event.event_type for event in events] == ["price_change", "book"]
    assert [event.coverage_state.value for event in events] == [
        "RESYNCING",
        "RESYNCING",
    ]
    assert states == ["RESYNCING", "VALID", "CLOSED"]
    assert catalog.connection.execute(
        "select count(*) from tape_reconstruction_errors"
    ).fetchone()[0] == 0
    catalog.close()


def test_malformed_delta_after_valid_book_records_reconstruction_error(
    tmp_path: Path,
) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=MalformedDeltaAfterBookTransport(),
            max_messages=2,
            telemetry_seconds=0,
        )
    )

    error = catalog.connection.execute(
        "select reason from tape_reconstruction_errors"
    ).fetchone()
    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals "
            "where token_id = 'market-1-yes' order by id"
        )
    ]

    assert error["reason"] == "malformed_price_change"
    assert states == ["RESYNCING", "VALID", "GAPPED", "CLOSED"]
    catalog.close()


def test_malformed_book_is_accounted_and_never_marks_coverage_valid(tmp_path: Path) -> None:
    catalog = TapeCatalog(tmp_path / "catalog.sqlite")
    stats = asyncio.run(
        collect_market_tape(
            catalog,
            raw_directory=tmp_path / "raw",
            discovery=FakeTapeDiscovery(_tokens_from_markets([market()])),
            transport=FakeTransport(
                [{"event_type": "book", "asset_id": "market-1-yes", "bids": []}]
            ),
            max_messages=1,
        )
    )
    states = [
        row[0]
        for row in catalog.connection.execute(
            "select state from tape_coverage_intervals where token_id = 'market-1-yes' order by id"
        )
    ]
    report = evaluate_tape_health(catalog)

    assert stats.events == 1
    assert states == ["RESYNCING", "CLOSED"]
    assert report.reconstruction_errors == 1
    assert report.healthy is False
    assert "reconstruction_errors" in report.failures
    catalog.close()


class FakeMarketDiscovery:
    def __init__(self, markets: list[MarketSnapshot], warnings: list[str]) -> None:
        self.markets = markets
        self.last_warnings = warnings
        self.call: dict[str, object] = {}

    def discover(self, **kwargs):
        self.call = kwargs
        return self.markets


class FakeTapeDiscovery:
    def __init__(self, tokens, complete: bool = True) -> None:
        self.result = TapeDiscoveryResult(tuple(tokens), (), complete)

    def discover(self, *, market_limit: int):
        return self.result


class SequencedTapeDiscovery:
    def __init__(self, initial, expanded) -> None:
        self.results = [
            TapeDiscoveryResult(tuple(initial), (), True),
            TapeDiscoveryResult(tuple(expanded), (), True),
        ]
        self.calls = 0

    def discover(self, *, market_limit: int):
        index = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[index]


class FakeTransport:
    def __init__(self, messages) -> None:
        self.messages = messages
        self.subscriptions = []

    async def stream(self, token_ids, *, subscription_updates=None):
        self.subscriptions.append(token_ids)
        for message in self.messages:
            yield message


class FailingTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        if False:
            yield {}
        raise RuntimeError("socket failed")


class RecoveringTransport:
    def __init__(self) -> None:
        self.attempts = 0

    async def stream(self, token_ids, *, subscription_updates=None):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("transient socket failure")
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "bids": [],
            "asks": [],
        }


class RepeatedlyInterruptedTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bids": [],
            "asks": [],
        }
        raise RuntimeError("connection closed")


class StaleThenFreshTransport:
    def __init__(self) -> None:
        self.attempts = 0

    async def stream(self, token_ids, *, subscription_updates=None):
        self.attempts += 1
        timestamp = datetime.now(timezone.utc)
        if self.attempts == 1:
            timestamp -= timedelta(seconds=30)
        yield {
            "event_type": (
                "price_change"
                if self.attempts == 1
                else "book"
            ),
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": timestamp.isoformat(),
            "price": "0.5",
            "bids": [],
            "asks": [],
        }


class OldTimestampBookTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": (
                datetime.now(timezone.utc) - timedelta(seconds=30)
            ).isoformat(),
            "bids": [],
            "asks": [],
        }


class PreBookDeltaTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        yield {
            "event_type": "price_change",
            "market": "market-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price_changes": [
                {
                    "asset_id": "market-1-yes",
                    "price": "0.5",
                    "size": "10",
                    "side": "BUY",
                }
            ],
        }
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bids": [],
            "asks": [],
        }


class MalformedDeltaAfterBookTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bids": [],
            "asks": [],
        }
        yield {
            "event_type": "price_change",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": "0.5",
            "size": "10",
        }


class SilentTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        await asyncio.sleep(1)
        if False:
            yield {}


class IdleAfterBookTransport:
    async def stream(self, token_ids, *, subscription_updates=None):
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "bids": [],
            "asks": [],
        }
        await asyncio.sleep(1)


class DynamicTransport:
    def __init__(self) -> None:
        self.connections = 0
        self.updates = []

    async def stream(self, token_ids, *, subscription_updates=None):
        self.connections += 1
        yield {
            "event_type": "book",
            "asset_id": "market-1-yes",
            "market": "market-1",
            "bids": [],
            "asks": [],
        }
        update = await subscription_updates.get()
        self.updates.append(update)
        subscription_updates.task_done()
        yield {
            "event_type": "book",
            "asset_id": "market-2-yes",
            "market": "market-2",
            "bids": [],
            "asks": [],
        }
        await asyncio.sleep(1)
