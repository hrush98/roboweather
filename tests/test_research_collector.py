from __future__ import annotations

from datetime import date, datetime, time, timezone

import requests

from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    Decision,
    MarketSnapshot,
    Signal,
    StationDateDecisionTrace,
    StationDateOutcome,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.engine import PaperTradingEngine
from weather_trader.execution.grouping import GroupMarketContext, GroupSelection
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import StationWeatherState
from weather_trader.research.collector import ResearchCollector, ResearchConfig, build_prediction_snapshot, due_delay_buckets
from weather_trader.research.resolver import score_snapshot


def test_due_delay_buckets_use_actual_obs_age_bucket() -> None:
    config = ResearchConfig(
        entry_start_local=time(10, 0),
        entry_end_local=time(15, 0),
        delay_minutes=(0, 5, 10, 15, 30),
        instant_max_age_minutes=3,
        max_obs_age_minutes=30,
    )

    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 2, tzinfo=timezone.utc), config) == ["instant"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 6, tzinfo=timezone.utc), config) == ["5m"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 12, tzinfo=timezone.utc), config) == ["10m"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 31, tzinfo=timezone.utc), config) == []


def test_due_delay_buckets_require_obs_inside_entry_window() -> None:
    config = ResearchConfig(entry_start_local=time(10, 0), entry_end_local=time(15, 0))

    assert due_delay_buckets(_weather("2026-05-06T13:30:00+00:00"), datetime(2026, 5, 6, 13, 36, tzinfo=timezone.utc), config) == []


def test_score_snapshot_scores_selected_side_against_final_high() -> None:
    snapshot = {
        "id": 10,
        "obs_delay_bucket": "5m",
        "selected_market_id": "m1",
        "selected_bucket": "80-81F",
        "selected_side": "BUY_YES",
        "selected_yes_ask": 0.4,
        "selected_no_ask": 0.7,
        "selected_edge": 0.2,
        "decision_time_local": "2026-05-06T12:05:00-04:00",
        "obs_age_minutes": 5.0,
    }
    outcome = StationDateOutcome(
        timestamp="now",
        station="KATL",
        market_date=date(2026, 5, 6),
        final_high_tmpf=81.0,
        source="IEM_ASOS",
        resolved_at="later",
    )

    result = score_snapshot(snapshot, outcome)

    assert result.winning_side == TradeAction.BUY_YES
    assert result.correct is True
    assert result.entry_price == 0.4
    assert result.paper_pnl == 0.6


def test_research_collector_records_discovery_timeout_without_crashing(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    collector = ResearchCollector(store=store, model_paths=[], discovery=_FailingDiscovery())

    result = collector.run_once(datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc))

    assert result.snapshots_written == 0
    assert result.engine_state.discovered_markets == 0
    assert result.engine_state.errors
    assert result.engine_state.errors[0].startswith("discovery:")
    row = store.connection.execute("select mode, discovered_markets, raw_json from engine_state").fetchone()
    assert row["mode"] == "research"
    assert row["discovered_markets"] == 0
    assert "gamma stalled" in row["raw_json"]


def test_research_collector_warns_when_books_are_missing(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    collector = ResearchCollector(
        store=store,
        model_paths=[],
        discovery=_StaticDiscovery([_market(market_date=date(2026, 5, 13))]),
        book_client=_EmptyBookClient(),
    )

    result = collector.run_once(datetime(2026, 5, 13, 16, 5, tzinfo=timezone.utc))

    assert result.engine_state.discovered_markets == 1
    assert result.engine_state.errors == ["book_coverage_empty: requested=2 returned=0"]


def test_paper_engine_records_discovery_timeout_without_crashing(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "paper.sqlite")
    engine = PaperTradingEngine(
        store=store,
        fair_value_engine=object(),
        discovery=_FailingDiscovery(),
    )

    result = engine.run_once(datetime(2026, 5, 13, 0, 5, tzinfo=timezone.utc))

    assert result.signals == []
    assert result.engine_state.discovered_markets == 0
    assert result.engine_state.errors
    assert result.engine_state.errors[0].startswith("discovery:")
    row = store.connection.execute("select mode, discovered_markets, raw_json from engine_state").fetchone()
    assert row["mode"] == "paper"
    assert row["discovered_markets"] == 0
    assert "gamma stalled" in row["raw_json"]


def test_build_prediction_snapshot_persists_buy_no_liquidity(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")

    snapshot = build_prediction_snapshot(
        selection=_selection(TradeAction.BUY_NO),
        contexts=[_context()],
        weather=_weather("2026-05-06T16:00:00+00:00"),
        market_date=date(2026, 5, 6),
        as_of_utc=datetime(2026, 5, 6, 16, 5, tzinfo=timezone.utc),
        obs_delay_bucket="5m",
        model_name="model",
    )
    snapshot_id = store.insert_prediction_snapshot(snapshot)

    row = store.connection.execute(
        """
        select selected_best_ask, selected_depth_at_ask, selected_book_age_seconds,
               selected_liquidity_json, hrrr_current_temp,
               hrrr_current_temp_minus_current_temp,
               hrrr_remaining_max_minus_selected_lower,
               hrrr_remaining_max_minus_selected_upper, raw_json
        from prediction_snapshots
        where id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    assert row["selected_best_ask"] == 0.6
    assert row["selected_depth_at_ask"] == 60
    assert row["selected_book_age_seconds"] == 300
    assert row["hrrr_current_temp"] == 74
    assert row["hrrr_current_temp_minus_current_temp"] == -1
    assert row["hrrr_remaining_max_minus_selected_lower"] == 7
    assert row["hrrr_remaining_max_minus_selected_upper"] == 6
    assert '"ask_plus_0_05"' in row["selected_liquidity_json"]
    assert '"selected_liquidity"' in row["raw_json"]
    assert '"hrrr_current_temp"' in row["raw_json"]


def test_build_prediction_snapshot_persists_buy_yes_liquidity(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")

    snapshot = build_prediction_snapshot(
        selection=_selection(TradeAction.BUY_YES),
        contexts=[_context()],
        weather=_weather("2026-05-06T16:00:00+00:00"),
        market_date=date(2026, 5, 6),
        as_of_utc=datetime(2026, 5, 6, 16, 1, tzinfo=timezone.utc),
        obs_delay_bucket="instant",
        model_name="model",
    )
    snapshot_id = store.insert_prediction_snapshot(snapshot)

    row = store.connection.execute(
        "select selected_best_bid, selected_best_ask, selected_spread, selected_depth_ask_plus_0_05 from prediction_snapshots where id = ?",
        (snapshot_id,),
    ).fetchone()
    assert row["selected_best_bid"] == 0.44
    assert row["selected_best_ask"] == 0.5
    assert row["selected_spread"] == 0.06
    assert row["selected_depth_ask_plus_0_05"] == 160


def test_build_prediction_snapshot_persists_sweep_and_bid_ladder(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")

    snapshot = build_prediction_snapshot(
        selection=_selection(TradeAction.BUY_YES, expected_value=0.3, fair_yes=0.8, fair_no=0.2),
        contexts=[_context()],
        weather=_weather("2026-05-06T16:00:00+00:00"),
        market_date=date(2026, 5, 6),
        as_of_utc=datetime(2026, 5, 6, 16, 1, tzinfo=timezone.utc),
        obs_delay_bucket="instant",
        model_name="model",
    )
    snapshot_id = store.insert_prediction_snapshot(snapshot)

    row = store.connection.execute(
        """
        select selected_ask_sweep_json, selected_bid_ladder_json,
               selected_sweep_price_cap, selected_sweep_fillable_50_usd,
               selected_bid_ladder_top_price, selected_bid_ladder_levels,
               selected_bid_ladder_total_notional_usd
        from prediction_snapshots
        where id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    assert '"mode": "ask_sweep"' in row["selected_ask_sweep_json"]
    assert '"mode": "post_only_bid_ladder"' in row["selected_bid_ladder_json"]
    assert row["selected_sweep_price_cap"] == 0.55
    assert row["selected_sweep_fillable_50_usd"] == 50
    assert row["selected_bid_ladder_top_price"] == 0.49
    assert row["selected_bid_ladder_levels"] == 10
    assert row["selected_bid_ladder_total_notional_usd"] == 500


class _FailingDiscovery:
    def discover(self, limit: int = 50000, validate_stations: bool = True):
        raise requests.ReadTimeout("gamma stalled")


class _StaticDiscovery:
    def __init__(self, markets: list[MarketSnapshot]) -> None:
        self.markets = markets
        self.last_warnings: list[str] = []

    def discover(self, limit: int = 50000, validate_stations: bool = True):
        return self.markets


class _EmptyBookClient:
    def fetch_books(self, token_ids: list[str]):
        return {}


def _selection(side: TradeAction, expected_value: float = 0.1, fair_yes: float = 0.7, fair_no: float = 0.3) -> GroupSelection:
    decision = Decision(
        timestamp="2026-05-06T16:00:00+00:00",
        market_id="m1",
        token_id="yes" if side == TradeAction.BUY_YES else "no",
        action=side,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        max_price=0.5 if side == TradeAction.BUY_YES else 0.6,
        target_usd=10,
        expected_value=expected_value,
        skip_reasons=[],
        reason_codes=[],
    )
    trace = StationDateDecisionTrace(
        timestamp="2026-05-06T16:00:00+00:00",
        station="KATL",
        market_date=date(2026, 5, 6),
        candidate_count=1,
        selected_market_id="m1",
        selected_action=side,
        selected_strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        selected_edge=0.1,
        selected_score=0.1,
        skip_reason=None,
        distribution=[],
        candidates=[
            {
                "market_id": "m1",
                "bucket": "75-76F",
                "fair_yes": fair_yes,
                "fair_no": fair_no,
                "selected": True,
            }
        ],
    )
    return GroupSelection(decisions={"m1": decision}, selected_decision=decision, trace=trace)


def _context() -> GroupMarketContext:
    market = _market()
    signal = Signal(
        timestamp="2026-05-06T16:00:00+00:00",
        market_id="m1",
        question=market.question,
        station="KATL",
        market_date=date(2026, 5, 6),
        lower_f=75,
        upper_f=76,
        current_temp=74,
        high_so_far=74,
        latest_obs_time="2026-05-06T16:00:00+00:00",
        hrrr_remaining_max=None,
        fair_yes=0.7,
        fair_no=0.3,
        yes_bid=0.44,
        yes_ask=0.5,
        yes_depth_usd=50,
        no_bid=0.54,
        no_ask=0.6,
        no_depth_usd=60,
        edge_yes=0.2,
        edge_no=-0.3,
        signal_side=TradeAction.BUY_YES,
        reason_codes=[],
        model_name="model",
        model_features_hash="hash",
    )
    return GroupMarketContext(
        market=market,
        signal=signal,
        yes_book=BookSnapshot(
            token_id="yes",
            bids=[BookLevel(0.44, 100)],
            asks=[BookLevel(0.5, 100), BookLevel(0.55, 200)],
            timestamp="2026-05-06T16:00:00+00:00",
        ),
        no_book=BookSnapshot(
            token_id="no",
            bids=[BookLevel(0.54, 100)],
            asks=[BookLevel(0.6, 100), BookLevel(0.65, 200)],
            timestamp="2026-05-06T16:00:00+00:00",
        ),
    )


def _market(market_date: date = date(2026, 5, 6)) -> MarketSnapshot:
    return MarketSnapshot(
        market_id="m1",
        condition_id=None,
        question="Will Atlanta hit 75-76F?",
        slug="slug",
        city="Atlanta",
        station="KATL",
        market_date=market_date,
        lower_f=75,
        upper_f=76,
        yes_token_id="yes",
        no_token_id="no",
        end_date="2026-05-06",
        resolution_source="test",
        discovered_at="2026-05-06T15:00:00+00:00",
    )


def _weather(latest_obs_time: str) -> StationWeatherState:
    return StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 6),
        latest_obs_time=latest_obs_time,
        latest_obs_age_minutes=0,
        current_temp=75,
        high_so_far=80,
        hour_local=12,
        day_of_year=126,
        temp_change_1h=0,
        temp_change_3h=0,
        dewpoint=60,
        wind_speed=5,
        wind_dir_sin=0,
        wind_dir_cos=1,
        cloud_cover_code=0,
        hrrr_current_temp=74,
        hrrr_remaining_max=82,
        stale=False,
    )
