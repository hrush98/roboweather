"""Policy-neutral, tape-backed strategy discovery contracts and helpers."""

from weather_trader.discovery.contracts import (
    BroadDiscoveryRow,
    CandidateRule,
    DiscoveryRunSpec,
    StrategyManifest,
)
from weather_trader.discovery.orchestrator import (
    DiscoveryBudgets,
    RecurringDiscoveryOrchestrator,
)
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.discovery.scheduler import BoundedDiscoveryScheduler, SchedulerConfig
from weather_trader.discovery.transitions import ResearchRoleTransitionEngine, TransitionPolicy

__all__ = [
    "BroadDiscoveryRow",
    "BoundedDiscoveryScheduler",
    "DiscoveryBudgets",
    "CandidateRule",
    "DiscoveryRegistry",
    "DiscoveryRunSpec",
    "RecurringDiscoveryOrchestrator",
    "ResearchRoleTransitionEngine",
    "SchedulerConfig",
    "StrategyManifest",
    "TransitionPolicy",
]
