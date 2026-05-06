from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from weather_trader.models.bucket_classifier import (
    GROUP_COLUMNS,
    LadderConfig,
    build_grouped_metrics,
    build_synthetic_bucket_dataset,
    normalize_grouped_probabilities,
)


REGRESSION_FEATURE_COLUMNS = [
    "station",
    "hour_local",
    "day_of_year",
    "current_temp",
    "max_temp_so_far",
    "temp_change_1h",
    "temp_change_3h",
    "dewpoint",
    "wind_speed",
    "wind_dir_sin",
    "wind_dir_cos",
    "cloud_cover_code",
    "hrrr_current_temp",
    "hrrr_remaining_max",
    "hrrr_current_temp_minus_current_temp",
]
CAT_COLUMNS = ["station"]
WINDOWS = {
    "early_09_10": (9, 10),
    "midday_11_12": (11, 12),
    "late_13_plus": (13, 24),
}


@dataclass(frozen=True)
class HighRegressionArtifacts:
    model: Pipeline
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]
    residuals: pd.DataFrame
    ladder_config: LadderConfig


@dataclass(frozen=True)
class NGBoostArtifacts:
    model: dict[str, object]
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]
    ladder_config: LadderConfig


def train_high_regressor(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    ladder_config: LadderConfig | None = None,
) -> HighRegressionArtifacts:
    config = ladder_config or LadderConfig()
    snapshots = build_snapshot_regression_dataset(dataset)
    snapshots["local_date"] = pd.to_datetime(snapshots["local_date"])
    train = snapshots.loc[snapshots["local_date"].dt.year < validation_year].copy()
    validation = snapshots.loc[snapshots["local_date"].dt.year == validation_year].copy()
    if train.empty or validation.empty:
        raise ValueError("Need non-empty train and validation sets under chronological split")

    feature_columns = _select_active_feature_columns(train)
    cat_columns = [column for column in CAT_COLUMNS if column in feature_columns]
    num_columns = [column for column in feature_columns if column not in cat_columns]
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
                        ("encoder", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
                    ]
                ),
                cat_columns,
            ),
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True))]), num_columns),
        ],
        remainder="drop",
    )
    model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "regressor",
                HistGradientBoostingRegressor(
                    learning_rate=0.05,
                    max_depth=5,
                    max_iter=250,
                    min_samples_leaf=30,
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(train[feature_columns], train["final_high_tmpf"].astype(float))
    train_pred = model.predict(train[feature_columns])
    validation_pred = model.predict(validation[feature_columns])
    train_residuals = train[["station", "hour_local"]].copy()
    train_residuals["window"] = train_residuals["hour_local"].map(entry_window)
    train_residuals["residual"] = train["final_high_tmpf"].astype(float).to_numpy() - train_pred
    metrics = _regression_metrics(validation["final_high_tmpf"].astype(float), validation_pred)
    metrics.update(_window_regression_metrics(validation, validation_pred))
    return HighRegressionArtifacts(
        model=model,
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=feature_columns,
        residuals=train_residuals,
        ladder_config=config,
    )


def save_high_regression_artifacts(artifacts: HighRegressionArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_type": "high_regression_empirical_residual",
            "model": artifacts.model,
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
            "residuals": artifacts.residuals,
            "ladder_config": artifacts.ladder_config.__dict__,
        },
        output_path,
    )


def train_ngboost_high_regressor(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    ladder_config: LadderConfig | None = None,
    n_estimators: int = 350,
    learning_rate: float = 0.03,
) -> NGBoostArtifacts:
    try:
        from ngboost import NGBRegressor
        from ngboost.distns import Normal
        from ngboost.scores import CRPScore
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("NGBoost is not installed. Run this command in the NGBoost conda environment.") from exc

    config = ladder_config or LadderConfig()
    snapshots = build_snapshot_regression_dataset(dataset)
    snapshots["local_date"] = pd.to_datetime(snapshots["local_date"])
    train = snapshots.loc[snapshots["local_date"].dt.year < validation_year].copy()
    validation = snapshots.loc[snapshots["local_date"].dt.year == validation_year].copy()
    if train.empty or validation.empty:
        raise ValueError("Need non-empty train and validation sets under chronological split")

    feature_columns = _select_active_feature_columns(train)
    preprocessor = _build_preprocessor(feature_columns)
    X_train = preprocessor.fit_transform(train[feature_columns])
    X_validation = preprocessor.transform(validation[feature_columns])
    model = NGBRegressor(
        Dist=Normal,
        Score=CRPScore,
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        minibatch_frac=0.75,
        random_state=42,
        verbose=False,
    )
    model.fit(X_train, train["final_high_tmpf"].astype(float).to_numpy())
    validation_dist = model.pred_dist(X_validation)
    validation_mean = _ngboost_dist_mean(validation_dist)
    validation_scale = _ngboost_dist_scale(validation_dist)
    metrics = _regression_metrics(validation["final_high_tmpf"].astype(float), validation_mean)
    metrics["avg_predicted_sigma"] = float(np.mean(validation_scale))
    metrics.update(_window_regression_metrics(validation, validation_mean))
    return NGBoostArtifacts(
        model={"preprocessor": preprocessor, "ngboost": model},
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=feature_columns,
        ladder_config=config,
    )


def save_ngboost_artifacts(artifacts: NGBoostArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_type": "ngboost_normal_crps",
            "model": artifacts.model,
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
            "ladder_config": artifacts.ladder_config.__dict__,
        },
        output_path,
    )


def build_snapshot_regression_dataset(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"]).dt.date
    frame["snapshot_time_local"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    frame = frame.loc[frame["final_high_tmpf"].notna()].copy()
    snapshots = (
        frame.sort_values(["station", "local_date", "snapshot_time_local"])
        .drop_duplicates(["station", "local_date", "snapshot_time_local"])
        .reset_index(drop=True)
    )
    for column in REGRESSION_FEATURE_COLUMNS:
        if column not in snapshots:
            snapshots[column] = np.nan
    snapshots["window"] = snapshots["hour_local"].map(entry_window)
    return snapshots


def build_regression_bucket_validation_predictions(
    dataset: pd.DataFrame,
    model,
    feature_columns: list[str],
    residuals: pd.DataFrame,
    validation_year: int,
    ladder_config: LadderConfig | None = None,
    residual_scope: str = "window",
) -> pd.DataFrame:
    candidates = build_synthetic_bucket_dataset(dataset, ladder_config)
    candidates["local_date"] = pd.to_datetime(candidates["local_date"])
    validation = candidates.loc[candidates["local_date"].dt.year == validation_year].copy()
    if validation.empty:
        raise ValueError(f"No validation rows for {validation_year}")
    for column in feature_columns:
        if column not in validation:
            validation[column] = np.nan
    validation["predicted_high_tmpf"] = model.predict(validation[feature_columns])
    validation["window"] = validation["hour_local"].map(entry_window)
    validation["raw_probability"] = _empirical_bucket_probabilities(validation, residuals, residual_scope)
    validation["normalized_probability"] = normalize_grouped_probabilities(validation, "raw_probability")
    validation["error"] = validation["normalized_probability"] - validation["target"]
    validation["abs_error"] = validation["error"].abs()
    validation["squared_error"] = validation["error"] ** 2
    return validation


def build_ngboost_bucket_validation_predictions(
    dataset: pd.DataFrame,
    model: dict[str, object],
    feature_columns: list[str],
    validation_year: int,
    ladder_config: LadderConfig | None = None,
) -> pd.DataFrame:
    candidates = build_synthetic_bucket_dataset(dataset, ladder_config)
    candidates["local_date"] = pd.to_datetime(candidates["local_date"])
    validation = candidates.loc[candidates["local_date"].dt.year == validation_year].copy()
    if validation.empty:
        raise ValueError(f"No validation rows for {validation_year}")
    for column in feature_columns:
        if column not in validation:
            validation[column] = np.nan
    preprocessor = model["preprocessor"]
    ngboost = model["ngboost"]
    dist = ngboost.pred_dist(preprocessor.transform(validation[feature_columns]))
    mean = _ngboost_dist_mean(dist)
    scale = _ngboost_dist_scale(dist)
    validation["predicted_high_tmpf"] = mean
    validation["predicted_sigma"] = scale
    validation["window"] = validation["hour_local"].map(entry_window)
    validation["raw_probability"] = _normal_bucket_probabilities(validation, mean, scale)
    validation["normalized_probability"] = normalize_grouped_probabilities(validation, "raw_probability")
    validation["error"] = validation["normalized_probability"] - validation["target"]
    validation["abs_error"] = validation["error"].abs()
    validation["squared_error"] = validation["error"] ** 2
    return validation


def build_regression_report(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window, frame in predictions.groupby("window", observed=True):
        metrics = build_grouped_metrics(frame)
        rows.append({"window": window, **metrics})
    rows.append({"window": "all", **build_grouped_metrics(predictions)})
    return pd.DataFrame(rows)


def entry_window(hour_local: float | int) -> str:
    if pd.isna(hour_local):
        return "unknown"
    hour = int(hour_local)
    if hour < 11:
        return "early_09_10"
    if hour < 13:
        return "midday_11_12"
    return "late_13_plus"


def _empirical_bucket_probabilities(candidates: pd.DataFrame, residuals: pd.DataFrame, residual_scope: str) -> pd.Series:
    residual_frame = residuals.copy()
    if "window" not in residual_frame:
        residual_frame["window"] = residual_frame["hour_local"].map(entry_window)
    all_residuals = residual_frame["residual"].dropna().astype(float).to_numpy()
    by_window = {
        str(window): frame["residual"].dropna().astype(float).to_numpy()
        for window, frame in residual_frame.groupby("window", observed=True)
    }
    probabilities = pd.Series(index=candidates.index, dtype=float)
    for index, row in candidates.iterrows():
        bucket_residuals = by_window.get(str(row["window"]), all_residuals) if residual_scope == "window" else all_residuals
        if len(bucket_residuals) == 0:
            probabilities.loc[index] = np.nan
            continue
        mu = float(row["predicted_high_tmpf"])
        lower = row["bucket_lower"]
        upper = row["bucket_upper"]
        lower_residual = -np.inf if pd.isna(lower) else float(lower) - mu
        upper_residual = np.inf if pd.isna(upper) else float(upper) - mu
        in_bucket = (bucket_residuals >= lower_residual) & (bucket_residuals < upper_residual)
        probabilities.loc[index] = float(in_bucket.mean())
    return probabilities


def _normal_bucket_probabilities(candidates: pd.DataFrame, mean: np.ndarray, scale: np.ndarray) -> pd.Series:
    probabilities = pd.Series(index=candidates.index, dtype=float)
    safe_scale = np.maximum(scale.astype(float), 1e-6)
    for offset, (index, row) in enumerate(candidates.iterrows()):
        lower = row["bucket_lower"]
        upper = row["bucket_upper"]
        lower_cdf = 0.0 if pd.isna(lower) else norm.cdf(float(lower), loc=mean[offset], scale=safe_scale[offset])
        upper_cdf = 1.0 if pd.isna(upper) else norm.cdf(float(upper), loc=mean[offset], scale=safe_scale[offset])
        probabilities.loc[index] = max(float(upper_cdf - lower_cdf), 0.0)
    return probabilities


def _select_active_feature_columns(frame: pd.DataFrame) -> list[str]:
    active: list[str] = []
    for column in REGRESSION_FEATURE_COLUMNS:
        if column in CAT_COLUMNS:
            active.append(column)
        elif column in frame and frame[column].notna().any():
            active.append(column)
    return active


def _build_preprocessor(feature_columns: list[str]) -> ColumnTransformer:
    cat_columns = [column for column in CAT_COLUMNS if column in feature_columns]
    num_columns = [column for column in feature_columns if column not in cat_columns]
    return ColumnTransformer(
        transformers=[
            (
                "cat",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
                        ("encoder", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
                    ]
                ),
                cat_columns,
            ),
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True))]), num_columns),
        ],
        remainder="drop",
    )


def _ngboost_dist_mean(distribution) -> np.ndarray:
    if hasattr(distribution, "loc"):
        return np.asarray(distribution.loc, dtype=float)
    return np.asarray(distribution.mean(), dtype=float)


def _ngboost_dist_scale(distribution) -> np.ndarray:
    if hasattr(distribution, "scale"):
        return np.asarray(distribution.scale, dtype=float)
    params = getattr(distribution, "params", {})
    if "scale" in params:
        return np.asarray(params["scale"], dtype=float)
    if "var" in params:
        return np.sqrt(np.asarray(params["var"], dtype=float))
    raise AttributeError("Could not find scale parameter on NGBoost distribution")


def _regression_metrics(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(root_mean_squared_error(y_true, y_pred)),
    }


def _window_regression_metrics(validation: pd.DataFrame, y_pred: np.ndarray) -> dict[str, float]:
    frame = validation.copy()
    frame["prediction"] = y_pred
    frame["window"] = frame["hour_local"].map(entry_window)
    metrics: dict[str, float] = {}
    for window, window_frame in frame.groupby("window", observed=True):
        window_metrics = _regression_metrics(window_frame["final_high_tmpf"].astype(float), window_frame["prediction"].to_numpy())
        metrics[f"{window}_mae"] = window_metrics["mae"]
        metrics[f"{window}_rmse"] = window_metrics["rmse"]
    return metrics
