from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from weather_trader.execution.contracts import MarketFamily, StrategyBucket, TradeAction


PRICE_SHEET_V2_CONTRACT_VERSION = "price_sheet_v2_contract_v1"
V2A_DATASET_VERSION = "price_sheet_v2a_dataset_v1"
V2A_CALIBRATION_VERSION = "price_sheet_v2a_calibration_v1"
V2A_PRICING_VERSION = "price_sheet_v2a_pricing_v1"
V1_ROLLBACK_VERSION = "phase1_price_maker_v1"

HRRR_RICH_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_V2_DYNAMIC_MODEL = "dynamic_bucket_hrrr_v2_obs_2022_2025"
PILOT_STATIONS = (
    "KATL",
    "KBKF",
    "KBOS",
    "KDAL",
    "KDCA",
    "KHOU",
    "KLAX",
    "KLGA",
    "KMIA",
    "KORD",
    "KSEA",
    "KSFO",
)


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class DatasetRole(StringEnum):
    CALIBRATION_FIT = "CALIBRATION_FIT"
    FROZEN_POLICY_EVALUATION = "FROZEN_POLICY_EVALUATION"


class MarketReferenceKind(StringEnum):
    MIDPOINT = "MIDPOINT"
    SAME_SIDE_ASK = "SAME_SIDE_ASK"
    MISSING = "MISSING"


class OutcomeLabelSource(StringEnum):
    IEM_ASOS_RESEARCH_HIGH = "IEM_ASOS_RESEARCH_HIGH"
    POLYMARKET_SETTLEMENT = "POLYMARKET_SETTLEMENT"


class V2SkipReason(StringEnum):
    SIGNAL_OUT_OF_SCOPE = "SIGNAL_OUT_OF_SCOPE"
    MISSING_RAW_FAIR = "MISSING_RAW_FAIR"
    MISSING_OUTCOME_LABEL = "MISSING_OUTCOME_LABEL"
    INVALID_TIMESTAMP_ORDER = "INVALID_TIMESTAMP_ORDER"
    PRE_ACTIVATION = "PRE_ACTIVATION"
    MISSING_MARKET_REFERENCE = "MISSING_MARKET_REFERENCE"
    STALE_MARKET_REFERENCE = "STALE_MARKET_REFERENCE"
    CROSSED_MARKET_REFERENCE = "CROSSED_MARKET_REFERENCE"
    CALIBRATOR_NOT_SELECTED = "CALIBRATOR_NOT_SELECTED"
    CALIBRATION_PREDICTION_MISSING = "CALIBRATION_PREDICTION_MISSING"
    INSUFFICIENT_PRIOR_OOF_DATES = "INSUFFICIENT_PRIOR_OOF_DATES"
    NO_POSITIVE_QUOTE_AFTER_RESERVES = "NO_POSITIVE_QUOTE_AFTER_RESERVES"
    INVALID_TAPE = "INVALID_TAPE"
    NOT_POSTABLE = "NOT_POSTABLE"
    CAPACITY_BELOW_MINIMUM = "CAPACITY_BELOW_MINIMUM"


@dataclass(frozen=True)
class SignalSpec:
    signal_spec_id: str
    version: int
    activation_timestamp: str
    model_ids: tuple[str, ...]
    market_family: MarketFamily
    station_allowlist: tuple[str, ...]
    selected_sides: tuple[TradeAction, ...]
    strategy_buckets: tuple[StrategyBucket, ...]
    local_decision_start: str
    local_decision_end: str
    entry_price_min: float
    entry_price_max: float
    observation_delay_buckets: tuple[str, ...]
    lifecycle_horizon: str
    dedupe_key_fields: tuple[str, ...]
    outcome_label_source: OutcomeLabelSource
    market_reference_rule: str
    max_market_reference_age_seconds: float
    split_rule: str
    weighting_rule: str
    retrospective_start_date: str
    forward_start_date: str
    v1_rollback_version: str = V1_ROLLBACK_VERSION

    def __post_init__(self) -> None:
        _parse_utc(self.activation_timestamp)
        date.fromisoformat(self.retrospective_start_date)
        date.fromisoformat(self.forward_start_date)
        if not self.signal_spec_id or self.version < 1:
            raise ValueError("signal spec requires a stable id and positive version")
        if not self.model_ids or not self.station_allowlist:
            raise ValueError("signal spec requires models and stations")
        if self.local_decision_start >= self.local_decision_end:
            raise ValueError("local decision window must be increasing")
        if not 0.0 <= self.entry_price_min <= self.entry_price_max <= 1.0:
            raise ValueError("entry price bounds must be ordered probabilities")
        if self.max_market_reference_age_seconds <= 0:
            raise ValueError("market reference age must be positive")

    @property
    def spec_hash(self) -> str:
        return stable_hash(self.canonical_payload())

    def canonical_payload(self) -> dict[str, Any]:
        return _jsonable(asdict(self))

    def decision_id(self, source_row: dict[str, Any]) -> str:
        identity = {
            "signal_spec_hash": self.spec_hash,
            "source_snapshot_ids": sorted(_source_ids(source_row)),
            "station": source_row.get("station"),
            "market_date": source_row.get("market_date"),
            "market_id": source_row.get("selected_market_id"),
            "bucket": source_row.get("selected_bucket"),
            "side": source_row.get("selected_side"),
            "obs_delay_bucket": source_row.get("obs_delay_bucket"),
        }
        return f"v2d_{stable_hash(identity)[:24]}"


@dataclass(frozen=True)
class PriceSheetV2A:
    price_sheet_version: str
    signal_spec_id: str
    signal_spec_hash: str
    decision_id: str
    decision_time_utc: str
    quote_ready_time_utc: str
    model_ids: tuple[str, ...]
    raw_token_fair: float
    market_reference: float | None
    market_reference_kind: MarketReferenceKind
    calibrator_version: str
    calibrator_training_cutoff: str
    calibrated_outcome_fair: float
    uncertainty_reserve: float
    conservative_outcome_fair: float
    minimum_profit_reserve: float
    known_cost_reserve: float
    maximum_quote_price: float | None
    eligible: bool
    skip_reason: V2SkipReason | None


@dataclass(frozen=True)
class V2BExecutionOverlay:
    tape_session_id: str | None
    coverage_interval_id: int | None
    coverage_valid: bool
    fill_scenario_version: str
    toxicity_reserve: float
    latency_reserve: float
    execution_adjusted_maximum_quote: float | None
    size_capacity_limit_usd: float
    ttl_seconds: int
    cancellation_rules: tuple[str, ...]
    inventory_risk_reserve: float
    eligible: bool
    skip_reason: V2SkipReason | None


@dataclass(frozen=True)
class PriceSheetV2:
    contract_version: str
    v2a: PriceSheetV2A
    v2b: V2BExecutionOverlay | None


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_ids(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("source_prediction_snapshot_ids")
    if isinstance(raw, (list, tuple)):
        return tuple(int(value) for value in raw)
    if row.get("id") is None:
        raise ValueError("source row lacks a prediction snapshot id")
    return (int(row["id"]),)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _pilot_signal(signal_spec_id: str, model_id: str) -> SignalSpec:
    return SignalSpec(
        signal_spec_id=signal_spec_id,
        version=1,
        activation_timestamp="2026-07-16T00:00:00+00:00",
        model_ids=(model_id,),
        market_family=MarketFamily.HIGH_TEMP,
        station_allowlist=PILOT_STATIONS,
        selected_sides=(TradeAction.BUY_NO,),
        strategy_buckets=(StrategyBucket.HIGH_CONVICTION,),
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        entry_price_max=0.50,
        observation_delay_buckets=(),
        lifecycle_horizon="d0_late",
        dedupe_key_fields=(
            "station",
            "market_date",
            "market_family",
            "selected_bucket",
            "selected_side",
            "obs_delay_bucket",
        ),
        outcome_label_source=OutcomeLabelSource.IEM_ASOS_RESEARCH_HIGH,
        market_reference_rule="causal_midpoint_then_same_side_ask",
        max_market_reference_age_seconds=60.0,
        split_rule="expanding_market_date_train_strictly_before_evaluation_date",
        weighting_rule="equal_market_date_then_equal_station_date_then_snapshot",
        retrospective_start_date="2026-05-08",
        forward_start_date="2026-07-16",
    )


PILOT_SIGNAL_SPECS = (
    _pilot_signal("late_hrrr_rich_tuned_dynamic_buy_no_v1", HRRR_RICH_DYNAMIC_TUNED_MODEL),
    _pilot_signal("late_hrrr_v2_dynamic_buy_no_v1", HRRR_V2_DYNAMIC_MODEL),
)
