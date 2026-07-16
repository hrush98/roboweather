from __future__ import annotations

from datetime import date, datetime, time, timezone

import requests
import pandas as pd

from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    Decision,
    MarketSnapshot,
    MarketFamily,
    Signal,
    StationDateDecisionTrace,
    StationDateOutcome,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.engine import PaperTradingEngine
from weather_trader.execution.grouping import GroupMarketContext, GroupSelection
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import CelsiusWeatherFeatureService, StationWeatherState
from weather_trader.research import collector as collector_module
from weather_trader.research import resolver as resolver_module
from weather_trader.research.collector import ResearchCollector, ResearchConfig, build_prediction_snapshot, due_delay_buckets
from weather_trader.research.resolver import ResearchResolver, score_snapshot


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


def test_low_due_delay_buckets_use_midnight_to_10am_window() -> None:
    config = ResearchConfig(entry_start_local=time(10, 0), entry_end_local=time(15, 0))

    assert due_delay_buckets(
        _weather("2026-05-06T08:00:00+00:00"),
        datetime(2026, 5, 6, 8, 6, tzinfo=timezone.utc),
        config,
        market_family=str(MarketFamily.LOW_TEMP),
    ) == ["5m"]
    assert due_delay_buckets(
        _weather("2026-05-06T16:00:00+00:00"),
        datetime(2026, 5, 6, 16, 6, tzinfo=timezone.utc),
        config,
        market_family=str(MarketFamily.LOW_TEMP),
    ) == []



def test_due_delay_buckets_can_use_wider_snapshot_window_than_entry_window() -> None:
    config = ResearchConfig(
        entry_start_local=time(10, 0),
        entry_end_local=time(15, 0),
        snapshot_start_local=time(7, 0),
        snapshot_end_local=time(18, 0),
    )

    assert due_delay_buckets(_weather("2026-05-06T13:30:00+00:00"), datetime(2026, 5, 6, 13, 36, tzinfo=timezone.utc), config) == ["5m"]


def test_low_due_delay_buckets_can_use_configured_snapshot_window() -> None:
    config = ResearchConfig(
        entry_start_local=time(10, 0),
        entry_end_local=time(15, 0),
        low_snapshot_start_local=time(0, 0),
        low_snapshot_end_local=time(23, 59),
    )

    assert due_delay_buckets(
        _weather("2026-05-06T16:00:00+00:00"),
        datetime(2026, 5, 6, 16, 6, tzinfo=timezone.utc),
        config,
        market_family=str(MarketFamily.LOW_TEMP),
    ) == ["5m"]


def test_celsius_weather_feature_service_converts_observations() -> None:
    service = CelsiusWeatherFeatureService(obs_client=_InternationalObservationsClient(), hrrr_client=None)

    state = service.get_state("RJTT", datetime(2026, 5, 6, 1, 30, tzinfo=timezone.utc))

    assert state.current_temp == 21.0
    assert state.high_so_far == 21.0
    assert state.low_so_far == 20.0
    assert state.dewpoint == 10.0
    assert state.hrrr_current_temp is None
    assert state.hrrr_remaining_max is None

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


def test_score_snapshot_scores_low_family_against_final_low() -> None:
    snapshot = {
        "id": 11,
        "market_family": "LOW_TEMP",
        "obs_delay_bucket": "5m",
        "selected_market_id": "m1",
        "selected_bucket": "<=72F",
        "selected_side": "BUY_YES",
        "selected_yes_ask": 0.4,
        "selected_no_ask": 0.7,
        "decision_time_local": "2026-05-06T05:05:00-04:00",
        "obs_age_minutes": 5.0,
    }
    outcome = StationDateOutcome(
        timestamp="now",
        station="KATL",
        market_date=date(2026, 5, 6),
        final_high_tmpf=81.0,
        final_low_tmpf=71.0,
        source="IEM_ASOS",
        resolved_at="later",
    )

    result = score_snapshot(snapshot, outcome)

    assert result.market_family == MarketFamily.LOW_TEMP
    assert result.final_low_tmpf == 71.0
    assert result.winning_side == TradeAction.BUY_YES
    assert result.correct is True


def test_global_resolver_uses_hko_daily_high_and_low(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    resolver = ResearchResolver(store=store, hko_client=_HKOClient(), market_scope="global")

    outcome = resolver._resolve_station_date("VHHH", date(2026, 5, 6))

    assert outcome.station == "VHHH"
    assert outcome.final_high_tmpf == 31.2
    assert outcome.final_low_tmpf == 25.4
    assert outcome.source == "HKO_CLMMAXT_CLMMINT_C"


def test_all_scope_resolver_routes_international_station_to_global_source(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    resolver = ResearchResolver(store=store, hko_client=_HKOClient(), market_scope="all")

    outcome = resolver._resolve_station_date("VHHH", date(2026, 5, 6))

    assert outcome.station == "VHHH"
    assert outcome.final_high_tmpf == 31.2
    assert outcome.final_low_tmpf == 25.4
    assert outcome.source == "HKO_CLMMAXT_CLMMINT_C"


def test_global_resolver_falls_back_to_metar_when_hko_daily_missing(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    resolver = ResearchResolver(
        store=store,
        obs_client=_InternationalObservationsClient(),
        hko_client=_EmptyHKOClient(),
        market_scope="all",
    )

    outcome = resolver._resolve_station_date("VHHH", date(2026, 5, 6))

    assert outcome.station == "VHHH"
    assert outcome.final_high_tmpf == 21.0
    assert outcome.final_low_tmpf == 21.0
    assert outcome.source == "IEM_ASOS_METAR_C"


def test_global_low_resolver_uses_polymarket_settlement_for_asian_stations(monkeypatch, tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    for value in (20, 21, 22):
        store.upsert_market(
            _market(
                market_date=date(2026, 5, 6),
                market_family=MarketFamily.LOW_TEMP,
                market_id=f"tokyo-{value}",
                station="RJTT",
                city="Tokyo",
                lower_f=value,
                upper_f=value,
                slug=f"lowest-temperature-in-tokyo-on-may-6-2026-{value}c",
            )
        )

    monkeypatch.setattr(
        resolver_module.requests,
        "get",
        lambda url, timeout: _GammaResponse(
            {
                "slug": "lowest-temperature-in-tokyo-on-may-6-2026",
                "resolutionSource": "https://www.wunderground.com/history/daily/jp/tokyo/RJTT",
                "markets": [
                    {"id": "tokyo-20", "closed": True, "outcomePrices": '["0", "1"]'},
                    {"id": "tokyo-21", "closed": True, "outcomePrices": '["1", "0"]'},
                    {"id": "tokyo-22", "closed": True, "outcomePrices": '["0", "1"]'},
                ],
            }
        ),
    )
    resolver = ResearchResolver(store=store, obs_client=_InternationalObservationsClient())

    outcome = resolver._resolve_station_date("RJTT", date(2026, 5, 6), market_families={MarketFamily.LOW_TEMP})

    assert outcome.station == "RJTT"
    assert outcome.final_high_tmpf == 21.0
    assert outcome.final_low_tmpf == 21.0
    assert outcome.source == "POLYMARKET_GAMMA_LOW_TEMP:IEM_ASOS_METAR_C"


def test_global_high_resolver_does_not_require_polymarket_settlement(monkeypatch, tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    monkeypatch.setattr(
        resolver_module.requests,
        "get",
        lambda url, timeout: (_ for _ in ()).throw(AssertionError("unexpected Gamma request")),
    )
    resolver = ResearchResolver(store=store, obs_client=_InternationalObservationsClient())

    outcome = resolver._resolve_station_date("RJTT", date(2026, 5, 6), market_families={MarketFamily.HIGH_TEMP})

    assert outcome.final_high_tmpf == 21.0
    assert outcome.final_low_tmpf == 20.0
    assert outcome.source == "IEM_ASOS_METAR_C"


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
    assert '"rss_kib_by_stage"' in row["raw_json"]
    assert '"cycle_duration_seconds"' in row["raw_json"]


def test_research_collector_warns_when_books_are_missing(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    collector = ResearchCollector(
        store=store,
        model_paths=[],
        discovery=_StaticDiscovery([_market(market_date=date(2026, 5, 13))]),
        book_client=_EmptyBookClient(),
        weather_service=_StaticWeatherService(),
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


def test_prediction_snapshots_keep_high_and_low_families_separate(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    high_snapshot = build_prediction_snapshot(
        selection=_selection(TradeAction.BUY_YES),
        contexts=[_context()],
        weather=_weather("2026-05-06T16:00:00+00:00"),
        market_date=date(2026, 5, 6),
        as_of_utc=datetime(2026, 5, 6, 16, 1, tzinfo=timezone.utc),
        obs_delay_bucket="instant",
        model_name="model",
    )
    low_context = _context(market_family=MarketFamily.LOW_TEMP)
    low_snapshot = build_prediction_snapshot(
        selection=_selection(TradeAction.BUY_YES),
        contexts=[low_context],
        weather=_weather("2026-05-06T16:00:00+00:00"),
        market_date=date(2026, 5, 6),
        as_of_utc=datetime(2026, 5, 6, 16, 1, tzinfo=timezone.utc),
        obs_delay_bucket="instant",
        model_name="model",
    )

    assert store.insert_prediction_snapshot(high_snapshot) is not None
    assert store.insert_prediction_snapshot(low_snapshot) is not None
    rows = store.connection.execute("select market_family, low_so_far from prediction_snapshots order by id").fetchall()
    assert [row["market_family"] for row in rows] == ["HIGH_TEMP", "LOW_TEMP"]
    assert rows[1]["low_so_far"] == 72


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


def test_scoped_weather_feature_service_routes_by_station_scope(monkeypatch) -> None:
    us_service = _RecordingWeatherService("KATL")
    global_service = _RecordingWeatherService("RJTT")
    monkeypatch.setattr(collector_module, "WeatherFeatureService", lambda max_obs_age_minutes=30: us_service)
    monkeypatch.setattr(collector_module, "CelsiusWeatherFeatureService", lambda max_obs_age_minutes=30: global_service)

    service = collector_module.ScopedWeatherFeatureService(max_obs_age_minutes=30)

    assert service.get_state("KATL", datetime(2026, 5, 6, 16, 0, tzinfo=timezone.utc)).station == "KATL"
    assert service.get_state("RJTT", datetime(2026, 5, 6, 1, 0, tzinfo=timezone.utc)).station == "RJTT"
    assert us_service.calls == ["KATL"]
    assert global_service.calls == ["RJTT"]


def test_model_station_scope_keeps_us_and_global_models_separate() -> None:
    assert collector_module._model_supports_station("dynamic_bucket_pm_active_us12_obs_2022_2025", "KATL")
    assert not collector_module._model_supports_station("dynamic_bucket_pm_active_us12_obs_2022_2025", "RJTT")
    assert collector_module._model_supports_station("dynamic_bucket_international_celsius_high_obs_2022_2025", "RJTT")
    assert not collector_module._model_supports_station("dynamic_bucket_international_celsius_high_obs_2022_2025", "KATL")


class _FailingDiscovery:
    def discover(self, limit: int = 50000, validate_stations: bool = True, market_scope: str = "us"):
        raise requests.ReadTimeout("gamma stalled")


class _StaticDiscovery:
    def __init__(self, markets: list[MarketSnapshot]) -> None:
        self.markets = markets
        self.last_warnings: list[str] = []

    def discover(self, limit: int = 50000, validate_stations: bool = True, market_scope: str = "us"):
        return self.markets


class _EmptyBookClient:
    def fetch_books(self, token_ids: list[str]):
        return {}


class _GammaResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _StaticWeatherService:
    def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
        return _weather(as_of_utc.isoformat())


class _RecordingWeatherService:
    def __init__(self, station: str) -> None:
        self.station = station
        self.calls: list[str] = []

    def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
        self.calls.append(station_id)
        state = _weather(as_of_utc.isoformat())
        return StationWeatherState(**{**state.__dict__, "station": self.station})


class _InternationalObservationsClient:
    def fetch_observations(self, station, start, end):
        return pd.DataFrame(
            {
                "valid": pd.to_datetime(["2026-05-05T15:00:00Z", "2026-05-06T01:00:00Z"], utc=True),
                "tmpf": [68.0, 69.8],
                "dwpf": [50.0, 50.0],
                "sknt": [5.0, 5.0],
                "drct": [90.0, 100.0],
                "skyc1": ["FEW", "SCT"],
                "skyc2": [None, None],
                "skyc3": [None, None],
            }
        )


class _HKOClient:
    def fetch_daily_temperature_series(self, metric, station="HKO"):
        column = "final_low_tmpf" if metric == "low" else "final_high_tmpf"
        value = 25.4 if metric == "low" else 31.2
        return pd.DataFrame({"local_date": [date(2026, 5, 6)], column: [value]})


class _EmptyHKOClient:
    def fetch_daily_temperature_series(self, metric, station="HKO"):
        column = "final_low_tmpf" if metric == "low" else "final_high_tmpf"
        return pd.DataFrame({"local_date": [], column: []})


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


def _context(market_family: MarketFamily = MarketFamily.HIGH_TEMP) -> GroupMarketContext:
    market = _market(market_family=market_family)
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


def _market(
    market_date: date = date(2026, 5, 6),
    market_family: MarketFamily = MarketFamily.HIGH_TEMP,
    market_id: str = "m1",
    station: str = "KATL",
    city: str = "Atlanta",
    lower_f: float | None = 75,
    upper_f: float | None = 76,
    slug: str = "slug",
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id=None,
        question="Will Atlanta hit 75-76F?",
        slug=slug,
        city=city,
        station=station,
        market_date=market_date,
        lower_f=lower_f,
        upper_f=upper_f,
        yes_token_id="yes",
        no_token_id="no",
        end_date="2026-05-06",
        resolution_source="test",
        discovered_at="2026-05-06T15:00:00+00:00",
        market_family=market_family,
    )


def _weather(latest_obs_time: str) -> StationWeatherState:
    return StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 6),
        latest_obs_time=latest_obs_time,
        latest_obs_age_minutes=0,
        current_temp=75,
        high_so_far=80,
        low_so_far=72,
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
        hrrr_remaining_min=70,
        stale=False,
    )
