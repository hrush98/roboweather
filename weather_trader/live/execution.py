from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import requests
import time

from weather_trader.config import DEFAULT_LIVE_DB, MODELS_DIR
from weather_trader.calibration.bucket_probability import DEFAULT_BUCKET_CALIBRATION_PATH
from weather_trader.execution.books import RestBookClient
from weather_trader.execution.clob_executor import CancelSubmission, ClobExecutor, OrderSubmission
from weather_trader.execution.contracts import (
    BookSnapshot,
    LiveOrderAttempt,
    LiveOrderMode,
    LivePolicyPosition,
    LivePositionState,
    LiveRiskSnapshot,
    LiveStrategy,
    LiveTradeEvent,
    LiveTradeEventType,
    MarketFamily,
    MarketSnapshot,
    Signal,
    StrategyBucket,
    TradeAction,
    dataclass_to_jsonable,
    utc_now_iso,
)
from weather_trader.execution.discovery import MarketDiscoveryService, same_day_markets
from weather_trader.execution.fair_value import FairValueEngine, FairValueResult
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine, group_key
from weather_trader.execution.liquidity import quantize_price, quantize_shares, quantize_usdc, walk_ask_ladder
from weather_trader.execution.price_maker import build_phase1_price_sheet
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import CelsiusWeatherFeatureService, StationWeatherState, WeatherFeatureService
from weather_trader.live.settings import LiveSettings, load_live_settings, private_key_from_env_or_keyfile
from weather_trader.live.sizing import INSUFFICIENT_DEPTH, LiveSizingDecision, LiveSizingModel
from weather_trader.research.collector import ResearchConfig, build_prediction_snapshot, due_delay_buckets
from weather_trader.stations.metadata import get_station
from weather_trader.research.policies import (
    CATBOOST_MODEL,
    DYNAMIC_TUNED_MODEL,
    GLOBAL_LOW_DYNAMIC_MODEL,
    GLOBAL_LOW_MVP_MODEL,
    HRRR_RICH_DYNAMIC_TUNED_MODEL,
    METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
    NGBOOST_MODEL,
    ResearchPolicyEvaluator,
    ResearchPolicySpec,
)


LIVE_POLICY_NAME = "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first"
EDGE_CORE_POLICY_NAME = "pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first"
METAR_HRRR_RICH_CATBOOST_MVP_POLICY_NAME = "metar_hrrr_rich_catboost_mvp_entry_05_50"
HRRR_V2_THREE_MODEL_CONSENSUS_POLICY_NAME = "hrrr_v2_three_model_consensus_entry_05_50"
MOONSHOT_POLICY_NAME = "pm_us12_dynamic_tuned_hc_late_entry_05_10_buy_no_by_bucket_side_delay_first"
HRRR_INLAND_DISAGREEMENT_POLICY_NAME = "hrrr_dynamic_tuned_inland_late_disagreement_entry_00_50_by_bucket_side_delay_first"
METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME = "metar_hrrr_dynamic_tuned_inland_late_disagreement_entry_00_50_by_bucket_side_delay_first"
LIVE_MODEL_GROUP = "obs_bucket_consensus"
DEFAULT_LIVE_ENTRY_PRICE_MAX = 0.50
LIVE_ENTRY_PRICE_MAX = DEFAULT_LIVE_ENTRY_PRICE_MAX
EDGE_CORE_OBS_DELAY_BUCKET = "15m"
CONSENSUS_NOTIONAL_USD = 50.0
EDGE_CORE_NOTIONAL_USD = 50.0
MOONSHOT_MIN_EDGE = 0.90
MOONSHOT_NOTIONAL_USD = 2.0
NGBOOST_BEST_BUY_YES_POLICY_NAME = "pm_us12_ngboost_best_bucket_late_buy_yes_medium_by_bucket_side_delay_first"
NGBOOST_BEST_BUY_YES_NOTIONAL_USD = 10.0
HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD = 25.0
HRRR_INLAND_DISAGREEMENT_EDGE_MIN = 0.25
HRRR_INLAND_DISAGREEMENT_MIN = 0.15
HRRR_INLAND_OBS_EDGE_MAX = 0.10
METAR_HRRR_RICH_CATBOOST_MODEL = "catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_MVP_MODEL = "mvp_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_V2_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_hrrr_v2_obs_2022_2025"
HRRR_V2_CATBOOST_MODEL = "catboost_bucket_hrrr_v2_obs_2022_2025"
HRRR_V2_MVP_MODEL = "mvp_hrrr_v2_obs_2022_2025"
GLOBAL_LOW_CANARY_POLICY_NAME = "global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first"
GLOBAL_LOW_MVP_BUY_NO_POLICY_NAME = "global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first"
GLOBAL_LOW_MODEL_GROUP = "global_low_dynamic_mvp"
GLOBAL_LOW_NOTIONAL_USD = 100.0
GLOBAL_LOW_MVP_BUY_NO_NOTIONAL_USD = 50.0
GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN = 0.05
GLOBAL_LOW_ENTRY_PRICE_MAX = 0.75
GLOBAL_LOW_MVP_BUY_NO_ENTRY_PRICE_MAX = 0.50
GLOBAL_LOW_LOCAL_DECISION_START = "00:30"
GLOBAL_LOW_LOCAL_DECISION_END = "05:00"
LIVE_MODEL_PATHS = (
    MODELS_DIR / f"{DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{CATBOOST_MODEL}.joblib",
    MODELS_DIR / f"{HRRR_RICH_DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{METAR_HRRR_RICH_CATBOOST_MODEL}.joblib",
    MODELS_DIR / f"{METAR_HRRR_RICH_MVP_MODEL}.joblib",
    MODELS_DIR / f"{HRRR_V2_DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{HRRR_V2_CATBOOST_MODEL}.joblib",
    MODELS_DIR / f"{HRRR_V2_MVP_MODEL}.joblib",
    MODELS_DIR / f"{GLOBAL_LOW_MVP_MODEL}.joblib",
)
PM_ACTIVE_US12_STATIONS = frozenset(
    {
        "KATL",
        "KBOS",
        "KDCA",
        "KLGA",
        "KORD",
        "KBKF",
        "KDAL",
        "KLAX",
        "KMIA",
        "KSFO",
        "KSEA",
        "KHOU",
    }
)
HRRR_INLAND_STATIONS = frozenset({"KATL", "KDAL", "KORD"})
GLOBAL_LOW_STATIONS = frozenset({"EGLC", "LFPB", "RJTT", "RKSI", "VHHH", "ZSPD"})
CALIBRATION_FAMILY_MAP = {
    LIVE_POLICY_NAME: "obs",
    EDGE_CORE_POLICY_NAME: "obs",
    METAR_HRRR_RICH_CATBOOST_MVP_POLICY_NAME: "metar_hrrr_rich",
    HRRR_V2_THREE_MODEL_CONSENSUS_POLICY_NAME: "hrrr_v2",
    MOONSHOT_POLICY_NAME: "obs",
    HRRR_INLAND_DISAGREEMENT_POLICY_NAME: "hrrr_rich",
    METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME: "metar_hrrr_rich",
}
CALIBRATION_HRRR_EXECUTION_EXPERIMENT_POLICIES = frozenset(
    {
        HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
    }
)
CALIBRATION_MODE_TRADE_BLOCKER = "trade_blocker"
CALIBRATION_MODE_HRRR_EXECUTION_EXPERIMENT = "hrrr_execution_experiment"
CALIBRATION_COUNTER_KEYS = (
    "BLOCK",
    "CANARY",
    "WATCH",
    "TRADE",
    "INSUFFICIENT_DATA",
    "UNMAPPED",
    "EXPERIMENT_ALLOW",
    "TRADE_ALLOW",
    "DISABLED_BY_BUCKET_CALIBRATION",
    "bucket_missing",
    "family_unmapped",
    "disabled",
    "disabled_by_bucket_calibration",
)


@dataclass(frozen=True)
class LiveExecutionConfig:
    live_db_path: Path = DEFAULT_LIVE_DB
    model_paths: tuple[Path, ...] = LIVE_MODEL_PATHS
    mode: str = "dry-run"
    market_limit: int = 50000
    market_scope: str = "all"
    max_obs_age_minutes: int = 30
    max_book_age_seconds: float = 10.0
    max_notional_usd: float = CONSENSUS_NOTIONAL_USD
    consensus_notional_usd: float = CONSENSUS_NOTIONAL_USD
    min_entry_price: float = 0.05
    max_entry_price: float | None = DEFAULT_LIVE_ENTRY_PRICE_MAX
    require_allowance_check: bool = True
    retry_wait_seconds: float = 5.0
    enable_resting_fallback: bool = True
    resting_fallback_ttl_seconds: float = 420.0
    resting_fallback_notional_fraction: float = 1.0
    resting_fallback_chunk_usd: float = 25.0
    resting_fallback_price_step: float = 0.01
    initial_fak_slippage_cents: int = 1
    retry_fak_slippage_cents: int = 1
    resting_ladder_offsets_cents: tuple[int, ...] = (1, 0, -1, -2)
    resting_ladder_weights: tuple[float, ...] = (0.30, 0.40, 0.20, 0.10)
    resting_post_only_offsets_cents: tuple[int, ...] = (0, -1, -2)
    calibration_path: Path | None = None
    calibration_canary_notional_usd: float = 5.0
    calibration_unknown_behavior: str = "allow"
    bucket_calibration_path: Path | None = DEFAULT_BUCKET_CALIBRATION_PATH
    bucket_calibration_mode: str = "apply"


@dataclass(frozen=True)
class LiveCycleResult:
    candidates: int
    reserved: int
    submitted: int
    rejected: int
    skipped: int
    errors: list[str]
    debug: dict[str, Any] | None = None


@dataclass(frozen=True)
class LiveStrategyPlan:
    strategy: LiveStrategy
    policies: tuple[ResearchPolicySpec, ...]
    target_notional_usd: float
    selected_side: TradeAction | None = None
    min_entry_price: float | None = None


@dataclass(frozen=True)
class LiveAttemptResult:
    response: OrderSubmission
    state: LivePositionState
    limit_price: float
    target_notional_usd: float
    target_shares: float
    filled_shares: float
    cost_usd: float
    avg_price: float | None


@dataclass(frozen=True)
class LiveCandidate:
    plan: LiveStrategyPlan
    position: Any
    live_candidate_id: str | None = None


@dataclass(frozen=True)
class CalibrationDecision:
    candidate: LiveCandidate
    reject_reason: str | None
    metadata: dict[str, Any]


class LiveSubmitter(Protocol):
    def check_kill_switch(self) -> bool:
        ...

    def check_allowance_buy(self, required_usdc: float):
        ...

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        ...

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None, post_only: bool = False) -> OrderSubmission:
        ...

    def get_order(self, order_id: str) -> dict[str, Any]:
        ...

    def cancel_order(self, order_id: str) -> CancelSubmission:
        ...


def default_live_strategy(max_notional_usd: float = 3.0) -> LiveStrategy:
    return LiveStrategy(
        name=LIVE_POLICY_NAME,
        active=True,
        source="consensus",
        model_group=LIVE_MODEL_GROUP,
        model_names=[DYNAMIC_TUNED_MODEL, CATBOOST_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "resolved": 30,
                "win_rate": 0.667,
                "pnl": 7.952,
                "rr": 0.660,
                "sharpe": 0.542,
                "avg_entry": 0.402,
                "avg_edge": 0.560,
                "avg_fair": 0.961,
            }
        },
    )


def moonshot_live_strategy() -> LiveStrategy:
    return LiveStrategy(
        name=MOONSHOT_POLICY_NAME,
        active=True,
        source="model",
        model_group=DYNAMIC_TUNED_MODEL,
        model_names=[DYNAMIC_TUNED_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=MOONSHOT_NOTIONAL_USD,
        raw_payload={
            "report": {
                "resolved": 19,
                "win_rate": 0.158,
                "rr": 2.958,
                "sharpe": 0.337,
                "avg_entry": 0.039,
                "entry_price_max": 0.10,
                "or_edge_min": MOONSHOT_MIN_EDGE,
                "selected_side": str(TradeAction.BUY_NO),
            }
        },
    )


def edge_core_live_strategy(max_notional_usd: float = 3.0) -> LiveStrategy:
    return LiveStrategy(
        name=EDGE_CORE_POLICY_NAME,
        active=True,
        source="consensus",
        model_group=LIVE_MODEL_GROUP,
        model_names=[DYNAMIC_TUNED_MODEL, CATBOOST_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.0,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "resolved": 20,
                "win_rate": 0.400,
                "pnl": 4.553,
                "rr": 1.321,
                "sharpe": 0.505,
                "avg_entry": 0.172,
                "avg_edge": 0.819,
                "avg_fair": 0.992,
                "obs_delay_bucket": EDGE_CORE_OBS_DELAY_BUCKET,
                "replaced": "pm_us12_dynamic_tuned_hc_late_buy_no_edge_025_by_bucket_side_delay_first",
            }
        },
    )


def metar_hrrr_rich_catboost_mvp_live_strategy(max_notional_usd: float = CONSENSUS_NOTIONAL_USD) -> LiveStrategy:
    return LiveStrategy(
        name=METAR_HRRR_RICH_CATBOOST_MVP_POLICY_NAME,
        active=True,
        source="consensus",
        model_group="metar_hrrr_rich_catboost_mvp",
        model_names=[METAR_HRRR_RICH_CATBOOST_MODEL, METAR_HRRR_RICH_MVP_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "role": "live_promoted_candidate",
                "target_notional_usd": max_notional_usd,
                "entry_price_min": 0.05,
                "entry_price_max": DEFAULT_LIVE_ENTRY_PRICE_MAX,
            }
        },
    )


def hrrr_v2_three_model_consensus_live_strategy(max_notional_usd: float = CONSENSUS_NOTIONAL_USD) -> LiveStrategy:
    return LiveStrategy(
        name=HRRR_V2_THREE_MODEL_CONSENSUS_POLICY_NAME,
        active=True,
        source="consensus",
        model_group="hrrr_v2_three_model_consensus",
        model_names=[HRRR_V2_DYNAMIC_TUNED_MODEL, HRRR_V2_CATBOOST_MODEL, HRRR_V2_MVP_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "role": "live_promoted_candidate",
                "target_notional_usd": max_notional_usd,
                "entry_price_min": 0.05,
                "entry_price_max": DEFAULT_LIVE_ENTRY_PRICE_MAX,
            }
        },
    )


def ngboost_best_buy_yes_live_strategy() -> LiveStrategy:
    return LiveStrategy(
        name=NGBOOST_BEST_BUY_YES_POLICY_NAME,
        active=True,
        source="model",
        model_group=NGBOOST_MODEL,
        model_names=[NGBOOST_MODEL],
        strategy_bucket=StrategyBucket.BEST_BUCKET,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.05,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=NGBOOST_BEST_BUY_YES_NOTIONAL_USD,
        raw_payload={
            "report": {
                "resolved": 69,
                "win_rate": 0.449,
                "pnl": 3.409,
                "rr": 0.124,
                "recent_resolved": 32,
                "recent_win_rate": 0.625,
                "recent_rr": 0.372,
                "selected_side": str(TradeAction.BUY_YES),
            }
        },
    )


def hrrr_inland_disagreement_live_strategy() -> LiveStrategy:
    return LiveStrategy(
        name=HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        active=True,
        source="model",
        model_group=HRRR_RICH_DYNAMIC_TUNED_MODEL,
        model_names=[HRRR_RICH_DYNAMIC_TUNED_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.0,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
        raw_payload={
            "report": {
                "role": "execution_experiment",
                "target_notional_usd": HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
                "entry_price_max": DEFAULT_LIVE_ENTRY_PRICE_MAX,
                "edge_min": HRRR_INLAND_DISAGREEMENT_EDGE_MIN,
                "hrrr_disagreement_min": HRRR_INLAND_DISAGREEMENT_MIN,
                "obs_edge_max": HRRR_INLAND_OBS_EDGE_MAX,
                "local_decision_start": "12:00",
                "local_decision_end": "15:00",
                "stations": sorted(HRRR_INLAND_STATIONS),
            }
        },
    )


def metar_hrrr_inland_disagreement_live_strategy() -> LiveStrategy:
    return LiveStrategy(
        name=METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        active=True,
        source="model",
        model_group=METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
        model_names=[METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.HIGH_TEMP,
        local_decision_start="12:00",
        local_decision_end="15:00",
        entry_price_min=0.0,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
        raw_payload={
            "report": {
                "role": "execution_experiment",
                "target_notional_usd": HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
                "entry_price_max": DEFAULT_LIVE_ENTRY_PRICE_MAX,
                "edge_min": HRRR_INLAND_DISAGREEMENT_EDGE_MIN,
                "hrrr_disagreement_min": HRRR_INLAND_DISAGREEMENT_MIN,
                "obs_edge_max": HRRR_INLAND_OBS_EDGE_MAX,
                "local_decision_start": "12:00",
                "local_decision_end": "15:00",
                "stations": sorted(HRRR_INLAND_STATIONS),
            }
        },
    )


def live_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        LIVE_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group=LIVE_MODEL_GROUP,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        entry_price_min=config.min_entry_price,
        entry_price_max=config.max_entry_price,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def edge_core_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        EDGE_CORE_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group=LIVE_MODEL_GROUP,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        obs_delay_bucket=EDGE_CORE_OBS_DELAY_BUCKET,
        entry_price_min=0.0,
        entry_price_max=config.max_entry_price,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def moonshot_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        MOONSHOT_POLICY_NAME,
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=DYNAMIC_TUNED_MODEL,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        entry_price_min=0.05,
        entry_price_max=0.10,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def moonshot_edge_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        MOONSHOT_POLICY_NAME,
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=DYNAMIC_TUNED_MODEL,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        entry_price_min=0.05,
        entry_price_max=config.max_entry_price,
        edge_min=MOONSHOT_MIN_EDGE,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def ngboost_best_buy_yes_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        NGBOOST_BEST_BUY_YES_POLICY_NAME,
        "model",
        StrategyBucket.BEST_BUCKET,
        model_name=NGBOOST_MODEL,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        entry_price_min=0.05,
        entry_price_max=config.max_entry_price,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def hrrr_inland_disagreement_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=HRRR_RICH_DYNAMIC_TUNED_MODEL,
        station_allow_set=HRRR_INLAND_STATIONS,
        entry_price_min=0.0,
        entry_price_max=DEFAULT_LIVE_ENTRY_PRICE_MAX,
        edge_min=HRRR_INLAND_DISAGREEMENT_EDGE_MIN,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        hrrr_disagreement_min=HRRR_INLAND_DISAGREEMENT_MIN,
        obs_edge_max=HRRR_INLAND_OBS_EDGE_MAX,
    )


def metar_hrrr_inland_disagreement_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        METAR_HRRR_INLAND_DISAGREEMENT_POLICY_NAME,
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL,
        station_allow_set=HRRR_INLAND_STATIONS,
        entry_price_min=0.0,
        entry_price_max=DEFAULT_LIVE_ENTRY_PRICE_MAX,
        edge_min=HRRR_INLAND_DISAGREEMENT_EDGE_MIN,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        hrrr_disagreement_min=HRRR_INLAND_DISAGREEMENT_MIN,
        obs_edge_max=HRRR_INLAND_OBS_EDGE_MAX,
    )


def metar_hrrr_rich_catboost_mvp_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        METAR_HRRR_RICH_CATBOOST_MVP_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="metar_hrrr_rich_catboost_mvp",
        entry_price_min=0.05,
        entry_price_max=DEFAULT_LIVE_ENTRY_PRICE_MAX,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def hrrr_v2_three_model_consensus_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        HRRR_V2_THREE_MODEL_CONSENSUS_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="hrrr_v2_three_model_consensus",
        entry_price_min=0.05,
        entry_price_max=DEFAULT_LIVE_ENTRY_PRICE_MAX,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def global_low_canary_live_strategy(max_notional_usd: float = GLOBAL_LOW_NOTIONAL_USD) -> LiveStrategy:
    return LiveStrategy(
        name=GLOBAL_LOW_CANARY_POLICY_NAME,
        active=True,
        source="consensus",
        model_group=GLOBAL_LOW_MODEL_GROUP,
        model_names=[GLOBAL_LOW_DYNAMIC_MODEL, GLOBAL_LOW_MVP_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.LOW_TEMP,
        local_decision_start=GLOBAL_LOW_LOCAL_DECISION_START,
        local_decision_end=GLOBAL_LOW_LOCAL_DECISION_END,
        entry_price_min=GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "role": "live_canary",
                "target_notional_usd": max_notional_usd,
                "entry_price_min": GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
                "entry_price_max": GLOBAL_LOW_ENTRY_PRICE_MAX,
                "local_decision_start": GLOBAL_LOW_LOCAL_DECISION_START,
                "local_decision_end": GLOBAL_LOW_LOCAL_DECISION_END,
                "selected_side": str(TradeAction.BUY_NO),
            }
        },
    )


def global_low_mvp_buy_no_live_strategy(max_notional_usd: float = GLOBAL_LOW_MVP_BUY_NO_NOTIONAL_USD) -> LiveStrategy:
    return LiveStrategy(
        name=GLOBAL_LOW_MVP_BUY_NO_POLICY_NAME,
        active=True,
        source="model",
        model_group=GLOBAL_LOW_MVP_MODEL,
        model_names=[GLOBAL_LOW_MVP_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.LOW_TEMP,
        local_decision_start="00:00",
        local_decision_end="23:59",
        entry_price_min=GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "role": "live_additive_canary",
                "target_notional_usd": max_notional_usd,
                "entry_price_min": GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
                "entry_price_max": GLOBAL_LOW_MVP_BUY_NO_ENTRY_PRICE_MAX,
                "selected_side": str(TradeAction.BUY_NO),
            }
        },
    )


def global_low_canary_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        GLOBAL_LOW_CANARY_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group=GLOBAL_LOW_MODEL_GROUP,
        selected_side=TradeAction.BUY_NO,
        station_allow_set=GLOBAL_LOW_STATIONS,
        entry_price_min=GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
        entry_price_max=GLOBAL_LOW_ENTRY_PRICE_MAX,
        local_decision_start=GLOBAL_LOW_LOCAL_DECISION_START,
        local_decision_end=GLOBAL_LOW_LOCAL_DECISION_END,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def global_low_mvp_buy_no_policy_spec() -> ResearchPolicySpec:
    return ResearchPolicySpec(
        GLOBAL_LOW_MVP_BUY_NO_POLICY_NAME,
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=GLOBAL_LOW_MVP_MODEL,
        selected_side=TradeAction.BUY_NO,
        station_allow_set=GLOBAL_LOW_STATIONS,
        entry_price_min=GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
        entry_price_max=GLOBAL_LOW_MVP_BUY_NO_ENTRY_PRICE_MAX,
        local_decision_start="00:00",
        local_decision_end="23:59",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


def live_strategy_plans(config: LiveExecutionConfig) -> tuple[LiveStrategyPlan, ...]:
    return (
        LiveStrategyPlan(
            default_live_strategy(config.consensus_notional_usd),
            (live_policy_spec(config),),
            config.consensus_notional_usd,
            min_entry_price=config.min_entry_price,
        ),
        LiveStrategyPlan(
            metar_hrrr_rich_catboost_mvp_live_strategy(),
            (metar_hrrr_rich_catboost_mvp_policy_spec(),),
            CONSENSUS_NOTIONAL_USD,
            min_entry_price=0.05,
        ),
        LiveStrategyPlan(
            hrrr_v2_three_model_consensus_live_strategy(),
            (hrrr_v2_three_model_consensus_policy_spec(),),
            CONSENSUS_NOTIONAL_USD,
            min_entry_price=0.05,
        ),
        LiveStrategyPlan(
            moonshot_live_strategy(),
            (moonshot_policy_spec(), moonshot_edge_policy_spec(config)),
            MOONSHOT_NOTIONAL_USD,
            TradeAction.BUY_NO,
            config.min_entry_price,
        ),
        LiveStrategyPlan(
            hrrr_inland_disagreement_live_strategy(),
            (hrrr_inland_disagreement_policy_spec(),),
            HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
            min_entry_price=0.0,
        ),
        LiveStrategyPlan(
            metar_hrrr_inland_disagreement_live_strategy(),
            (metar_hrrr_inland_disagreement_policy_spec(),),
            HRRR_INLAND_DISAGREEMENT_NOTIONAL_USD,
            min_entry_price=0.0,
        ),
        LiveStrategyPlan(
            global_low_mvp_buy_no_live_strategy(),
            (global_low_mvp_buy_no_policy_spec(),),
            GLOBAL_LOW_MVP_BUY_NO_NOTIONAL_USD,
            TradeAction.BUY_NO,
            GLOBAL_LOW_CANARY_ENTRY_PRICE_MIN,
        ),
    )


class LiveWeatherFeatureService:
    def __init__(
        self,
        max_obs_age_minutes: int = 30,
        us_service: WeatherFeatureService | None = None,
        global_service: CelsiusWeatherFeatureService | None = None,
    ) -> None:
        self.us_service = us_service or WeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes)
        self.global_service = global_service or CelsiusWeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes)

    def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
        try:
            get_station(station_id)
        except KeyError:
            return self.global_service.get_state(station_id, as_of_utc)
        return self.us_service.get_state(station_id, as_of_utc)


class LiveExecutionEngine:
    def __init__(
        self,
        store: ExecutionStore,
        config: LiveExecutionConfig | None = None,
        discovery: MarketDiscoveryService | None = None,
        book_client: RestBookClient | None = None,
        weather_service: WeatherFeatureService | None = None,
        submitter: LiveSubmitter | None = None,
        settings: LiveSettings | None = None,
    ) -> None:
        self.store = store
        self.config = config or LiveExecutionConfig(live_db_path=store.path)
        self.discovery = discovery or MarketDiscoveryService()
        self.book_client = book_client or RestBookClient()
        self.weather_service = weather_service or LiveWeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        self.decision_engine = StationDateDecisionEngine()
        self.fair_value_engines = [
            FairValueEngine(
                path,
                bucket_calibration_path=self.config.bucket_calibration_path,
                bucket_calibration_mode=self.config.bucket_calibration_mode,
            )
            for path in self.config.model_paths
        ]
        self.strategy_plans = live_strategy_plans(self.config)
        self.policy_evaluator = ResearchPolicyEvaluator(store, tuple(policy for plan in self.strategy_plans for policy in plan.policies))
        self.settings = settings or load_live_settings()
        self.sizing_model = LiveSizingModel(self.settings)
        self.submitter = submitter
        self._default_submitter_instance: LiveSubmitter | None = None
        legacy_calibration_path = self.config.calibration_path if self.config.bucket_calibration_mode == "off" else None
        self.calibration = self._load_calibration(legacy_calibration_path)

    def run_once(self, as_of_utc: datetime | None = None) -> LiveCycleResult:
        now = as_of_utc or datetime.now(timezone.utc)
        errors: list[str] = []
        debug: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "mode": self.config.mode,
            "market_limit": self.config.market_limit,
            "market_scope": self.config.market_scope,
            "max_obs_age_minutes": self.config.max_obs_age_minutes,
            "max_book_age_seconds": self.config.max_book_age_seconds,
            "min_entry_price": self.config.min_entry_price,
            "max_entry_price": self.config.max_entry_price,
            "models": [str(path) for path in self.config.model_paths],
            "calibration_enabled": self.config.calibration_path is not None and self.config.bucket_calibration_mode == "off",
            "calibration_path": str(self.config.calibration_path) if self.config.calibration_path and self.config.bucket_calibration_mode == "off" else None,
            "calibration": self._empty_calibration_counts(),
            "bucket_calibration_mode": self.config.bucket_calibration_mode,
            "bucket_calibration_path": str(self.config.bucket_calibration_path) if self.config.bucket_calibration_path else None,
            "bucket_calibration_active": any(engine.bucket_calibration_active for engine in self.fair_value_engines),
        }
        for plan in self.strategy_plans:
            self.store.upsert_live_strategy(plan.strategy)
        self.store.deactivate_live_strategies_except({plan.strategy.name for plan in self.strategy_plans})
        try:
            discovered_markets = self.discovery.discover(limit=self.config.market_limit, market_scope=self.config.market_scope)
            same_day = same_day_markets(discovered_markets, now)
            markets = [market for market in same_day if _market_admitted_by_strategy_plans(market, self.strategy_plans)]
        except requests.RequestException as exc:
            debug["discovery_error"] = str(exc)
            return LiveCycleResult(0, 0, 0, 0, 0, [f"discovery: {exc}"], debug)
        debug.update(
            {
                "discovered_markets": len(discovered_markets),
                "same_day_markets": len(same_day),
                "live_admitted_markets": len(markets),
                "live_markets_by_family": _market_counts_by_family(markets),
                "discovery_warnings": list(getattr(self.discovery, "last_warnings", []))[:25],
            }
        )
        for market in markets:
            self.store.upsert_market(market)
        books = self._fetch_books(markets)
        debug["books"] = len(books)
        weather_by_station = self._fetch_weather(markets, now, errors)
        debug["weather"] = {
            "requested_stations": len({market.station for market in markets}),
            "loaded_stations": len(weather_by_station),
            "missing_stations": sorted({market.station for market in markets} - set(weather_by_station)),
        }
        candidates = self._build_candidates(markets, books, weather_by_station, now, errors, debug)

        reserved = submitted = rejected = skipped = 0
        reject_reasons: dict[str, int] = {}
        market_by_id = {market.market_id: market for market in markets}
        book_by_market_side = _book_by_market_side(markets, books)
        for candidate in candidates:
            market = market_by_id.get(candidate.position.selected_market_id)
            if market is None:
                skipped += 1
                reject_reasons["MISSING_MARKET"] = reject_reasons.get("MISSING_MARKET", 0) + 1
                continue
            selected_book = book_by_market_side.get((candidate.position.selected_market_id, str(candidate.position.selected_side)))
            reject_reason = self._candidate_reject_reason(candidate, selected_book)
            calibration_decision = self._apply_calibration(candidate)
            self._increment_calibration_debug(debug, calibration_decision.metadata)
            candidate = calibration_decision.candidate
            if reject_reason is None and calibration_decision.reject_reason is not None:
                reject_reason = calibration_decision.reject_reason
            sizing = self._size_candidate(candidate, now)
            if reject_reason is None and sizing.blocked_reason is not None:
                reject_reason = sizing.blocked_reason
            position = self._live_position(candidate, market, reject_reason=reject_reason, sizing=sizing, calibration=calibration_decision.metadata)
            position_id = self.store.insert_live_policy_position(position)
            if position_id is None:
                skipped += 1
                reject_reasons["DUPLICATE_POSITION"] = reject_reasons.get("DUPLICATE_POSITION", 0) + 1
                continue
            self.store.link_live_candidate_position(candidate.live_candidate_id, position_id)
            reserved += 1
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    position.strategy_name,
                    LiveTradeEventType.ENTRY_RESERVED,
                    "entry reserved",
                    position.raw_json,
                    live_candidate_id=position.live_candidate_id,
                )
            )
            if reject_reason is not None:
                rejected += 1
                reject_reasons[reject_reason] = reject_reasons.get(reject_reason, 0) + 1
                self._record_rejected(position_id, position, reject_reason)
                continue
            result_state = self._submit(position_id, position, market=market, initial_book=selected_book, as_of_utc=now, errors=errors)
            if result_state in {LivePositionState.SUBMITTED, LivePositionState.FILLED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}:
                submitted += 1
            elif result_state == LivePositionState.REJECTED:
                rejected += 1
                reject_reasons[str(result_state)] = reject_reasons.get(str(result_state), 0) + 1
        self._record_risk_snapshot()
        debug["result"] = {
            "candidates": len(candidates),
            "reserved": reserved,
            "submitted": submitted,
            "rejected": rejected,
            "skipped": skipped,
            "reject_reasons": reject_reasons,
            "errors": errors[:25],
        }
        return LiveCycleResult(len(candidates), reserved, submitted, rejected, skipped, errors, debug)

    def _fetch_books(self, markets: list[MarketSnapshot]) -> dict[str, BookSnapshot]:
        token_ids = sorted({token for market in markets for token in (market.yes_token_id, market.no_token_id) if token})
        books = self.book_client.fetch_books(token_ids)
        for book in books.values():
            self.store.insert_book_snapshot(book)
        return books

    def _fetch_weather(
        self,
        markets: list[MarketSnapshot],
        as_of_utc: datetime,
        errors: list[str],
    ) -> dict[str, StationWeatherState]:
        weather: dict[str, StationWeatherState] = {}
        for station in sorted({market.station for market in markets}):
            try:
                weather[station] = self.weather_service.get_state(station, as_of_utc)
            except Exception as exc:
                errors.append(f"weather:{station}: {exc}")
        return weather

    def _build_candidates(
        self,
        markets: list[MarketSnapshot],
        books: dict[str, BookSnapshot],
        weather_by_station: dict[str, StationWeatherState],
        as_of_utc: datetime,
        errors: list[str],
        debug: dict[str, Any] | None = None,
    ) -> list[Any]:
        snapshots: list[dict[str, Any]] = []
        grouped: dict[tuple[str, Any, str], list[MarketSnapshot]] = {}
        for market in markets:
            grouped.setdefault(group_key(market), []).append(market)
        cycle_timestamp = as_of_utc.isoformat()
        market_by_id = {market.market_id: market for market in markets}
        book_by_market_side = _book_by_market_side(markets, books)
        research_config = ResearchConfig(max_obs_age_minutes=self.config.max_obs_age_minutes, bankroll_usd=1000.0, market_limit=self.config.market_limit)
        debug_groups: list[dict[str, Any]] = []
        model_snapshot_counts: dict[str, int] = {}
        due_group_count = 0
        for engine in self.fair_value_engines:
            for key, group_markets in grouped.items():
                station_id, market_date, market_family = key
                if market_date is None or not engine.supports_market_family(market_family):
                    continue
                weather = weather_by_station.get(station_id)
                if weather is None:
                    continue
                due_buckets = due_delay_buckets(weather, as_of_utc, research_config, market_family=market_family)
                if not due_buckets:
                    continue
                due_group_count += 1
                try:
                    fair_values = engine.price_markets(group_markets, weather)
                    contexts = [
                        GroupMarketContext(
                            market=market,
                            signal=self._build_signal(market, books, weather, fair_values[market.market_id]),
                            yes_book=books.get(market.yes_token_id or ""),
                            no_book=books.get(market.no_token_id or ""),
                        )
                        for market in group_markets
                    ]
                    required_strategy_buckets = sorted(
                        {
                            policy.strategy_bucket
                            for plan in self.strategy_plans
                            for policy in plan.policies
                            if policy.strategy_bucket is not None
                        },
                        key=str,
                    )
                    for strategy_bucket in required_strategy_buckets:
                        selection = self.decision_engine.select_strategy(contexts, 1000.0, strategy_bucket)
                        for bucket in due_buckets:
                            snapshot = build_prediction_snapshot(selection, contexts, weather, market_date, as_of_utc, bucket, engine.model_name)
                            persisted_snapshot_id = self.store.insert_or_get_prediction_snapshot(snapshot)
                            item = dataclass_to_jsonable(snapshot)
                            item["id"] = persisted_snapshot_id
                            item["live_candidate_id"] = self._record_model_candidate(
                                item,
                                prediction_snapshot_id=persisted_snapshot_id,
                                cycle_timestamp=cycle_timestamp,
                                as_of_utc=as_of_utc,
                                market_by_id=market_by_id,
                                book_by_market_side=book_by_market_side,
                            )
                            snapshots.append(item)
                            model_snapshot_counts[engine.model_name] = model_snapshot_counts.get(engine.model_name, 0) + 1
                            if len(debug_groups) < 100:
                                debug_groups.append(
                                    {
                                        "model": engine.model_name,
                                        "station": station_id,
                                        "market_date": str(market_date),
                                        "market_family": str(market_family),
                                        "strategy_bucket": str(strategy_bucket),
                                        "obs_delay_bucket": str(bucket),
                                        "selected_side": item.get("selected_side"),
                                        "selected_bucket": item.get("selected_bucket"),
                                        "selected_market_id": item.get("selected_market_id"),
                                        "selected_edge": item.get("selected_edge"),
                                        "selected_yes_ask": item.get("selected_yes_ask"),
                                        "selected_no_ask": item.get("selected_no_ask"),
                                        "skip_reason": item.get("skip_reason"),
                                    }
                                )
                except Exception as exc:
                    errors.append(f"model:{engine.model_name}:group:{station_id}:{market_date}: {exc}")
        consensus = self.policy_evaluator._build_consensus(snapshots)
        candidates: list[LiveCandidate] = []
        plan_debug: list[dict[str, Any]] = []
        for plan in self.strategy_plans:
            plan_candidates: dict[tuple[object, ...], LiveCandidate] = {}
            policy_debug: list[dict[str, Any]] = []
            for policy in plan.policies:
                filtered = self.policy_evaluator._candidates_for_policy(policy, snapshots, consensus)
                pre_side_count = len(filtered)
                if plan.selected_side is not None:
                    filtered = [item for item in filtered if item.get("selected_side") == str(plan.selected_side)]
                first_by_scope = self.policy_evaluator._first_by_scope(policy, filtered)
                position_count = 0
                for candidate in first_by_scope:
                    position = self.policy_evaluator._position_from_candidate(policy, candidate)
                    if position is None:
                        continue
                    live_candidate_id = self._record_policy_candidate(
                        plan,
                        policy,
                        position,
                        cycle_timestamp=cycle_timestamp,
                        as_of_utc=as_of_utc,
                        market_by_id=market_by_id,
                        book_by_market_side=book_by_market_side,
                    )
                    position = replace(
                        position,
                        raw_policy={
                            **dict(position.raw_policy or {}),
                            "live_candidate_id": live_candidate_id,
                            "source_live_candidate_ids": self._source_live_candidate_ids(candidate),
                        },
                    )
                    price_sheet_record = self._record_phase1_price_sheet(
                        plan,
                        policy,
                        position,
                        live_candidate_id=live_candidate_id,
                        as_of_utc=as_of_utc,
                        market_by_id=market_by_id,
                        book_by_market_side=book_by_market_side,
                    )
                    if price_sheet_record is not None:
                        price_sheet_id, price_sheet = price_sheet_record
                        position = replace(
                            position,
                            raw_policy={
                                **dict(position.raw_policy or {}),
                                "phase1_price_sheet_id": price_sheet_id,
                                "phase1_price_sheet": dataclass_to_jsonable(price_sheet),
                            },
                        )
                    position_count += 1
                    key = (position.station, position.market_date, position.market_family, position.scope_key)
                    existing = plan_candidates.get(key)
                    if existing is None or position.timestamp < existing.position.timestamp:
                        plan_candidates[key] = LiveCandidate(plan, position, live_candidate_id)
                policy_debug.append(
                    {
                        "policy": policy.name,
                        "source": policy.source,
                        "model_name": policy.model_name,
                        "model_group": policy.model_group,
                        "strategy_bucket": str(policy.strategy_bucket) if policy.strategy_bucket else None,
                        "pre_side_filter": pre_side_count,
                        "post_side_filter": len(filtered),
                        "first_by_scope": len(first_by_scope),
                        "positions": position_count,
                    }
                )
            for candidate in sorted(plan_candidates.values(), key=lambda item: (item.position.timestamp, item.position.station, item.position.scope_key)):
                position = candidate.position
                if position is not None:
                    candidates.append(candidate)
            plan_debug.append(
                {
                    "strategy": plan.strategy.name,
                    "selected_side": str(plan.selected_side) if plan.selected_side else None,
                    "candidates": len(plan_candidates),
                    "policies": policy_debug,
                }
            )
        if debug is not None:
            debug["candidate_build"] = {
                "groups": len(grouped),
                "due_model_groups": due_group_count,
                "snapshots": len(snapshots),
                "snapshots_by_model": model_snapshot_counts,
                "consensus": len(consensus),
                "sample_snapshots": debug_groups,
                "plans": plan_debug,
            }
        return candidates

    def _record_model_candidate(
        self,
        item: dict[str, Any],
        *,
        prediction_snapshot_id: int,
        cycle_timestamp: str,
        as_of_utc: datetime,
        market_by_id: dict[str, MarketSnapshot],
        book_by_market_side: dict[tuple[str, str], BookSnapshot],
    ) -> str:
        selected_side = _optional_text(item.get("selected_side"))
        selected_market_id = _optional_text(item.get("selected_market_id"))
        selected_token_id = self._selected_token_id(selected_market_id, selected_side, market_by_id)
        selected_book = book_by_market_side.get((selected_market_id or "", selected_side or ""))
        candidate_id = self._stable_live_candidate_id(
            "model",
            cycle_timestamp,
            prediction_snapshot_id,
            item.get("station"),
            item.get("market_date"),
            item.get("market_family"),
            item.get("obs_delay_bucket"),
            item.get("strategy_bucket"),
            item.get("model_name"),
            selected_market_id,
            selected_side,
            item.get("selected_bucket"),
        )
        self.store.insert_live_candidate_snapshot(
            candidate_id=candidate_id,
            cycle_timestamp=cycle_timestamp,
            local_receipt_timestamp=utc_now_iso(),
            source_stage="MODEL_SNAPSHOT",
            station=str(item.get("station") or ""),
            market_date=item.get("market_date"),
            market_family=str(item.get("market_family") or "HIGH_TEMP"),
            obs_delay_bucket=_optional_text(item.get("obs_delay_bucket")),
            strategy_bucket=_optional_text(item.get("strategy_bucket")),
            model_name=_optional_text(item.get("model_name")),
            prediction_snapshot_id=prediction_snapshot_id,
            source_prediction_snapshot_ids=[prediction_snapshot_id],
            selected_market_id=selected_market_id,
            selected_token_id=selected_token_id,
            selected_side=selected_side,
            selected_bucket=_optional_text(item.get("selected_bucket")),
            entry_price=_entry_price_from_item(item),
            entry_fair=_selected_fair_from_item(item),
            entry_edge=_float_or_none(item.get("selected_edge")),
            quote_features=self._quote_lifecycle_features(selected_token_id, selected_book, as_of_utc),
            raw_payload=item,
        )
        return candidate_id

    def _record_policy_candidate(
        self,
        plan: LiveStrategyPlan,
        policy: ResearchPolicySpec,
        position: Any,
        *,
        cycle_timestamp: str,
        as_of_utc: datetime,
        market_by_id: dict[str, MarketSnapshot],
        book_by_market_side: dict[tuple[str, str], BookSnapshot],
    ) -> str:
        selected_side = str(position.selected_side)
        selected_market_id = str(position.selected_market_id)
        selected_token_id = self._selected_token_id(selected_market_id, selected_side, market_by_id)
        selected_book = book_by_market_side.get((selected_market_id, selected_side))
        source_ids = [int(value) for value in position.source_prediction_snapshot_ids]
        candidate_id = self._stable_live_candidate_id(
            "policy",
            cycle_timestamp,
            plan.strategy.name,
            policy.name,
            position.station,
            position.market_date,
            position.market_family,
            position.scope_key,
            *source_ids,
        )
        self.store.insert_live_candidate_snapshot(
            candidate_id=candidate_id,
            cycle_timestamp=cycle_timestamp,
            local_receipt_timestamp=utc_now_iso(),
            source_stage="POLICY_CANDIDATE",
            strategy_name=plan.strategy.name,
            policy_name=policy.name,
            model_group=str(position.model_group),
            station=str(position.station),
            market_date=position.market_date,
            market_family=str(position.market_family),
            obs_delay_bucket=str(position.obs_delay_bucket),
            strategy_bucket=str(position.strategy_bucket),
            source_prediction_snapshot_ids=source_ids,
            selected_market_id=selected_market_id,
            selected_token_id=selected_token_id,
            selected_side=selected_side,
            selected_bucket=position.selected_bucket,
            entry_price=position.entry_price,
            entry_fair=position.entry_fair,
            entry_edge=position.entry_edge,
            quote_features=self._quote_lifecycle_features(selected_token_id, selected_book, as_of_utc),
            raw_payload=dataclass_to_jsonable(position),
        )
        return candidate_id

    def _record_phase1_price_sheet(
        self,
        plan: LiveStrategyPlan,
        policy: ResearchPolicySpec,
        position: Any,
        *,
        live_candidate_id: str,
        as_of_utc: datetime,
        market_by_id: dict[str, MarketSnapshot],
        book_by_market_side: dict[tuple[str, str], BookSnapshot],
    ) -> tuple[int | None, Any] | None:
        if plan.strategy.name != LIVE_POLICY_NAME:
            return None
        selected_side = str(position.selected_side)
        selected_market_id = str(position.selected_market_id)
        selected_token_id = self._selected_token_id(selected_market_id, selected_side, market_by_id)
        selected_book = book_by_market_side.get((selected_market_id, selected_side))
        quote_features = self._quote_lifecycle_features(selected_token_id, selected_book, as_of_utc)
        sheet = build_phase1_price_sheet(
            live_candidate_id=live_candidate_id,
            strategy_name=plan.strategy.name,
            policy_name=policy.name,
            source=position,
            selected_token_id=selected_token_id,
            quote_features=quote_features,
            as_of_utc=as_of_utc,
            target_notional_usd=plan.target_notional_usd,
        )
        sheet_id = self.store.insert_live_price_sheet(sheet)
        return sheet_id, sheet

    def _quote_lifecycle_features(
        self,
        token_id: str | None,
        book: BookSnapshot | None,
        as_of_utc: datetime,
    ) -> dict[str, Any]:
        features: dict[str, Any] = {
            "version": 1,
            "source": "rest_book_plus_clob_feed_events",
            "decision_time_utc": as_of_utc.isoformat(),
            "token_id": token_id,
            "top_book_age_seconds": None,
            "top_level_add_count_5m": 0,
            "top_level_cancel_count_5m": 0,
            "spread_change_count_5m": 0,
            "recent_trade_count_5m": 0,
            "selected_ask_just_posted": None,
            "selected_ask_just_depleted": None,
        }
        if book is not None:
            features.update(
                {
                    "rest_book_timestamp": book.timestamp,
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "spread": book.spread,
                    "top_bid_size": book.bids[0].size if book.bids else None,
                    "top_ask_size": book.asks[0].size if book.asks else None,
                    "ask_depth_at_top": book.ask_depth_usd(book.best_ask) if book.best_ask is not None else 0.0,
                    "bid_depth_at_top": book.bid_depth_usd(book.best_bid) if book.best_bid is not None else 0.0,
                }
            )
            features["top_book_age_seconds"] = _seconds_between(book.timestamp, as_of_utc)
            bid_size = float(book.bids[0].size) if book.bids else 0.0
            ask_size = float(book.asks[0].size) if book.asks else 0.0
            total = bid_size + ask_size
            features["top_of_book_imbalance"] = ((bid_size - ask_size) / total) if total > 0 else None
        if token_id:
            summary = self.store.clob_feed_summary(token_id, received_before=as_of_utc.isoformat(), lookback_seconds=300.0)
            features["feed_event_summary_5m"] = summary
            features["top_level_add_count_5m"] = summary["top_level_add_count"]
            features["top_level_cancel_count_5m"] = summary["top_level_cancel_count"]
            features["spread_change_count_5m"] = summary["event_counts"].get("best_bid_ask", 0)
            features["recent_trade_count_5m"] = summary["event_counts"].get("last_trade_price", 0)
            if summary["latest_event_received_at"] is not None:
                features["latest_feed_event_received_at"] = summary["latest_event_received_at"]
                features["selected_ask_just_posted"] = summary["top_level_add_count"] > 0
                features["selected_ask_just_depleted"] = summary["top_level_cancel_count"] > 0
        return features

    def _source_live_candidate_ids(self, candidate: dict[str, Any]) -> list[str]:
        result: list[str] = []
        values = candidate.get("source_live_candidate_ids")
        if isinstance(values, list):
            result.extend(str(item) for item in values if item)
        value = candidate.get("live_candidate_id")
        if isinstance(value, str) and value and value not in result:
            result.append(value)
        return result

    def _selected_token_id(
        self,
        selected_market_id: str | None,
        selected_side: str | None,
        market_by_id: dict[str, MarketSnapshot],
    ) -> str | None:
        if selected_market_id is None or selected_side is None:
            return None
        market = market_by_id.get(selected_market_id)
        if market is None:
            return None
        if selected_side == str(TradeAction.BUY_YES):
            return market.yes_token_id
        if selected_side == str(TradeAction.BUY_NO):
            return market.no_token_id
        return None

    def _stable_live_candidate_id(self, stage: str, *parts: Any) -> str:
        payload = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"{stage}_{digest}"

    def _build_signal(
        self,
        market: MarketSnapshot,
        books: dict[str, BookSnapshot],
        weather: StationWeatherState,
        fair: FairValueResult,
    ) -> Signal:
        yes_book = books.get(market.yes_token_id or "")
        no_book = books.get(market.no_token_id or "")
        yes_ask = yes_book.best_ask if yes_book else None
        no_ask = no_book.best_ask if no_book else None
        edge_yes = fair.fair_yes - yes_ask if yes_ask is not None else None
        edge_no = fair.fair_no - no_ask if no_ask is not None else None
        signal_side = TradeAction.SKIP
        if edge_yes is not None or edge_no is not None:
            if (edge_yes if edge_yes is not None else float("-inf")) >= (edge_no if edge_no is not None else float("-inf")):
                signal_side = TradeAction.BUY_YES
            else:
                signal_side = TradeAction.BUY_NO
        return Signal(
            timestamp=utc_now_iso(),
            market_id=market.market_id,
            question=market.question,
            station=market.station,
            market_date=market.market_date,
            lower_f=market.lower_f,
            upper_f=market.upper_f,
            current_temp=weather.current_temp,
            high_so_far=weather.high_so_far,
            latest_obs_time=weather.latest_obs_time,
            hrrr_remaining_max=weather.hrrr_remaining_max,
            fair_yes=fair.fair_yes,
            fair_no=fair.fair_no,
            yes_bid=yes_book.best_bid if yes_book else None,
            yes_ask=yes_ask,
            yes_depth_usd=yes_book.ask_depth_usd(yes_ask) if yes_book and yes_ask is not None else 0.0,
            no_bid=no_book.best_bid if no_book else None,
            no_ask=no_ask,
            no_depth_usd=no_book.ask_depth_usd(no_ask) if no_book and no_ask is not None else 0.0,
            edge_yes=edge_yes,
            edge_no=edge_no,
            signal_side=signal_side,
            reason_codes=fair.reason_codes,
            model_name=fair.model_name,
            model_features_hash=fair.model_features_hash,
            market_family=market.market_family,
            low_so_far=weather.low_so_far,
            hrrr_remaining_min=weather.hrrr_remaining_min,
            raw_fair_yes=fair.raw_fair_yes,
            raw_fair_no=fair.raw_fair_no,
            bucket_calibration=fair.bucket_calibration,
        )

    def _candidate_reject_reason(self, candidate: LiveCandidate, book: BookSnapshot | None) -> str | None:
        if book is None or book.best_ask is None:
            return "MISSING_BOOK"
        if candidate.plan.min_entry_price is not None and candidate.position.entry_price < candidate.plan.min_entry_price:
            return "ENTRY_PRICE_TOO_LOW"
        if candidate.position.selected_book_age_seconds is not None and candidate.position.selected_book_age_seconds > self.config.max_book_age_seconds:
            return "STALE_BOOK"
        return None

    def _load_calibration(self, path: Path | None) -> dict[str, Any]:
        if path is None:
            return {"version": 1, "families": {}}
        with Path(path).expanduser().open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("families"), dict):
            raise ValueError(f"Invalid calibration JSON: {path}")
        return payload

    def _empty_calibration_counts(self) -> dict[str, int]:
        return {key: 0 for key in CALIBRATION_COUNTER_KEYS}

    def _increment_calibration_debug(self, debug: dict[str, Any], metadata: dict[str, Any]) -> None:
        counts = debug.setdefault("calibration", self._empty_calibration_counts())
        reason = str(metadata.get("reason") or "")
        decision = str(metadata.get("decision") or "")
        effect = str(metadata.get("calibration_effect") or "")
        seen: set[str] = set()
        reason_key = reason if reason in {"disabled", "family_unmapped", "bucket_missing", "disabled_by_bucket_calibration"} else None
        for key in (reason_key, decision, effect):
            if key in counts and key not in seen:
                counts[key] += 1
                seen.add(key)

    def _apply_calibration(self, candidate: LiveCandidate) -> CalibrationDecision:
        if self.config.bucket_calibration_mode == "apply":
            metadata = {
                "enabled": False,
                "reason": "disabled_by_bucket_calibration",
                "decision": "DISABLED",
                "bucket_calibration_mode": self.config.bucket_calibration_mode,
                "bucket_calibration_path": str(self.config.bucket_calibration_path) if self.config.bucket_calibration_path else None,
                "legacy_calibration_path_ignored": str(self.config.calibration_path) if self.config.calibration_path else None,
                "calibration_effect": "DISABLED_BY_BUCKET_CALIBRATION",
            }
            return CalibrationDecision(candidate, None, metadata)
        metadata = self._calibration_metadata(candidate)
        decision = metadata.get("decision")
        if metadata.get("enabled") is False:
            return CalibrationDecision(candidate, None, metadata)

        mode = self._calibration_mode(candidate)
        metadata = dict(metadata)
        metadata["calibration_mode"] = mode
        if mode == CALIBRATION_MODE_HRRR_EXECUTION_EXPERIMENT:
            if decision == "BLOCK":
                metadata["calibration_effect"] = "BLOCK"
                return CalibrationDecision(candidate, "CALIBRATION_BLOCK", metadata)
            metadata["calibration_effect"] = "EXPERIMENT_ALLOW"
            return CalibrationDecision(candidate, None, metadata)

        if decision == "TRADE":
            metadata["calibration_effect"] = "TRADE_ALLOW"
            return CalibrationDecision(candidate, None, metadata)

        metadata["calibration_effect"] = "BLOCK"
        return CalibrationDecision(candidate, "CALIBRATION_BLOCK", metadata)

    def _calibration_mode(self, candidate: LiveCandidate) -> str:
        if candidate.plan.strategy.name in CALIBRATION_HRRR_EXECUTION_EXPERIMENT_POLICIES:
            return CALIBRATION_MODE_HRRR_EXECUTION_EXPERIMENT
        return CALIBRATION_MODE_TRADE_BLOCKER

    def _calibration_metadata(self, candidate: LiveCandidate) -> dict[str, Any]:
        if self.config.calibration_path is None:
            return {"enabled": False, "reason": "disabled", "decision": "DISABLED"}
        family = CALIBRATION_FAMILY_MAP.get(candidate.plan.strategy.name)
        if family is None:
            return {
                "enabled": True,
                "reason": "family_unmapped",
                "decision": "UNMAPPED",
                "strategy": candidate.plan.strategy.name,
                "generated_at": self.calibration.get("generated_at"),
            }
        source = candidate.position
        side = str(source.selected_side)
        band = _calibration_entry_band(float(source.entry_price))
        family_data = self.calibration.get("families", {}).get(family) or {}
        bucket_data = (
            family_data.get("buckets", {})
            .get(str(source.station), {})
            .get(side, {})
            .get(band)
        )
        base = {
            "enabled": True,
            "family": family,
            "station": str(source.station),
            "side": side,
            "entry_band": band,
            "generated_at": self.calibration.get("generated_at"),
        }
        if not isinstance(bucket_data, dict):
            base.update({"reason": "bucket_missing", "decision": "INSUFFICIENT_DATA"})
            return base
        base.update({key: value for key, value in bucket_data.items() if not str(key).startswith("_")})
        base["reason"] = "bucket_match"
        return base


    def _initial_execution_price(self, entry_price: float) -> float:
        return _quantize_cent_price(float(entry_price) + (float(self.config.initial_fak_slippage_cents) / 100.0))

    def _retry_execution_price(self, entry_price: float) -> float:
        return _quantize_cent_price(float(entry_price) + (float(self.config.retry_fak_slippage_cents) / 100.0))

    def _execution_contract(self, source: Any) -> dict[str, Any]:
        scored_entry = _quantize_cent_price(float(source.entry_price))
        return {
            "version": 1,
            "scored_entry_price": scored_entry,
            "max_immediate_price": self._initial_execution_price(scored_entry),
            "max_retry_price": self._retry_execution_price(scored_entry),
            "resting_ladder_offsets_cents": list(self.config.resting_ladder_offsets_cents),
            "resting_ladder_weights": list(self.config.resting_ladder_weights),
            "resting_post_only_offsets_cents": list(self.config.resting_post_only_offsets_cents),
            "resting_ttl_seconds": float(self.config.resting_fallback_ttl_seconds),
            "model_fair_price": source.entry_fair,
            "model_edge_at_entry": source.entry_edge,
            "selected_book_timestamp": source.selected_book_timestamp,
            "selected_book_age_seconds": source.selected_book_age_seconds,
            "legacy_selected_sweep_price_cap": source.selected_sweep_price_cap,
        }

    def _size_candidate(self, candidate: LiveCandidate, as_of_utc: datetime) -> LiveSizingDecision:
        source = candidate.position
        entry_price = self._initial_execution_price(source.entry_price)
        exposure = self.store.live_exposure_summary()
        depth_limited = self.sizing_model.size_candidate(
            strategy_name=candidate.plan.strategy.name,
            entry_price=entry_price,
            station=str(source.station),
            market_date=source.market_date,
            selected_side=source.selected_side,
            selected_bucket=source.selected_bucket,
            sweep_depth_to_cap=source.selected_depth_ask_plus_0_01,
            exposure=exposure,
            target_notional_usd=candidate.plan.target_notional_usd,
            as_of_utc=as_of_utc,
        )
        if depth_limited.blocked_reason is not None and depth_limited.blocked_reason != INSUFFICIENT_DEPTH:
            return depth_limited

        risk_limited = self.sizing_model.size_candidate(
            strategy_name=candidate.plan.strategy.name,
            entry_price=entry_price,
            station=str(source.station),
            market_date=source.market_date,
            selected_side=source.selected_side,
            selected_bucket=source.selected_bucket,
            sweep_depth_to_cap=None,
            exposure=exposure,
            target_notional_usd=candidate.plan.target_notional_usd,
            as_of_utc=as_of_utc,
        )
        if risk_limited.blocked_reason is not None:
            return risk_limited

        depth_cap = depth_limited.caps.get(INSUFFICIENT_DEPTH)
        depth_clipped = (
            depth_cap is not None
            and depth_limited.target_notional_usd < risk_limited.target_notional_usd
        )
        if not depth_clipped and depth_limited.blocked_reason != INSUFFICIENT_DEPTH:
            return depth_limited

        raw = dict(risk_limited.raw_json)
        raw["depth_limited_sweep_sizing"] = depth_limited.raw_json
        raw["resting_ladder_source_reason"] = INSUFFICIENT_DEPTH
        if depth_limited.blocked_reason == INSUFFICIENT_DEPTH:
            raw["resting_ladder_on_insufficient_depth"] = True
        else:
            raw["initial_fak_notional_usd"] = depth_limited.target_notional_usd
            raw["resting_ladder_after_depth_limited_fak"] = True
        return LiveSizingDecision(
            risk_limited.target_notional_usd,
            risk_limited.base_notional_usd,
            risk_limited.policy_multiplier,
            risk_limited.price_multiplier,
            risk_limited.pre_cap_target_usd,
            risk_limited.caps,
            risk_limited.blocked_reason,
            raw,
        )

    def _live_position(
        self,
        candidate: LiveCandidate,
        market: MarketSnapshot,
        *,
        reject_reason: str | None,
        sizing: LiveSizingDecision,
        calibration: dict[str, Any] | None = None,
    ) -> LivePolicyPosition:
        source = candidate.position
        token_id = market.yes_token_id if source.selected_side == TradeAction.BUY_YES else market.no_token_id
        limit_price = self._initial_execution_price(source.entry_price)
        execution_contract = self._execution_contract(source)
        target_notional = quantize_usdc(sizing.target_notional_usd)
        target_shares = quantize_shares(target_notional / limit_price) if limit_price > 0 else 0.0
        return LivePolicyPosition(
            timestamp=utc_now_iso(),
            strategy_name=candidate.plan.strategy.name,
            station=source.station,
            market_date=source.market_date,
            market_family=MarketFamily(str(source.market_family)),
            scope_key=source.scope_key,
            selected_market_id=source.selected_market_id,
            selected_token_id=str(token_id or ""),
            selected_side=source.selected_side,
            selected_bucket=source.selected_bucket,
            obs_delay_bucket=source.obs_delay_bucket,
            entry_price=source.entry_price,
            entry_fair=source.entry_fair,
            entry_edge=source.entry_edge,
            target_notional_usd=target_notional,
            target_shares=target_shares,
            state=LivePositionState.RESERVED,
            source_prediction_snapshot_ids=source.source_prediction_snapshot_ids,
            live_candidate_id=candidate.live_candidate_id,
            raw_json={
                "candidate": dataclass_to_jsonable(source),
                "strategy": dataclass_to_jsonable(candidate.plan.strategy),
                "live_candidate_id": candidate.live_candidate_id,
                "limit_price": limit_price,
                "reject_reason": reject_reason,
                "sizing": sizing.raw_json,
                "calibration": calibration or {"enabled": False, "reason": "disabled", "decision": "DISABLED"},
            },
        )

    def _record_rejected(self, position_id: int, position: LivePolicyPosition, reason: str) -> None:
        self.store.update_live_policy_position_execution(position_id, state=str(LivePositionState.REJECTED), raw_patch={"final_reason": reason})
        self.store.insert_live_order_attempt(
            LiveOrderAttempt(
                utc_now_iso(),
                position_id,
                self.store.next_live_attempt_seq(position_id),
                position.selected_token_id,
                position.selected_side,
                LiveOrderMode.FAK,
                float(position.raw_json["limit_price"]),
                position.target_notional_usd,
                position.target_shares,
                None,
                None,
                LivePositionState.REJECTED,
                reason,
                0.0,
                None,
                0.0,
                {"blocked": True, "reason": reason, "calibration": position.raw_json.get("calibration")},
                live_candidate_id=position.live_candidate_id,
            )
        )

    def _submit(
        self,
        position_id: int,
        position: LivePolicyPosition,
        *,
        market: MarketSnapshot | None = None,
        initial_book: BookSnapshot | None = None,
        as_of_utc: datetime | None = None,
        errors: list[str] | None = None,
    ) -> LivePositionState:
        errors = errors if errors is not None else []
        limit_price = self._initial_execution_price(float(position.entry_price))
        sizing_raw = position.raw_json.get("sizing") if isinstance(position.raw_json, dict) else None
        if isinstance(sizing_raw, dict) and sizing_raw.get("resting_ladder_on_insufficient_depth"):
            fallback_state = self._submit_resting_fallback(
                position_id=position_id,
                position=position,
                current_cost_usd=0.0,
                current_filled_shares=0.0,
                book=initial_book,
                source_reason=str(sizing_raw.get("resting_ladder_source_reason") or INSUFFICIENT_DEPTH),
                errors=errors,
            )
            if fallback_state is not None:
                return fallback_state
            self.store.update_live_policy_position_execution(
                position_id,
                state=str(LivePositionState.REJECTED),
                raw_patch={"final_reason": "RESTING_LADDER_SKIPPED_AFTER_INSUFFICIENT_DEPTH"},
            )
            return LivePositionState.REJECTED

        initial_fak_notional = _initial_fak_notional_usd(position)
        first_target_notional = initial_fak_notional if initial_fak_notional is not None else position.target_notional_usd
        first_attempt = self._place_attempt(
            position=position,
            limit_price=limit_price,
            target_notional_usd=first_target_notional,
            assume_filled=self.config.mode == "dry-run",
        )
        full_target_notional = float(position.target_notional_usd)
        remaining_after_first = max(0.0, full_target_notional - max(0.0, first_attempt.cost_usd))
        depth_limited_initial = (
            initial_fak_notional is not None
            and initial_fak_notional < full_target_notional - 1e-6
            and remaining_after_first >= float(self.settings.live_min_order_notional)
        )
        first_position_state = LivePositionState.PARTIAL if depth_limited_initial and first_attempt.cost_usd > 0 else first_attempt.state
        first_raw_patch: dict[str, Any] = {"submit_phase": "initial"}
        if initial_fak_notional is not None:
            first_raw_patch["initial_fak_notional_usd"] = initial_fak_notional
            first_raw_patch["full_target_notional_usd"] = full_target_notional
            first_raw_patch["remaining_after_initial_fak_usd"] = remaining_after_first
        self._record_live_attempt(
            position_id,
            position,
            attempt_label="initial",
            attempt=first_attempt,
            update_position=True,
            position_state=first_position_state,
            position_filled_shares=first_attempt.filled_shares,
            position_cost_usd=first_attempt.cost_usd,
            position_avg_price=first_attempt.avg_price,
            raw_patch=first_raw_patch,
        )
        continue_after_depth_limited_fill = depth_limited_initial and first_attempt.cost_usd > 0
        if not self._is_retryable_attempt(first_attempt) and not continue_after_depth_limited_fill:
            return first_attempt.state
        if first_attempt.response.order_id is None:
            fallback_state = self._submit_resting_fallback(
                position_id=position_id,
                position=position,
                current_cost_usd=first_attempt.cost_usd,
                current_filled_shares=first_attempt.filled_shares,
                source_reason=first_attempt.response.error_msg or first_attempt.response.status or "initial_retryable_rejection",
                errors=errors,
            )
            return fallback_state or first_attempt.state
        if market is None or initial_book is None:
            return first_position_state

        first_order_open = first_attempt.state in {LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
        time.sleep(float(self.config.retry_wait_seconds))
        if not continue_after_depth_limited_fill:
            refreshed = self._refresh_order_state(first_attempt.response.order_id, position, limit_price, first_attempt.target_notional_usd)
            if refreshed is not None:
                refreshed_state, refreshed_filled_shares, refreshed_cost_usd, refreshed_avg_price, refreshed_raw = refreshed
                first_order_open = refreshed_state in {LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
                if refreshed_filled_shares > 0 or refreshed_state in {LivePositionState.FILLED, LivePositionState.PARTIAL}:
                    self.store.update_live_policy_position_execution(
                        position_id,
                        state=str(refreshed_state),
                        filled_shares=refreshed_filled_shares,
                        avg_entry_price=refreshed_avg_price,
                        cost_usd=refreshed_cost_usd,
                        raw_patch={
                            "retry": {
                                "attempt": "refresh",
                                "wait_seconds": float(self.config.retry_wait_seconds),
                                "order_id": first_attempt.response.order_id,
                                "state": str(refreshed_state),
                                "filled_shares": refreshed_filled_shares,
                                "cost_usd": refreshed_cost_usd,
                                "avg_price": refreshed_avg_price,
                            }
                        },
                    )
                    self.store.insert_live_trade_event(
                        LiveTradeEvent(
                            utc_now_iso(),
                            position_id,
                            position.strategy_name,
                            LiveTradeEventType.ENTRY_CONFIRMED,
                            "first order filled during retry wait",
                            {"order_id": first_attempt.response.order_id, "state": str(refreshed_state), "raw": refreshed_raw},
                            live_candidate_id=position.live_candidate_id,
                        )
                    )
                    if refreshed_state != LivePositionState.PARTIAL:
                        return refreshed_state
                self.store.insert_live_trade_event(
                    LiveTradeEvent(
                        utc_now_iso(),
                        position_id,
                        position.strategy_name,
                        LiveTradeEventType.ENTRY_SUBMIT,
                        "first order still open after retry wait",
                        {"order_id": first_attempt.response.order_id, "state": str(refreshed_state), "raw": refreshed_raw},
                        live_candidate_id=position.live_candidate_id,
                    )
                )
        else:
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    position.strategy_name,
                    LiveTradeEventType.ENTRY_SUBMIT,
                    "initial depth-limited FAK filled; retrying remaining target",
                    {
                        "order_id": first_attempt.response.order_id,
                        "initial_fak_notional_usd": initial_fak_notional,
                        "full_target_notional_usd": full_target_notional,
                        "remaining_after_initial_fak_usd": remaining_after_first,
                    },
                    live_candidate_id=position.live_candidate_id,
                )
            )

        retry_book = self._refresh_retry_book(position.selected_token_id, errors)
        if retry_book is None or retry_book.best_ask is None:
            return first_attempt.state
        current = self._current_live_position_metrics(position_id)
        current_cost_usd = float(current["cost_usd"] or 0.0) if current is not None else first_attempt.cost_usd
        current_filled_shares = float(current["filled_shares"] or 0.0) if current is not None else first_attempt.filled_shares
        retry_limit_price, retry_target_notional, retry_reason = self._retry_order_parameters(position, retry_book, current_cost_usd)
        if retry_reason is not None or retry_target_notional <= 0.0:
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    position.strategy_name,
                    LiveTradeEventType.ENTRY_SUBMIT,
                    f"retry skipped: {retry_reason or 'NO_TARGET'}",
                    {"retry_book_timestamp": retry_book.timestamp, "retry_reason": retry_reason},
                    live_candidate_id=position.live_candidate_id,
                )
            )
            if not first_order_open:
                fallback_state = self._submit_resting_fallback(
                    position_id=position_id,
                    position=position,
                    current_cost_usd=current_cost_usd,
                    current_filled_shares=current_filled_shares,
                    book=retry_book,
                    source_reason=retry_reason or "retry_skipped",
                    errors=errors,
                )
                if fallback_state is not None:
                    return fallback_state
            return first_attempt.state

        second_attempt = self._place_attempt(
            position=position,
            limit_price=retry_limit_price,
            target_notional_usd=retry_target_notional,
            assume_filled=False,
        )
        cumulative_filled_shares = current_filled_shares + second_attempt.filled_shares
        cumulative_cost_usd = current_cost_usd + second_attempt.cost_usd
        cumulative_avg_price = cumulative_cost_usd / cumulative_filled_shares if cumulative_filled_shares > 0 else None
        second_attempt_state = second_attempt.state
        second_remaining_notional = max(0.0, float(position.target_notional_usd) - max(0.0, cumulative_cost_usd))
        if (
            cumulative_filled_shares > 0
            and second_remaining_notional >= float(self.settings.live_min_order_notional)
            and second_attempt_state in {LivePositionState.FILLED, LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
        ):
            second_attempt_state = LivePositionState.PARTIAL
        self._record_live_attempt(
            position_id,
            position,
            attempt_label="retry",
            attempt=second_attempt,
            update_position=second_attempt.state != LivePositionState.REJECTED,
            position_state=second_attempt_state,
            position_filled_shares=cumulative_filled_shares,
            position_cost_usd=cumulative_cost_usd,
            position_avg_price=cumulative_avg_price,
            raw_patch={
                "retry": {
                    "attempt": "retry",
                    "wait_seconds": float(self.config.retry_wait_seconds),
                    "limit_price": retry_limit_price,
                    "target_notional_usd": retry_target_notional,
                    "retry_book_timestamp": retry_book.timestamp,
                    "retry_reason": retry_reason,
                    "first_attempt_order_id": first_attempt.response.order_id,
                    "first_attempt_state": str(first_attempt.state),
                }
            },
        )
        remaining_notional = max(0.0, float(position.target_notional_usd) - max(0.0, cumulative_cost_usd))
        if remaining_notional < float(self.settings.live_min_order_notional):
            return second_attempt_state if second_attempt_state in {LivePositionState.SUBMITTED, LivePositionState.FILLED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN} else first_attempt.state

        fallback_state = self._submit_resting_fallback(
            position_id=position_id,
            position=position,
            current_cost_usd=cumulative_cost_usd,
            current_filled_shares=cumulative_filled_shares,
            book=retry_book,
            source_reason=(
                "retry_remainder_rejected"
                if second_attempt.state == LivePositionState.REJECTED
                else "retry_remainder_partial"
                if second_attempt_state == LivePositionState.PARTIAL
                else "retry_remainder_open"
            ),
            errors=errors,
        )
        if fallback_state is not None:
            return fallback_state
        return second_attempt_state if second_attempt_state in {LivePositionState.SUBMITTED, LivePositionState.FILLED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN} else first_attempt.state

    def _place_attempt(
        self,
        *,
        position: LivePolicyPosition,
        limit_price: float,
        target_notional_usd: float,
        assume_filled: bool,

    ) -> LiveAttemptResult:
        limit_price = quantize_price(limit_price)
        target_notional_usd = quantize_usdc(target_notional_usd)
        target_shares = quantize_shares(target_notional_usd / limit_price) if limit_price > 0 else 0.0
        if self.config.mode == "dry-run":
            response = OrderSubmission(True, None, "dry_run", None, {"dry_run": True})
            state = LivePositionState.SUBMITTED
        else:
            submitter = self.submitter or self._default_submitter()
            if submitter.check_kill_switch():
                response = OrderSubmission(False, None, "rejected", "kill_switch", {"success": False, "errorMsg": "kill_switch"})
                state = LivePositionState.REJECTED
                return LiveAttemptResult(response, state, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
            if self.config.require_allowance_check and self.settings.live_require_allowance_check:
                allowance = submitter.check_allowance_buy(target_notional_usd)
                if not allowance.ok:
                    response = OrderSubmission(False, None, "rejected", f"allowance:{allowance.reason}", {"success": False, "errorMsg": f"allowance:{allowance.reason}", "allowance": allowance.raw})
                    state = LivePositionState.REJECTED
                    return LiveAttemptResult(response, state, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
            response = submitter.place_fak_order(
                token_id=position.selected_token_id,
                side="BUY",
                price=limit_price,
                amount=target_notional_usd,
            )
            state = _state_from_response(response)
        filled_shares, cost_usd, avg_price = _buy_fill_from_response(
            response,
            state,
            position,
            limit_price,
            target_notional_usd,
            assume_filled=assume_filled,
        )
        state = _state_from_fill_amount(state, cost_usd, target_notional_usd, filled_shares, target_shares)
        return LiveAttemptResult(response, state, limit_price, target_notional_usd, target_shares, filled_shares, cost_usd, avg_price)

    def _submit_resting_fallback(
        self,
        *,
        position_id: int,
        position: LivePolicyPosition,
        current_cost_usd: float,
        current_filled_shares: float,
        source_reason: str,
        errors: list[str],
        book: BookSnapshot | None = None,
    ) -> LivePositionState | None:
        if not self._resting_fallback_enabled(position):
            return None
        resting_book = book or self._refresh_retry_book(position.selected_token_id, errors)
        if resting_book is None:
            return None
        ladder_orders, resting_reason = self._resting_ladder_orders(position, resting_book, current_cost_usd)
        if resting_reason is not None or not ladder_orders:
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    position.strategy_name,
                    LiveTradeEventType.ENTRY_SUBMIT,
                    f"resting fallback skipped: {resting_reason or 'NO_TARGET'}",
                    {"source_reason": source_reason, "resting_book_timestamp": resting_book.timestamp, "resting_reason": resting_reason},
                    live_candidate_id=position.live_candidate_id,
                )
            )
            return None

        raw_base: dict[str, Any] = {
            "resting_fallback": {
                "source_reason": source_reason,
                "ttl_seconds": float(self.config.resting_fallback_ttl_seconds),
                "notional_fraction": float(self.config.resting_fallback_notional_fraction),
                "chunk_usd": float(self.config.resting_fallback_chunk_usd),
                "price_step": float(self.config.resting_fallback_price_step),
                "resting_book_timestamp": resting_book.timestamp,
                "best_bid": resting_book.best_bid,
                "best_ask": resting_book.best_ask,
                "orders": [dict(order) for order in ladder_orders],
                "reason": resting_reason,
            }
        }

        submitted: list[tuple[dict[str, float], LiveAttemptResult]] = []
        for order in ladder_orders:
            attempt = self._place_gtc_attempt(
                position=position,
                limit_price=float(order["limit_price"]),
                target_notional_usd=float(order["target_notional_usd"]),
                post_only=bool(order.get("post_only", False)),
            )
            submitted.append((order, attempt))

        open_children = [
            attempt
            for _, attempt in submitted
            if attempt.response.order_id is not None
            and attempt.state in {LivePositionState.SUBMITTED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
        ]
        if open_children:
            time.sleep(float(self.config.resting_fallback_ttl_seconds))

        cumulative_filled_shares = current_filled_shares
        cumulative_cost_usd = current_cost_usd
        any_new_fill = False
        any_live_state = False
        final_states: list[LivePositionState] = []
        for index, (order, attempt) in enumerate(submitted, start=1):
            final_attempt = attempt
            update_position = attempt.filled_shares > 0 or attempt.state in {LivePositionState.FILLED, LivePositionState.PARTIAL}
            cancel_response: CancelSubmission | None = None
            refreshed_raw: dict[str, Any] | None = None
            if attempt.response.order_id is not None and attempt.state in {LivePositionState.SUBMITTED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}:
                refreshed = self._refresh_order_state(
                    attempt.response.order_id,
                    position,
                    float(order["limit_price"]),
                    float(order["target_notional_usd"]),
                )
                if refreshed is not None:
                    refreshed_state, refreshed_filled_shares, refreshed_cost_usd, refreshed_avg_price, refreshed_raw = refreshed
                    if refreshed_filled_shares > 0 and refreshed_state == LivePositionState.SUBMITTED:
                        refreshed_state = LivePositionState.PARTIAL
                    final_attempt = LiveAttemptResult(
                        OrderSubmission(
                            True,
                            attempt.response.order_id,
                            str(refreshed_state),
                            None,
                            {"submit": attempt.response.raw, "after_ttl": refreshed_raw},
                        ),
                        refreshed_state,
                        float(order["limit_price"]),
                        float(order["target_notional_usd"]),
                        attempt.target_shares,
                        refreshed_filled_shares,
                        refreshed_cost_usd,
                        refreshed_avg_price,
                    )
                    update_position = refreshed_filled_shares > 0 or refreshed_state in {LivePositionState.FILLED, LivePositionState.PARTIAL}
                if final_attempt.state != LivePositionState.FILLED:
                    cancel_response = self._cancel_resting_order(attempt.response.order_id)
                    if not update_position:
                        cancel_state = LivePositionState.CANCELLED if cancel_response.success else LivePositionState.UNKNOWN
                        final_attempt = LiveAttemptResult(
                            OrderSubmission(
                                cancel_response.success,
                                attempt.response.order_id,
                                str(cancel_state),
                                cancel_response.error_msg or "RESTING_TTL_EXPIRED",
                                {"submit": attempt.response.raw, "after_ttl": refreshed_raw, "cancel": cancel_response.raw},
                            ),
                            cancel_state,
                            float(order["limit_price"]),
                            float(order["target_notional_usd"]),
                            attempt.target_shares,
                            0.0,
                            0.0,
                            None,
                        )
                    else:
                        final_attempt = LiveAttemptResult(
                            OrderSubmission(
                                final_attempt.response.success,
                                attempt.response.order_id,
                                final_attempt.response.status,
                                final_attempt.response.error_msg,
                                {"submit": attempt.response.raw, "after_ttl": refreshed_raw, "cancel": cancel_response.raw},
                            ),
                            final_attempt.state,
                            final_attempt.limit_price,
                            final_attempt.target_notional_usd,
                            final_attempt.target_shares,
                            final_attempt.filled_shares,
                            final_attempt.cost_usd,
                            final_attempt.avg_price,
                        )

            cumulative_filled_shares += final_attempt.filled_shares
            cumulative_cost_usd += final_attempt.cost_usd
            cumulative_avg_price = cumulative_cost_usd / cumulative_filled_shares if cumulative_filled_shares > 0 else None
            any_new_fill = any_new_fill or final_attempt.filled_shares > 0 or final_attempt.cost_usd > 0
            any_live_state = any_live_state or final_attempt.state in {LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
            final_states.append(final_attempt.state)
            raw_patch = dict(raw_base)
            raw_patch["resting_fallback"] = {**raw_base["resting_fallback"], "child_index": index, "child_order": dict(order), "post_only": bool(order.get("post_only", False))}
            raw_patch["resting_fallback"].update(
                {
                    "order_status_after_ttl": final_attempt.response.status,
                    "cancel_response": cancel_response.raw if cancel_response is not None else None,
                }
            )
            self._record_live_attempt(
                position_id,
                position,
                attempt_label=f"resting_ladder_{index}",
                attempt=final_attempt,
                update_position=update_position,
                position_state=final_attempt.state,
                position_filled_shares=cumulative_filled_shares,
                position_cost_usd=cumulative_cost_usd,
                position_avg_price=cumulative_avg_price,
                raw_patch=raw_patch,
                order_mode=LiveOrderMode.GTC,
            )

        target_notional = float(position.target_notional_usd)
        cumulative_avg_price = cumulative_cost_usd / cumulative_filled_shares if cumulative_filled_shares > 0 else None
        if cumulative_cost_usd >= target_notional - 1e-6:
            self.store.update_live_policy_position_execution(
                position_id,
                state=str(LivePositionState.FILLED),
                filled_shares=cumulative_filled_shares,
                avg_entry_price=cumulative_avg_price,
                cost_usd=cumulative_cost_usd,
            )
            return LivePositionState.FILLED
        if any_new_fill:
            self.store.update_live_policy_position_execution(
                position_id,
                state=str(LivePositionState.PARTIAL),
                filled_shares=cumulative_filled_shares,
                avg_entry_price=cumulative_avg_price,
                cost_usd=cumulative_cost_usd,
            )
            return LivePositionState.PARTIAL
        if any_live_state:
            return LivePositionState.SUBMITTED
        if any(state in {LivePositionState.DELAYED, LivePositionState.UNKNOWN} for state in final_states):
            return LivePositionState.UNKNOWN
        if final_states and all(state == LivePositionState.CANCELLED for state in final_states):
            position_state = LivePositionState.PARTIAL if cumulative_cost_usd > 0 else LivePositionState.REJECTED
            cumulative_avg_price = cumulative_cost_usd / cumulative_filled_shares if cumulative_filled_shares > 0 else None
            self.store.update_live_policy_position_execution(
                position_id,
                state=str(position_state),
                filled_shares=cumulative_filled_shares,
                avg_entry_price=cumulative_avg_price,
                cost_usd=cumulative_cost_usd,
                raw_patch={"final_reason": "RESTING_TTL_EXPIRED"},
            )
            return position_state
        return None

    def _resting_ladder_orders(
        self,
        position: LivePolicyPosition,
        book: BookSnapshot,
        current_cost_usd: float,
    ) -> tuple[list[dict[str, float | bool]], str | None]:
        target_notional_usd, resting_reason = self._resting_target_notional(position, current_cost_usd)
        if resting_reason is not None or target_notional_usd <= 0.0:
            return [], resting_reason
        offsets = tuple(int(offset) for offset in self.config.resting_ladder_offsets_cents)
        weights = tuple(float(weight) for weight in self.config.resting_ladder_weights)
        if len(offsets) != len(weights) or not offsets:
            return [], "INVALID_LADDER_CONFIG"
        weight_sum = sum(max(0.0, weight) for weight in weights)
        if weight_sum <= 0.0:
            return [], "INVALID_LADDER_CONFIG"

        min_order = float(self.settings.live_min_order_notional)
        scored_entry = _quantize_cent_price(float(position.entry_price))
        post_only_offsets = {int(offset) for offset in self.config.resting_post_only_offsets_cents}
        orders: list[dict[str, float | bool]] = []
        for offset, weight in zip(offsets, weights):
            child_price = _quantize_cent_price(scored_entry + (offset / 100.0))
            if child_price <= 0.0:
                continue
            child_notional = quantize_usdc(target_notional_usd * (max(0.0, weight) / weight_sum))
            if child_notional < min_order:
                continue
            orders.append(
                {
                    "child_index": float(len(orders) + 1),
                    "limit_price": child_price,
                    "target_notional_usd": child_notional,
                    "ladder_offset_cents": float(offset),
                    "post_only": bool(offset in post_only_offsets),
                }
            )
        if not orders:
            return [], "NO_VALID_LADDER_ORDERS"
        return orders, None

    def _resting_target_notional(
        self,
        position: LivePolicyPosition,
        current_cost_usd: float,
    ) -> tuple[float, str | None]:
        remaining_notional = max(0.0, float(position.target_notional_usd) - max(0.0, current_cost_usd))
        target_notional = quantize_usdc(remaining_notional * max(0.0, float(self.config.resting_fallback_notional_fraction)))
        if target_notional < float(self.settings.live_min_order_notional):
            return target_notional, "NO_REMAINING_NOTIONAL"
        return target_notional, None

    def _place_gtc_attempt(
        self,
        *,
        position: LivePolicyPosition,
        limit_price: float,
        target_notional_usd: float,
        post_only: bool = False,
    ) -> LiveAttemptResult:
        limit_price = quantize_price(limit_price)
        target_notional_usd = quantize_usdc(target_notional_usd)
        target_shares = quantize_shares(target_notional_usd / limit_price) if limit_price > 0 else 0.0
        submitter = self.submitter or self._default_submitter()
        if submitter.check_kill_switch():
            response = OrderSubmission(False, None, "rejected", "kill_switch", {"success": False, "errorMsg": "kill_switch"})
            return LiveAttemptResult(response, LivePositionState.REJECTED, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
        if self.config.require_allowance_check and self.settings.live_require_allowance_check:
            allowance = submitter.check_allowance_buy(target_notional_usd)
            if not allowance.ok:
                response = OrderSubmission(False, None, "rejected", f"allowance:{allowance.reason}", {"success": False, "errorMsg": f"allowance:{allowance.reason}", "allowance": allowance.raw})
                return LiveAttemptResult(response, LivePositionState.REJECTED, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
        place_gtc = getattr(submitter, "place_gtc_order", None)
        if not callable(place_gtc):
            response = OrderSubmission(False, None, "blocked", "GTC_UNSUPPORTED", {"blocked": True, "reason": "GTC_UNSUPPORTED", "post_only": post_only})
            return LiveAttemptResult(response, LivePositionState.REJECTED, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
        try:
            response = place_gtc(
                token_id=position.selected_token_id,
                side="BUY",
                price=limit_price,
                amount=target_notional_usd,
                post_only=post_only,
            )
        except TypeError:
            if post_only:
                response = OrderSubmission(False, None, "blocked", "POST_ONLY_UNSUPPORTED", {"blocked": True, "reason": "POST_ONLY_UNSUPPORTED", "post_only": True})
                return LiveAttemptResult(response, LivePositionState.REJECTED, limit_price, target_notional_usd, target_shares, 0.0, 0.0, None)
            response = place_gtc(
                token_id=position.selected_token_id,
                side="BUY",
                price=limit_price,
                amount=target_notional_usd,
            )
        state = _state_from_response(response)
        filled_shares, cost_usd, avg_price = _buy_fill_from_response(response, state, position, limit_price, target_notional_usd, assume_filled=False)
        state = _state_from_fill_amount(state, cost_usd, target_notional_usd, filled_shares, target_shares)
        return LiveAttemptResult(response, state, limit_price, target_notional_usd, target_shares, filled_shares, cost_usd, avg_price)

    def _cancel_resting_order(self, order_id: str) -> CancelSubmission:
        submitter = self.submitter or self._default_submitter()
        try:
            return submitter.cancel_order(order_id)
        except Exception as exc:
            return CancelSubmission(False, [], {order_id: str(exc)}, str(exc), {"exception_type": type(exc).__name__, "exception": str(exc)})

    def _resting_fallback_enabled(self, position: LivePolicyPosition) -> bool:
        return bool(self.config.enable_resting_fallback and self.config.mode == "live")

    def _resting_order_parameters(
        self,
        position: LivePolicyPosition,
        book: BookSnapshot,
        current_cost_usd: float,
    ) -> tuple[float, float, str | None]:
        best_bid = book.best_bid
        best_ask = book.best_ask
        if best_bid is None or best_ask is None:
            return 0.0, 0.0, "MISSING_BOOK"
        target_notional, reason = self._resting_target_notional(position, current_cost_usd)
        if reason is not None:
            return 0.0, target_notional, reason
        return _quantize_cent_price(float(position.entry_price)), target_notional, None

    def _record_live_attempt(
        self,
        position_id: int,
        position: LivePolicyPosition,
        *,
        attempt_label: str,
        attempt: LiveAttemptResult,
        update_position: bool,
        position_state: LivePositionState | None = None,
        position_filled_shares: float | None = None,
        position_cost_usd: float | None = None,
        position_avg_price: float | None = None,
        raw_patch: dict[str, Any] | None = None,
        order_mode: LiveOrderMode = LiveOrderMode.FAK,
    ) -> None:
        attempt_payload = dict(attempt.response.raw)
        scored_entry_price = _quantize_cent_price(float(position.entry_price))
        max_immediate_price = self._initial_execution_price(scored_entry_price)
        slippage_vs_entry = (attempt.avg_price - scored_entry_price) if attempt.avg_price is not None else None
        execution_price_violation = bool(attempt.avg_price is not None and attempt.avg_price > max_immediate_price + 1e-9)
        attempt_payload["execution"] = {
            "attempt_label": attempt_label,
            "update_position": update_position,
            "limit_price": attempt.limit_price,
            "target_notional_usd": attempt.target_notional_usd,
            "target_shares": attempt.target_shares,
            "filled_shares": attempt.filled_shares,
            "cost_usd": attempt.cost_usd,
            "avg_price": attempt.avg_price,
            "execution_contract_version": 1,
            "scored_entry_price": scored_entry_price,
            "max_immediate_price": max_immediate_price,
            "slippage_vs_entry": slippage_vs_entry,
            "execution_price_violation": execution_price_violation,
            "price_policy_reason": "entry_anchored",
        }
        if raw_patch:
            attempt_payload["execution"].update(raw_patch)
        self.store.insert_live_order_attempt(
            LiveOrderAttempt(
                utc_now_iso(),
                position_id,
                self.store.next_live_attempt_seq(position_id),
                position.selected_token_id,
                position.selected_side,
                order_mode,
                attempt.limit_price,
                attempt.target_notional_usd,
                attempt.target_shares,
                attempt.response.order_id,
                attempt.response.status,
                attempt.state,
                attempt.response.error_msg or attempt.response.status or "submitted",
                attempt.filled_shares,
                attempt.avg_price,
                attempt.cost_usd,
                attempt_payload,
                live_candidate_id=position.live_candidate_id,
            )
        )
        if update_position:
            self.store.update_live_policy_position_execution(
                position_id,
                state=str(position_state or attempt.state),
                filled_shares=attempt.filled_shares if position_filled_shares is None else position_filled_shares,
                avg_entry_price=attempt.avg_price if position_avg_price is None else position_avg_price,
                cost_usd=attempt.cost_usd if position_cost_usd is None else position_cost_usd,
                raw_patch={
                    "attempt": attempt_label,
                    "limit_price": attempt.limit_price,
                    "target_notional_usd": attempt.target_notional_usd,
                    "target_shares": attempt.target_shares,
                    "external_order_id": attempt.response.order_id,
                    "external_status": attempt.response.status,
                    "submit_response": attempt.response.raw,
                    "actual_filled_shares": attempt.filled_shares,
                    "actual_cost_usd": attempt.cost_usd,
                    "actual_avg_entry_price": attempt.avg_price,
                    **(raw_patch or {}),
                },
            )
        self.store.insert_live_trade_event(
            LiveTradeEvent(
                utc_now_iso(),
                position_id,
                position.strategy_name,
                LiveTradeEventType.ENTRY_SUBMIT,
                f"{attempt_label} {attempt.state}",
                attempt.response.raw,
                live_candidate_id=position.live_candidate_id,
            )
        )

    def _current_live_position_metrics(self, position_id: int) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "select filled_shares, cost_usd, state from live_policy_positions where id = ?",
            (position_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _refresh_order_state(
        self,
        order_id: str,
        position: LivePolicyPosition,
        limit_price: float,
        target_notional_usd: float,
    ) -> tuple[LivePositionState, float, float, float | None, dict[str, Any]] | None:
        submitter = self.submitter or self._default_submitter()
        get_order = getattr(submitter, "get_order", None)
        if not callable(get_order):
            return None
        try:
            raw = get_order(order_id)
        except Exception:
            return None
        raw_payload = raw if isinstance(raw, dict) else {"raw": raw}
        response = OrderSubmission(True, order_id, str(raw_payload.get("status") or raw_payload.get("state") or "submitted"), None, raw_payload)
        state = _state_from_response(response)
        filled_shares, cost_usd, avg_price = _buy_fill_from_response(
            response,
            state,
            position,
            limit_price,
            target_notional_usd,
            assume_filled=False,
        )
        target_shares = quantize_shares(target_notional_usd / limit_price) if limit_price > 0 else 0.0
        state = _state_from_fill_amount(state, cost_usd, target_notional_usd, filled_shares, target_shares)
        if _refresh_payload_is_partially_matched(raw_payload):
            state = LivePositionState.PARTIAL
        return state, filled_shares, cost_usd, avg_price, raw_payload

    def _refresh_retry_book(self, token_id: str, errors: list[str]) -> BookSnapshot | None:
        try:
            books = self.book_client.fetch_books([token_id])
        except requests.RequestException as exc:
            errors.append(f"retry-book:{token_id}: {exc}")
            return None
        book = books.get(token_id)
        if book is not None:
            self.store.insert_book_snapshot(book)
        return book

    def _retry_order_parameters(
        self,
        position: LivePolicyPosition,
        book: BookSnapshot,
        current_cost_usd: float,
    ) -> tuple[float, float, str | None]:
        best_ask = book.best_ask
        if best_ask is None:
            return 0.0, 0.0, "MISSING_BOOK"
        limit_price = self._retry_execution_price(float(position.entry_price))
        if limit_price <= 0.0:
            return 0.0, 0.0, "NO_RETRY_PRICE"
        if best_ask > limit_price + 1e-9:
            return limit_price, 0.0, "ASK_ABOVE_ENTRY_CAP"
        remaining_notional = max(0.0, float(position.target_notional_usd) - max(0.0, current_cost_usd))
        if remaining_notional <= 0.0:
            return limit_price, 0.0, "NO_REMAINING_NOTIONAL"
        walk = walk_ask_ladder(
            book=book,
            limit_price=best_ask,
            target_notional_usd=remaining_notional,
            execution_price_cap=limit_price,
        )
        retry_target = quantize_usdc(min(remaining_notional, walk.cost_usd))
        if retry_target < float(self.settings.live_min_order_notional):
            return limit_price, retry_target, "INSUFFICIENT_DEPTH"
        return limit_price, retry_target, None

    def _is_retryable_attempt(self, attempt: LiveAttemptResult) -> bool:
        if attempt.state in {LivePositionState.SUBMITTED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}:
            return True
        if attempt.state != LivePositionState.REJECTED or not attempt.response.error_msg:
            return False
        reason = attempt.response.error_msg.lower()
        return any(token in reason for token in ("liquid", "depth", "book", "fill"))

    def _default_submitter(self) -> LiveSubmitter:
        default_submitter = getattr(self, "_default_submitter_instance", None)
        if default_submitter is None:
            private_key = private_key_from_env_or_keyfile(self.settings)
            try:
                default_submitter = ClobExecutor(private_key=private_key, settings=self.settings)
                self._default_submitter_instance = default_submitter
            finally:
                private_key = ""
        return default_submitter

    def _record_risk_snapshot(self) -> None:
        exposure = self.store.live_exposure_summary()
        self.store.insert_live_risk_snapshot(
            LiveRiskSnapshot(
                utc_now_iso(),
                int(exposure["open_positions"]),
                float(exposure["open_risk_usd"]),
                dict(exposure["station_date_exposure_usd"]),
                exposure,
            )
        )


def _calibration_entry_band(entry_price: float) -> str:
    if entry_price < 0.15:
        return "<0.15"
    if entry_price < 0.25:
        return "0.15-0.25"
    if entry_price < 0.35:
        return "0.25-0.35"
    if entry_price < 0.45:
        return "0.35-0.45"
    if entry_price < 0.55:
        return "0.45-0.55"
    return ">=0.55"


def _initial_fak_notional_usd(position: LivePolicyPosition) -> float | None:
    raw = position.raw_json.get("sizing") if isinstance(position.raw_json, dict) else None
    if not isinstance(raw, dict):
        return None
    value = raw.get("initial_fak_notional_usd")
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0.0:
        return None
    return min(quantize_usdc(amount), float(position.target_notional_usd))


def _quantize_cent_price(value: float) -> float:
    return round(max(0.0, min(1.0, math.floor((value + 1e-12) * 100.0) / 100.0)), 2)


def _buy_fill_from_response(
    response: OrderSubmission,
    state: LivePositionState,
    position: LivePolicyPosition,
    limit_price: float,
    target_notional_usd: float,
    *,
    assume_filled: bool = False,
) -> tuple[float, float, float | None]:
    if not response.success:
        return 0.0, 0.0, None
    actual_cost = _float_response_field(response.raw, "makingAmount")
    actual_shares = _float_response_field(response.raw, "takingAmount")
    if actual_cost is not None and actual_shares is not None and actual_cost > 0.0 and actual_shares > 0.0:
        avg_price = actual_cost / actual_shares
        return quantize_shares(actual_shares), quantize_usdc(actual_cost), quantize_price(avg_price)
    matched_shares = _float_response_field(response.raw, "size_matched")
    order_price = _float_response_field(response.raw, "price")
    if matched_shares is not None and order_price is not None and matched_shares > 0.0 and order_price > 0.0:
        actual_cost = matched_shares * order_price
        return quantize_shares(matched_shares), quantize_usdc(actual_cost), quantize_price(order_price)
    if assume_filled and target_notional_usd > 0.0 and limit_price > 0.0:
        filled_shares = quantize_shares(target_notional_usd / limit_price)
        return filled_shares, quantize_usdc(target_notional_usd), limit_price
    return 0.0, 0.0, None


def _state_from_fill_amount(
    state: LivePositionState,
    cost_usd: float,
    target_notional_usd: float,
    filled_shares: float,
    target_shares: float,
) -> LivePositionState:
    if state not in {LivePositionState.FILLED, LivePositionState.PARTIAL}:
        return state
    target = max(0.0, float(target_notional_usd))
    cost = max(0.0, float(cost_usd))
    shares = max(0.0, float(filled_shares))
    target_share_count = max(0.0, float(target_shares))
    if state == LivePositionState.PARTIAL:
        return state
    if target_share_count > 0.0 and shares + 1e-6 >= target_share_count:
        return LivePositionState.FILLED
    if target > 0.0 and cost + 0.01 >= target:
        return LivePositionState.FILLED
    if cost > 0.0 or shares > 0.0:
        return LivePositionState.PARTIAL
    return state


def _refresh_payload_is_partially_matched(raw: dict[str, Any]) -> bool:
    matched_shares = _float_response_field(raw, "size_matched")
    original_shares = _float_response_field(raw, "original_size")
    if matched_shares is None or original_shares is None:
        return False
    return 0.0 < matched_shares < original_shares


def _float_response_field(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None and isinstance(raw.get("raw_payload"), dict):
        value = raw["raw_payload"].get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _book_by_market_side(markets: list[MarketSnapshot], books: dict[str, BookSnapshot]) -> dict[tuple[str, str], BookSnapshot]:
    result: dict[tuple[str, str], BookSnapshot] = {}
    for market in markets:
        if market.yes_token_id and market.yes_token_id in books:
            result[(market.market_id, str(TradeAction.BUY_YES))] = books[market.yes_token_id]
        if market.no_token_id and market.no_token_id in books:
            result[(market.market_id, str(TradeAction.BUY_NO))] = books[market.no_token_id]
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_price_from_item(item: dict[str, Any]) -> float | None:
    side = str(item.get("selected_side") or "")
    if side == str(TradeAction.BUY_YES):
        return _float_or_none(item.get("selected_yes_ask"))
    if side == str(TradeAction.BUY_NO):
        return _float_or_none(item.get("selected_no_ask"))
    return None


def _selected_fair_from_item(item: dict[str, Any]) -> float | None:
    side = str(item.get("selected_side") or "")
    if side == str(TradeAction.BUY_YES):
        return _float_or_none(item.get("selected_fair_yes"))
    if side == str(TradeAction.BUY_NO):
        return _float_or_none(item.get("selected_fair_no"))
    return None


def _seconds_between(timestamp: str, as_of_utc: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (as_of_utc - parsed.astimezone(timezone.utc)).total_seconds())


def _state_from_response(response: OrderSubmission) -> LivePositionState:
    status = (response.status or "").strip().lower()
    if not response.success:
        return LivePositionState.REJECTED
    if status == "delayed":
        return LivePositionState.DELAYED
    if status in {"matched", "filled"}:
        return LivePositionState.FILLED
    if status in {"partial", "partially_filled", "partially_matched"}:
        return LivePositionState.PARTIAL
    if status in {"cancelled", "canceled"}:
        return LivePositionState.CANCELLED
    if status in {"", "live", "submitted", "open"}:
        return LivePositionState.SUBMITTED
    return LivePositionState.UNKNOWN


def _market_admitted_by_strategy_plans(market: MarketSnapshot, plans: tuple[LiveStrategyPlan, ...]) -> bool:
    for plan in plans:
        if not plan.strategy.active:
            continue
        if market.market_family != plan.strategy.market_family:
            continue
        for policy in plan.policies:
            if policy.station_allow_set is not None and market.station not in policy.station_allow_set:
                continue
            if policy.station_exclude_set is not None and market.station in policy.station_exclude_set:
                continue
            return True
    return False


def _market_counts_by_family(markets: list[MarketSnapshot]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for market in markets:
        key = str(market.market_family)
        counts[key] = counts.get(key, 0) + 1
    return counts
