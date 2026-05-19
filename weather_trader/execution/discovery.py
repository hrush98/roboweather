from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.markets.polymarket_reader import PolymarketReader, WeatherEventTarget
from weather_trader.stations.metadata import get_station


@dataclass(frozen=True)
class WeatherEventConfig:
    city_slug: str
    timezone: str


WEATHER_EVENT_CONFIGS: tuple[WeatherEventConfig, ...] = (
    WeatherEventConfig("nyc", "America/New_York"),
    WeatherEventConfig("atlanta", "America/New_York"),
    WeatherEventConfig("miami", "America/New_York"),
    WeatherEventConfig("chicago", "America/Chicago"),
    WeatherEventConfig("dallas", "America/Chicago"),
    WeatherEventConfig("houston", "America/Chicago"),
    WeatherEventConfig("denver", "America/Denver"),
    WeatherEventConfig("los-angeles", "America/Los_Angeles"),
    WeatherEventConfig("san-francisco", "America/Los_Angeles"),
    WeatherEventConfig("seattle", "America/Los_Angeles"),
)


class MarketDiscoveryService:
    def __init__(self, reader: PolymarketReader | None = None) -> None:
        self.reader = reader or PolymarketReader()
        self.last_warnings: list[str] = []

    def discover(self, limit: int = 50000, validate_stations: bool = True) -> list[MarketSnapshot]:
        now = datetime.now(timezone.utc)
        discovered_at = now.isoformat()
        self.last_warnings = []
        targets = [
            WeatherEventTarget(config.city_slug, now.astimezone(ZoneInfo(config.timezone)).date(), family)
            for config in WEATHER_EVENT_CONFIGS
            for family in (MarketFamily.HIGH_TEMP, MarketFamily.LOW_TEMP)
        ]
        event_items, missing_events = self.reader._fetch_weather_event_markets(targets)
        self.last_warnings.extend(f"missing_event:{slug}" for slug in missing_events)

        snapshots = self._items_to_snapshots(
            event_items,
            discovered_at=discovered_at,
            validate_stations=validate_stations,
        )
        if snapshots:
            return snapshots

        if event_items:
            self.last_warnings.append("targeted_event_parse_empty")
        else:
            self.last_warnings.append("targeted_event_discovery_empty")
        source_items = self.reader._fetch_gamma_markets(limit=limit)
        if not source_items:
            self.last_warnings.append("broad_market_discovery_empty")
        return self._items_to_snapshots(
            source_items,
            discovered_at=discovered_at,
            validate_stations=validate_stations,
        )

    def _items_to_snapshots(
        self,
        items: list[dict],
        discovered_at: str,
        validate_stations: bool,
    ) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []
        seen_market_ids: set[str] = set()
        for item in items:
            market = self.reader._parse_weather_market(item)
            if market is None:
                continue
            if market.market_id in seen_market_ids:
                continue
            if validate_stations:
                try:
                    get_station(market.station)
                except KeyError:
                    continue
            seen_market_ids.add(market.market_id)
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
                    active=_active_flag(item),
                    market_family=market.market_family,
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


def _active_flag(item: dict) -> bool:
    value = item.get("active", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no"}
    return bool(value)
