from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


TAPE_SCHEMA_VERSION = 1
TAPE_PARSER_VERSION = "market_tape_v1"


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class CoverageState(StringEnum):
    VALID = "VALID"
    PRE_SUBSCRIPTION = "PRE_SUBSCRIPTION"
    RECONNECTING = "RECONNECTING"
    GAPPED = "GAPPED"
    STALE = "STALE"
    RESYNCING = "RESYNCING"
    CLOSED = "CLOSED"


class TokenOutcome(StringEnum):
    YES = "YES"
    NO = "NO"


class ReplaySide(StringEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class DecisionTiming:
    decision_id: str
    hypothesis_version: str
    activation_timestamp: str
    token_id: str
    observation_source_timestamp: str
    observation_received_at_utc: str
    decision_started_at_utc: str
    decision_finished_at_utc: str
    latency_ms: int = 0
    quote_termination_at_utc: str | None = None
    source_type: str = "decision_json"
    source_ref: str | None = None

    def __post_init__(self) -> None:
        _require(self.decision_id, "decision_id")
        _require(self.hypothesis_version, "hypothesis_version")
        _require(self.token_id, "token_id")
        _require(self.source_type, "source_type")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")


@dataclass(frozen=True)
class DecisionTapeJoin:
    decision_id: str
    hypothesis_version: str
    token_id: str
    session_id: str
    quote_ready_at_utc: str
    first_visible_event_id: str | None
    first_visible_event_at_utc: str | None
    coverage_valid: bool
    invalid_reason: str | None
    pre_signal_seconds: float
    reconstruction_hash: str | None
    quote_termination_at_utc: str | None = None
    termination_event_id: str | None = None
    termination_event_at_utc: str | None = None
    termination_reconstruction_hash: str | None = None
    tape_observed_through_at_utc: str | None = None
    coverage_interval_id: int | None = None
    coverage_started_at_utc: str | None = None
    coverage_ended_at_utc: str | None = None
    source_type: str = "decision_json"
    source_ref: str | None = None


class FillScenario(StringEnum):
    UNFILLED = "UNFILLED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TokenRegistryEntry:
    token_id: str
    market_id: str
    condition_id: str | None
    outcome: TokenOutcome
    station: str
    market_date: str
    market_family: str
    discovered_at_utc: str
    active_from_utc: str
    resolution_source: str
    lower_bound: float | None = None
    upper_bound: float | None = None
    sibling_token_id: str | None = None
    sibling_market_id: str | None = None
    market_end_at_utc: str | None = None
    active_until_utc: str | None = None
    subscription_state: str = "PENDING"
    last_health_status: str | None = None
    listing_timestamp_source: str = "discovery_fallback"

    def __post_init__(self) -> None:
        _require(self.token_id, "token_id")
        _require(self.market_id, "market_id")
        _require(self.station, "station")


@dataclass(frozen=True)
class CollectorSession:
    session_id: str
    started_at_utc: str
    started_monotonic_ns: int
    collector_version: str
    hostname: str
    validation_run_id: str = "unspecified"
    build_fingerprint: str = "unspecified"
    config_fingerprint: str = "unspecified"
    finished_at_utc: str | None = None
    finish_reason: str | None = None

    def __post_init__(self) -> None:
        _require(self.session_id, "session_id")
        _require(self.validation_run_id, "validation_run_id")
        _require(self.build_fingerprint, "build_fingerprint")
        _require(self.config_fingerprint, "config_fingerprint")
        if self.started_monotonic_ns < 0:
            raise ValueError("started_monotonic_ns must be non-negative")


@dataclass(frozen=True)
class CollectorMetric:
    session_id: str
    captured_at_utc: str
    messages: int
    events: int
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    rss_bytes: int
    raw_disk_bytes: int
    receipt_lag_ms: float | None
    reconnect_attempt: int

    def __post_init__(self) -> None:
        for field_name in (
            "messages",
            "events",
            "queue_depth",
            "queue_capacity",
            "queue_high_water",
            "rss_bytes",
            "raw_disk_bytes",
            "reconnect_attempt",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True)
class SubscriptionGeneration:
    session_id: str
    generation: int
    effective_at_utc: str
    token_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("generation must be >= 1")
        if not self.token_ids:
            raise ValueError("token_ids must not be empty")


@dataclass(frozen=True)
class CoverageInterval:
    session_id: str
    token_id: str
    state: CoverageState
    started_at_utc: str
    ended_at_utc: str | None
    subscription_generation: int
    reason: str | None = None
    gap_id: str | None = None

    @property
    def replay_valid(self) -> bool:
        return self.state is CoverageState.VALID


@dataclass(frozen=True)
class MarketTapeEvent:
    collector_session_id: str
    token_id: str
    market_id: str
    event_type: str
    raw_payload: dict[str, Any] | list[Any]
    received_at_utc: str
    received_monotonic_ns: int
    receipt_sequence: int
    subscription_generation: int
    feed_timestamp: str | None = None
    exchange_sequence: int | None = None
    coverage_state: CoverageState = CoverageState.VALID
    gap_id: str | None = None
    schema_version: int = TAPE_SCHEMA_VERSION
    parser_version: str = TAPE_PARSER_VERSION
    partition_id: str | None = None
    append_offset: int | None = None
    stable_event_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.collector_session_id, "collector_session_id")
        _require(self.token_id, "token_id")
        _require(self.market_id, "market_id")
        _require(self.event_type, "event_type")
        if self.received_monotonic_ns < 0:
            raise ValueError("received_monotonic_ns must be non-negative")
        if self.receipt_sequence < 1:
            raise ValueError("receipt_sequence must be >= 1")
        if self.subscription_generation < 1:
            raise ValueError("subscription_generation must be >= 1")
        if self.schema_version != TAPE_SCHEMA_VERSION:
            raise ValueError(f"unsupported tape schema version: {self.schema_version}")


@dataclass(frozen=True)
class BookCheckpoint:
    checkpoint_id: str
    session_id: str
    token_id: str
    event_id: str
    event_offset: int
    captured_at_utc: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    reconstruction_hash: str
    coverage_state: CoverageState


@dataclass(frozen=True)
class ReplayInput:
    decision_id: str
    hypothesis_version: str
    activation_timestamp: str
    token_id: str
    quote_ready_timestamp: str
    side: ReplaySide
    quote_price_rule: str
    size: float
    expiry_timestamp: str
    cancellation_rule: str
    latency_assumption_ms: int
    queue_assumption: str

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("size must be positive")
        if self.latency_assumption_ms < 0:
            raise ValueError("latency_assumption_ms must be non-negative")


@dataclass(frozen=True)
class ReplayOutput:
    decision_id: str
    hypothesis_version: str
    coverage_valid: bool
    invalid_reason: str | None
    initial_book: dict[str, Any] | None
    queue_ahead: float | None
    postable: bool | None
    crossing: bool | None
    authoritative_flow: float
    conservative_fill: FillScenario
    base_fill: FillScenario
    optimistic_fill: FillScenario
    filled_size_by_scenario: dict[str, float]
    cancellation_trigger: str | None
    cancellation_timestamp: str | None
    adverse_movement: dict[str, float | None]
    markouts: dict[str, Any]
    settlement_pnl: float | None
    replay_version: str
    parser_version: str


def contract_to_dict(value: Any) -> dict[str, Any]:
    return _jsonable(asdict(value))


def market_tape_event_from_dict(value: dict[str, Any]) -> MarketTapeEvent:
    data = dict(value)
    data["coverage_state"] = CoverageState(data["coverage_state"])
    return MarketTapeEvent(**data)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _require(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
