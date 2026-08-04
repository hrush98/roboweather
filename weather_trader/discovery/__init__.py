"""Policy-neutral, tape-backed strategy discovery contracts and helpers."""

from weather_trader.discovery.contracts import (
    BroadDiscoveryRow,
    CandidateRule,
    DiscoveryRunSpec,
    StrategyManifest,
)
from weather_trader.discovery.registry import DiscoveryRegistry

__all__ = [
    "BroadDiscoveryRow",
    "CandidateRule",
    "DiscoveryRegistry",
    "DiscoveryRunSpec",
    "StrategyManifest",
]
