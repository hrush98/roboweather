from __future__ import annotations

import requests

from weather_trader.markets.polymarket_reader import PolymarketReader


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


def test_gamma_market_fetch_retries_transient_timeout(monkeypatch) -> None:
    calls = 0

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return []

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
