from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder


GROUP_COLUMNS = ["station", "local_date", "snapshot_time_local", "synthetic_ladder_id"]
CAT_COLUMNS = ["station"]
BASE_FEATURE_COLUMNS = [
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
    "bucket_lower",
    "bucket_upper",
    "bucket_span",
    "lower_minus_current_temp",
    "upper_minus_current_temp",
    "lower_minus_max_so_far",
    "upper_minus_max_so_far",
    "is_left_tail",
    "is_right_tail",
]
HRRR_FEATURE_COLUMNS = [
    "hrrr_current_temp",
    "hrrr_remaining_max",
    "hrrr_current_temp_minus_current_temp",
    "hrrr_lower_minus_current_temp",
    "hrrr_upper_minus_current_temp",
    "hrrr_remaining_max_minus_lower",
    "hrrr_remaining_max_minus_upper",
]
FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + HRRR_FEATURE_COLUMNS


@dataclass(frozen=True)
class LadderConfig:
    bounded_buckets_each_side: int = 3
    tail_gap_f: int = 0


@dataclass(frozen=True)
class BucketTrainingArtifacts:
    model: CalibratedClassifierCV
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]
    feature_columns: list[str]
    ladder_config: LadderConfig


def build_synthetic_bucket_dataset(
    same_day_dataset: pd.DataFrame,
    ladder_config: LadderConfig | None = None,
) -> pd.DataFrame:
    config = ladder_config or LadderConfig()
    frame = same_day_dataset.copy()
    frame["local_date"] = pd.to_datetime(frame["local_date"]).dt.date
    frame["snapshot_time_local"] = pd.to_datetime(frame["snapshot_time_local"], utc=True)
    frame = frame.loc[frame["final_high_tmpf"].notna()].copy()

    snapshots = (
        frame.sort_values(["station", "local_date", "snapshot_time_local"])
        .drop_duplicates(["station", "local_date", "snapshot_time_local"])
        .reset_index(drop=True)
    )

    rows: list[dict[str, object]] = []
    for index, item in enumerate(snapshots.itertuples(index=False)):
        final_high = float(item.final_high_tmpf)
        center = int(np.floor(final_high))
        first_lower = center - config.bounded_buckets_each_side
        last_lower = center + config.bounded_buckets_each_side
        ladder_id = f"{item.station}_{item.local_date}_{index}"
        specs: list[tuple[float | None, float | None]] = [(None, float(first_lower))]
        specs.extend((float(lower), float(lower + 1)) for lower in range(first_lower, last_lower + 1))
        specs.append((float(last_lower + 1), None))
        winner_index = _winning_bucket_index(final_high, specs)

        for bucket_index, (lower, upper) in enumerate(specs):
            rows.append(_candidate_row(item, ladder_id, bucket_index, lower, upper, bucket_index == winner_index))
    return pd.DataFrame(rows)


def train_bucket_classifier(
    dataset: pd.DataFrame,
    validation_year: int = 2025,
    ladder_config: LadderConfig | None = None,
) -> BucketTrainingArtifacts:
    config = ladder_config or LadderConfig()
    candidates = build_synthetic_bucket_dataset(dataset, config)
    candidates["local_date"] = pd.to_datetime(candidates["local_date"])
    train = candidates.loc[candidates["local_date"].dt.year < validation_year].copy()
    validation = candidates.loc[candidates["local_date"].dt.year == validation_year].copy()
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
                    min_samples_leaf=20,
                    random_state=42,
                    categorical_features=categorical_features,
                ),
            ),
        ]
    )
    calibrated = CalibratedClassifierCV(estimator=base_model, method="sigmoid", cv=_calibration_folds(train["target"]))
    calibrated.fit(train[feature_columns], train["target"].astype(int))
    predictions = build_bucket_validation_predictions(
        dataset=dataset,
        model=calibrated,
        feature_columns=feature_columns,
        validation_year=validation_year,
        ladder_config=config,
    )
    metrics = build_grouped_metrics(predictions)
    return BucketTrainingArtifacts(
        model=calibrated,
        train_rows=len(train),
        validation_rows=len(validation),
        metrics=metrics,
        feature_columns=feature_columns,
        ladder_config=config,
    )


def save_bucket_artifacts(artifacts: BucketTrainingArtifacts, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model_type": "dynamic_bucket",
            "model": artifacts.model,
            "train_rows": artifacts.train_rows,
            "validation_rows": artifacts.validation_rows,
            "metrics": artifacts.metrics,
            "feature_columns": artifacts.feature_columns,
            "ladder_config": asdict(artifacts.ladder_config),
        },
        output_path,
    )


def build_bucket_validation_predictions(
    dataset: pd.DataFrame,
    model,
    feature_columns: list[str],
    validation_year: int,
    ladder_config: LadderConfig | None = None,
) -> pd.DataFrame:
    candidates = build_synthetic_bucket_dataset(dataset, ladder_config)
    candidates["local_date"] = pd.to_datetime(candidates["local_date"])
    validation = candidates.loc[candidates["local_date"].dt.year == validation_year].copy()
    if validation.empty:
        raise ValueError(f"No validation rows for {validation_year}")
    validation["raw_probability"] = model.predict_proba(validation[feature_columns])[:, 1]
    validation["normalized_probability"] = normalize_grouped_probabilities(validation, "raw_probability")
    validation["error"] = validation["normalized_probability"] - validation["target"]
    validation["abs_error"] = validation["error"].abs()
    validation["squared_error"] = validation["error"] ** 2
    return validation


def build_threshold_bucket_validation_predictions(
    dataset: pd.DataFrame,
    threshold_model,
    threshold_feature_columns: list[str],
    validation_year: int,
    ladder_config: LadderConfig | None = None,
) -> pd.DataFrame:
    config = ladder_config or LadderConfig()
    candidates = build_synthetic_bucket_dataset(dataset, ladder_config)
    candidates["local_date"] = pd.to_datetime(candidates["local_date"])
    validation = candidates.loc[candidates["local_date"].dt.year == validation_year].copy()
    if validation.empty:
        raise ValueError(f"No validation rows for {validation_year}")

    lower_thresholds = pd.to_numeric(validation["bucket_lower"], errors="coerce")
    upper_thresholds = pd.to_numeric(validation["bucket_upper"], errors="coerce")
    lower_survival = _predict_threshold_survival(
        candidates=validation,
        thresholds=lower_thresholds,
        threshold_model=threshold_model,
        feature_columns=threshold_feature_columns,
        default_probability=1.0,
    )
    upper_survival = _predict_threshold_survival(
        candidates=validation,
        thresholds=upper_thresholds,
        threshold_model=threshold_model,
        feature_columns=threshold_feature_columns,
        default_probability=0.0,
    )
    validation["_lower_threshold"] = lower_thresholds
    validation["_upper_threshold"] = upper_thresholds
    validation["_lower_survival"] = lower_survival
    validation["_upper_survival"] = upper_survival
    validation["raw_probability"] = _derive_monotonic_bucket_probabilities(validation)
    validation["normalized_probability"] = normalize_grouped_probabilities(validation, "raw_probability")
    validation["error"] = validation["normalized_probability"] - validation["target"]
    validation["abs_error"] = validation["error"].abs()
    validation["squared_error"] = validation["error"] ** 2
    return validation.drop(columns=["_lower_threshold", "_upper_threshold", "_lower_survival", "_upper_survival"])


def normalize_grouped_probabilities(frame: pd.DataFrame, score_column: str) -> pd.Series:
    scores = pd.to_numeric(frame[score_column], errors="coerce").clip(lower=0.0)
    normalized = pd.Series(index=frame.index, dtype=float)
    for _, group in frame.assign(_score=scores).groupby(GROUP_COLUMNS, observed=True):
        total = float(group["_score"].sum())
        if np.isfinite(total) and total > 0:
            normalized.loc[group.index] = group["_score"] / total
        else:
            normalized.loc[group.index] = 1.0 / len(group)
    return normalized


def build_grouped_metrics(predictions: pd.DataFrame) -> dict[str, float]:
    winners = predictions.loc[predictions["target"].astype(int) == 1].copy()
    if winners.empty:
        raise ValueError("No winning buckets in validation predictions")
    top = predictions.sort_values("normalized_probability", ascending=False).groupby(GROUP_COLUMNS, as_index=False).head(1)
    group_count = int(predictions.groupby(GROUP_COLUMNS, observed=True).ngroups)
    uniform_losses = []
    uniform_briers = []
    max_so_far_hits = []
    for _, group in predictions.groupby(GROUP_COLUMNS, observed=True):
        ladder_size = len(group)
        target = group["target"].astype(float).to_numpy()
        uniform = np.full(ladder_size, 1.0 / ladder_size)
        uniform_losses.append(float(-np.log(1.0 / ladder_size)))
        uniform_briers.append(float(np.mean((uniform - target) ** 2)))
        max_so_far_hits.append(float(group.loc[group["contains_max_so_far"], "target"].astype(int).max()) if group["contains_max_so_far"].any() else 0.0)
    y_true = predictions["target"].astype(float).to_numpy()
    y_prob = predictions["normalized_probability"].astype(float).clip(1e-12, 1.0).to_numpy()
    return {
        "groups": group_count,
        "grouped_log_loss": float(-np.log(winners["normalized_probability"].astype(float).clip(1e-12, 1.0)).mean()),
        "grouped_brier_score": float(np.mean((y_prob - y_true) ** 2)),
        "top_bucket_accuracy": float(top["target"].astype(int).mean()),
        "uniform_grouped_log_loss": float(np.mean(uniform_losses)),
        "uniform_grouped_brier_score": float(np.mean(uniform_briers)),
        "max_so_far_bucket_accuracy": float(np.mean(max_so_far_hits)),
    }


def build_ladder_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    columns = GROUP_COLUMNS + [
        "bucket_index",
        "bucket_label",
        "target",
        "raw_probability",
        "normalized_probability",
        "final_high_tmpf",
        "contains_max_so_far",
    ]
    return predictions[columns].sort_values(GROUP_COLUMNS + ["bucket_index"]).reset_index(drop=True)


def build_bucket_calibration_report(predictions: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    frame = predictions.copy()
    frame["probability_bin"] = pd.cut(
        frame["normalized_probability"].astype(float).clip(0.0, 1.0),
        bins=np.linspace(0.0, 1.0, bins + 1),
        include_lowest=True,
    )
    rows = []
    for bucket, bucket_frame in frame.groupby("probability_bin", observed=True):
        rows.append(
            {
                "bucket": str(bucket),
                "rows": int(len(bucket_frame)),
                "avg_probability": float(bucket_frame["normalized_probability"].mean()),
                "event_rate": float(bucket_frame["target"].astype(int).mean()),
                "abs_calibration_error": float(abs(bucket_frame["normalized_probability"].mean() - bucket_frame["target"].astype(int).mean())),
            }
        )
    return pd.DataFrame(rows)


def build_bucket_heuristic_report(predictions: pd.DataFrame) -> pd.DataFrame:
    metrics = build_grouped_metrics(predictions)
    return pd.DataFrame(
        [
            {
                "heuristic": "model",
                "grouped_log_loss": metrics["grouped_log_loss"],
                "grouped_brier_score": metrics["grouped_brier_score"],
                "top_bucket_accuracy": metrics["top_bucket_accuracy"],
            },
            {
                "heuristic": "uniform_over_ladder",
                "grouped_log_loss": metrics["uniform_grouped_log_loss"],
                "grouped_brier_score": metrics["uniform_grouped_brier_score"],
                "top_bucket_accuracy": np.nan,
            },
            {
                "heuristic": "bucket_containing_max_so_far",
                "grouped_log_loss": np.nan,
                "grouped_brier_score": np.nan,
                "top_bucket_accuracy": metrics["max_so_far_bucket_accuracy"],
            },
        ]
    )


def build_model_comparison_report(
    dynamic_predictions: pd.DataFrame,
    threshold_bucket_predictions: pd.DataFrame,
) -> pd.DataFrame:
    dynamic_metrics = build_grouped_metrics(dynamic_predictions)
    threshold_metrics = build_grouped_metrics(threshold_bucket_predictions)
    return pd.DataFrame(
        [
            {
                "model": "dynamic_bucket_classifier",
                "grouped_log_loss": dynamic_metrics["grouped_log_loss"],
                "grouped_brier_score": dynamic_metrics["grouped_brier_score"],
                "top_bucket_accuracy": dynamic_metrics["top_bucket_accuracy"],
            },
            {
                "model": "cumulative_threshold_derived_bucket",
                "grouped_log_loss": threshold_metrics["grouped_log_loss"],
                "grouped_brier_score": threshold_metrics["grouped_brier_score"],
                "top_bucket_accuracy": threshold_metrics["top_bucket_accuracy"],
            },
            {
                "model": "uniform_over_ladder",
                "grouped_log_loss": dynamic_metrics["uniform_grouped_log_loss"],
                "grouped_brier_score": dynamic_metrics["uniform_grouped_brier_score"],
                "top_bucket_accuracy": np.nan,
            },
            {
                "model": "bucket_containing_max_so_far",
                "grouped_log_loss": np.nan,
                "grouped_brier_score": np.nan,
                "top_bucket_accuracy": dynamic_metrics["max_so_far_bucket_accuracy"],
            },
        ]
    )


def _candidate_row(item, ladder_id: str, bucket_index: int, lower: float | None, upper: float | None, target: bool) -> dict[str, object]:
    current_temp = float(item.current_temp)
    max_so_far = float(item.max_temp_so_far)
    row = {
        "station": item.station,
        "local_date": item.local_date,
        "snapshot_time_local": item.snapshot_time_local,
        "synthetic_ladder_id": ladder_id,
        "bucket_index": bucket_index,
        "bucket_label": _bucket_label(lower, upper),
        "bucket_lower": lower,
        "bucket_upper": upper,
        "bucket_span": (upper - lower) if lower is not None and upper is not None else np.nan,
        "is_left_tail": int(lower is None),
        "is_right_tail": int(upper is None),
        "hour_local": item.hour_local,
        "day_of_year": item.day_of_year,
        "current_temp": item.current_temp,
        "max_temp_so_far": item.max_temp_so_far,
        "temp_change_1h": getattr(item, "temp_change_1h", np.nan),
        "temp_change_3h": getattr(item, "temp_change_3h", np.nan),
        "dewpoint": getattr(item, "dewpoint", np.nan),
        "wind_speed": getattr(item, "wind_speed", np.nan),
        "wind_dir_sin": getattr(item, "wind_dir_sin", np.nan),
        "wind_dir_cos": getattr(item, "wind_dir_cos", np.nan),
        "cloud_cover_code": getattr(item, "cloud_cover_code", np.nan),
        "lower_minus_current_temp": lower - current_temp if lower is not None else np.nan,
        "upper_minus_current_temp": upper - current_temp if upper is not None else np.nan,
        "lower_minus_max_so_far": lower - max_so_far if lower is not None else np.nan,
        "upper_minus_max_so_far": upper - max_so_far if upper is not None else np.nan,
        "final_high_tmpf": float(item.final_high_tmpf),
        "target": int(target),
        "contains_max_so_far": _contains(max_so_far, lower, upper),
    }
    hrrr_current = getattr(item, "hrrr_current_temp", np.nan)
    hrrr_remaining = getattr(item, "hrrr_remaining_max", np.nan)
    row.update(
        {
            "hrrr_current_temp": hrrr_current,
            "hrrr_remaining_max": hrrr_remaining,
            "hrrr_current_temp_minus_current_temp": hrrr_current - current_temp if pd.notna(hrrr_current) else np.nan,
            "hrrr_lower_minus_current_temp": lower - hrrr_current if lower is not None and pd.notna(hrrr_current) else np.nan,
            "hrrr_upper_minus_current_temp": upper - hrrr_current if upper is not None and pd.notna(hrrr_current) else np.nan,
            "hrrr_remaining_max_minus_lower": hrrr_remaining - lower if lower is not None and pd.notna(hrrr_remaining) else np.nan,
            "hrrr_remaining_max_minus_upper": hrrr_remaining - upper if upper is not None and pd.notna(hrrr_remaining) else np.nan,
        }
    )
    return row


def _predict_threshold_survival(
    candidates: pd.DataFrame,
    thresholds: pd.Series,
    threshold_model,
    feature_columns: list[str],
    default_probability: float,
) -> pd.Series:
    probabilities = pd.Series(default_probability, index=candidates.index, dtype=float)
    active = thresholds.notna()
    if not active.any():
        return probabilities
    examples = candidates.loc[active].copy()
    examples["threshold"] = thresholds.loc[active].astype(float)
    examples["threshold_minus_current_temp"] = examples["threshold"] - examples["current_temp"].astype(float)
    examples["threshold_minus_max_so_far"] = examples["threshold"] - examples["max_temp_so_far"].astype(float)
    if "hrrr_remaining_max" in examples:
        examples["hrrr_remaining_max_minus_threshold"] = examples["hrrr_remaining_max"] - examples["threshold"]
    for column in feature_columns:
        if column not in examples:
            examples[column] = np.nan
    probabilities.loc[active] = threshold_model.predict_proba(examples[feature_columns])[:, 1]
    return probabilities


def _derive_monotonic_bucket_probabilities(candidates: pd.DataFrame) -> pd.Series:
    probabilities = pd.Series(index=candidates.index, dtype=float)
    for _, group in candidates.groupby(GROUP_COLUMNS, observed=True):
        cutpoint_rows = []
        for threshold_column, survival_column in [
            ("_lower_threshold", "_lower_survival"),
            ("_upper_threshold", "_upper_survival"),
        ]:
            values = group[[threshold_column, survival_column]].dropna()
            values.columns = ["threshold", "survival"]
            cutpoint_rows.append(values)
        cutpoints = pd.concat(cutpoint_rows, ignore_index=True).groupby("threshold", as_index=False)["survival"].mean()
        cutpoints = cutpoints.sort_values("threshold")
        if cutpoints.empty:
            probabilities.loc[group.index] = 1.0 / len(group)
            continue
        thresholds = cutpoints["threshold"].astype(float).to_numpy()
        survival = cutpoints["survival"].astype(float).clip(0.0, 1.0).to_numpy()
        if len(cutpoints) > 1:
            survival = IsotonicRegression(increasing=False, y_min=0.0, y_max=1.0, out_of_bounds="clip").fit_transform(thresholds, survival)
        survival_by_threshold = dict(zip(thresholds, survival))
        for index, row in group.iterrows():
            lower_survival = 1.0 if pd.isna(row["_lower_threshold"]) else survival_by_threshold[float(row["_lower_threshold"])]
            upper_survival = 0.0 if pd.isna(row["_upper_threshold"]) else survival_by_threshold[float(row["_upper_threshold"])]
            probabilities.loc[index] = max(float(lower_survival - upper_survival), 0.0)
    return probabilities


def _winning_bucket_index(final_high: float, specs: list[tuple[float | None, float | None]]) -> int:
    for index, (lower, upper) in enumerate(specs):
        if _contains(final_high, lower, upper):
            return index
    raise ValueError(f"No winning bucket for final high {final_high}")


def _contains(value: float, lower: float | None, upper: float | None) -> bool:
    if lower is None:
        return value < float(upper)
    if upper is None:
        return value >= float(lower)
    return float(lower) <= value < float(upper)


def _bucket_label(lower: float | None, upper: float | None) -> str:
    if lower is None:
        return f"<{upper:g}F"
    if upper is None:
        return f">={lower:g}F"
    return f"{lower:g}-{upper:g}F"


def _select_active_feature_columns(frame: pd.DataFrame) -> list[str]:
    active: list[str] = []
    for column in FEATURE_COLUMNS:
        if column in CAT_COLUMNS:
            active.append(column)
        elif column in frame and frame[column].notna().any():
            active.append(column)
    return active


def _calibration_folds(target: pd.Series) -> int:
    counts = target.astype(int).value_counts()
    if len(counts) < 2:
        raise ValueError("Training candidates need both positive and negative targets")
    return int(min(3, counts.min()))
