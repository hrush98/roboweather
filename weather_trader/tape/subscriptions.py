from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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
        try:
            result = self.discovery.discover(market_limit=market_limit)
        except Exception as exc:
            completed_at_utc = _utc_now()
            self.catalog.record_discovery_refresh(
                session_id=session_id,
                attempted_at_utc=effective_at_utc,
                completed_at_utc=completed_at_utc,
                complete=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        completed_at_utc = _utc_now()
        return self.apply_discovery(
            result,
            session_id=session_id,
            effective_at_utc=completed_at_utc,
            attempted_at_utc=effective_at_utc,
        )

    def apply_discovery(
        self,
        result: TapeDiscoveryResult,
        *,
        session_id: str,
        effective_at_utc: str,
        attempted_at_utc: str | None = None,
    ) -> RegistryRefresh:
        upserted = self.catalog.upsert_tokens(list(result.tokens))
        self.catalog.record_discovery_refresh(
            session_id=session_id,
            attempted_at_utc=attempted_at_utc or effective_at_utc,
            completed_at_utc=effective_at_utc,
            complete=result.complete,
            token_ids_and_markets=tuple(
                (entry.token_id, entry.market_id) for entry in result.tokens
            ),
            warnings=result.warnings,
        )
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
