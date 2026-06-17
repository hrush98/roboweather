from __future__ import annotations

from datetime import date

import joblib
import numpy as np
import pandas as pd
import pytest

from weather_trader.execution.contracts import BookLevel, BookSnapshot, MarketSnapshot, StrategyBucket
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.grouping import GroupMarketContext, StationDateDecisionEngine
from weather_trader.execution.weather import StationWeatherState


class FakeDynamicBucketModel:
    def predict_proba(self, frame):
        probabilities = np.array([0.2, 0.3, 0.5])[: len(frame)]
        return np.column_stack([1.0 - probabilities, probabilities])


class FakeRegressionModel:
    def predict(self, frame):
        return np.full(len(frame), 71.0)


class FakePreprocessor:
    def transform(self, frame):
        return frame


class FakeDistribution:
    loc = np.array([71.0])
    scale = np.array([1.0])


class FakeNGBoost:
    def pred_dist(self, frame):
        return FakeDistribution()


def test_high_regression_prices_and_normalizes_bucket_ladder(tmp_path) -> None:
    model_path = tmp_path / "high_regression_obs_2022_2025.joblib"
    joblib.dump(
        {
            "model_type": "high_regression_empirical_residual",
            "model": FakeRegressionModel(),
            "feature_columns": ["station", "hour_local", "current_temp", "max_temp_so_far"],
            "residuals": pd.DataFrame({"window": ["midday_11_12", "midday_11_12", "midday_11_12"], "residual": [-2.0, 0.0, 2.0]}),
        },
        model_path,
    )

    values = FairValueEngine(model_path).price_markets(_markets(), _weather(high_so_far=68))

    assert round(sum(value.fair_yes for value in values.values()), 8) == 1.0
    assert {market_id: round(value.fair_yes, 6) for market_id, value in values.items()} == {
        "left": 0.333333,
        "middle": 0.333333,
        "right": 0.333333,
    }


def test_ngboost_prices_and_blocks_exceeded_buckets(tmp_path) -> None:
    model_path = tmp_path / "ngboost_normal_obs_2022_2025.joblib"
    joblib.dump(
        {
            "model_type": "ngboost_normal_crps",
            "model": {"preprocessor": FakePreprocessor(), "ngboost": FakeNGBoost()},
            "feature_columns": ["station", "hour_local", "current_temp", "max_temp_so_far"],
        },
        model_path,
    )

    values = FairValueEngine(model_path).price_markets(_markets(), _weather(high_so_far=73))

    assert values["left"].fair_yes == 0.0
    assert values["middle"].fair_yes == 0.0
    assert values["right"].fair_yes == 1.0
    assert "HIGH_SO_FAR_ABOVE_BUCKET" in values["middle"].reason_codes


def test_dynamic_bucket_applies_bucket_probability_calibration(tmp_path) -> None:
    model_path = tmp_path / "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025.joblib"
    joblib.dump(
        {
            "model_type": "dynamic_bucket",
            "model": FakeDynamicBucketModel(),
            "feature_columns": ["station", "hour_local", "current_temp", "max_temp_so_far"],
        },
        model_path,
    )
    calibration_path = _bucket_calibration_artifact(
        tmp_path,
        model_name="dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
        station="KATL",
        intercept=-0.25,
        coef=0.5,
    )

    values = FairValueEngine(
        model_path,
        bucket_calibration_path=calibration_path,
        bucket_calibration_mode="apply",
    ).price_markets(_markets(), _weather(high_so_far=68))

    raw_right = 0.5
    expected_right = 1.0 / (1.0 + np.exp(-(-0.25 + 0.5 * np.log(raw_right / (1.0 - raw_right)))))
    assert values["right"].raw_fair_yes == pytest.approx(raw_right)
    assert values["right"].fair_yes == pytest.approx(expected_right)
    assert values["right"].fair_no == pytest.approx(1.0 - expected_right)
    assert "BUCKET_CALIBRATION_APPLIED" in values["right"].reason_codes
    assert "BUCKET_CALIBRATION_MODEL_STATION" in values["right"].reason_codes
    assert values["right"].bucket_calibration["fit_n"] == 500


def test_dynamic_bucket_calibration_off_preserves_raw_probability(tmp_path) -> None:
    model_path = tmp_path / "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025.joblib"
    joblib.dump(
        {
            "model_type": "dynamic_bucket",
            "model": FakeDynamicBucketModel(),
            "feature_columns": ["station", "hour_local", "current_temp", "max_temp_so_far"],
        },
        model_path,
    )
    calibration_path = _bucket_calibration_artifact(
        tmp_path,
        model_name="dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
        station="KATL",
        intercept=-2.0,
        coef=0.1,
    )

    values = FairValueEngine(
        model_path,
        bucket_calibration_path=calibration_path,
        bucket_calibration_mode="off",
    ).price_markets(_markets(), _weather(high_so_far=68))

    assert values["right"].fair_yes == pytest.approx(0.5)
    assert "BUCKET_CALIBRATION_APPLIED" not in values["right"].reason_codes
    assert values["right"].bucket_calibration["reason"] == "mode_off"


def test_group_selection_recomputes_edge_from_calibrated_fair(tmp_path) -> None:
    model_path = tmp_path / "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025.joblib"
    joblib.dump(
        {
            "model_type": "dynamic_bucket",
            "model": FakeDynamicBucketModel(),
            "feature_columns": ["station", "hour_local", "current_temp", "max_temp_so_far"],
        },
        model_path,
    )
    calibration_path = _bucket_calibration_artifact(
        tmp_path,
        model_name="dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
        station="KATL",
        intercept=-0.25,
        coef=0.5,
    )
    markets = _markets()
    values = FairValueEngine(
        model_path,
        bucket_calibration_path=calibration_path,
        bucket_calibration_mode="apply",
    ).price_markets(markets, _weather(high_so_far=68))
    contexts = [
        GroupMarketContext(
            market=market,
            signal=_signal(market, values[market.market_id]),
            yes_book=_book(market.yes_token_id or "", 0.30),
            no_book=_book(market.no_token_id or "", 0.70),
        )
        for market in markets
    ]

    selection = StationDateDecisionEngine().select_strategy(contexts, 1000.0, strategy_bucket=StrategyBucket.BEST_BUCKET)
    selected = next(candidate for candidate in selection.trace.candidates if candidate["selected"])

    assert selected["market_id"] == "right"
    assert selected["fair_yes"] == pytest.approx(values["right"].fair_yes)
    assert selected["raw_fair_yes"] == pytest.approx(0.5)
    assert selected["bucket_calibration"]["reason"] == "applied"


def _markets() -> list[MarketSnapshot]:
    return [
        _market("left", None, 69),
        _market("middle", 70, 71),
        _market("right", 72, None),
    ]


def _market(market_id: str, lower_f: float | None, upper_f: float | None) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        condition_id=f"condition-{market_id}",
        question=market_id,
        slug=market_id,
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 7),
        lower_f=lower_f,
        upper_f=upper_f,
        yes_token_id=f"yes-{market_id}",
        no_token_id=f"no-{market_id}",
        end_date="",
        resolution_source="",
        discovered_at="now",
    )


def _weather(high_so_far: float) -> StationWeatherState:
    return StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 7),
        latest_obs_time="2026-05-07T16:00:00+00:00",
        latest_obs_age_minutes=15,
        current_temp=70,
        high_so_far=high_so_far,
        low_so_far=65,
        hour_local=12,
        day_of_year=127,
        temp_change_1h=1.0,
        temp_change_3h=3.0,
        dewpoint=60,
        wind_speed=5,
        wind_dir_sin=0.0,
        wind_dir_cos=1.0,
        cloud_cover_code=0.0,
        hrrr_current_temp=None,
        hrrr_remaining_max=None,
        hrrr_remaining_min=None,
        stale=False,
    )


def _signal(market: MarketSnapshot, fair):
    from weather_trader.execution.contracts import Signal, TradeAction, utc_now_iso

    yes_ask = 0.30
    no_ask = 0.70
    return Signal(
        timestamp=utc_now_iso(),
        market_id=market.market_id,
        question=market.question,
        station=market.station,
        market_date=market.market_date,
        lower_f=market.lower_f,
        upper_f=market.upper_f,
        current_temp=70,
        high_so_far=68,
        latest_obs_time="2026-05-07T16:00:00+00:00",
        hrrr_remaining_max=None,
        fair_yes=fair.fair_yes,
        fair_no=fair.fair_no,
        yes_bid=0.20,
        yes_ask=yes_ask,
        yes_depth_usd=100.0,
        no_bid=0.60,
        no_ask=no_ask,
        no_depth_usd=100.0,
        edge_yes=fair.fair_yes - yes_ask,
        edge_no=fair.fair_no - no_ask,
        signal_side=TradeAction.BUY_YES,
        reason_codes=fair.reason_codes,
        model_name=fair.model_name,
        model_features_hash=fair.model_features_hash,
        raw_fair_yes=fair.raw_fair_yes,
        raw_fair_no=fair.raw_fair_no,
        bucket_calibration=fair.bucket_calibration,
    )


def _book(token_id: str, ask: float) -> BookSnapshot:
    return BookSnapshot(token_id=token_id, bids=[BookLevel(max(ask - 0.05, 0.01), 100.0)], asks=[BookLevel(ask, 100.0)], timestamp="now")


def _bucket_calibration_artifact(
    tmp_path,
    *,
    model_name: str,
    station: str,
    intercept: float,
    coef: float,
):
    path = tmp_path / "bucket-calibration.json"
    path.write_text(
        (
            '{"version":1,"kind":"bucket_yes_platt_calibration","feature":"logit","fits":['
            f'{{"model_name":"{model_name}","station":"*","scope":"model_global","intercept":0.0,"coef":1.0,"n":1000}},'
            f'{{"model_name":"{model_name}","station":"{station}","scope":"model_station","intercept":{intercept},"coef":{coef},"n":500}}'
            "]}"
        ),
        encoding="utf-8",
    )
    return path
