from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from weather_trader.forecasting.evaluation import FixedSupport, normalize_probability_matrix


REMAINING_HEATING_CONTRACT_VERSION = "remaining_heating_hurdle_ordinal_v1"

OBSERVATION_FEATURES = (
    "hour_local", "day_of_year_sin", "day_of_year_cos", "current_temp",
    "max_temp_so_far", "temp_range_so_far", "temp_change_1h", "temp_change_3h",
    "dewpoint", "relative_humidity", "wet_bulb_approx", "pressure_tendency_3h",
    "wind_speed", "wind_dir_sin", "wind_dir_cos", "cloud_cover_code",
)
HRRR_FEATURES = (
    "hrrr_current_temp", "hrrr_remaining_max", "hrrr_temp_next_3h_max",
    "hrrr_temp_next_3h_mean", "hrrr_temp_trend_next_3h",
    "hrrr_dewpoint_current", "hrrr_dewpoint_next_3h_mean", "hrrr_rh_current",
    "hrrr_rh_next_3h_mean", "hrrr_wind_speed_current",
    "hrrr_wind_speed_next_3h_mean", "hrrr_cloud_cover_current",
    "hrrr_cloud_cover_next_3h_mean", "hrrr_shortwave_next_3h_mean",
    "hrrr_shortwave_remaining_max", "hrrr_current_temp_minus_current_temp",
)
DEFAULT_FEATURES = OBSERVATION_FEATURES + HRRR_FEATURES


@dataclass(frozen=True)
class RemainingHeatingContract:
    version: str = REMAINING_HEATING_CONTRACT_VERSION
    support: FixedSupport = FixedSupport()
    numeric_features: tuple[str, ...] = DEFAULT_FEATURES
    categorical_features: tuple[str, ...] = ("station",)
    regularization_c: float = 0.5
    min_class_rows: int = 20
    random_state: int = 20260813


class RemainingHeatingModel:
    """Coherent hurdle/ordinal distribution for the final integer daily high."""

    def __init__(self, contract: RemainingHeatingContract | None = None) -> None:
        self.contract = contract or RemainingHeatingContract()
        self.preprocessor: ColumnTransformer | None = None
        self.peak_model: LogisticRegression | float | None = None
        self.threshold_models: dict[int, LogisticRegression | float] = {}
        self.maximum_train_additional = 0
        self.training_summary: dict[str, Any] = {}

    def fit(self, snapshots: pd.DataFrame) -> "RemainingHeatingModel":
        frame, high_integer = self._prepare(snapshots)
        if "final_high_tmpf" not in frame:
            raise ValueError("training snapshots require final_high_tmpf")
        target = np.rint(pd.to_numeric(frame["final_high_tmpf"], errors="raise")).astype(int)
        additional = target - high_integer
        if (additional < 0).any():
            count = int((additional < 0).sum())
            raise ValueError(f"{count} rows have integer final high below integer high-so-far")

        self.preprocessor = self._build_preprocessor()
        transformed = self.preprocessor.fit_transform(frame)
        peak_passed = (additional == 0).astype(int)
        self.peak_model = self._fit_binary(transformed, peak_passed)

        positive = additional > 0
        positive_values = additional[positive]
        self.maximum_train_additional = int(positive_values.max()) if positive.any() else 0
        self.threshold_models = {}
        for threshold in range(2, self.maximum_train_additional + 1):
            self.threshold_models[threshold] = self._fit_binary(
                transformed[positive], (positive_values >= threshold).astype(int)
            )
        self.training_summary = {
            "rows": int(len(frame)),
            "weather_dates": int(pd.to_datetime(frame["local_date"]).dt.date.nunique()),
            "stations": int(frame["station"].nunique()),
            "peak_passed_rows": int(peak_passed.sum()),
            "positive_heating_rows": int(positive.sum()),
            "maximum_train_additional_f": self.maximum_train_additional,
            "feature_columns": list(self.contract.categorical_features + self.contract.numeric_features),
        }
        return self

    def predict_proba(self, snapshots: pd.DataFrame) -> np.ndarray:
        if self.preprocessor is None or self.peak_model is None:
            raise ValueError("remaining-heating model has not been fitted")
        frame, high_integer = self._prepare(snapshots)
        transformed = self.preprocessor.transform(frame)
        peak_probability = self._predict_binary(self.peak_model, transformed)
        support = self.contract.support.values
        matrix = np.zeros((len(frame), len(support)), dtype=float)

        if self.maximum_train_additional == 0:
            matrix[np.arange(len(frame)), high_integer - self.contract.support.minimum] = 1.0
            return matrix

        survival = np.ones((len(frame), self.maximum_train_additional + 1), dtype=float)
        for threshold in range(2, self.maximum_train_additional + 1):
            survival[:, threshold - 1] = self._predict_binary(
                self.threshold_models[threshold], transformed
            )
        survival[:, :-1] = np.minimum.accumulate(survival[:, :-1], axis=1)
        survival[:, -1] = 0.0
        conditional_mass = np.clip(survival[:, :-1] - survival[:, 1:], 0.0, 1.0)

        for row_index, high in enumerate(high_integer):
            matrix[row_index, high - self.contract.support.minimum] += peak_probability[row_index]
            positive_probability = 1.0 - peak_probability[row_index]
            for delta, probability in enumerate(conditional_mass[row_index], start=1):
                final_value = min(high + delta, self.contract.support.maximum)
                matrix[row_index, final_value - self.contract.support.minimum] += positive_probability * probability
        return normalize_probability_matrix(matrix)

    def _prepare(self, snapshots: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        required = {"station", "local_date", "max_temp_so_far"}
        missing = sorted(required - set(snapshots.columns))
        if missing:
            raise ValueError(f"snapshots missing required columns: {', '.join(missing)}")
        frame = snapshots.copy()
        day = (
            pd.to_numeric(frame["day_of_year"], errors="coerce")
            if "day_of_year" in frame
            else pd.Series(np.nan, index=frame.index)
        )
        if day.isna().all():
            day = pd.to_datetime(frame["local_date"], errors="raise").dt.dayofyear
        frame["day_of_year_sin"] = np.sin(2.0 * np.pi * day / 365.25)
        frame["day_of_year_cos"] = np.cos(2.0 * np.pi * day / 365.25)
        for column in self.contract.numeric_features:
            if column not in frame:
                frame[column] = np.nan
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        high_integer = np.rint(
            pd.to_numeric(frame["max_temp_so_far"], errors="raise")
        ).astype(int)
        outside = (high_integer < self.contract.support.minimum) | (
            high_integer > self.contract.support.maximum
        )
        if outside.any():
            raise ValueError("integer high-so-far falls outside frozen support")
        return frame, high_integer

    def _build_preprocessor(self) -> ColumnTransformer:
        categorical = Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore")),
        ])
        numeric = Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ])
        return ColumnTransformer([
            ("categorical", categorical, list(self.contract.categorical_features)),
            ("numeric", numeric, list(self.contract.numeric_features)),
        ], remainder="drop")

    def _fit_binary(
        self, transformed: Any, target: Sequence[int]
    ) -> LogisticRegression | float:
        values = np.asarray(target, dtype=int)
        positives = int(values.sum())
        negatives = int(len(values) - positives)
        if min(positives, negatives) < self.contract.min_class_rows:
            return float((positives + 1.0) / (len(values) + 2.0))
        model = LogisticRegression(
            C=self.contract.regularization_c,
            max_iter=2000,
            random_state=self.contract.random_state,
        )
        model.fit(transformed, values)
        return model

    @staticmethod
    def _predict_binary(model: LogisticRegression | float, transformed: Any) -> np.ndarray:
        if isinstance(model, float):
            return np.full(transformed.shape[0], model, dtype=float)
        return np.asarray(model.predict_proba(transformed)[:, 1], dtype=float)
