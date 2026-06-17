from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from weather_trader.execution.books import RestBookClient
from weather_trader.execution.contracts import (
    BookSnapshot,
    EngineState,
    MarketSnapshot,
    PredictionSnapshot,
    Signal,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.decision import DecisionEngine
from weather_trader.execution.discovery import MarketDiscoveryService, same_day_markets
from weather_trader.calibration.bucket_probability import DEFAULT_BUCKET_CALIBRATION_PATH
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine, group_key
from weather_trader.execution.liquidity import selected_side_execution_modes, selected_side_liquidity
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import CelsiusWeatherFeatureService, StationWeatherState, WeatherFeatureService
from weather_trader.stations.metadata import get_station, get_station_any


@dataclass(frozen=True)
class ResearchConfig:
    entry_start_local: day_time = day_time(10, 0)
    entry_end_local: day_time = day_time(15, 0)
    snapshot_start_local: day_time | None = None
    snapshot_end_local: day_time | None = None
    low_snapshot_start_local: day_time = day_time(0, 0)
    low_snapshot_end_local: day_time = day_time(10, 0)
    delay_minutes: tuple[int, ...] = (0, 5, 10, 15, 30)
    instant_max_age_minutes: int = 3
    max_obs_age_minutes: int = 30
    bankroll_usd: float = 1000.0
    market_limit: int = 50000
    market_scope: str = "us"
    bucket_calibration_path: Path | None = DEFAULT_BUCKET_CALIBRATION_PATH
    bucket_calibration_mode: str = "apply"


@dataclass(frozen=True)
class ResearchCycleResult:
    engine_state: EngineState
    snapshots_written: int


class ResearchCollector:
    def __init__(
        self,
        store: ExecutionStore,
        model_paths: list[Path],
        config: ResearchConfig | None = None,
        discovery: MarketDiscoveryService | None = None,
        book_client: RestBookClient | None = None,
        weather_service: WeatherFeatureService | None = None,
        decision_engine: DecisionEngine | None = None,
    ) -> None:
        self.store = store
        self.config = config or ResearchConfig()
        self.discovery = discovery or MarketDiscoveryService()
        self.book_client = book_client or RestBookClient()
        if weather_service is not None:
            self.weather_service = weather_service
        elif self.config.market_scope == "global":
            self.weather_service = CelsiusWeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        elif self.config.market_scope == "all":
            self.weather_service = ScopedWeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        else:
            self.weather_service = WeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        self.decision_engine = decision_engine or DecisionEngine()
        self.station_date_decision_engine = StationDateDecisionEngine(self.decision_engine)
        self.fair_value_engines = [
            FairValueEngine(
                path,
                bucket_calibration_path=self.config.bucket_calibration_path,
                bucket_calibration_mode=self.config.bucket_calibration_mode,
            )
            for path in model_paths
        ]

    def run_once(self, as_of_utc: datetime | None = None) -> ResearchCycleResult:
        now = as_of_utc or datetime.now(timezone.utc)
        errors: list[str] = []
        snapshots_written = 0
        skipped = 0

        try:
            markets = same_day_markets(self.discovery.discover(limit=self.config.market_limit, market_scope=self.config.market_scope), now)
            errors.extend(getattr(self.discovery, "last_warnings", []))
        except requests.RequestException as exc:
            errors.append(f"discovery: {exc}")
            engine_state = EngineState(
                timestamp=utc_now_iso(),
                mode="research",
                discovered_markets=0,
                actionable_signals=0,
                orders_submitted=0,
                skipped=0,
                errors=errors,
            )
            self.store.insert_engine_state(engine_state)
            return ResearchCycleResult(engine_state=engine_state, snapshots_written=0)
        for market in markets:
            self.store.upsert_market(market)

        token_ids = sorted(
            {
                token_id
                for market in markets
                for token_id in (market.yes_token_id, market.no_token_id)
                if token_id
            }
        )
        books = self.book_client.fetch_books(token_ids)
        if token_ids and not books:
            errors.append(f"book_coverage_empty: requested={len(token_ids)} returned=0")
        elif token_ids and len(books) < len(token_ids):
            errors.append(f"book_coverage_partial: requested={len(token_ids)} returned={len(books)}")
        for book in books.values():
            self.store.insert_book_snapshot(book)

        weather_by_station = self._fetch_weather_by_station(markets, now, errors)
        markets_by_group: dict[tuple[str, date | None, str], list[MarketSnapshot]] = {}
        for market in markets:
            markets_by_group.setdefault(group_key(market), []).append(market)

        for engine in self.fair_value_engines:
            contexts_by_group: dict[tuple[str, date | None, str], list[GroupMarketContext]] = {}
            for key, group_markets in markets_by_group.items():
                station_id, _, market_family = key
                if not engine.supports_market_family(market_family):
                    continue
                if not _model_supports_station(engine.model_name, station_id):
                    continue
                try:
                    weather = weather_by_station.get(station_id)
                    if weather is None:
                        continue
                    fair_values = engine.price_markets(group_markets, weather)
                    for market in group_markets:
                        signal = self._build_signal(market, books, weather, fair_values[market.market_id])
                        contexts_by_group.setdefault(group_key(market), []).append(
                            GroupMarketContext(
                                market=market,
                                signal=signal,
                                yes_book=books.get(market.yes_token_id or ""),
                                no_book=books.get(market.no_token_id or ""),
                            )
                        )
                except Exception as exc:
                    errors.append(f"model:{engine.model_name}:group:{station_id}: {exc}")

            for key, contexts in contexts_by_group.items():
                station_id, market_date, market_family = key
                if market_date is None:
                    skipped += len(contexts)
                    continue
                weather = weather_by_station.get(station_id)
                if weather is None:
                    skipped += len(contexts)
                    continue
                due_buckets = due_delay_buckets(
                    weather=weather,
                    as_of_utc=now,
                    config=self.config,
                    market_family=market_family,
                )
                if not due_buckets:
                    skipped += len(contexts)
                    continue
                try:
                    selections = self.station_date_decision_engine.select_all_strategies(
                        contexts=contexts,
                        bankroll_usd=self.config.bankroll_usd,
                    )
                    for selection in selections:
                        for bucket in due_buckets:
                            snapshot = build_prediction_snapshot(
                                selection=selection,
                                contexts=contexts,
                                weather=weather,
                                market_date=market_date,
                                as_of_utc=now,
                                obs_delay_bucket=bucket,
                                model_name=engine.model_name,
                            )
                            snapshot_id = self.store.insert_prediction_snapshot(snapshot)
                            if snapshot_id is not None:
                                snapshots_written += 1
                except Exception as exc:
                    errors.append(f"model:{engine.model_name}:group:{station_id}:{market_date}: {exc}")

        engine_state = EngineState(
            timestamp=utc_now_iso(),
            mode="research",
            discovered_markets=len(markets),
            actionable_signals=snapshots_written,
            orders_submitted=0,
            skipped=skipped,
            errors=errors,
        )
        self.store.insert_engine_state(engine_state)
        return ResearchCycleResult(engine_state=engine_state, snapshots_written=snapshots_written)

    def _fetch_weather_by_station(
        self,
        markets: list[MarketSnapshot],
        as_of_utc: datetime,
        errors: list[str],
    ) -> dict[str, StationWeatherState]:
        weather_by_station: dict[str, StationWeatherState] = {}
        for station in sorted({market.station for market in markets}):
            try:
                weather_by_station[station] = self.weather_service.get_state(station, as_of_utc)
            except Exception as exc:
                errors.append(f"weather:{station}: {exc}")
        return weather_by_station

    def _build_signal(
        self,
        market: MarketSnapshot,
        books: dict[str, BookSnapshot],
        weather: StationWeatherState,
        fair,
    ) -> Signal:
        yes_book = books.get(market.yes_token_id or "")
        no_book = books.get(market.no_token_id or "")
        yes_ask = yes_book.best_ask if yes_book else None
        no_ask = no_book.best_ask if no_book else None
        edge_yes = fair.fair_yes - yes_ask if yes_ask is not None else None
        edge_no = fair.fair_no - no_ask if no_ask is not None else None
        signal_side = TradeAction.SKIP
        if edge_yes is not None or edge_no is not None:
            yes_edge = edge_yes if edge_yes is not None else float("-inf")
            no_edge = edge_no if edge_no is not None else float("-inf")
            if max(yes_edge, no_edge) > 0:
                signal_side = TradeAction.BUY_YES if yes_edge >= no_edge else TradeAction.BUY_NO
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


class ScopedWeatherFeatureService:
    def __init__(self, max_obs_age_minutes: int = 30) -> None:
        self.us_service = WeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes)
        self.global_service = CelsiusWeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes)

    def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
        if _station_scope(station_id) == "global":
            return self.global_service.get_state(station_id, as_of_utc)
        return self.us_service.get_state(station_id, as_of_utc)


def _model_supports_station(model_name: str, station_id: str) -> bool:
    return _model_scope(model_name) == _station_scope(station_id)


def _model_scope(model_name: str) -> str:
    return "global" if "international_celsius" in model_name else "us"


def _station_scope(station_id: str) -> str:
    try:
        get_station(station_id)
    except KeyError:
        return "global"
    return "us"


def due_delay_buckets(
    weather: StationWeatherState,
    as_of_utc: datetime,
    config: ResearchConfig,
    market_family: str = "HIGH_TEMP",
) -> list[str]:
    station = get_station_any(weather.station)
    zone = ZoneInfo(station.timezone)
    latest_obs_utc = datetime.fromisoformat(weather.latest_obs_time)
    if latest_obs_utc.tzinfo is None:
        latest_obs_utc = latest_obs_utc.replace(tzinfo=timezone.utc)
    latest_obs_local = latest_obs_utc.astimezone(zone)
    decision_local = as_of_utc.astimezone(zone)
    if market_family == "LOW_TEMP":
        start = config.low_snapshot_start_local
        end = config.low_snapshot_end_local
    else:
        start = config.snapshot_start_local or config.entry_start_local
        end = config.snapshot_end_local or config.entry_end_local
    if not _time_in_window(latest_obs_local.time(), start, end):
        return []
    if decision_local.date() != latest_obs_local.date():
        return []
    age_minutes = max(0.0, (as_of_utc - latest_obs_utc.astimezone(timezone.utc)).total_seconds() / 60.0)
    if age_minutes > config.max_obs_age_minutes:
        return []
    buckets: list[str] = []
    sorted_delays = sorted(config.delay_minutes)
    for index, delay in enumerate(sorted_delays):
        if delay == 0:
            if age_minutes <= config.instant_max_age_minutes:
                buckets.append("instant")
            continue
        next_delay = next((item for item in sorted_delays[index + 1 :] if item > delay), None)
        upper_bound = next_delay if next_delay is not None else config.max_obs_age_minutes + 1
        if delay <= age_minutes < upper_bound:
            buckets.append(f"{delay}m")
    return buckets


def build_prediction_snapshot(
    selection,
    contexts: list[GroupMarketContext],
    weather: StationWeatherState,
    market_date: date,
    as_of_utc: datetime,
    obs_delay_bucket: str,
    model_name: str = "",
) -> PredictionSnapshot:
    selected_market_id = selection.selected_decision.market_id if selection.selected_decision else None
    selected_candidate = next(
        (candidate for candidate in selection.trace.candidates if candidate.get("market_id") == selected_market_id),
        None,
    )
    selected_context = next(
        (context for context in contexts if context.market.market_id == selected_market_id),
        None,
    )
    station = get_station_any(weather.station)
    zone = ZoneInfo(station.timezone)
    latest_obs_utc = datetime.fromisoformat(weather.latest_obs_time)
    if latest_obs_utc.tzinfo is None:
        latest_obs_utc = latest_obs_utc.replace(tzinfo=timezone.utc)
    selected_side = selection.selected_decision.action if selection.selected_decision else TradeAction.SKIP
    selected_book = None
    if selected_context is not None:
        if selected_side == TradeAction.BUY_YES:
            selected_book = selected_context.yes_book
        elif selected_side == TradeAction.BUY_NO:
            selected_book = selected_context.no_book
    liquidity = selected_side_liquidity(selected_book, as_of_utc=as_of_utc)
    selected_fair = None
    if selected_side == TradeAction.BUY_YES:
        selected_fair = _candidate_float(selected_candidate, "fair_yes", selected_context.signal.fair_yes if selected_context else None)
    elif selected_side == TradeAction.BUY_NO:
        selected_fair = _candidate_float(selected_candidate, "fair_no", selected_context.signal.fair_no if selected_context else None)
    execution_modes = selected_side_execution_modes(
        selected_book,
        selected_side=str(selected_side),
        fair=selected_fair,
        entry_edge=selection.selected_decision.expected_value if selection.selected_decision else None,
    )
    ask_sweep = execution_modes["ask_sweep"]
    bid_ladder = execution_modes["bid_ladder"]
    return PredictionSnapshot(
        timestamp=utc_now_iso(),
        station=weather.station,
        market_date=market_date,
        decision_time_utc=as_of_utc.astimezone(timezone.utc).isoformat(),
        decision_time_local=as_of_utc.astimezone(zone).isoformat(),
        latest_obs_time_utc=latest_obs_utc.astimezone(timezone.utc).isoformat(),
        latest_obs_time_local=latest_obs_utc.astimezone(zone).isoformat(),
        obs_age_minutes=max(0.0, (as_of_utc - latest_obs_utc.astimezone(timezone.utc)).total_seconds() / 60.0),
        obs_delay_bucket=obs_delay_bucket,
        current_temp=weather.current_temp,
        high_so_far=weather.high_so_far,
        low_so_far=weather.low_so_far,
        hrrr_remaining_max=weather.hrrr_remaining_max,
        hrrr_remaining_min=weather.hrrr_remaining_min,
        hrrr_current_temp=weather.hrrr_current_temp,
        hrrr_temp_next_3h_max=weather.hrrr_temp_next_3h_max,
        hrrr_temp_next_3h_mean=weather.hrrr_temp_next_3h_mean,
        hrrr_temp_trend_next_3h=weather.hrrr_temp_trend_next_3h,
        hrrr_dewpoint_current=weather.hrrr_dewpoint_current,
        hrrr_dewpoint_next_3h_mean=weather.hrrr_dewpoint_next_3h_mean,
        hrrr_dewpoint_remaining_mean=weather.hrrr_dewpoint_remaining_mean,
        hrrr_rh_current=weather.hrrr_rh_current,
        hrrr_rh_next_3h_mean=weather.hrrr_rh_next_3h_mean,
        hrrr_rh_remaining_mean=weather.hrrr_rh_remaining_mean,
        hrrr_wind_speed_current=weather.hrrr_wind_speed_current,
        hrrr_wind_speed_next_3h_mean=weather.hrrr_wind_speed_next_3h_mean,
        hrrr_wind_speed_remaining_max=weather.hrrr_wind_speed_remaining_max,
        hrrr_gust_remaining_max=weather.hrrr_gust_remaining_max,
        hrrr_cloud_cover_current=weather.hrrr_cloud_cover_current,
        hrrr_cloud_cover_next_3h_mean=weather.hrrr_cloud_cover_next_3h_mean,
        hrrr_cloud_cover_remaining_mean=weather.hrrr_cloud_cover_remaining_mean,
        hrrr_cloud_cover_remaining_max=weather.hrrr_cloud_cover_remaining_max,
        hrrr_shortwave_next_3h_mean=weather.hrrr_shortwave_next_3h_mean,
        hrrr_shortwave_remaining_max=weather.hrrr_shortwave_remaining_max,
        hrrr_forecast_hours_count=weather.hrrr_forecast_hours_count,
        hrrr_current_temp_minus_current_temp=_diff(weather.hrrr_current_temp, weather.current_temp),
        hrrr_remaining_max_minus_selected_lower=_diff(
            weather.hrrr_remaining_max,
            selected_context.market.lower_f if selected_context else None,
        ),
        hrrr_remaining_max_minus_selected_upper=_diff(
            weather.hrrr_remaining_max,
            selected_context.market.upper_f if selected_context else None,
        ),
        hrrr_remaining_min_minus_selected_lower=_diff(
            weather.hrrr_remaining_min,
            selected_context.market.lower_f if selected_context else None,
        ),
        hrrr_remaining_min_minus_selected_upper=_diff(
            weather.hrrr_remaining_min,
            selected_context.market.upper_f if selected_context else None,
        ),
        strategy_bucket=selection.trace.selected_strategy_bucket,
        selected_market_id=selected_market_id,
        selected_bucket=str(selected_candidate.get("bucket")) if selected_candidate else None,
        selected_side=selected_side,
        selected_edge=selection.selected_decision.expected_value if selection.selected_decision else None,
        selected_fair_yes=_candidate_float(selected_candidate, "fair_yes", selected_context.signal.fair_yes if selected_context else None),
        selected_fair_no=_candidate_float(selected_candidate, "fair_no", selected_context.signal.fair_no if selected_context else None),
        selected_yes_ask=selected_context.signal.yes_ask if selected_context else None,
        selected_no_ask=selected_context.signal.no_ask if selected_context else None,
        model_name=model_name,
        high_conviction=bool(selection.selected_decision and selection.selected_decision.strategy_bucket == StrategyBucket.HIGH_CONVICTION),
        skip_reason=selection.trace.skip_reason,
        candidate_count=selection.trace.candidate_count,
        candidate_distribution=selection.trace.candidates,
        selected_best_bid=liquidity["best_bid"],
        selected_best_ask=liquidity["best_ask"],
        selected_spread=liquidity["spread"],
        selected_depth_at_ask=liquidity["depth_at_ask"],
        selected_depth_ask_plus_0_01=liquidity["depth_ask_plus_0_01"],
        selected_depth_ask_plus_0_03=liquidity["depth_ask_plus_0_03"],
        selected_depth_ask_plus_0_05=liquidity["depth_ask_plus_0_05"],
        selected_book_timestamp=liquidity["book_timestamp"],
        selected_book_age_seconds=liquidity["book_age_seconds"],
        selected_liquidity=liquidity["summary"],
        selected_ask_sweep=ask_sweep,
        selected_bid_ladder=bid_ladder,
        selected_sweep_price_cap=ask_sweep.get("price_cap"),
        selected_sweep_depth_to_cap=ask_sweep.get("depth_to_cap"),
        selected_sweep_fillable_25_usd=_target_value(ask_sweep, "25", "fillable_notional_usd"),
        selected_sweep_fillable_50_usd=_target_value(ask_sweep, "50", "fillable_notional_usd"),
        selected_sweep_fillable_100_usd=_target_value(ask_sweep, "100", "fillable_notional_usd"),
        selected_sweep_vwap_25=_target_value(ask_sweep, "25", "vwap"),
        selected_sweep_vwap_50=_target_value(ask_sweep, "50", "vwap"),
        selected_sweep_vwap_100=_target_value(ask_sweep, "100", "vwap"),
        selected_bid_ladder_top_price=bid_ladder.get("post_only_top_bid"),
        selected_bid_ladder_low_price=bid_ladder.get("low_bid"),
        selected_bid_ladder_levels=bid_ladder.get("level_count"),
        selected_bid_ladder_total_notional_usd=bid_ladder.get("total_notional_usd"),
        selected_bid_ladder_top_distance_from_ask=bid_ladder.get("top_distance_from_ask"),
        selected_bid_ladder_top_improvement_over_best_bid=bid_ladder.get("top_improvement_over_best_bid"),
        selected_bid_ladder_min_edge=bid_ladder.get("min_edge"),
        selected_bid_ladder_max_edge=bid_ladder.get("max_edge"),
        market_family=selected_context.market.market_family if selected_context else contexts[0].market.market_family,
    )


def _candidate_float(candidate: dict[str, object] | None, key: str, default: float | None) -> float | None:
    if candidate is None:
        return default
    try:
        value = candidate.get(key)
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _target_value(plan: dict[str, object], target: str, key: str) -> float | None:
    targets = plan.get("targets")
    if not isinstance(targets, dict):
        return None
    row = targets.get(target)
    if not isinstance(row, dict):
        return None
    value = row.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _time_in_window(value: day_time, start: day_time, end: day_time) -> bool:
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def run_research_loop(
    store: ExecutionStore,
    model_paths: list[Path],
    config: ResearchConfig,
    interval_seconds: int,
    max_cycles: int | None = None,
    resolver=None,
    resolver_interval_seconds: int = 3600,
    policy_evaluator=None,
    paper_policy_trader=None,
) -> None:
    collector = ResearchCollector(store=store, model_paths=model_paths, config=config)
    cycle = 0
    last_resolved_at = 0.0
    try:
        while True:
            cycle += 1
            started = time.time()
            result = collector.run_once()
            policy_positions_written = policy_evaluator.evaluate() if policy_evaluator is not None else 0
            paper_policy_result = None
            if paper_policy_trader is not None:
                paper_policy_result = paper_policy_trader.run_once(market_date=store.latest_research_market_date())
            print(
                {
                    "cycle": cycle,
                    "timestamp": result.engine_state.timestamp,
                    "markets": result.engine_state.discovered_markets,
                    "snapshots_written": result.snapshots_written,
                    "policy_positions_written": policy_positions_written,
                    "paper_policy": None if paper_policy_result is None else paper_policy_result.__dict__,
                    "errors": result.engine_state.errors[:5],
                },
                flush=True,
            )
            now_ts = time.time()
            if resolver is not None and now_ts - last_resolved_at >= resolver_interval_seconds:
                resolver.resolve_due()
                last_resolved_at = now_ts
            if max_cycles is not None and cycle >= max_cycles:
                break
            time.sleep(max(1.0, interval_seconds - (time.time() - started)))
    except KeyboardInterrupt:
        print("research-loop stopped")
