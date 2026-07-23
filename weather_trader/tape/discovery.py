from __future__ import annotations

from dataclasses import dataclass

from weather_trader.execution.contracts import MarketSnapshot
from weather_trader.execution.discovery import MarketDiscoveryService
from weather_trader.tape.contracts import TokenOutcome, TokenRegistryEntry


@dataclass(frozen=True)
class TapeDiscoveryResult:
    tokens: tuple[TokenRegistryEntry, ...]
    warnings: tuple[str, ...]
    complete: bool


class TapeDiscoveryService:
    """Discover the active weather-token universe without consulting policies."""

    def __init__(self, discovery: MarketDiscoveryService | None = None) -> None:
        self.discovery = discovery or MarketDiscoveryService()

    def discover(self, *, market_limit: int = 50000) -> TapeDiscoveryResult:
        markets = self.discovery.discover(
            limit=market_limit,
            validate_stations=True,
            market_scope="all",
            include_future=True,
        )
        warnings = tuple(self.discovery.last_warnings)
        tokens = tuple(_tokens_from_markets(markets))
        incomplete_markers = (
            "targeted_event_discovery_empty",
            "targeted_event_parse_empty",
            "broad_market_discovery_empty",
        )
        complete = bool(tokens) and not any(marker in warnings for marker in incomplete_markers)
        return TapeDiscoveryResult(tokens=tokens, warnings=warnings, complete=complete)


def _tokens_from_markets(markets: list[MarketSnapshot]) -> list[TokenRegistryEntry]:
    entries: list[TokenRegistryEntry] = []
    seen: set[str] = set()
    for market in markets:
        if not market.active or market.market_date is None:
            continue
        pairs = (
            (market.yes_token_id, market.no_token_id, TokenOutcome.YES),
            (market.no_token_id, market.yes_token_id, TokenOutcome.NO),
        )
        for token_id, sibling_token_id, outcome in pairs:
            if not token_id or token_id in seen:
                continue
            seen.add(token_id)
            entries.append(
                TokenRegistryEntry(
                    token_id=token_id,
                    market_id=market.market_id,
                    condition_id=market.condition_id,
                    outcome=outcome,
                    station=market.station,
                    market_date=market.market_date.isoformat(),
                    market_family=market.market_family.value,
                    lower_bound=market.lower_f,
                    upper_bound=market.upper_f,
                    sibling_token_id=sibling_token_id,
                    sibling_market_id=market.market_id,
                    market_end_at_utc=market.end_date or None,
                    discovered_at_utc=market.discovered_at,
                    active_from_utc=market.listed_at or market.discovered_at,
                    resolution_source=market.resolution_source,
                    listing_timestamp_source="gamma_created_at" if market.listed_at else "discovery_fallback",
                )
            )
    return entries
