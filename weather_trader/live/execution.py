from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import requests

from weather_trader.config import DEFAULT_LIVE_DB, MODELS_DIR
from weather_trader.execution.books import RestBookClient
from weather_trader.execution.clob_executor import ClobExecutor, OrderSubmission
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
from weather_trader.execution.liquidity import quantize_price, quantize_shares, quantize_usdc
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState, WeatherFeatureService
from weather_trader.live.settings import LiveSettings, load_live_settings, private_key_from_env_or_keyfile
from weather_trader.research.collector import ResearchConfig, build_prediction_snapshot, due_delay_buckets
from weather_trader.research.policies import CATBOOST_MODEL, DYNAMIC_TUNED_MODEL, ResearchPolicyEvaluator, ResearchPolicySpec


LIVE_POLICY_NAME = "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first"
LIVE_MODEL_GROUP = "obs_bucket_consensus"
LIVE_MODEL_PATHS = (
    MODELS_DIR / f"{DYNAMIC_TUNED_MODEL}.joblib",
    MODELS_DIR / f"{CATBOOST_MODEL}.joblib",
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


@dataclass(frozen=True)
class LiveExecutionConfig:
    live_db_path: Path = DEFAULT_LIVE_DB
    model_paths: tuple[Path, ...] = LIVE_MODEL_PATHS
    mode: str = "dry-run"
    market_limit: int = 50000
    max_obs_age_minutes: int = 30
    max_book_age_seconds: float = 10.0
    max_notional_usd: float = 3.0
    min_entry_price: float = 0.05
    require_allowance_check: bool = True


@dataclass(frozen=True)
class LiveCycleResult:
    candidates: int
    reserved: int
    submitted: int
    rejected: int
    skipped: int
    errors: list[str]


class LiveSubmitter(Protocol):
    def check_kill_switch(self) -> bool:
        ...

    def check_allowance_buy(self, required_usdc: float):
        ...

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
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


def live_policy_spec(config: LiveExecutionConfig) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        LIVE_POLICY_NAME,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group=LIVE_MODEL_GROUP,
        station_allow_set=PM_ACTIVE_US12_STATIONS,
        entry_price_min=config.min_entry_price,
        local_decision_start="12:00",
        local_decision_end="15:00",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
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
        self.policy_evaluator = ResearchPolicyEvaluator(store, (live_policy_spec(self.config),))
        self.settings = settings or load_live_settings()
        self.submitter = submitter

    def run_once(self, as_of_utc: datetime | None = None) -> LiveCycleResult:
        now = as_of_utc or datetime.now(timezone.utc)
        errors: list[str] = []
        self.store.upsert_live_strategy(default_live_strategy(self.config.max_notional_usd))
        self.store.insert_live_trade_event(
            LiveTradeEvent(utc_now_iso(), None, LIVE_POLICY_NAME, LiveTradeEventType.STRATEGY_REGISTERED, "strategy active", {})
        )
        try:
            markets = same_day_markets(self.discovery.discover(limit=self.config.market_limit), now)
            markets = [
                market
                for market in markets
                if market.market_family == MarketFamily.HIGH_TEMP and market.station in PM_ACTIVE_US12_STATIONS
            ]
        except requests.RequestException as exc:
            return LiveCycleResult(0, 0, 0, 0, 0, [f"discovery: {exc}"])
        for market in markets:
            self.store.upsert_market(market)
        books = self._fetch_books(markets)
        weather_by_station = self._fetch_weather(markets, now, errors)
        candidates = self._build_candidates(markets, books, weather_by_station, now, errors)

        reserved = submitted = rejected = skipped = 0
        market_by_id = {market.market_id: market for market in markets}
        book_by_market_side = _book_by_market_side(markets, books)
        for candidate in candidates:
            market = market_by_id.get(candidate.selected_market_id)
            if market is None:
                skipped += 1
                continue
            selected_book = book_by_market_side.get((candidate.selected_market_id, str(candidate.selected_side)))
            reject_reason = self._candidate_reject_reason(candidate, selected_book)
            position = self._live_position(candidate, market, reject_reason=reject_reason)
            position_id = self.store.insert_live_policy_position(position)
            if position_id is None:
                skipped += 1
                continue
            reserved += 1
            self.store.insert_live_trade_event(
                LiveTradeEvent(utc_now_iso(), position_id, LIVE_POLICY_NAME, LiveTradeEventType.ENTRY_RESERVED, "entry reserved", position.raw_json)
            )
            if reject_reason is not None:
                rejected += 1
                self._record_rejected(position_id, position, reject_reason)
                continue
            result_state = self._submit(position_id, position)
            if result_state in {LivePositionState.SUBMITTED, LivePositionState.FILLED, LivePositionState.PARTIAL, LivePositionState.DELAYED, LivePositionState.UNKNOWN}:
                submitted += 1
            elif result_state == LivePositionState.REJECTED:
                rejected += 1
        self._record_risk_snapshot()
        return LiveCycleResult(len(candidates), reserved, submitted, rejected, skipped, errors)

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
    ) -> list[Any]:
        snapshots: list[dict[str, Any]] = []
        snapshot_id = 1
        grouped: dict[tuple[str, Any, str], list[MarketSnapshot]] = {}
        for market in markets:
            grouped.setdefault(group_key(market), []).append(market)
        research_config = ResearchConfig(max_obs_age_minutes=self.config.max_obs_age_minutes, bankroll_usd=1000.0, market_limit=self.config.market_limit)
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
                    selection = self.decision_engine.select_strategy(contexts, 1000.0, StrategyBucket.HIGH_CONVICTION)
                    for bucket in due_buckets:
                        snapshot = build_prediction_snapshot(selection, contexts, weather, market_date, as_of_utc, bucket, engine.model_name)
                        item = dataclass_to_jsonable(snapshot)
                        item["id"] = snapshot_id
                        snapshot_id += 1
                        snapshots.append(item)
                except Exception as exc:
                    errors.append(f"model:{engine.model_name}:group:{station_id}:{market_date}: {exc}")
        consensus = self.policy_evaluator._build_consensus(snapshots)
        filtered = self.policy_evaluator._candidates_for_policy(live_policy_spec(self.config), snapshots, consensus)
        return [
            position
            for candidate in self.policy_evaluator._first_by_scope(live_policy_spec(self.config), filtered)
            if (position := self.policy_evaluator._position_from_candidate(live_policy_spec(self.config), candidate)) is not None
        ]

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
        )

    def _candidate_reject_reason(self, candidate, book: BookSnapshot | None) -> str | None:
        if book is None or book.best_ask is None:
            return "MISSING_BOOK"
        if candidate.entry_price < self.config.min_entry_price:
            return "ENTRY_PRICE_TOO_LOW"
        if candidate.selected_book_age_seconds is not None and candidate.selected_book_age_seconds > self.config.max_book_age_seconds:
            return "STALE_BOOK"
        if float(candidate.selected_sweep_depth_to_cap or 0.0) < self.config.max_notional_usd:
            return "INSUFFICIENT_DEPTH"
        return None

    def _live_position(self, candidate, market: MarketSnapshot, *, reject_reason: str | None) -> LivePolicyPosition:
        token_id = market.yes_token_id if candidate.selected_side == TradeAction.BUY_YES else market.no_token_id
        limit_price = quantize_price(float(candidate.selected_sweep_price_cap or candidate.entry_price))
        target_notional = quantize_usdc(self.config.max_notional_usd)
        target_shares = quantize_shares(target_notional / limit_price) if limit_price > 0 else 0.0
        return LivePolicyPosition(
            timestamp=utc_now_iso(),
            strategy_name=LIVE_POLICY_NAME,
            station=candidate.station,
            market_date=candidate.market_date,
            market_family=MarketFamily(str(candidate.market_family)),
            scope_key=candidate.scope_key,
            selected_market_id=candidate.selected_market_id,
            selected_token_id=str(token_id or ""),
            selected_side=candidate.selected_side,
            selected_bucket=candidate.selected_bucket,
            obs_delay_bucket=candidate.obs_delay_bucket,
            entry_price=candidate.entry_price,
            entry_fair=candidate.entry_fair,
            entry_edge=candidate.entry_edge,
            target_notional_usd=target_notional,
            target_shares=target_shares,
            state=LivePositionState.RESERVED,
            source_prediction_snapshot_ids=candidate.source_prediction_snapshot_ids,
            raw_json={
                "candidate": dataclass_to_jsonable(candidate),
                "limit_price": limit_price,
                "reject_reason": reject_reason,
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

    def _submit(self, position_id: int, position: LivePolicyPosition) -> LivePositionState:
        limit_price = float(position.raw_json["limit_price"])
        if self.config.mode == "dry-run":
            state = LivePositionState.SUBMITTED
            response = OrderSubmission(True, None, "dry_run", None, {"dry_run": True})
        else:
            submitter = self.submitter or self._default_submitter()
            if submitter.check_kill_switch():
                self._record_rejected(position_id, position, "kill_switch")
                return LivePositionState.REJECTED
            if self.config.require_allowance_check and self.settings.live_require_allowance_check:
                allowance = submitter.check_allowance_buy(position.target_notional_usd)
                if not allowance.ok:
                    self._record_rejected(position_id, position, f"allowance:{allowance.reason}")
                    return LivePositionState.REJECTED
            response = submitter.place_fak_order(
                token_id=position.selected_token_id,
                side="BUY",
                price=limit_price,
                amount=position.target_notional_usd,
            )
            state = _state_from_response(response)
        filled_shares = position.target_shares if state in {LivePositionState.FILLED, LivePositionState.SUBMITTED} and response.success else 0.0
        cost_usd = position.target_notional_usd if filled_shares > 0 else 0.0
        self.store.insert_live_order_attempt(
            LiveOrderAttempt(
                utc_now_iso(),
                position_id,
                self.store.next_live_attempt_seq(position_id),
                position.selected_token_id,
                position.selected_side,
                LiveOrderMode.FAK,
                limit_price,
                position.target_notional_usd,
                position.target_shares,
                response.order_id,
                response.status,
                state,
                response.error_msg or response.status or "submitted",
                filled_shares,
                limit_price if filled_shares > 0 else None,
                cost_usd,
                response.raw,
            )
        )
        self.store.update_live_policy_position_execution(
            position_id,
            state=str(state),
            filled_shares=filled_shares,
            avg_entry_price=limit_price if filled_shares > 0 else None,
            cost_usd=cost_usd,
            raw_patch={"external_order_id": response.order_id, "external_status": response.status, "submit_response": response.raw},
        )
        self.store.insert_live_trade_event(
            LiveTradeEvent(utc_now_iso(), position_id, LIVE_POLICY_NAME, LiveTradeEventType.ENTRY_SUBMIT, str(state), response.raw)
        )
        return state

    def _default_submitter(self) -> LiveSubmitter:
        private_key = private_key_from_env_or_keyfile(self.settings)
        return ClobExecutor(private_key=private_key, settings=self.settings)

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
    if status in {"", "live", "submitted"}:
        return LivePositionState.SUBMITTED
    return LivePositionState.UNKNOWN
