from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

from weather_trader.execution.books import RestBookClient
from weather_trader.execution.contracts import (
    BookSnapshot,
    EngineState,
    MarketSnapshot,
    OrderState,
    Position,
    Signal,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.decision import DecisionEngine
from weather_trader.execution.discovery import MarketDiscoveryService, same_day_markets
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine, group_key
from weather_trader.execution.paper_executor import PaperOrderExecutor
from weather_trader.execution.positions import PositionTracker, mark_position
from weather_trader.execution.risk import RiskConfig, RiskManager
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState, WeatherFeatureService


@dataclass(frozen=True)
class PaperCycleResult:
    engine_state: EngineState
    signals: list[Signal]
    orders_submitted: int


class PaperTradingEngine:
    def __init__(
        self,
        store: ExecutionStore,
        fair_value_engine: FairValueEngine,
        discovery: MarketDiscoveryService | None = None,
        book_client: RestBookClient | None = None,
        weather_service: WeatherFeatureService | None = None,
        decision_engine: DecisionEngine | None = None,
        station_date_decision_engine: StationDateDecisionEngine | None = None,
        risk_manager: RiskManager | None = None,
        paper_executor: PaperOrderExecutor | None = None,
    ) -> None:
        self.store = store
        self.discovery = discovery or MarketDiscoveryService()
        self.book_client = book_client or RestBookClient()
        self.weather_service = weather_service or WeatherFeatureService()
        self.fair_value_engine = fair_value_engine
        self.decision_engine = decision_engine or DecisionEngine()
        self.station_date_decision_engine = station_date_decision_engine or StationDateDecisionEngine(self.decision_engine)
        self.risk_manager = risk_manager or RiskManager()
        self.paper_executor = paper_executor or PaperOrderExecutor(strict_fok=True)
        self.position_tracker = PositionTracker(load_positions_from_store(store))

    def run_once(
        self,
        as_of_utc: datetime | None = None,
        market_limit: int = 50000,
        submit_paper_orders: bool = False,
    ) -> PaperCycleResult:
        now = as_of_utc or datetime.now(timezone.utc)
        errors: list[str] = []
        signals: list[Signal] = []
        orders_submitted = 0
        actionable_decisions = 0
        skipped = 0

        try:
            markets = self.discovery.discover(limit=market_limit)
            markets = same_day_markets(markets, now)
        except requests.RequestException as exc:
            errors.append(f"discovery: {exc}")
            engine_state = EngineState(
                timestamp=utc_now_iso(),
                mode="paper",
                discovered_markets=0,
                actionable_signals=0,
                orders_submitted=0,
                skipped=0,
                errors=errors,
            )
            self.store.insert_engine_state(engine_state)
            return PaperCycleResult(engine_state=engine_state, signals=[], orders_submitted=0)
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
                signal = self._build_signal(market, books, now, weather)
                signals.append(signal)
                self.store.insert_signal(signal)
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

        market_by_id = {market.market_id: market for market in markets}
        for contexts in contexts_by_group.values():
            try:
                selection = self.station_date_decision_engine.select(
                    contexts=contexts,
                    bankroll_usd=self.risk_manager.config.bankroll_usd,
                )
                self.store.insert_station_date_decision(selection.trace)
                for decision in selection.decisions.values():
                    market = market_by_id[decision.market_id]
                    if selection.selected_decision is not None and decision.market_id == selection.selected_decision.market_id:
                        decision = self.risk_manager.apply(
                            decision=decision,
                            market_station=market.station,
                            market_date=market.market_date,
                            positions=self.position_tracker.open_positions(),
                            now_ts=time.time(),
                        )
                    self.store.insert_decision(decision)
                    if decision.action == TradeAction.SKIP:
                        skipped += 1
                        continue
                    actionable_decisions += 1
                    if not submit_paper_orders:
                        continue
                    side_book = books.get(decision.token_id or "")
                    order = self.paper_executor.submit(decision, side_book)
                    orders_submitted += 1
                    self.store.insert_paper_order(order)
                    if order.state in {OrderState.FILLED, OrderState.PARTIAL}:
                        self.risk_manager.mark_order_submitted(time.time())
                        current_bid = side_book.best_bid if side_book else None
                        position = self.position_tracker.apply_order(order, market, current_bid=current_bid)
                        if position:
                            self.store.upsert_position(position)
            except Exception as exc:
                group_label = f"{contexts[0].market.station}:{contexts[0].market.market_date}" if contexts else "empty"
                errors.append(f"group:{group_label}: {exc}")

        risk_state = self.risk_manager.current_state(self.position_tracker.open_positions())
        self.store.insert_risk_state(risk_state)
        self._mark_open_positions(books=books, as_of_utc=now, errors=errors, weather_by_station=weather_by_station)

        engine_state = EngineState(
            timestamp=utc_now_iso(),
            mode="paper",
            discovered_markets=len(markets),
            actionable_signals=actionable_decisions,
            orders_submitted=orders_submitted,
            skipped=skipped,
            errors=errors,
        )
        self.store.insert_engine_state(engine_state)
        return PaperCycleResult(engine_state=engine_state, signals=signals, orders_submitted=orders_submitted)

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

    def _mark_open_positions(
        self,
        books: dict[str, BookSnapshot],
        as_of_utc: datetime,
        errors: list[str],
        weather_by_station: dict[str, StationWeatherState] | None = None,
    ) -> None:
        positions = self.position_tracker.open_positions()
        missing_token_ids = sorted({position.token_id for position in positions if position.token_id not in books})
        if missing_token_ids:
            try:
                fresh_books = self.book_client.fetch_books(missing_token_ids)
                books.update(fresh_books)
                for book in fresh_books.values():
                    self.store.insert_book_snapshot(book)
            except Exception as exc:
                errors.append(f"position_books: {exc}")

        high_by_station: dict[str, float | None] = {
            station: weather.high_so_far for station, weather in (weather_by_station or {}).items()
        }
        for position in positions:
            if position.station not in high_by_station:
                try:
                    high_by_station[position.station] = self.weather_service.get_state(position.station, as_of_utc).high_so_far
                except Exception as exc:
                    errors.append(f"position_weather:{position.station}: {exc}")
                    high_by_station[position.station] = None
            mark = mark_position(
                position=position,
                book=books.get(position.token_id),
                high_so_far=high_by_station[position.station],
            )
            self.store.insert_position_mark(mark)
            updated = self.position_tracker.update_mark(mark)
            if updated:
                self.store.upsert_position(updated)

    def _build_signal(
        self,
        market: MarketSnapshot,
        books: dict[str, BookSnapshot],
        as_of_utc: datetime,
        weather: StationWeatherState,
    ) -> Signal:
        fair = self.fair_value_engine.price_market(market, weather)
        yes_book = books.get(market.yes_token_id or "")
        no_book = books.get(market.no_token_id or "")
        reason_codes = list(fair.reason_codes)
        if weather.hour_local < 10 or weather.hour_local > 15:
            reason_codes.append("OUTSIDE_TRADING_WINDOW_BLOCKED")
        if yes_book is None or no_book is None:
            reason_codes.append("MISSING_BOOK_BLOCKED")

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
            reason_codes=reason_codes,
            model_name=fair.model_name,
            model_features_hash=fair.model_features_hash,
            raw_fair_yes=fair.raw_fair_yes,
            raw_fair_no=fair.raw_fair_no,
            bucket_calibration=fair.bucket_calibration,
        )


def load_positions_from_store(store: ExecutionStore) -> list[Position]:
    positions: list[Position] = []
    for row in store.recent_positions():
        market_date = row.get("market_date")
        if market_date:
            market_date = date.fromisoformat(str(market_date))
        positions.append(
            Position(
                position_id=row["position_id"],
                market_id=row["market_id"],
                token_id=row["token_id"],
                side=TradeAction(row["side"]),
                station=row["station"],
                market_date=market_date,
                lower_f=row.get("lower_f"),
                upper_f=row.get("upper_f"),
                shares=float(row["shares"]),
                avg_entry_price=float(row["avg_entry_price"]),
                cost=float(row["cost"]),
                current_bid=row.get("current_bid"),
                mark_value=float(row["mark_value"]),
                unrealized_pnl=float(row["unrealized_pnl"]),
                state=row.get("state", "OPEN"),
            )
        )
    return positions
