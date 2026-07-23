from __future__ import annotations

from dataclasses import dataclass

from weather_trader.tape.catalog import TapeCatalog
from weather_trader.tape.contracts import SubscriptionGeneration
from weather_trader.tape.discovery import TapeDiscoveryResult, TapeDiscoveryService


@dataclass(frozen=True)
class RegistryRefresh:
    discovery: TapeDiscoveryResult
    tokens_upserted: int
    subscription: SubscriptionGeneration | None


class SubscriptionRegistry:
    """Refresh discovery and emit an immutable generation only on membership change."""

    def __init__(self, catalog: TapeCatalog, discovery: TapeDiscoveryService) -> None:
        self.catalog = catalog
        self.discovery = discovery

    def refresh(
        self,
        *,
        session_id: str,
        effective_at_utc: str,
        market_limit: int = 50000,
    ) -> RegistryRefresh:
        result = self.discovery.discover(market_limit=market_limit)
        return self.apply_discovery(
            result,
            session_id=session_id,
            effective_at_utc=effective_at_utc,
        )

    def apply_discovery(
        self,
        result: TapeDiscoveryResult,
        *,
        session_id: str,
        effective_at_utc: str,
    ) -> RegistryRefresh:
        upserted = self.catalog.upsert_tokens(list(result.tokens))
        if result.complete:
            self.catalog.retire_missing_tokens(
                tuple(entry.token_id for entry in result.tokens),
                retired_at_utc=effective_at_utc,
            )
        active_token_ids = tuple(entry.token_id for entry in self.catalog.active_tokens())
        subscription = None
        if active_token_ids:
            subscription = self.catalog.reconcile_subscription(
                session_id,
                token_ids=active_token_ids,
                effective_at_utc=effective_at_utc,
                reason="active_weather_universe_refresh",
            )
        return RegistryRefresh(
            discovery=result,
            tokens_upserted=upserted,
            subscription=subscription,
        )
