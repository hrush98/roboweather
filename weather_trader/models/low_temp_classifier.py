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


LOW_FEATURE_COLUMNS = [
    "station",
    "hour_local",
    "day_of_year",
    "current_temp",
    "min_temp_so_far",
    "threshold",
    "threshold_minus_current_temp",
    "threshold_minus_min_so_far",
    "current_temp_minus_threshold",
    "min_so_far_minus_threshold",
    "temp_change_1h",
    "temp_change_3h",
    "dewpoint",
    "wind_speed",
    "wind_dir_sin",
    "wind_dir_cos",
    "cloud_cover_code",
    "hrrr_current_temp",
    "hrrr_remaining_min",
    "hrrr_remaining_min_minus_threshold",
    "hrrr_current_temp_minus_current_temp",
]
LOW_CAT_COLUMNS = ["station"]


@dataclass
class LowTrainingArtifacts:
    model: CalibratedClassifierCV
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]


@dataclass(frozen=True)
class LowModelConfig:
    name: str = "default"
    learning_rate: float = 0.05
    max_depth: int | None = 6
    max_iter: int = 300
    min_samples_leaf: int = 50
    l2_regularization: float = 0.0
    calibration_method: str = "isotonic"


def train_low_temp_classifier(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    config: LowModelConfig = LowModelConfig(),
) -> LowTrainingArtifacts:
    frame = _prepare_low_frame(dataset)
    train = frame.loc[frame["local_date"].dt.year < validation_year].copy()
    validation = frame.loc[frame["local_date"].dt.year == validation_year].copy()
    if train.empty or validation.empty:
        raise ValueError("Need non-empty train and validation sets under chronological split")
    for name, split in [("train", train), ("validation", validation)]:
        if split["target"].astype(int).nunique() < 2:
            raise ValueError(f"{name} split has only one target class")

    active_feature_columns = _select_active_feature_columns(train)
    active_cat_columns = [column for column in LOW_CAT_COLUMNS if column in active_feature_columns]
    active_num_columns = [column for column in active_feature_columns if column not in active_cat_columns]

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
                active_cat_columns,
            ),
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True))]), active_num_columns),
        ],
        remainder="drop",
    )
    categorical_features = [True] * len(active_cat_columns) + [False] * len(active_num_columns)
    base_model = Pipeline(
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
    calibrated = CalibratedClassifierCV(estimator=base_model, method=config.calibration_method, cv=3)
    calibrated.fit(train[active_feature_columns], train["target"].astype(int))
    probabilities = calibrated.predict_proba(validation[active_feature_columns])[:, 1]
    y_validation = validation["target"].astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y_validation, (probabilities >= 0.5).astype(int))),
        "brier_score": float(brier_score_loss(y_validation, probabilities)),
        "log_loss": float(log_loss(y_validation, probabilities)),
        "roc_auc": float(roc_auc_score(y_validation, probabilities)),
    }
    return LowTrainingArtifacts(
        model=calibrated,
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=active_feature_columns,
    )


def save_low_artifacts(artifacts: LowTrainingArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": artifacts.model,
            "model_type": "low_threshold",
            "temperature_metric": "LOW_TEMP",
            "target_definition": "final_low_tmpf <= threshold",
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
        },
        output_path,
    )


def build_low_validation_predictions(
    dataset: pd.DataFrame,
    model,
    feature_columns: list[str],
    validation_year: int = 2025,
) -> pd.DataFrame:
    frame = _prepare_low_frame(dataset)
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
        "min_temp_so_far",
        "threshold",
        "threshold_minus_current_temp",
        "threshold_minus_min_so_far",
        "hrrr_current_temp",
        "hrrr_remaining_min",
        "hrrr_remaining_min_minus_threshold",
        "final_low_tmpf",
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


def build_low_bucket_reports(predictions: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frame = predictions.copy()
    frame["probability_bucket"] = pd.cut(
        frame["fair_yes"],
        bins=[0.0, 0.1, 0.25, 0.4, 0.6, 0.75, 0.9, 1.0],
        labels=["0-10", "10-25", "25-40", "40-60", "60-75", "75-90", "90-100"],
        include_lowest=True,
    )
    frame["threshold_bucket"] = pd.cut(
        frame["threshold"],
        bins=[-np.inf, 20, 30, 40, 50, 60, 70, np.inf],
        labels=["<=20F", "20-30F", "30-40F", "40-50F", "50-60F", "60-70F", ">70F"],
    )
    frame["min_so_far_gap_bucket"] = pd.cut(
        frame["threshold_minus_min_so_far"],
        bins=[-np.inf, -5, -2, 0, 2, 5, np.inf],
        labels=["<=-5F", "-5--2F", "-2-0F", "0-2F", "2-5F", ">5F"],
    )
    reports = {
        "probability": _summarize_bucket(frame, "probability_bucket"),
        "threshold_temp": _summarize_bucket(frame, "threshold_bucket"),
        "min_so_far_gap": _summarize_bucket(frame, "min_so_far_gap_bucket"),
    }
    if "hrrr_remaining_min_minus_threshold" in frame:
        frame["hrrr_min_gap_bucket"] = pd.cut(
            frame["hrrr_remaining_min_minus_threshold"],
            bins=[-np.inf, -5, -2, 0, 2, 5, np.inf],
            labels=["<=-5F", "-5--2F", "-2-0F", "0-2F", "2-5F", ">5F"],
        )
        reports["hrrr_min_gap"] = _summarize_bucket(frame.dropna(subset=["hrrr_min_gap_bucket"]), "hrrr_min_gap_bucket")
    return reports


def build_low_reliability_report(predictions: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    frame = predictions.copy()
    frame["fair_yes"] = frame["fair_yes"].astype(float).clip(0.0, 1.0)
    frame["probability_bin"] = pd.cut(frame["fair_yes"], bins=np.linspace(0.0, 1.0, bins + 1), include_lowest=True)
    return _summarize_bucket(frame, "probability_bin").rename(columns={"avg_probability": "avg_fair_yes"})


def build_low_station_report(predictions: pd.DataFrame) -> pd.DataFrame:
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


def _prepare_low_frame(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    for column in LOW_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    required = {"station", "local_date", "snapshot_time_local", "threshold", "target", "final_low_tmpf"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required low-temperature columns: {', '.join(missing)}")
    frame["local_date"] = pd.to_datetime(frame["local_date"])
    target = pd.to_numeric(frame["target"], errors="coerce")
    if target.isna().any() or not set(target.unique()) <= {0, 1}:
        raise ValueError("Low-temperature target must be binary and non-null")
    expected = pd.to_numeric(frame["final_low_tmpf"], errors="coerce") <= pd.to_numeric(frame["threshold"], errors="coerce")
    mismatches = int((target.astype(bool) != expected).sum())
    if mismatches:
        raise ValueError(f"{mismatches} rows disagree with final_low_tmpf <= threshold")
    return frame


def _select_active_feature_columns(frame: pd.DataFrame) -> list[str]:
    active = []
    for column in LOW_FEATURE_COLUMNS:
        if column not in frame.columns:
            continue
        if column in LOW_CAT_COLUMNS:
            active.append(column)
            continue
        if frame[column].notna().any():
            active.append(column)
    return active


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
