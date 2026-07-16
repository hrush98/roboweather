from __future__ import annotations

from datetime import date
import asyncio
from pathlib import Path

import pytest

from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.collector import collect_market_tape
from weather_trader.tape.contracts import CollectorSession, TokenOutcome
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


def test_tape_discovery_forces_full_scope_and_reports_completeness() -> None:
    underlying = FakeMarketDiscovery([market()], warnings=[])
    result = TapeDiscoveryService(underlying).discover(market_limit=123)

    assert len(result.tokens) == 2
    assert result.complete is True
    assert underlying.call == {"limit": 123, "validate_stations": True, "market_scope": "all"}


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
    assert report.healthy is True
    assert report.events == 1
    assert report.partitions == 1
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


class FakeTransport:
    def __init__(self, messages) -> None:
        self.messages = messages
        self.subscriptions = []

    async def stream(self, token_ids):
        self.subscriptions.append(token_ids)
        for message in self.messages:
            yield message


class FailingTransport:
    async def stream(self, token_ids):
        if False:
            yield {}
        raise RuntimeError("socket failed")


class RecoveringTransport:
    def __init__(self) -> None:
        self.attempts = 0

    async def stream(self, token_ids):
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


class SilentTransport:
    async def stream(self, token_ids):
        await asyncio.sleep(1)
        if False:
            yield {}
