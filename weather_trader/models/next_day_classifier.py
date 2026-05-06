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


NEXT_DAY_FEATURE_COLUMNS = [
    "station",
    "prediction_hour_local",
    "prediction_day_of_year",
    "target_day_of_year",
    "current_temp",
    "today_high_so_far",
    "threshold",
    "threshold_minus_current_temp",
    "threshold_minus_today_high_so_far",
    "temp_change_1h",
    "temp_change_3h",
    "dewpoint",
    "wind_speed",
    "wind_dir_sin",
    "wind_dir_cos",
    "cloud_cover_code",
    "prior_day_high",
    "prior_3day_high_mean",
    "prior_7day_high_mean",
    "prior_day_high_minus_threshold",
    "prior_7day_high_mean_minus_threshold",
]
NEXT_DAY_CAT_COLUMNS = ["station"]
NEXT_DAY_THRESHOLD_OFFSETS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]


@dataclass(frozen=True)
class NextDayTrainingArtifacts:
    model: CalibratedClassifierCV
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]


def build_next_day_threshold_dataset(
    same_day_dataset: pd.DataFrame,
    threshold_offsets: list[int] | None = None,
) -> pd.DataFrame:
    threshold_offsets = threshold_offsets or NEXT_DAY_THRESHOLD_OFFSETS
    frame = same_day_dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"]).dt.date
    frame["snapshot_time_local"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)

    snapshots = (
        frame.sort_values("snapshot_time_local")
        .drop_duplicates(["station", "local_date", "snapshot_time_local"])
        .sort_values(["station", "local_date", "snapshot_time_local"])
    )
    latest_snapshots = snapshots.groupby(["station", "local_date"], as_index=False).tail(1).copy()

    daily = (
        frame[["station", "city", "timezone", "local_date", "final_high_tmpf"]]
        .drop_duplicates(["station", "local_date"])
        .sort_values(["station", "local_date"])
        .copy()
    )
    daily["target_date"] = daily.groupby("station")["local_date"].shift(-1)
    daily["target_final_high_tmpf"] = daily.groupby("station")["final_high_tmpf"].shift(-1)
    daily["prior_day_high"] = daily.groupby("station")["final_high_tmpf"].shift(1)
    daily["prior_3day_high_mean"] = daily.groupby("station")["final_high_tmpf"].transform(
        lambda series: series.shift(1).rolling(3, min_periods=1).mean()
    )
    daily["prior_7day_high_mean"] = daily.groupby("station")["final_high_tmpf"].transform(
        lambda series: series.shift(1).rolling(7, min_periods=1).mean()
    )
    expected_target_date = pd.to_datetime(daily["local_date"]) + pd.Timedelta(days=1)
    daily = daily.loc[pd.to_datetime(daily["target_date"]) == expected_target_date].copy()

    base = latest_snapshots.merge(
        daily[
            [
                "station",
                "local_date",
                "target_date",
                "target_final_high_tmpf",
                "prior_day_high",
                "prior_3day_high_mean",
                "prior_7day_high_mean",
            ]
        ],
        on=["station", "local_date"],
        how="inner",
    )
    base = base.loc[base["target_final_high_tmpf"].notna()].copy()

    rows: list[dict[str, object]] = []
    for item in base.itertuples(index=False):
        target_date = pd.Timestamp(item.target_date)
        for offset in threshold_offsets:
            threshold = round(float(item.target_final_high_tmpf) + offset)
            rows.append(
                {
                    "station": item.station,
                    "city": item.city,
                    "timezone": item.timezone,
                    "local_date": target_date.date(),
                    "prediction_date": item.local_date,
                    "prediction_snapshot_time_local": item.snapshot_time_local,
                    "prediction_hour_local": int(item.hour_local),
                    "prediction_day_of_year": int(item.day_of_year),
                    "target_day_of_year": int(target_date.dayofyear),
                    "current_temp": float(item.current_temp),
                    "today_high_so_far": float(item.max_temp_so_far),
                    "threshold": float(threshold),
                    "threshold_minus_current_temp": float(threshold - item.current_temp),
                    "threshold_minus_today_high_so_far": float(threshold - item.max_temp_so_far),
                    "temp_change_1h": item.temp_change_1h,
                    "temp_change_3h": item.temp_change_3h,
                    "dewpoint": item.dewpoint,
                    "wind_speed": item.wind_speed,
                    "wind_dir_sin": item.wind_dir_sin,
                    "wind_dir_cos": item.wind_dir_cos,
                    "cloud_cover_code": item.cloud_cover_code,
                    "prior_day_high": item.prior_day_high,
                    "prior_3day_high_mean": item.prior_3day_high_mean,
                    "prior_7day_high_mean": item.prior_7day_high_mean,
                    "prior_day_high_minus_threshold": item.prior_day_high - threshold,
                    "prior_7day_high_mean_minus_threshold": item.prior_7day_high_mean - threshold,
                    "target_final_high_tmpf": float(item.target_final_high_tmpf),
                    "target": int(float(item.target_final_high_tmpf) >= threshold),
                }
            )
    return pd.DataFrame(rows)


def train_next_day_classifier(dataset: pd.DataFrame, validation_year: int = 2025) -> NextDayTrainingArtifacts:
    frame = dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"])
    frame = _ensure_columns(frame)
    train = frame.loc[frame["local_date"].dt.year < validation_year].copy()
    validation = frame.loc[frame["local_date"].dt.year == validation_year].copy()
    if train.empty or validation.empty:
        raise ValueError("Need non-empty train and validation sets under chronological split")

    active_features = _select_active_feature_columns(train)
    cat_columns = [column for column in NEXT_DAY_CAT_COLUMNS if column in active_features]
    num_columns = [column for column in active_features if column not in cat_columns]

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
    categorical_features = [True] * len(cat_columns) + [False] * len(num_columns)
    base_model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    learning_rate=0.05,
                    max_depth=5,
                    max_iter=250,
                    min_samples_leaf=75,
                    random_state=42,
                    categorical_features=categorical_features,
                ),
            ),
        ]
    )
    calibrated = CalibratedClassifierCV(estimator=base_model, method="isotonic", cv=3)
    calibrated.fit(train[active_features], train["target"].astype(int))
    probabilities = calibrated.predict_proba(validation[active_features])[:, 1]
    labels = (probabilities >= 0.5).astype(int)
    y_validation = validation["target"].astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y_validation, labels)),
        "brier_score": float(brier_score_loss(y_validation, probabilities)),
        "log_loss": float(log_loss(y_validation, probabilities)),
        "roc_auc": float(roc_auc_score(y_validation, probabilities)),
    }
    return NextDayTrainingArtifacts(
        model=calibrated,
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=active_features,
    )


def save_next_day_artifacts(artifacts: NextDayTrainingArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": artifacts.model,
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
            "model_type": "next_day_threshold",
        },
        output_path,
    )


def build_next_day_validation_predictions(
    dataset: pd.DataFrame,
    model,
    feature_columns: list[str],
    validation_year: int,
) -> pd.DataFrame:
    frame = dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"])
    frame = _ensure_columns(frame)
    validation = frame.loc[frame["local_date"].dt.year == validation_year].copy()
    probabilities = model.predict_proba(validation[feature_columns])[:, 1]
    keep_columns = [
        "station",
        "local_date",
        "prediction_date",
        "prediction_snapshot_time_local",
        "current_temp",
        "today_high_so_far",
        "prior_day_high",
        "prior_7day_high_mean",
        "threshold",
        "target_final_high_tmpf",
        "target",
    ]
    predictions = validation[keep_columns].copy()
    predictions["fair_yes"] = probabilities
    predictions["error"] = predictions["fair_yes"] - predictions["target"]
    predictions["abs_error"] = predictions["error"].abs()
    predictions["squared_error"] = predictions["error"] ** 2
    return predictions


def build_next_day_bucket_reports(predictions: pd.DataFrame) -> dict[str, pd.DataFrame]:
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
    frame["tomorrow_vs_prior_7day_bucket"] = pd.cut(
        frame["threshold"] - frame["prior_7day_high_mean"],
        bins=[-np.inf, -8, -4, 0, 4, 8, np.inf],
        labels=["<=-8F", "-8--4F", "-4-0F", "0-4F", "4-8F", ">8F"],
    )
    return {
        "probability": _summarize_bucket(frame, "probability_bucket"),
        "threshold_temp": _summarize_bucket(frame, "threshold_bucket"),
        "tomorrow_vs_prior_7day": _summarize_bucket(frame, "tomorrow_vs_prior_7day_bucket"),
    }


def _ensure_columns(frame: pd.DataFrame) -> pd.DataFrame:
    for column in NEXT_DAY_FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame


def _select_active_feature_columns(frame: pd.DataFrame) -> list[str]:
    active: list[str] = []
    for column in NEXT_DAY_FEATURE_COLUMNS:
        if column in NEXT_DAY_CAT_COLUMNS:
            active.append(column)
        elif column in frame and frame[column].notna().any():
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
    return pd.DataFrame(rows)
