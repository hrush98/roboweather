from __future__ import annotations

from datetime import datetime, timezone

from weather_trader.execution.contracts import MarketSnapshot
from weather_trader.markets.polymarket_reader import PolymarketReader
from weather_trader.stations.metadata import get_station


class MarketDiscoveryService:
    def __init__(self, reader: PolymarketReader | None = None) -> None:
        self.reader = reader or PolymarketReader()

    def discover(self, limit: int = 50000, validate_stations: bool = True) -> list[MarketSnapshot]:
        discovered_at = datetime.now(timezone.utc).isoformat()
        snapshots: list[MarketSnapshot] = []
        for item in self.reader._fetch_gamma_markets(limit=limit):
            market = self.reader._parse_weather_market(item)
            if market is None:
                continue
            if validate_stations:
                try:
                    get_station(market.station)
                except KeyError:
                    continue
            snapshots.append(
                MarketSnapshot(
                    market_id=market.market_id,
                    condition_id=str(item.get("conditionId") or item.get("condition_id") or "") or None,
                    question=market.question,
                    slug=market.slug,
                    city=market.city,
                    station=market.station,
                    market_date=market.market_date,
                    lower_f=market.lower_f,
                    upper_f=market.upper_f,
                    yes_token_id=market.yes_token_id,
                    no_token_id=market.no_token_id,
                    end_date=market.end_date,
                    resolution_source=market.resolution_source,
                    discovered_at=discovered_at,
                    active=True,
                )
            )
        return snapshots


def same_day_markets(markets: list[MarketSnapshot], as_of_utc) -> list[MarketSnapshot]:
    filtered: list[MarketSnapshot] = []
    for market in markets:
        try:
            station = get_station(market.station)
        except KeyError:
            continue
        local_date = as_of_utc.astimezone(__import__("zoneinfo").ZoneInfo(station.timezone)).date()
        if market.market_date is None or market.market_date == local_date:
            filtered.append(market)
    return filtered
