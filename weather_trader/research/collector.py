from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine, group_key
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState, WeatherFeatureService
from weather_trader.stations.metadata import get_station


@dataclass(frozen=True)
class ResearchConfig:
    entry_start_local: day_time = day_time(10, 0)
    entry_end_local: day_time = day_time(15, 0)
    delay_minutes: tuple[int, ...] = (0, 5, 10, 15, 30)
    instant_max_age_minutes: int = 3
    max_obs_age_minutes: int = 30
    bankroll_usd: float = 1000.0
    market_limit: int = 50000


@dataclass(frozen=True)
class ResearchCycleResult:
    engine_state: EngineState
    snapshots_written: int


class ResearchCollector:
    def __init__(
        self,
        store: ExecutionStore,
        model_path: Path,
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
        self.weather_service = weather_service or WeatherFeatureService(max_obs_age_minutes=self.config.max_obs_age_minutes)
        self.fair_value_engine = FairValueEngine(model_path)
        self.decision_engine = decision_engine or DecisionEngine()
        self.station_date_decision_engine = StationDateDecisionEngine(self.decision_engine)

    def run_once(self, as_of_utc: datetime | None = None) -> ResearchCycleResult:
        now = as_of_utc or datetime.now(timezone.utc)
        errors: list[str] = []
        snapshots_written = 0
        skipped = 0

        markets = same_day_markets(self.discovery.discover(limit=self.config.market_limit), now)
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
        for book in books.values():
            self.store.insert_book_snapshot(book)

        weather_by_station = self._fetch_weather_by_station(markets, now, errors)
        contexts_by_group: dict[tuple[str, date | None], list[GroupMarketContext]] = {}
        for market in markets:
            try:
                weather = weather_by_station.get(market.station)
                if weather is None:
                    errors.append(f"{market.market_id}: missing weather for {market.station}")
                    continue
                signal = self._build_signal(market, books, weather)
                contexts_by_group.setdefault(group_key(market), []).append(
                    GroupMarketContext(
                        market=market,
                        signal=signal,
                        yes_book=books.get(market.yes_token_id or ""),
                        no_book=books.get(market.no_token_id or ""),
                    )
                )
            except Exception as exc:
                errors.append(f"{market.market_id}: {exc}")

        for key, contexts in contexts_by_group.items():
            station_id, market_date = key
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
            )
            if not due_buckets:
                skipped += len(contexts)
                continue
            try:
                selection = self.station_date_decision_engine.select(
                    contexts=contexts,
                    bankroll_usd=self.config.bankroll_usd,
                )
                for bucket in due_buckets:
                    snapshot = build_prediction_snapshot(
                        selection=selection,
                        contexts=contexts,
                        weather=weather,
                        market_date=market_date,
                        as_of_utc=now,
                        obs_delay_bucket=bucket,
                    )
                    snapshot_id = self.store.insert_prediction_snapshot(snapshot)
                    if snapshot_id is not None:
                        snapshots_written += 1
            except Exception as exc:
                errors.append(f"group:{station_id}:{market_date}: {exc}")

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
    ) -> Signal:
        fair = self.fair_value_engine.price_market(market, weather)
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
        )


def due_delay_buckets(
    weather: StationWeatherState,
    as_of_utc: datetime,
    config: ResearchConfig,
) -> list[str]:
    station = get_station(weather.station)
    zone = ZoneInfo(station.timezone)
    latest_obs_utc = datetime.fromisoformat(weather.latest_obs_time)
    if latest_obs_utc.tzinfo is None:
        latest_obs_utc = latest_obs_utc.replace(tzinfo=timezone.utc)
    latest_obs_local = latest_obs_utc.astimezone(zone)
    decision_local = as_of_utc.astimezone(zone)
    if not _time_in_window(latest_obs_local.time(), config.entry_start_local, config.entry_end_local):
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
    station = get_station(weather.station)
    zone = ZoneInfo(station.timezone)
    latest_obs_utc = datetime.fromisoformat(weather.latest_obs_time)
    if latest_obs_utc.tzinfo is None:
        latest_obs_utc = latest_obs_utc.replace(tzinfo=timezone.utc)
    selected_side = selection.selected_decision.action if selection.selected_decision else TradeAction.SKIP
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
        hrrr_remaining_max=weather.hrrr_remaining_max,
        selected_market_id=selected_market_id,
        selected_bucket=str(selected_candidate.get("bucket")) if selected_candidate else None,
        selected_side=selected_side,
        selected_edge=selection.selected_decision.expected_value if selection.selected_decision else None,
        selected_fair_yes=selected_context.signal.fair_yes if selected_context else None,
        selected_fair_no=selected_context.signal.fair_no if selected_context else None,
        selected_yes_ask=selected_context.signal.yes_ask if selected_context else None,
        selected_no_ask=selected_context.signal.no_ask if selected_context else None,
        high_conviction=bool(selection.selected_decision and selection.selected_decision.strategy_bucket == StrategyBucket.HIGH_CONVICTION),
        skip_reason=selection.trace.skip_reason,
        candidate_count=selection.trace.candidate_count,
        candidate_distribution=selection.trace.candidates,
    )


def _time_in_window(value: day_time, start: day_time, end: day_time) -> bool:
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def run_research_loop(
    store: ExecutionStore,
    model_path: Path,
    config: ResearchConfig,
    interval_seconds: int,
    max_cycles: int | None = None,
    resolver=None,
    resolver_interval_seconds: int = 3600,
) -> None:
    collector = ResearchCollector(store=store, model_path=model_path, config=config)
    cycle = 0
    last_resolved_at = 0.0
    try:
        while True:
            cycle += 1
            started = time.time()
            result = collector.run_once()
            print(
                {
                    "cycle": cycle,
                    "timestamp": result.engine_state.timestamp,
                    "markets": result.engine_state.discovered_markets,
                    "snapshots_written": result.snapshots_written,
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
