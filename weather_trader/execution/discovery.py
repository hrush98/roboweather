from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from weather_trader.execution.contracts import MarketFamily, MarketSnapshot
from weather_trader.markets.polymarket_reader import PolymarketReader, WeatherEventTarget
from weather_trader.stations.metadata import get_station, get_station_any


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

GLOBAL_WEATHER_EVENT_CONFIGS: tuple[WeatherEventConfig, ...] = (
    WeatherEventConfig("hong-kong", "Asia/Hong_Kong"),
    WeatherEventConfig("london", "Europe/London"),
    WeatherEventConfig("tokyo", "Asia/Tokyo"),
    WeatherEventConfig("seoul", "Asia/Seoul"),
    WeatherEventConfig("shanghai", "Asia/Shanghai"),
    WeatherEventConfig("singapore", "Asia/Singapore"),
    WeatherEventConfig("mexico-city", "America/Mexico_City"),
    WeatherEventConfig("paris", "Europe/Paris"),
    WeatherEventConfig("amsterdam", "Europe/Amsterdam"),
    WeatherEventConfig("munich", "Europe/Berlin"),
)

GLOBAL_CITY_SLUGS = frozenset(config.city_slug for config in GLOBAL_WEATHER_EVENT_CONFIGS)


class MarketDiscoveryService:
    def __init__(self, reader: PolymarketReader | None = None) -> None:
        self.reader = reader or PolymarketReader()
        self.last_warnings: list[str] = []

    def discover(
        self,
        limit: int = 50000,
        validate_stations: bool = True,
        market_scope: str = "us",
        include_future: bool = False,
    ) -> list[MarketSnapshot]:
        now = datetime.now(timezone.utc)
        discovered_at = now.isoformat()
        self.last_warnings = []
        configs = _configs_for_scope(market_scope)
        targets = [
            WeatherEventTarget(config.city_slug, now.astimezone(ZoneInfo(config.timezone)).date(), family)
            for config in configs
            for family in (MarketFamily.HIGH_TEMP, MarketFamily.LOW_TEMP)
        ]
        event_items, missing_events = self.reader._fetch_weather_event_markets(targets)
        self.last_warnings.extend(f"missing_event:{slug}" for slug in missing_events)

        targeted_snapshots = self._items_to_snapshots(
            event_items,
            discovered_at=discovered_at,
            validate_stations=validate_stations,
            market_scope=market_scope,
        )
        if targeted_snapshots and not include_future:
            return targeted_snapshots

        if not targeted_snapshots:
            if event_items:
                self.last_warnings.append("targeted_event_parse_empty")
            else:
                self.last_warnings.append("targeted_event_discovery_empty")
        broad_items = self.reader._fetch_gamma_markets(limit=limit)
        if not broad_items:
            self.last_warnings.append("broad_market_discovery_empty")
        broad_snapshots = self._items_to_snapshots(
            broad_items,
            discovered_at=discovered_at,
            validate_stations=validate_stations,
            market_scope=market_scope,
        )
        if not include_future:
            return broad_snapshots
        by_market_id = {market.market_id: market for market in broad_snapshots}
        by_market_id.update({market.market_id: market for market in targeted_snapshots})
        return sorted(by_market_id.values(), key=lambda market: (market.market_date or now.date(), market.station, market.market_id))

    def _items_to_snapshots(
        self,
        items: list[dict],
        discovered_at: str,
        validate_stations: bool,
        market_scope: str = "us",
    ) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []
        seen_market_ids: set[str] = set()
        for item in items:
            market = self.reader._parse_weather_market(item, require_price=False)
            if market is None:
                continue
            if not _market_in_scope(market.city, market_scope):
                continue
            if market.market_id in seen_market_ids:
                continue
            if validate_stations:
                try:
                    get_station_any(market.station) if market_scope in {"global", "all"} else get_station(market.station)
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
                    listed_at=_listing_timestamp(item),
                )
            )
        return snapshots


def same_day_markets(markets: list[MarketSnapshot], as_of_utc) -> list[MarketSnapshot]:
    filtered: list[MarketSnapshot] = []
    for market in markets:
        try:
            station = get_station_any(market.station)
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


def _listing_timestamp(item: dict) -> str | None:
    # Gamma's creation timestamp is the listing provenance needed for lifecycle
    # evidence. ``startDate`` is a schedule field and may differ from listing.
    value = item.get("createdAt") or item.get("created_at")
    text = str(value or "").strip()
    return text or None


def _configs_for_scope(market_scope: str) -> tuple[WeatherEventConfig, ...]:
    if market_scope == "us":
        return WEATHER_EVENT_CONFIGS
    if market_scope == "global":
        return GLOBAL_WEATHER_EVENT_CONFIGS
    if market_scope == "all":
        return (*WEATHER_EVENT_CONFIGS, *GLOBAL_WEATHER_EVENT_CONFIGS)
    raise ValueError(f"Unsupported market_scope: {market_scope}")


def _market_in_scope(city: str, market_scope: str) -> bool:
    if market_scope == "all":
        return True
    slug = city.strip().lower().replace(" ", "-")
    is_global = slug in GLOBAL_CITY_SLUGS
    if market_scope == "global":
        return is_global
    if market_scope == "us":
        return not is_global
    raise ValueError(f"Unsupported market_scope: {market_scope}")
