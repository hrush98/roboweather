from __future__ import annotations

from datetime import date

import numpy as np

from weather_trader.execution.books import parse_book_snapshot
from weather_trader.execution.contracts import (
    BookLevel,
    BookSnapshot,
    Decision,
    EffectiveStatus,
    MarketSnapshot,
    Position,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.decision import DecisionConfig, DecisionEngine, finalize_decision
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine
from weather_trader.execution.paper_executor import PaperOrderExecutor
from weather_trader.execution.positions import effective_status_for_position, mark_position
from weather_trader.execution.risk import RiskConfig, RiskManager
from weather_trader.execution.weather import StationWeatherState


def test_parse_book_snapshot_sorts_bids_and_asks() -> None:
    book = parse_book_snapshot(
        "token",
        {
            "bids": [{"price": "0.21", "size": "10"}, {"price": "0.27", "size": "5"}],
            "asks": [{"price": "0.35", "size": "3"}, {"price": "0.31", "size": "7"}],
        },
    )
    assert book.best_bid == 0.27
    assert book.best_ask == 0.31
    assert book.ask_depth_usd(0.31) == 0.31 * 7


def test_paper_executor_strict_fok_walks_depth() -> None:
    decision = Decision(
        timestamp="now",
        market_id="m1",
        token_id="yes",
        action=TradeAction.BUY_YES,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        max_price=0.50,
        target_usd=10.0,
        expected_value=0.2,
        skip_reasons=[],
        reason_codes=[],
    )
    book = BookSnapshot(
        token_id="yes",
        bids=[],
        asks=[BookLevel(0.40, 10), BookLevel(0.50, 10), BookLevel(0.51, 100)],
        timestamp="now",
    )
    order = PaperOrderExecutor(strict_fok=True).submit(decision, book)
    assert order.state == "FILLED"
    assert order.filled_shares == 20
    assert order.cost == 9
    assert order.avg_price == 0.45


def test_paper_executor_rejects_unfilled_fok() -> None:
    decision = Decision(
        timestamp="now",
        market_id="m1",
        token_id="yes",
        action=TradeAction.BUY_YES,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        max_price=0.50,
        target_usd=10.0,
        expected_value=0.2,
        skip_reasons=[],
        reason_codes=[],
    )
    book = BookSnapshot(token_id="yes", bids=[], asks=[BookLevel(0.40, 5)], timestamp="now")
    order = PaperOrderExecutor(strict_fok=True).submit(decision, book)
    assert order.state == "REJECTED"
    assert order.reject_reason == "FOK_NOT_FILLED"


def test_decision_engine_uses_no_ask_for_buy_no() -> None:
    market = _market()
    signal = _signal(fair_yes=0.1, fair_no=0.9, yes_ask=0.2, no_ask=0.6)
    decision = DecisionEngine(DecisionConfig(high_conviction_min_edge=0.1)).decide(
        market=market,
        signal=signal,
        yes_book=_book("yes", ask=0.2),
        no_book=_book("no", ask=0.6),
        bankroll_usd=1000,
    )
    decision = finalize_decision(decision, market.market_id, signal.reason_codes)
    assert decision.action == TradeAction.BUY_NO
    assert decision.max_price == 0.6


def test_decision_engine_vetoes_buy_yes_when_hrrr_stays_below_bucket() -> None:
    market = _market(lower=86, upper=87)
    signal = _signal(fair_yes=0.9, fair_no=0.1, yes_ask=0.5, no_ask=0.9)
    signal = _replace_signal(signal, lower_f=86, upper_f=87, high_so_far=82, hrrr_remaining_max=84)
    decision = DecisionEngine(DecisionConfig(high_conviction_min_edge=0.1)).decide(
        market=market,
        signal=signal,
        yes_book=_book("yes", ask=0.5),
        no_book=_book("no", ask=0.9),
        bankroll_usd=1000,
    )
    assert decision.action == TradeAction.SKIP
    assert "NO_DECISION_RULE_MATCH" in decision.skip_reasons


def test_decision_engine_vetoes_buy_no_when_hrrr_supports_current_bucket() -> None:
    market = _market(lower=80, upper=81)
    signal = _signal(fair_yes=0.2, fair_no=0.8, yes_ask=0.5, no_ask=0.5)
    signal = _replace_signal(signal, lower_f=80, upper_f=81, high_so_far=80, hrrr_remaining_max=81)
    decision = DecisionEngine(DecisionConfig(high_conviction_min_edge=0.1)).decide(
        market=market,
        signal=signal,
        yes_book=_book("yes", ask=0.5),
        no_book=_book("no", ask=0.5),
        bankroll_usd=1000,
    )
    assert decision.action == TradeAction.SKIP


def test_decision_engine_blocks_generic_blocked_reason() -> None:
    market = _market(lower=80, upper=81)
    signal = _signal(fair_yes=0.9, fair_no=0.1, yes_ask=0.5, no_ask=0.9)
    signal = _replace_signal(signal, reason_codes=["MODEL_PROBABILITY", "OUTSIDE_TRADING_WINDOW_BLOCKED"])
    decision = DecisionEngine().decide(
        market=market,
        signal=signal,
        yes_book=_book("yes", ask=0.5),
        no_book=_book("no", ask=0.9),
        bankroll_usd=1000,
    )
    assert decision.action == TradeAction.SKIP
    assert "OUTSIDE_TRADING_WINDOW_BLOCKED" in decision.skip_reasons


def test_fair_value_inside_bucket_conditions_on_not_overshooting_upper() -> None:
    engine = FairValueEngine.__new__(FairValueEngine)
    engine.model = _ThresholdModel()
    engine.feature_columns = ["threshold"]
    engine.model_name = "dummy"
    engine.model_features_hash = "hash"
    market = _market(lower=88, upper=89)
    weather = _weather(high_so_far=88)
    result = engine.price_market(market, weather)
    assert np.isclose(result.fair_yes, 0.8)
    assert "HIGH_SO_FAR_INSIDE_BUCKET" in result.reason_codes


def test_dynamic_bucket_fair_values_normalize_across_ladder() -> None:
    engine = FairValueEngine.__new__(FairValueEngine)
    engine.model = _BucketModel({"m1": 0.2, "m2": 0.6, "m3": 0.2})
    engine.feature_columns = ["bucket_lower", "bucket_upper"]
    engine.model_type = "dynamic_bucket"
    engine.model_name = "dummy"
    engine.model_features_hash = "hash"
    markets = [
        _market(lower=78, upper=79),
        _replace_market(_market(lower=80, upper=81), market_id="m2", yes_token_id="yes2", no_token_id="no2"),
        _replace_market(_market(lower=82, upper=83), market_id="m3", yes_token_id="yes3", no_token_id="no3"),
    ]

    results = engine.price_markets(markets, _weather(high_so_far=78))

    assert np.isclose(sum(result.fair_yes for result in results.values()), 1.0)
    assert np.isclose(results["m2"].fair_yes, 0.6)


def test_station_date_group_records_all_research_strategies() -> None:
    first = _market(lower=80, upper=81)
    second = _replace_market(_market(lower=82, upper=83), market_id="m2", yes_token_id="yes2", no_token_id="no2")
    third = _replace_market(_market(lower=86, upper=None), market_id="m3", yes_token_id="yes3", no_token_id="no3")
    contexts = [
        GroupMarketContext(
            market=first,
            signal=_replace_signal(_signal_for_market(first, fair_yes=0.14, fair_no=0.86, yes_ask=0.05, no_ask=0.9), high_so_far=80),
            yes_book=_book("yes", ask=0.05),
            no_book=_book("no", ask=0.9),
        ),
        GroupMarketContext(
            market=second,
            signal=_replace_signal(_signal_for_market(second, fair_yes=0.22, fair_no=0.78, yes_ask=0.14, no_ask=0.9), high_so_far=80),
            yes_book=_book("yes2", ask=0.14),
            no_book=_book("no2", ask=0.9),
        ),
        GroupMarketContext(
            market=third,
            signal=_replace_signal(_signal_for_market(third, fair_yes=0.03, fair_no=0.97, yes_ask=0.4, no_ask=0.9), high_so_far=80),
            yes_book=_book("yes3", ask=0.4),
            no_book=_book("no3", ask=0.9),
        ),
    ]

    selections = StationDateDecisionEngine(DecisionEngine()).select_all_strategies(contexts, bankroll_usd=1000)
    by_strategy = {selection.trace.selected_strategy_bucket: selection for selection in selections}

    assert by_strategy[StrategyBucket.BEST_BUCKET].selected_decision is not None
    assert by_strategy[StrategyBucket.BEST_BUCKET].selected_decision.market_id == "m2"
    assert by_strategy[StrategyBucket.BEST_BUCKET].selected_decision.action == TradeAction.BUY_YES
    assert by_strategy[StrategyBucket.TAIL].selected_decision is not None
    assert by_strategy[StrategyBucket.TAIL].selected_decision.market_id == "m1"
    assert by_strategy[StrategyBucket.TAIL].selected_decision.action == TradeAction.BUY_YES
    assert by_strategy[StrategyBucket.MAX_SO_FAR].selected_decision is not None
    assert by_strategy[StrategyBucket.MAX_SO_FAR].selected_decision.market_id == "m1"
    assert by_strategy[StrategyBucket.MAX_SO_FAR].selected_decision.target_usd == 0.0


def test_mark_position_uses_current_bid_for_unrealized_pnl() -> None:
    position = _position(side=TradeAction.BUY_NO, shares=10, cost=5, avg_entry_price=0.5)
    book = BookSnapshot(token_id="no", bids=[BookLevel(0.7, 100)], asks=[BookLevel(0.72, 100)], timestamp="now")
    mark = mark_position(position, book, high_so_far=79)

    assert mark.current_bid == 0.7
    assert mark.mark_value == 7
    assert mark.unrealized_pnl == 2
    assert mark.unrealized_pnl_pct == 0.4
    assert mark.effective_status == EffectiveStatus.LIVE


def test_mark_position_missing_bid_preserves_unavailable_mark() -> None:
    mark = mark_position(_position(), BookSnapshot(token_id="yes", bids=[], asks=[], timestamp="now"), high_so_far=79)

    assert mark.current_bid is None
    assert mark.mark_value is None
    assert mark.unrealized_pnl is None
    assert mark.reason == "MISSING_BID"


def test_effective_status_for_or_higher_market() -> None:
    assert effective_status_for_position(TradeAction.BUY_YES, lower_f=74, upper_f=None, high_so_far=74) == EffectiveStatus.EFFECTIVELY_WON
    assert effective_status_for_position(TradeAction.BUY_NO, lower_f=74, upper_f=None, high_so_far=74) == EffectiveStatus.EFFECTIVELY_LOST
    assert effective_status_for_position(TradeAction.BUY_YES, lower_f=74, upper_f=None, high_so_far=73) == EffectiveStatus.LIVE


def test_effective_status_for_bounded_bucket_overshoot() -> None:
    assert effective_status_for_position(TradeAction.BUY_YES, lower_f=66, upper_f=67, high_so_far=68) == EffectiveStatus.EFFECTIVELY_LOST
    assert effective_status_for_position(TradeAction.BUY_NO, lower_f=66, upper_f=67, high_so_far=68) == EffectiveStatus.EFFECTIVELY_WON
    assert effective_status_for_position(TradeAction.BUY_YES, lower_f=66, upper_f=67, high_so_far=66) == EffectiveStatus.LIVE


def test_station_date_group_selects_one_best_candidate() -> None:
    first = _market(lower=56, upper=57)
    second = _replace_market(_market(lower=58, upper=59), market_id="m2", yes_token_id="yes2", no_token_id="no2")
    contexts = [
        GroupMarketContext(
            market=first,
            signal=_signal_for_market(first, fair_yes=0.2, fair_no=0.8, yes_ask=0.9, no_ask=0.6),
            yes_book=_book("yes", ask=0.9),
            no_book=_book("no", ask=0.6),
        ),
        GroupMarketContext(
            market=second,
            signal=_signal_for_market(second, fair_yes=0.1, fair_no=0.9, yes_ask=0.9, no_ask=0.5),
            yes_book=_book("yes2", ask=0.9),
            no_book=_book("no2", ask=0.5),
        ),
    ]

    selection = StationDateDecisionEngine(DecisionEngine(DecisionConfig(high_conviction_min_edge=0.1))).select(contexts, bankroll_usd=1000)

    assert selection.selected_decision is not None
    assert selection.selected_decision.market_id == "m2"
    assert selection.decisions["m2"].action == TradeAction.BUY_NO
    assert selection.decisions["m1"].action == TradeAction.SKIP
    assert "GROUP_NOT_SELECTED" in selection.decisions["m1"].skip_reasons
    assert selection.trace.candidate_count == 2
    assert selection.trace.selected_market_id == "m2"


def test_risk_blocks_second_station_date_position() -> None:
    decision = Decision(
        timestamp="now",
        market_id="m2",
        token_id="no2",
        action=TradeAction.BUY_NO,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        max_price=0.5,
        target_usd=5.0,
        expected_value=0.2,
        skip_reasons=[],
        reason_codes=[],
    )
    existing = _position(side=TradeAction.BUY_NO, cost=5)

    result = RiskManager(RiskConfig(bankroll_usd=1000)).apply(
        decision=decision,
        market_station="KATL",
        market_date=date(2026, 5, 5),
        positions=[existing],
        now_ts=0,
    )

    assert result.action == TradeAction.SKIP
    assert "STATION_DATE_POSITION_EXISTS" in result.skip_reasons


def _market(lower: float = 80, upper: float = 81) -> MarketSnapshot:
    return MarketSnapshot(
        market_id="m1",
        condition_id="c1",
        question="Will temp be between 80-81F?",
        slug="slug",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 5),
        lower_f=lower,
        upper_f=upper,
        yes_token_id="yes",
        no_token_id="no",
        end_date="",
        resolution_source="",
        discovered_at="now",
    )


def _replace_market(market, **updates):
    from dataclasses import replace

    return replace(market, **updates)


def _signal(fair_yes: float, fair_no: float, yes_ask: float, no_ask: float):
    from weather_trader.execution.contracts import Signal

    return Signal(
        timestamp="now",
        market_id="m1",
        question="q",
        station="KATL",
        market_date=date(2026, 5, 5),
        lower_f=80,
        upper_f=81,
        current_temp=80,
        high_so_far=80,
        latest_obs_time="now",
        hrrr_remaining_max=None,
        fair_yes=fair_yes,
        fair_no=fair_no,
        yes_bid=0.1,
        yes_ask=yes_ask,
        yes_depth_usd=100,
        no_bid=0.5,
        no_ask=no_ask,
        no_depth_usd=100,
        edge_yes=fair_yes - yes_ask,
        edge_no=fair_no - no_ask,
        signal_side=TradeAction.BUY_NO,
        reason_codes=["MODEL_PROBABILITY"],
        model_name="m",
        model_features_hash="h",
    )


def _signal_for_market(market: MarketSnapshot, fair_yes: float, fair_no: float, yes_ask: float, no_ask: float):
    signal = _signal(fair_yes=fair_yes, fair_no=fair_no, yes_ask=yes_ask, no_ask=no_ask)
    return _replace_signal(
        signal,
        market_id=market.market_id,
        lower_f=market.lower_f,
        upper_f=market.upper_f,
    )


def _replace_signal(signal, **updates):
    from dataclasses import replace

    return replace(signal, **updates)


def _book(token_id: str, ask: float) -> BookSnapshot:
    return BookSnapshot(
        token_id=token_id,
        bids=[BookLevel(max(0.01, ask - 0.02), 100)],
        asks=[BookLevel(ask, 100)],
        timestamp="now",
    )


def _weather(high_so_far: float) -> StationWeatherState:
    return StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 5),
        latest_obs_time="now",
        latest_obs_age_minutes=5,
        current_temp=high_so_far,
        high_so_far=high_so_far,
        hour_local=12,
        day_of_year=125,
        temp_change_1h=1,
        temp_change_3h=3,
        dewpoint=60,
        wind_speed=5,
        wind_dir_sin=0,
        wind_dir_cos=1,
        cloud_cover_code=0,
        hrrr_current_temp=None,
        hrrr_remaining_max=None,
        stale=False,
    )


def _position(
    side: TradeAction = TradeAction.BUY_YES,
    shares: float = 10,
    cost: float = 5,
    avg_entry_price: float = 0.5,
) -> Position:
    return Position(
        position_id="m1:yes",
        market_id="m1",
        token_id="yes",
        side=side,
        station="KATL",
        market_date=date(2026, 5, 5),
        lower_f=80,
        upper_f=81,
        shares=shares,
        avg_entry_price=avg_entry_price,
        cost=cost,
        current_bid=None,
        mark_value=0,
        unrealized_pnl=0,
    )


class _ThresholdModel:
    def predict_proba(self, frame):
        threshold = float(frame["threshold"].iloc[0])
        p = 0.2 if threshold == 90 else 1.0
        return np.array([[1.0 - p, p]])


class _BucketModel:
    def __init__(self, probabilities: dict[str, float]) -> None:
        self.probabilities = probabilities

    def predict_proba(self, frame):
        values = []
        for row in frame.itertuples(index=False):
            if row.bucket_lower == 78:
                p = self.probabilities["m1"]
            elif row.bucket_lower == 80:
                p = self.probabilities["m2"]
            else:
                p = self.probabilities["m3"]
            values.append([1.0 - p, p])
        return np.array(values)
