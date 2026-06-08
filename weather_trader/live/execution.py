from __future__ import annotations

from dataclasses import dataclass
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import requests
import time

from weather_trader.config import DEFAULT_LIVE_DB, MODELS_DIR
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
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState, WeatherFeatureService
from weather_trader.live.settings import LiveSettings, load_live_settings, private_key_from_env_or_keyfile
from weather_trader.live.sizing import LiveSizingDecision, LiveSizingModel
from weather_trader.research.collector import ResearchConfig, build_prediction_snapshot, due_delay_buckets
from weather_trader.research.policies import (
    CATBOOST_MODEL,
    DYNAMIC_TUNED_MODEL,
    GLOBAL_LOW_DYNAMIC_MODEL,
    GLOBAL_LOW_MVP_MODEL,
    NGBOOST_MODEL,
    ResearchPolicyEvaluator,
    ResearchPolicySpec,
)


LIVE_POLICY_NAME = "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first"
EDGE_CORE_POLICY_NAME = "pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first"
MOONSHOT_POLICY_NAME = "pm_us12_dynamic_tuned_hc_late_entry_05_10_buy_no_by_bucket_side_delay_first"
NGBOOST_BEST_BUY_YES_POLICY_NAME = "pm_us12_ngboost_best_bucket_late_buy_yes_medium_by_bucket_side_delay_first"
GLOBAL_LOW_CANARY_POLICY_NAME = "global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first"
LIVE_MODEL_GROUP = "obs_bucket_consensus"
GLOBAL_LOW_MODEL_GROUP = "global_low_dynamic_mvp"
DEFAULT_LIVE_ENTRY_PRICE_MAX = 0.50
LIVE_ENTRY_PRICE_MAX = DEFAULT_LIVE_ENTRY_PRICE_MAX
EDGE_CORE_OBS_DELAY_BUCKET = "15m"
CONSENSUS_NOTIONAL_USD = 50.0
EDGE_CORE_NOTIONAL_USD = 50.0
MOONSHOT_MIN_EDGE = 0.90
MOONSHOT_NOTIONAL_USD = 2.0
NGBOOST_BEST_BUY_YES_NOTIONAL_USD = 10.0
GLOBAL_LOW_NOTIONAL_USD = 25.0
GLOBAL_LOW_ENTRY_PRICE_MAX = 0.75
LIVE_MODEL_PATHS = (
    MODELS_DIR / f"{DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{CATBOOST_MODEL}.joblib",
    MODELS_DIR / f"{NGBOOST_MODEL}.joblib",
    MODELS_DIR / f"{GLOBAL_LOW_DYNAMIC_MODEL}.joblib",
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
GLOBAL_LOW_STATIONS = frozenset({"EGLC", "LFPB", "RJTT", "RKSI", "VHHH", "ZSPD"})


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
    edge_core_notional_usd: float = EDGE_CORE_NOTIONAL_USD
    global_low_notional_usd: float = GLOBAL_LOW_NOTIONAL_USD
    min_entry_price: float = 0.05
    max_entry_price: float | None = DEFAULT_LIVE_ENTRY_PRICE_MAX
    global_low_entry_price_max: float = GLOBAL_LOW_ENTRY_PRICE_MAX
    require_allowance_check: bool = True
    retry_wait_seconds: float = 5.0
    enable_resting_fallback: bool = True
    resting_fallback_ttl_seconds: float = 120.0
    resting_fallback_notional_fraction: float = 1.0


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


class LiveSubmitter(Protocol):
    def check_kill_switch(self) -> bool:
        ...

    def check_allowance_buy(self, required_usdc: float):
        ...

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        ...

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
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


def global_low_canary_live_strategy(max_notional_usd: float = GLOBAL_LOW_NOTIONAL_USD) -> LiveStrategy:
    return LiveStrategy(
        name=GLOBAL_LOW_CANARY_POLICY_NAME,
        active=True,
        source="consensus",
        model_group=GLOBAL_LOW_MODEL_GROUP,
        model_names=[GLOBAL_LOW_DYNAMIC_MODEL, GLOBAL_LOW_MVP_MODEL],
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        market_family=MarketFamily.LOW_TEMP,
        local_decision_start="",
        local_decision_end="",
        entry_price_min=0.0,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
        max_notional_usd=max_notional_usd,
        raw_payload={
            "report": {
                "role": "live_canary",
                "target_notional_usd": max_notional_usd,
                "entry_price_max": GLOBAL_LOW_ENTRY_PRICE_MAX,
                "selected_side": str(TradeAction.BUY_NO),
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


def global_low_canary_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        GLOBAL_LOW_CANARY_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group=GLOBAL_LOW_MODEL_GROUP,
        selected_side=TradeAction.BUY_NO,
        station_allow_set=GLOBAL_LOW_STATIONS,
        entry_price_min=0.0,
        entry_price_max=config.global_low_entry_price_max,
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
            edge_core_live_strategy(config.edge_core_notional_usd),
            (edge_core_policy_spec(config),),
            config.edge_core_notional_usd,
            None,
            0.0,
        ),
        LiveStrategyPlan(
            moonshot_live_strategy(),
            (moonshot_policy_spec(), moonshot_edge_policy_spec(config)),
            MOONSHOT_NOTIONAL_USD,
            TradeAction.BUY_NO,
            config.min_entry_price,
        ),
        LiveStrategyPlan(
            ngboost_best_buy_yes_live_strategy(),
            (ngboost_best_buy_yes_policy_spec(config),),
            NGBOOST_BEST_BUY_YES_NOTIONAL_USD,
            TradeAction.BUY_YES,
            config.min_entry_price,
        ),
        LiveStrategyPlan(
            global_low_canary_live_strategy(config.global_low_notional_usd),
            (global_low_canary_policy_spec(config),),
            config.global_low_notional_usd,
            TradeAction.BUY_NO,
            0.0,
        ),
    )


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
        self.weather_service = weather_service or WeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        self.decision_engine = StationDateDecisionEngine()
        self.fair_value_engines = [FairValueEngine(path) for path in self.config.model_paths]
        self.strategy_plans = live_strategy_plans(self.config)
        self.policy_evaluator = ResearchPolicyEvaluator(store, tuple(policy for plan in self.strategy_plans for policy in plan.policies))
        self.settings = settings or load_live_settings()
        self.sizing_model = LiveSizingModel(self.settings)
        self.submitter = submitter
        self._default_submitter_instance: LiveSubmitter | None = None

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
            "global_low_notional_usd": self.config.global_low_notional_usd,
            "global_low_entry_price_max": self.config.global_low_entry_price_max,
            "models": [str(path) for path in self.config.model_paths],
        }
        for plan in self.strategy_plans:
            self.store.upsert_live_strategy(plan.strategy)
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
            sizing = self._size_candidate(candidate, now)
            if reject_reason is None and sizing.blocked_reason is not None:
                reject_reason = sizing.blocked_reason
            position = self._live_position(candidate, market, reject_reason=reject_reason, sizing=sizing)
            position_id = self.store.insert_live_policy_position(position)
            if position_id is None:
                skipped += 1
                reject_reasons["DUPLICATE_POSITION"] = reject_reasons.get("DUPLICATE_POSITION", 0) + 1
                continue
            reserved += 1
            self.store.insert_live_trade_event(
                LiveTradeEvent(utc_now_iso(), position_id, position.strategy_name, LiveTradeEventType.ENTRY_RESERVED, "entry reserved", position.raw_json)
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
        snapshot_id = 1
        grouped: dict[tuple[str, Any, str], list[MarketSnapshot]] = {}
        for market in markets:
            grouped.setdefault(group_key(market), []).append(market)
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
                            item = dataclass_to_jsonable(snapshot)
                            item["id"] = snapshot_id
                            snapshot_id += 1
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
                    position_count += 1
                    key = (position.station, position.market_date, position.market_family, position.scope_key)
                    existing = plan_candidates.get(key)
                    if existing is None or position.timestamp < existing.position.timestamp:
                        plan_candidates[key] = LiveCandidate(plan, position)
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
        )

    def _candidate_reject_reason(self, candidate: LiveCandidate, book: BookSnapshot | None) -> str | None:
        if book is None or book.best_ask is None:
            return "MISSING_BOOK"
        if candidate.plan.min_entry_price is not None and candidate.position.entry_price < candidate.plan.min_entry_price:
            return "ENTRY_PRICE_TOO_LOW"
        if candidate.position.selected_book_age_seconds is not None and candidate.position.selected_book_age_seconds > self.config.max_book_age_seconds:
            return "STALE_BOOK"
        return None

    def _size_candidate(self, candidate: LiveCandidate, as_of_utc: datetime) -> LiveSizingDecision:
        source = candidate.position
        entry_price = float(source.selected_sweep_price_cap or source.entry_price)
        return self.sizing_model.size_candidate(
            strategy_name=candidate.plan.strategy.name,
            entry_price=entry_price,
            station=str(source.station),
            market_date=source.market_date,
            selected_side=source.selected_side,
            selected_bucket=source.selected_bucket,
            sweep_depth_to_cap=source.selected_sweep_depth_to_cap,
            exposure=self.store.live_exposure_summary(),
            target_notional_usd=candidate.plan.target_notional_usd,
            as_of_utc=as_of_utc,
        )

    def _live_position(
        self,
        candidate: LiveCandidate,
        market: MarketSnapshot,
        *,
        reject_reason: str | None,
        sizing: LiveSizingDecision,
    ) -> LivePolicyPosition:
        source = candidate.position
        token_id = market.yes_token_id if source.selected_side == TradeAction.BUY_YES else market.no_token_id
        limit_price = quantize_price(float(source.selected_sweep_price_cap or source.entry_price))
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
            raw_json={
                "candidate": dataclass_to_jsonable(source),
                "strategy": dataclass_to_jsonable(candidate.plan.strategy),
                "limit_price": limit_price,
                "reject_reason": reject_reason,
                "sizing": sizing.raw_json,
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
                {"blocked": True, "reason": reason},
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
        limit_price = float(position.raw_json["limit_price"])
        first_attempt = self._place_attempt(
            position=position,
            limit_price=limit_price,
            target_notional_usd=position.target_notional_usd,
            assume_filled=self.config.mode == "dry-run",
        )
        self._record_live_attempt(
            position_id,
            position,
            attempt_label="initial",
            attempt=first_attempt,
            update_position=True,
            position_state=first_attempt.state,
            position_filled_shares=first_attempt.filled_shares,
            position_cost_usd=first_attempt.cost_usd,
            position_avg_price=first_attempt.avg_price,
            raw_patch={
                "submit_phase": "initial",
            },
        )
        if not self._is_retryable_attempt(first_attempt):
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
            return first_attempt.state

        first_order_open = first_attempt.state in {LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN}
        time.sleep(float(self.config.retry_wait_seconds))
        refreshed = self._refresh_order_state(first_attempt.response.order_id, position, limit_price, position.target_notional_usd)
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
        if second_attempt_state in {LivePositionState.SUBMITTED, LivePositionState.DELAYED, LivePositionState.UNKNOWN} and cumulative_filled_shares > 0:
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
        limit_price, target_notional_usd, resting_reason = self._resting_order_parameters(position, resting_book, current_cost_usd)
        if resting_reason is not None or target_notional_usd <= 0.0:
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    position.strategy_name,
                    LiveTradeEventType.ENTRY_SUBMIT,
                    f"resting fallback skipped: {resting_reason or 'NO_TARGET'}",
                    {"source_reason": source_reason, "resting_book_timestamp": resting_book.timestamp, "resting_reason": resting_reason},
                )
            )
            return None

        attempt = self._place_gtc_attempt(position=position, limit_price=limit_price, target_notional_usd=target_notional_usd)
        raw_patch: dict[str, Any] = {
            "resting_fallback": {
                "source_reason": source_reason,
                "ttl_seconds": float(self.config.resting_fallback_ttl_seconds),
                "notional_fraction": float(self.config.resting_fallback_notional_fraction),
                "resting_book_timestamp": resting_book.timestamp,
                "best_bid": resting_book.best_bid,
                "best_ask": resting_book.best_ask,
                "limit_price": limit_price,
                "target_notional_usd": target_notional_usd,
                "reason": resting_reason,
            }
        }
        final_attempt = attempt
        update_position = attempt.filled_shares > 0 or attempt.state in {LivePositionState.FILLED, LivePositionState.PARTIAL}
        cancel_response: CancelSubmission | None = None
        if attempt.response.order_id is not None and attempt.state in {LivePositionState.SUBMITTED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}:
            time.sleep(float(self.config.resting_fallback_ttl_seconds))
            refreshed = self._refresh_order_state(attempt.response.order_id, position, limit_price, target_notional_usd)
            refreshed_raw: dict[str, Any] | None = None
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
                    limit_price,
                    target_notional_usd,
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
                        limit_price,
                        target_notional_usd,
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
            raw_patch["resting_fallback"].update(
                {
                    "order_status_after_ttl": final_attempt.response.status,
                    "cancel_response": cancel_response.raw if cancel_response is not None else None,
                }
            )

        cumulative_filled_shares = current_filled_shares + final_attempt.filled_shares
        cumulative_cost_usd = current_cost_usd + final_attempt.cost_usd
        cumulative_avg_price = cumulative_cost_usd / cumulative_filled_shares if cumulative_filled_shares > 0 else None
        self._record_live_attempt(
            position_id,
            position,
            attempt_label="resting_fallback",
            attempt=final_attempt,
            update_position=update_position,
            position_state=final_attempt.state,
            position_filled_shares=cumulative_filled_shares,
            position_cost_usd=cumulative_cost_usd,
            position_avg_price=cumulative_avg_price,
            raw_patch=raw_patch,
            order_mode=LiveOrderMode.GTC,
        )
        return final_attempt.state if final_attempt.state in {LivePositionState.SUBMITTED, LivePositionState.FILLED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN} else None

    def _place_gtc_attempt(
        self,
        *,
        position: LivePolicyPosition,
        limit_price: float,
        target_notional_usd: float,
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
        response = submitter.place_gtc_order(
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
        if best_ask - best_bid <= 0.01:
            return 0.0, 0.0, "NO_SPREAD"
        fair_cap = (float(position.entry_fair) - 0.15) if position.entry_fair is not None else float(position.entry_price)
        entry_cap = float(position.raw_json.get("limit_price") or position.entry_price)
        price_cap = _quantize_cent_price(min(best_ask - 0.01, fair_cap, entry_cap))
        price_floor = _quantize_cent_price(best_bid + 0.01)
        midpoint = _quantize_cent_price((best_bid + best_ask) / 2.0)
        limit_price = _quantize_cent_price(min(price_cap, max(price_floor, midpoint)))
        if limit_price <= 0.0:
            return 0.0, 0.0, "NO_RESTING_PRICE"
        if limit_price <= best_bid:
            return limit_price, 0.0, "NO_BID_IMPROVEMENT"
        if limit_price >= best_ask:
            return limit_price, 0.0, "WOULD_CROSS_ASK"
        remaining_notional = max(0.0, float(position.target_notional_usd) - max(0.0, current_cost_usd))
        target_notional = quantize_usdc(remaining_notional * max(0.0, float(self.config.resting_fallback_notional_fraction)))
        if target_notional < float(self.settings.live_min_order_notional):
            return limit_price, target_notional, "NO_REMAINING_NOTIONAL"
        return limit_price, target_notional, None

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
        attempt_payload["execution"] = {
            "attempt_label": attempt_label,
            "update_position": update_position,
            "limit_price": attempt.limit_price,
            "target_notional_usd": attempt.target_notional_usd,
            "target_shares": attempt.target_shares,
            "filled_shares": attempt.filled_shares,
            "cost_usd": attempt.cost_usd,
            "avg_price": attempt.avg_price,
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
        fair = position.entry_fair
        limit_price = quantize_price(min(best_ask + 0.05, fair - 0.15) if fair is not None else best_ask + 0.05)
        if limit_price <= 0.0:
            return 0.0, 0.0, "NO_RETRY_PRICE"
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
