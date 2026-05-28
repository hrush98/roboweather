from __future__ import annotations

from datetime import date

import requests

from weather_trader.markets.polymarket_reader import PolymarketReader, WeatherEventTarget
from weather_trader.execution.contracts import MarketFamily


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200) -> None:
        self.payload = [] if payload is None else payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.payload


def test_market_parser_extracts_city_and_threshold() -> None:
    reader = PolymarketReader()
    market = reader._parse_weather_market(
        {
            "id": "1",
            "question": "Will the highest temperature in Atlanta be between 84-85°F on May 5?",
            "slug": "highest-temperature-in-atlanta-on-may-5-2026-84-85f",
            "bestBid": "0.42",
            "bestAsk": "0.47",
            "clobTokenIds": '["yes-token", "no-token"]',
            "endDate": "2026-05-05T23:59:00Z",
            "resolutionSource": "ASOS",
        }
    )
    assert market is not None
    assert market.station == "KATL"
    assert market.threshold_f == 84.0
    assert market.lower_f == 84.0
    assert market.upper_f == 85.0
    assert market.yes_token_id == "yes-token"
    assert str(market.market_date) == "2026-05-05"
    assert market.market_family == MarketFamily.HIGH_TEMP


def test_market_parser_extracts_lowest_temperature_market() -> None:
    reader = PolymarketReader()
    market = reader._parse_weather_market(
        {
            "id": "low-1",
            "question": "Will the lowest temperature in Miami be 72°F or below on May 23?",
            "slug": "lowest-temperature-in-miami-on-may-23-2026-72f-or-below",
            "bestBid": "0.42",
            "bestAsk": "0.47",
            "clobTokenIds": '["yes-token", "no-token"]',
            "resolutionSource": "ASOS",
        }
    )

    assert market is not None
    assert market.station == "KMIA"
    assert market.upper_f == 72.0
    assert market.market_family == MarketFamily.LOW_TEMP


def test_market_parser_extracts_international_celsius_buckets() -> None:
    reader = PolymarketReader()

    between = reader._parse_weather_market(
        {
            "id": "global-1",
            "question": "Will the highest temperature in Tokyo be between 28-29°C on May 28?",
            "slug": "highest-temperature-in-tokyo-on-may-28-2026-28-29c",
            "bestAsk": "0.47",
            "clobTokenIds": '["yes-token", "no-token"]',
        }
    )
    tail = reader._parse_weather_market(
        {
            "id": "global-2",
            "question": "Will the lowest temperature in Hong Kong be 24°C or below on May 28?",
            "slug": "lowest-temperature-in-hong-kong-on-may-28-2026-24c-or-below",
            "bestAsk": "0.52",
            "clobTokenIds": '["yes-token", "no-token"]',
        }
    )
    exact = reader._parse_weather_market(
        {
            "id": "global-3",
            "question": "Will the highest temperature in London be 18°C on May 28?",
            "slug": "highest-temperature-in-london-on-may-28-2026-18c",
            "bestAsk": "0.35",
            "clobTokenIds": '["yes-token", "no-token"]',
        }
    )

    assert between is not None
    assert between.station == "RJTT"
    assert between.lower_f == 28.0
    assert between.upper_f == 29.0
    assert tail is not None
    assert tail.station == "VHHH"
    assert tail.upper_f == 24.0
    assert tail.market_family == MarketFamily.LOW_TEMP
    assert exact is not None
    assert exact.lower_f == 18.0
    assert exact.upper_f == 18.0


def test_market_parser_maps_seattle_and_houston() -> None:
    reader = PolymarketReader()

    seattle = reader._parse_weather_market(
        {
            "id": "2",
            "question": "Will the highest temperature in Seattle be between 66-67°F on May 6?",
            "slug": "highest-temperature-in-seattle-on-may-6-2026-66-67f",
            "bestBid": "0.31",
            "bestAsk": "0.36",
            "clobTokenIds": '["yes-token", "no-token"]',
            "endDate": "2026-05-06T23:59:00Z",
            "resolutionSource": "ASOS",
        }
    )
    houston = reader._parse_weather_market(
        {
            "id": "3",
            "question": "Will the highest temperature in Houston be 91°F or higher on May 6?",
            "slug": "highest-temperature-in-houston-on-may-6-2026-91f-or-higher",
            "bestBid": "0.22",
            "bestAsk": "0.29",
            "clobTokenIds": '["yes-token", "no-token"]',
            "endDate": "2026-05-06T23:59:00Z",
            "resolutionSource": "ASOS",
        }
    )

    assert seattle is not None
    assert seattle.station == "KSEA"
    assert houston is not None
    assert houston.station == "KHOU"


def test_market_parser_accepts_ask_only_market() -> None:
    reader = PolymarketReader()

    market = reader._parse_weather_market(
        {
            "id": "4",
            "question": "Will the highest temperature in Seattle be between 70-71°F on May 14?",
            "slug": "highest-temperature-in-seattle-on-may-14-2026-70-71f",
            "bestBid": None,
            "bestAsk": "0.01",
            "clobTokenIds": '["yes-token", "no-token"]',
            "resolutionSource": "https://www.wunderground.com/history/daily/us/wa/seatac/KSEA",
        }
    )

    assert market is not None
    assert market.station == "KSEA"
    assert market.best_bid_yes == 0.0
    assert market.best_ask_yes == 0.01


def test_market_parser_uses_resolution_station_for_denver_buckley() -> None:
    reader = PolymarketReader()

    market = reader._parse_weather_market(
        {
            "id": "5",
            "question": "Will the highest temperature in Denver be between 60-61°F on May 14?",
            "slug": "highest-temperature-in-denver-on-may-14-2026-60-61f",
            "bestBid": "0.5",
            "bestAsk": "0.55",
            "clobTokenIds": '["yes-token", "no-token"]',
            "resolutionSource": "https://www.wunderground.com/history/daily/us/co/aurora/KBKF",
        }
    )

    assert market is not None
    assert market.station == "KBKF"


def test_weather_event_target_builds_rolling_slug() -> None:
    target = WeatherEventTarget("seattle", date(2026, 5, 14))

    assert target.event_slug == "highest-temperature-in-seattle-on-may-14-2026"


def test_low_weather_event_target_builds_rolling_slug() -> None:
    target = WeatherEventTarget("miami", date(2026, 5, 23), MarketFamily.LOW_TEMP)

    assert target.event_slug == "lowest-temperature-in-miami-on-may-23-2026"


def test_weather_event_fetch_returns_open_nested_markets(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        assert url.endswith("/events/slug/highest-temperature-in-seattle-on-may-14-2026")
        return FakeResponse(
            {
                "markets": [
                    {"id": "1", "question": "closed", "closed": True},
                    {
                        "id": "2",
                        "question": "Will the highest temperature in Seattle be between 58-59°F on May 14?",
                        "slug": "highest-temperature-in-seattle-on-may-14-2026-58-59f",
                        "bestBid": "0.42",
                        "bestAsk": "0.45",
                        "clobTokenIds": '["yes-token", "no-token"]',
                        "resolutionSource": "https://www.wunderground.com/history/daily/us/wa/seatac/KSEA",
                        "closed": False,
                    },
                ]
            }
        )

    monkeypatch.setattr("weather_trader.markets.polymarket_reader.requests.get", fake_get)
    reader = PolymarketReader()

    items, missing = reader._fetch_weather_event_markets([WeatherEventTarget("seattle", date(2026, 5, 14))])

    assert missing == []
    assert [item["id"] for item in items] == ["2"]


def test_weather_event_fetch_records_missing_404(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return FakeResponse({}, status_code=404)

    monkeypatch.setattr("weather_trader.markets.polymarket_reader.requests.get", fake_get)
    reader = PolymarketReader()

    items, missing = reader._fetch_weather_event_markets([WeatherEventTarget("seattle", date(2026, 5, 14))])

    assert items == []
    assert missing == ["highest-temperature-in-seattle-on-may-14-2026"]


def test_gamma_market_fetch_retries_transient_timeout(monkeypatch) -> None:
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ReadTimeout("gamma stalled")
        return FakeResponse()

    monkeypatch.setattr("weather_trader.markets.polymarket_reader.requests.get", fake_get)
    monkeypatch.setattr("weather_trader.markets.polymarket_reader.time.sleep", lambda seconds: None)

    reader = PolymarketReader(max_retries=2, retry_backoff_seconds=0)

    assert reader._fetch_gamma_markets(limit=10) == []
    assert calls == 2


def test_gamma_market_fetch_uses_100_row_pages_and_stops_on_offset_422(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(*args, **kwargs):
        calls.append(kwargs["params"])
        if kwargs["params"]["offset"] == 100:
            return FakeResponse({}, status_code=422)
        return FakeResponse([{"id": str(i)} for i in range(100)])

    monkeypatch.setattr("weather_trader.markets.polymarket_reader.requests.get", fake_get)

    reader = PolymarketReader(max_retries=1)

    assert len(reader._fetch_gamma_markets(limit=500)) == 100
    assert calls == [
        {"limit": 100, "offset": 0, "active": "true", "closed": "false"},
        {"limit": 100, "offset": 100, "active": "true", "closed": "false"},
    ]
