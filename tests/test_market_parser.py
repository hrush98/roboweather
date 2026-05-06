from __future__ import annotations

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
