from __future__ import annotations

from datetime import date

import joblib
import numpy as np
import pandas as pd

from weather_trader.execution.contracts import MarketSnapshot
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.weather import StationWeatherState


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
        stale=False,
    )
