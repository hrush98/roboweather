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
