from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder


METAR_ENRICHED_FEATURE_COLUMNS = [
    "temp_range_so_far",
    "relative_humidity",
    "wet_bulb_approx",
    "pressure_mslp",
    "pressure_tendency_3h",
    "visibility_miles",
    "precip_1h_in",
    "altimeter_inhg",
    "feels_like",
]

HRRR_RICH_FEATURE_COLUMNS = [
    "hrrr_temp_next_3h_max",
    "hrrr_temp_next_3h_mean",
    "hrrr_temp_trend_next_3h",
    "hrrr_dewpoint_current",
    "hrrr_dewpoint_next_3h_mean",
    "hrrr_dewpoint_remaining_mean",
    "hrrr_rh_current",
    "hrrr_rh_next_3h_mean",
    "hrrr_rh_remaining_mean",
    "hrrr_wind_speed_current",
    "hrrr_wind_speed_next_3h_mean",
    "hrrr_wind_speed_remaining_max",
    "hrrr_gust_remaining_max",
    "hrrr_cloud_cover_current",
    "hrrr_cloud_cover_next_3h_mean",
    "hrrr_cloud_cover_remaining_mean",
    "hrrr_cloud_cover_remaining_max",
    "hrrr_shortwave_next_3h_mean",
    "hrrr_shortwave_remaining_max",
    "hrrr_forecast_hours_count",
]

FEATURE_COLUMNS = [
    "station",
    "hour_local",
    "day_of_year",
    "current_temp",
    "max_temp_so_far",
    "min_temp_so_far",
    "threshold",
    "threshold_minus_current_temp",
    "threshold_minus_max_so_far",
    "threshold_minus_min_so_far",
    *METAR_ENRICHED_FEATURE_COLUMNS,
    "temp_change_1h",
    "temp_change_3h",
    "dewpoint",
    "wind_speed",
    "wind_dir_sin",
    "wind_dir_cos",
    "cloud_cover_code",
    "hrrr_current_temp",
    "hrrr_remaining_max",
    "hrrr_remaining_min",
    "hrrr_remaining_max_minus_threshold",
    "hrrr_remaining_min_minus_threshold",
    "hrrr_current_temp_minus_current_temp",
    "hrrr_temp_next_3h_max_minus_threshold",
    *HRRR_RICH_FEATURE_COLUMNS,
]
CAT_COLUMNS = ["station"]
NUM_COLUMNS = [column for column in FEATURE_COLUMNS if column not in CAT_COLUMNS]


@dataclass
class TrainingArtifacts:
    model: CalibratedClassifierCV
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]
    temperature_metric: str = "HIGH_TEMP"


@dataclass(frozen=True)
class ModelConfig:
    name: str
    learning_rate: float = 0.05
    max_depth: int | None = 6
    max_iter: int = 300
    min_samples_leaf: int = 50
    l2_regularization: float = 0.0
    calibration_method: str = "isotonic"


DEFAULT_MODEL_CONFIG = ModelConfig(name="default")

TUNING_CONFIGS = [
    DEFAULT_MODEL_CONFIG,
    ModelConfig(name="conservative_depth4", max_depth=4, min_samples_leaf=100, l2_regularization=0.05),
    ModelConfig(name="conservative_depth5", max_depth=5, min_samples_leaf=100, l2_regularization=0.05),
    ModelConfig(name="slow_depth4", learning_rate=0.03, max_depth=4, max_iter=500, min_samples_leaf=100, l2_regularization=0.1),
    ModelConfig(name="slow_depth5", learning_rate=0.03, max_depth=5, max_iter=500, min_samples_leaf=75, l2_regularization=0.05),
    ModelConfig(name="shallow_sigmoid", max_depth=4, min_samples_leaf=100, l2_regularization=0.05, calibration_method="sigmoid"),
]


def train_and_calibrate(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    config: ModelConfig = DEFAULT_MODEL_CONFIG,
    temperature_metric: str = "high",
) -> TrainingArtifacts:
    metric = _normalize_temperature_metric(temperature_metric)
    frame = prepare_temperature_metric_dataset(dataset, metric)
    frame["local_date"] = pd.to_datetime(frame["local_date"])
    frame = _ensure_optional_columns(frame)

    train = frame.loc[frame["local_date"].dt.year < validation_year].copy()
    validation = frame.loc[frame["local_date"].dt.year == validation_year].copy()
    if train.empty or validation.empty:
        raise ValueError("Need non-empty train and validation sets under chronological split")

    active_feature_columns = _select_active_feature_columns(train)
    active_cat_columns = [column for column in CAT_COLUMNS if column in active_feature_columns]
    active_num_columns = [column for column in active_feature_columns if column not in active_cat_columns]

    X_train = train[active_feature_columns]
    y_train = train["target"].astype(int)
    X_validation = validation[active_feature_columns]
    y_validation = validation["target"].astype(int)

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent", keep_empty_features=True)), ("encoder", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]), active_cat_columns),
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True))]), active_num_columns),
        ],
        remainder="drop",
    )

    categorical_features = [True] * len(active_cat_columns) + [False] * len(active_num_columns)
    base_model = _build_base_model(
        preprocessor=preprocessor,
        categorical_features=categorical_features,
        config=config,
    )
    calibrated = CalibratedClassifierCV(estimator=base_model, method=config.calibration_method, cv=3)
    calibrated.fit(X_train, y_train)
    probabilities = calibrated.predict_proba(X_validation)[:, 1]
    predicted_labels = (probabilities >= 0.5).astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y_validation, predicted_labels)),
        "brier_score": float(brier_score_loss(y_validation, probabilities)),
        "log_loss": float(log_loss(y_validation, probabilities)),
        "roc_auc": float(roc_auc_score(y_validation, probabilities)),
    }
    return TrainingArtifacts(
        model=calibrated,
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=active_feature_columns,
        temperature_metric=_artifact_metric(metric),
    )


def tune_model_configs(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    configs: list[ModelConfig] | None = None,
    temperature_metric: str = "high",
) -> pd.DataFrame:
    results = []
    for config in configs or TUNING_CONFIGS:
        artifacts = train_and_calibrate(dataset=dataset, validation_year=validation_year, config=config, temperature_metric=temperature_metric)
        results.append(
            {
                "config": config.name,
                "learning_rate": config.learning_rate,
                "max_depth": config.max_depth,
                "max_iter": config.max_iter,
                "min_samples_leaf": config.min_samples_leaf,
                "l2_regularization": config.l2_regularization,
                "calibration_method": config.calibration_method,
                "train_rows": artifacts.train_rows,
                "validation_rows": artifacts.validation_rows,
                **artifacts.metrics,
            }
        )
    return pd.DataFrame(results).sort_values(["brier_score", "log_loss"], ascending=True).reset_index(drop=True)


def save_artifacts(artifacts: TrainingArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": artifacts.model,
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
            "market_family": artifacts.temperature_metric,
            "temperature_metric": artifacts.temperature_metric,
        },
        output_path,
    )


def load_artifacts(path: Path) -> dict[str, object]:
    return joblib.load(path)


def build_validation_predictions(
    dataset: pd.DataFrame,
    model,
    feature_columns: list[str],
    validation_year: int,
    temperature_metric: str = "high",
) -> pd.DataFrame:
    frame = prepare_temperature_metric_dataset(dataset, temperature_metric)
    frame["local_date"] = pd.to_datetime(frame["local_date"])
    frame = _ensure_optional_columns(frame)
    validation = frame.loc[frame["local_date"].dt.year == validation_year].copy()
    if validation.empty:
        raise ValueError(f"No validation rows for {validation_year}")
    probabilities = model.predict_proba(validation[feature_columns])[:, 1]
    keep_columns = [
        "station",
        "local_date",
        "snapshot_time_local",
        "hour_local",
        "current_temp",
        "max_temp_so_far",
        "min_temp_so_far",
        "threshold",
        "threshold_minus_current_temp",
        "threshold_minus_max_so_far",
        "threshold_minus_min_so_far",
        "hrrr_current_temp",
        "hrrr_remaining_max",
        "hrrr_remaining_min",
        "hrrr_remaining_max_minus_threshold",
        "hrrr_remaining_min_minus_threshold",
        "target",
    ]
    for column in keep_columns:
        if column not in validation.columns:
            validation[column] = np.nan
    predictions = validation[keep_columns].copy()
    predictions["fair_yes"] = probabilities
    predictions["error"] = predictions["fair_yes"] - predictions["target"]
    predictions["abs_error"] = predictions["error"].abs()
    predictions["squared_error"] = predictions["error"] ** 2
    return predictions


def build_bucket_reports(predictions: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frame = predictions.copy()
    frame["probability_bucket"] = pd.cut(
        frame["fair_yes"],
        bins=[0.0, 0.1, 0.25, 0.4, 0.6, 0.75, 0.9, 1.0],
        labels=["0-10", "10-25", "25-40", "40-60", "60-75", "75-90", "90-100"],
        include_lowest=True,
    )
    frame["threshold_bucket"] = pd.cut(
        frame["threshold"],
        bins=[-np.inf, 50, 60, 70, 80, 90, np.inf],
        labels=["<=50F", "50-60F", "60-70F", "70-80F", "80-90F", ">90F"],
    )
    frame["max_so_far_gap_bucket"] = pd.cut(
        frame["threshold_minus_max_so_far"],
        bins=[-np.inf, -5, -2, 0, 2, 5, np.inf],
        labels=["<=-5F", "-5--2F", "-2-0F", "0-2F", "2-5F", ">5F"],
    )
    frame["hrrr_gap_bucket"] = pd.cut(
        frame["hrrr_remaining_max_minus_threshold"],
        bins=[-np.inf, -5, -2, 0, 2, 5, np.inf],
        labels=["<=-5F", "-5--2F", "-2-0F", "0-2F", "2-5F", ">5F"],
    )
    return {
        "probability": _summarize_bucket(frame, "probability_bucket"),
        "threshold_temp": _summarize_bucket(frame, "threshold_bucket"),
        "max_so_far_gap": _summarize_bucket(frame, "max_so_far_gap_bucket"),
        "hrrr_gap": _summarize_bucket(frame.dropna(subset=["hrrr_gap_bucket"]), "hrrr_gap_bucket"),
    }


def build_reliability_report(predictions: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    frame = predictions.copy()
    frame["fair_yes"] = frame["fair_yes"].astype(float).clip(0.0, 1.0)
    frame["probability_bin"] = pd.cut(
        frame["fair_yes"],
        bins=np.linspace(0.0, 1.0, bins + 1),
        include_lowest=True,
    )
    rows = []
    for bucket, bucket_frame in frame.groupby("probability_bin", observed=True):
        if bucket_frame.empty:
            continue
        avg_probability = float(bucket_frame["fair_yes"].mean())
        event_rate = float(bucket_frame["target"].astype(int).mean())
        rows.append(
            {
                "bucket": str(bucket),
                "rows": int(len(bucket_frame)),
                "avg_probability": avg_probability,
                "event_rate": event_rate,
                "calibration_error": avg_probability - event_rate,
                "abs_calibration_error": abs(avg_probability - event_rate),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["bucket", "rows", "avg_probability", "event_rate", "calibration_error", "abs_calibration_error"],
    )


def build_station_report(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for station, station_frame in predictions.groupby("station", observed=True):
        y_true = station_frame["target"].astype(int)
        y_prob = station_frame["fair_yes"].astype(float).clip(1e-6, 1 - 1e-6)
        rows.append(
            {
                "station": str(station),
                "rows": int(len(station_frame)),
                "event_rate": float(y_true.mean()),
                "avg_probability": float(y_prob.mean()),
                "brier_score": float(brier_score_loss(y_true, y_prob)),
                "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
                "roc_auc": float(roc_auc_score(y_true, y_prob)) if y_true.nunique() > 1 else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("brier_score").reset_index(drop=True)


def _ensure_optional_columns(frame: pd.DataFrame) -> pd.DataFrame:
    for column in FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame


def prepare_temperature_metric_dataset(dataset: pd.DataFrame, temperature_metric: str = "high") -> pd.DataFrame:
    metric = _normalize_temperature_metric(temperature_metric)
    frame = dataset.copy()
    if metric == "high":
        if "target" not in frame and {"final_high_tmpf", "threshold"} <= set(frame.columns):
            frame["target"] = (frame["final_high_tmpf"].astype(float) >= frame["threshold"].astype(float)).astype(int)
        return frame

    if "final_low_tmpf" not in frame:
        raise ValueError("--temperature-metric low requires final_low_tmpf")
    if "min_temp_so_far" not in frame:
        if "low_so_far" in frame:
            frame["min_temp_so_far"] = frame["low_so_far"]
        else:
            raise ValueError("--temperature-metric low requires min_temp_so_far or low_so_far")
    frame["target"] = (frame["final_low_tmpf"].astype(float) <= frame["threshold"].astype(float)).astype(int)
    frame["threshold_minus_current_temp"] = frame["threshold"].astype(float) - frame["current_temp"].astype(float)
    frame["threshold_minus_min_so_far"] = frame["threshold"].astype(float) - frame["min_temp_so_far"].astype(float)
    if "threshold_minus_max_so_far" not in frame and "max_temp_so_far" in frame:
        frame["threshold_minus_max_so_far"] = frame["threshold"].astype(float) - frame["max_temp_so_far"].astype(float)
    if "hrrr_remaining_min" in frame:
        frame["hrrr_remaining_min_minus_threshold"] = frame["hrrr_remaining_min"].astype(float) - frame["threshold"].astype(float)
    return frame


def _normalize_temperature_metric(value: str) -> str:
    text = str(value).lower()
    if text not in {"high", "low"}:
        raise ValueError("temperature_metric must be 'high' or 'low'")
    return text


def _artifact_metric(metric: str) -> str:
    return "LOW_TEMP" if metric == "low" else "HIGH_TEMP"


def _select_active_feature_columns(frame: pd.DataFrame) -> list[str]:
    active = []
    for column in FEATURE_COLUMNS:
        if column not in frame.columns:
            continue
        if column in CAT_COLUMNS:
            active.append(column)
            continue
        if frame[column].notna().any():
            active.append(column)
    return active


def _build_base_model(
    preprocessor: ColumnTransformer,
    categorical_features: list[bool],
    config: ModelConfig,
) -> Pipeline:
    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    learning_rate=config.learning_rate,
                    max_depth=config.max_depth,
                    max_iter=config.max_iter,
                    min_samples_leaf=config.min_samples_leaf,
                    l2_regularization=config.l2_regularization,
                    random_state=42,
                    categorical_features=categorical_features,
                ),
            ),
        ]
    )


def _summarize_bucket(frame: pd.DataFrame, bucket_column: str) -> pd.DataFrame:
    rows = []
    for bucket, bucket_frame in frame.groupby(bucket_column, observed=True):
        if bucket_frame.empty:
            continue
        y_true = bucket_frame["target"].astype(int)
        y_prob = bucket_frame["fair_yes"].astype(float).clip(1e-6, 1 - 1e-6)
        rows.append(
            {
                "bucket": str(bucket),
                "rows": int(len(bucket_frame)),
                "avg_probability": float(y_prob.mean()),
                "event_rate": float(y_true.mean()),
                "brier_score": float(brier_score_loss(y_true, y_prob)),
                "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
                "avg_abs_error": float((y_prob - y_true).abs().mean()),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["bucket", "rows", "avg_probability", "event_rate", "brier_score", "log_loss", "avg_abs_error"],
    )
