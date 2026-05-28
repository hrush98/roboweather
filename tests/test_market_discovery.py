from __future__ import annotations

from datetime import date

from weather_trader.execution.discovery import MarketDiscoveryService
from weather_trader.execution.contracts import MarketFamily
from weather_trader.markets.polymarket_reader import WeatherMarket


def test_discovery_prefers_targeted_event_markets() -> None:
    reader = FakeReader(event_items=[{"id": "event-market"}], broad_items=[{"id": "broad-market"}])
    discovery = MarketDiscoveryService(reader=reader)

    markets = discovery.discover()

    assert [market.market_id for market in markets] == ["event-market"]
    assert reader.broad_called is False


def test_discovery_falls_back_to_broad_markets_when_events_empty() -> None:
    reader = FakeReader(event_items=[], broad_items=[{"id": "broad-market"}], missing=["missing-event"])
    discovery = MarketDiscoveryService(reader=reader)

    markets = discovery.discover()

    assert [market.market_id for market in markets] == ["broad-market"]
    assert "missing_event:missing-event" in discovery.last_warnings
    assert "targeted_event_discovery_empty" in discovery.last_warnings


def test_discovery_dedupes_market_ids() -> None:
    reader = FakeReader(event_items=[{"id": "same"}, {"id": "same"}], broad_items=[])
    discovery = MarketDiscoveryService(reader=reader)

    markets = discovery.discover()

    assert [market.market_id for market in markets] == ["same"]


def test_discovery_preserves_gamma_active_flag() -> None:
    reader = FakeReader(event_items=[{"id": "inactive", "active": False}], broad_items=[])
    discovery = MarketDiscoveryService(reader=reader)

    markets = discovery.discover()

    assert markets[0].active is False


def test_global_discovery_targets_international_events_and_stations() -> None:
    reader = FakeReader(event_items=[{"id": "global-market", "city": "Tokyo", "station": "RJTT"}], broad_items=[])
    discovery = MarketDiscoveryService(reader=reader)

    markets = discovery.discover(market_scope="global")

    assert markets[0].station == "RJTT"
    assert all(target.city_slug in {"tokyo", "hong-kong", "london", "seoul", "shanghai", "singapore", "mexico-city", "paris", "amsterdam", "munich"} for target in reader.targets)
    assert {target.market_family for target in reader.targets} == {MarketFamily.HIGH_TEMP, MarketFamily.LOW_TEMP}


class FakeReader:
    def __init__(self, event_items: list[dict], broad_items: list[dict], missing: list[str] | None = None) -> None:
        self.event_items = event_items
        self.broad_items = broad_items
        self.missing = missing or []
        self.broad_called = False

    def _fetch_weather_event_markets(self, targets):
        self.targets = targets
        return self.event_items, self.missing

    def _fetch_gamma_markets(self, limit: int):
        self.broad_called = True
        return self.broad_items

    def _parse_weather_market(self, item: dict) -> WeatherMarket | None:
        return WeatherMarket(
            market_id=item["id"],
            question="Will the highest temperature in Seattle be between 58-59°F on May 14?",
            slug="highest-temperature-in-seattle-on-may-14-2026-58-59f",
            city=item.get("city", "Seattle"),
            station=item.get("station", "KSEA"),
            threshold_f=58.0,
            lower_f=58.0,
            upper_f=59.0,
            best_bid_yes=0.4,
            best_ask_yes=0.45,
            end_date="2026-05-14T12:00:00Z",
            resolution_source="https://www.wunderground.com/history/daily/us/wa/seatac/KSEA",
            yes_token_id="yes-token",
            no_token_id="no-token",
            market_date=date(2026, 5, 14),
        )
