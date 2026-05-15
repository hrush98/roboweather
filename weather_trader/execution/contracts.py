from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class TradeAction(StringEnum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SKIP = "SKIP"


class StrategyBucket(StringEnum):
    HIGH_CONVICTION = "HIGH_CONVICTION"
    TAIL = "TAIL"
    BEST_BUCKET = "BEST_BUCKET"
    MAX_SO_FAR = "MAX_SO_FAR"
    NONE = "NONE"


class OrderState(StringEnum):
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    RISK_CHECKED = "RISK_CHECKED"
    ORDER_WORKING = "ORDER_WORKING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    DONE = "DONE"


class EffectiveStatus(StringEnum):
    LIVE = "LIVE"
    EFFECTIVELY_WON = "EFFECTIVELY_WON"
    EFFECTIVELY_LOST = "EFFECTIVELY_LOST"
    UNKNOWN = "UNKNOWN"


class PaperPolicyOrderMode(StringEnum):
    FOK = "FOK"
    FAK = "FAK"


class PaperPolicyFinalState(StringEnum):
    RESERVED = "RESERVED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    DELAYED = "DELAYED"
    UNKNOWN = "UNKNOWN"
    STALE_BOOK = "STALE_BOOK"
    FOK_NOT_FILLED = "FOK_NOT_FILLED"
    EXPIRED_NO_LIQUIDITY = "EXPIRED_NO_LIQUIDITY"
    SETTLED = "SETTLED"


class PaperPolicyEventType(StringEnum):
    ENTRY_RESERVED = "ENTRY_RESERVED"
    ENTRY_SUBMIT = "ENTRY_SUBMIT"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    ENTRY_RETRY = "ENTRY_RETRY"
    MARK = "MARK"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    condition_id: str | None
    question: str
    slug: str
    city: str
    station: str
    market_date: date | None
    lower_f: float | None
    upper_f: float | None
    yes_token_id: str | None
    no_token_id: str | None
    end_date: str
    resolution_source: str
    discovered_at: str
    active: bool = True


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    token_id: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp: str
    source: str = "rest"

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def ask_depth_usd(self, max_price: float) -> float:
        return sum(level.price * level.size for level in self.asks if level.price <= max_price)

    def bid_depth_usd(self, min_price: float = 0.0) -> float:
        return sum(level.price * level.size for level in self.bids if level.price >= min_price)


@dataclass(frozen=True)
class Signal:
    timestamp: str
    market_id: str
    question: str
    station: str
    market_date: date | None
    lower_f: float | None
    upper_f: float | None
    current_temp: float
    high_so_far: float
    latest_obs_time: str
    hrrr_remaining_max: float | None
    fair_yes: float
    fair_no: float
    yes_bid: float | None
    yes_ask: float | None
    yes_depth_usd: float
    no_bid: float | None
    no_ask: float | None
    no_depth_usd: float
    edge_yes: float | None
    edge_no: float | None
    signal_side: TradeAction
    reason_codes: list[str]
    model_name: str
    model_features_hash: str


@dataclass(frozen=True)
class Decision:
    timestamp: str
    market_id: str
    token_id: str | None
    action: TradeAction
    strategy_bucket: StrategyBucket
    max_price: float | None
    target_usd: float
    expected_value: float | None
    skip_reasons: list[str]
    reason_codes: list[str]


@dataclass(frozen=True)
class PaperOrder:
    timestamp: str
    order_id: str
    market_id: str
    token_id: str
    action: TradeAction
    state: OrderState
    max_price: float
    target_usd: float
    filled_shares: float
    avg_price: float | None
    cost: float
    levels_consumed: list[dict[str, float]]
    reject_reason: str | None = None


@dataclass(frozen=True)
class Position:
    position_id: str
    market_id: str
    token_id: str
    side: TradeAction
    station: str
    market_date: date | None
    lower_f: float | None
    upper_f: float | None
    shares: float
    avg_entry_price: float
    cost: float
    current_bid: float | None
    mark_value: float
    unrealized_pnl: float
    state: str = "OPEN"


@dataclass(frozen=True)
class PositionMark:
    timestamp: str
    position_id: str
    market_id: str
    token_id: str
    side: TradeAction
    station: str
    market_date: date | None
    lower_f: float | None
    upper_f: float | None
    shares: float
    cost: float
    avg_entry_price: float
    current_bid: float | None
    mark_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    high_so_far: float | None
    effective_status: EffectiveStatus
    reason: str


@dataclass(frozen=True)
class StationDateDecisionTrace:
    timestamp: str
    station: str
    market_date: date | None
    candidate_count: int
    selected_market_id: str | None
    selected_action: TradeAction
    selected_strategy_bucket: StrategyBucket
    selected_edge: float | None
    selected_score: float | None
    skip_reason: str | None
    distribution: list[dict[str, Any]]
    candidates: list[dict[str, Any]]


@dataclass(frozen=True)
class Resolution:
    market_id: str
    station: str
    market_date: date
    final_high: float
    winning_side: TradeAction
    source: str
    resolved_at: str
    discrepancy_flag: bool = False


@dataclass(frozen=True)
class RiskState:
    timestamp: str
    bankroll_usd: float
    open_positions: int
    station_date_exposure_usd: dict[str, float]
    portfolio_exposure_usd: float
    daily_realized_pnl: float = 0.0
    kill_switch_active: bool = False


@dataclass(frozen=True)
class EngineState:
    timestamp: str
    mode: str
    discovered_markets: int
    actionable_signals: int
    orders_submitted: int
    skipped: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PredictionSnapshot:
    timestamp: str
    station: str
    market_date: date
    decision_time_utc: str
    decision_time_local: str
    latest_obs_time_utc: str
    latest_obs_time_local: str
    obs_age_minutes: float
    obs_delay_bucket: str
    current_temp: float
    high_so_far: float
    hrrr_remaining_max: float | None
    strategy_bucket: StrategyBucket
    selected_market_id: str | None
    selected_bucket: str | None
    selected_side: TradeAction
    selected_edge: float | None
    selected_fair_yes: float | None
    selected_fair_no: float | None
    selected_yes_ask: float | None
    selected_no_ask: float | None
    model_name: str
    high_conviction: bool
    skip_reason: str | None
    candidate_count: int
    candidate_distribution: list[dict[str, Any]]
    selected_best_bid: float | None = None
    selected_best_ask: float | None = None
    selected_spread: float | None = None
    selected_depth_at_ask: float | None = None
    selected_depth_ask_plus_0_01: float | None = None
    selected_depth_ask_plus_0_03: float | None = None
    selected_depth_ask_plus_0_05: float | None = None
    selected_book_timestamp: str | None = None
    selected_book_age_seconds: float | None = None
    selected_liquidity: dict[str, Any] | None = None


@dataclass(frozen=True)
class StationDateOutcome:
    timestamp: str
    station: str
    market_date: date
    final_high_tmpf: float
    source: str
    resolved_at: str


@dataclass(frozen=True)
class PredictionResult:
    timestamp: str
    prediction_snapshot_id: int
    station: str
    market_date: date
    obs_delay_bucket: str
    selected_market_id: str | None
    selected_bucket: str | None
    selected_side: TradeAction
    final_high_tmpf: float
    winning_side: TradeAction | None
    correct: bool | None
    entry_price: float | None
    paper_pnl: float | None
    edge: float | None
    decision_time_local: str
    obs_age_minutes: float
    resolved_at: str


@dataclass(frozen=True)
class ResearchPolicyPosition:
    timestamp: str
    policy_name: str
    station: str
    market_date: date
    scope_key: str
    model_group: str
    strategy_bucket: StrategyBucket
    obs_delay_bucket: str
    selected_market_id: str
    selected_side: TradeAction
    selected_bucket: str | None
    entry_price: float
    entry_edge: float | None
    entry_fair: float | None
    source_prediction_snapshot_ids: list[int]
    raw_policy: dict[str, Any]
    selected_best_bid: float | None = None
    selected_best_ask: float | None = None
    selected_spread: float | None = None
    selected_depth_at_ask: float | None = None
    selected_depth_ask_plus_0_01: float | None = None
    selected_depth_ask_plus_0_03: float | None = None
    selected_depth_ask_plus_0_05: float | None = None
    selected_book_timestamp: str | None = None
    selected_book_age_seconds: float | None = None
    selected_liquidity: dict[str, Any] | None = None


@dataclass(frozen=True)
class PaperPolicySizingDecision:
    target_notional_usd: float
    cap_reason: str
    raw_inputs: dict[str, Any]


@dataclass(frozen=True)
class PaperPolicyPosition:
    timestamp: str
    research_policy_position_id: int
    policy_name: str
    station: str
    market_date: date
    selected_market_id: str
    selected_token_id: str
    selected_side: TradeAction
    selected_bucket: str | None
    entry_limit_price: float
    target_notional_usd: float
    filled_shares: float
    avg_entry_price: float | None
    cost_usd: float
    state: PaperPolicyFinalState
    realized_pnl: float | None = None
    realized_rr: float | None = None
    mark_value: float | None = None
    unrealized_pnl: float | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperPolicyOrderAttempt:
    timestamp: str
    paper_position_id: int
    research_policy_position_id: int
    attempt_seq: int
    token_id: str
    side: TradeAction
    order_mode: PaperPolicyOrderMode
    limit_price: float
    target_notional_usd: float
    external_order_id: str | None
    external_status: str | None
    not_found_count: int
    final_state: PaperPolicyFinalState
    final_reason: str
    filled_shares: float
    avg_price: float | None
    cost_usd: float
    levels_consumed: list[dict[str, float]]
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class PaperPolicyTradeEvent:
    timestamp: str
    paper_position_id: int | None
    research_policy_position_id: int | None
    event_type: PaperPolicyEventType
    message: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class PaperPolicyRiskSnapshot:
    timestamp: str
    bankroll_usd: float
    open_positions: int
    open_risk_usd: float
    station_date_exposure_usd: dict[str, float]
    raw_payload: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, StringEnum):
        return str(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def dataclass_to_jsonable(instance: Any) -> dict[str, Any]:
    return to_jsonable(asdict(instance))
