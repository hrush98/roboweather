from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from weather_trader.models.next_day_classifier import NEXT_DAY_FEATURE_COLUMNS
from weather_trader.models.train_classifier import FEATURE_COLUMNS


FUTURE_OUTCOME_COLUMNS = {
    "target",
    "final_high_tmpf",
    "target_final_high_tmpf",
    "resolved_high_tmpf",
    "actual_high_tmpf",
}


@dataclass(frozen=True)
class DiagnosticIssue:
    severity: str
    check: str
    message: str


@dataclass(frozen=True)
class DatasetDiagnostics:
    rows: int
    columns: int
    summary: dict[str, object]
    issues: list[DiagnosticIssue]
    column_report: pd.DataFrame
    split_report: pd.DataFrame
    policy_report: pd.DataFrame

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def issue_frame(self) -> pd.DataFrame:
        return pd.DataFrame([issue.__dict__ for issue in self.issues], columns=["severity", "check", "message"])


def validate_same_day_dataset(dataset: pd.DataFrame, validation_year: int = 2025) -> DatasetDiagnostics:
    frame = dataset.copy()
    feature_columns = [column for column in FEATURE_COLUMNS if column in frame.columns]
    issues: list[DiagnosticIssue] = []
    _check_required_columns(frame, ["station", "local_date", "snapshot_time_local", "threshold", "target"], issues)
    _check_feature_leakage(feature_columns, issues)
    _check_binary_target(frame, issues)
    _check_split_health(frame, validation_year, issues)
    _check_duplicate_feature_conflicts(frame, feature_columns, issues)
    _check_same_day_time_order(frame, issues)
    _check_same_day_threshold_consistency(frame, issues)

    return DatasetDiagnostics(
        rows=len(frame),
        columns=len(frame.columns),
        summary=_build_summary(frame, validation_year),
        issues=issues,
        column_report=_build_column_report(frame, feature_columns),
        split_report=_build_split_report(frame, validation_year),
        policy_report=_build_same_day_policy_report(frame),
    )


def validate_next_day_dataset(dataset: pd.DataFrame, validation_year: int = 2025) -> DatasetDiagnostics:
    frame = dataset.copy()
    feature_columns = [column for column in NEXT_DAY_FEATURE_COLUMNS if column in frame.columns]
    issues: list[DiagnosticIssue] = []
    _check_required_columns(
        frame,
        ["station", "local_date", "prediction_date", "prediction_snapshot_time_local", "threshold", "target"],
        issues,
    )
    _check_feature_leakage(feature_columns, issues)
    _check_binary_target(frame, issues)
    _check_split_health(frame, validation_year, issues)
    _check_duplicate_feature_conflicts(frame, feature_columns, issues)
    _check_next_day_time_order(frame, issues)
    _check_next_day_threshold_consistency(frame, issues)

    return DatasetDiagnostics(
        rows=len(frame),
        columns=len(frame.columns),
        summary=_build_summary(frame, validation_year),
        issues=issues,
        column_report=_build_column_report(frame, feature_columns),
        split_report=_build_split_report(frame, validation_year),
        policy_report=_build_next_day_policy_report(frame),
    )


def _check_required_columns(frame: pd.DataFrame, required: Iterable[str], issues: list[DiagnosticIssue]) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        issues.append(
            DiagnosticIssue(
                severity="error",
                check="required_columns",
                message=f"Missing required columns: {', '.join(missing)}",
            )
        )


def _check_feature_leakage(feature_columns: list[str], issues: list[DiagnosticIssue]) -> None:
    leaked = sorted(set(feature_columns) & FUTURE_OUTCOME_COLUMNS)
    if leaked:
        issues.append(
            DiagnosticIssue(
                severity="error",
                check="feature_leakage",
                message=f"Outcome/future columns are configured as model features: {', '.join(leaked)}",
            )
        )


def _check_binary_target(frame: pd.DataFrame, issues: list[DiagnosticIssue]) -> None:
    if "target" not in frame:
        return
    target = pd.to_numeric(frame["target"], errors="coerce")
    if target.isna().any():
        issues.append(DiagnosticIssue("error", "binary_target", "Target contains null values"))
        return
    values = set(target.unique())
    if not values <= {0, 1}:
        issues.append(DiagnosticIssue("error", "binary_target", f"Target has non-binary values: {sorted(values)}"))


def _check_split_health(frame: pd.DataFrame, validation_year: int, issues: list[DiagnosticIssue]) -> None:
    if "local_date" not in frame or "target" not in frame:
        return
    local_dates = pd.to_datetime(frame["local_date"], errors="coerce")
    train = frame.loc[local_dates.dt.year < validation_year]
    validation = frame.loc[local_dates.dt.year == validation_year]
    if train.empty or validation.empty:
        issues.append(DiagnosticIssue("error", "chronological_split", "Train or validation split is empty"))
        return
    for name, split in [("train", train), ("validation", validation)]:
        classes = split["target"].dropna().astype(int).nunique()
        if classes < 2:
            issues.append(DiagnosticIssue("error", "chronological_split", f"{name} split has only one target class"))
        if len(split) < 100:
            issues.append(DiagnosticIssue("warning", "chronological_split", f"{name} split has only {len(split)} rows"))


def _check_duplicate_feature_conflicts(
    frame: pd.DataFrame,
    feature_columns: list[str],
    issues: list[DiagnosticIssue],
) -> None:
    if "target" not in frame or not feature_columns:
        return
    feature_hash = pd.util.hash_pandas_object(frame[feature_columns], index=False)
    grouped = frame.groupby(feature_hash, observed=True)["target"].nunique()
    conflicts = int((grouped > 1).sum())
    if conflicts:
        issues.append(
            DiagnosticIssue(
                "error",
                "duplicate_feature_conflicts",
                f"{conflicts} identical feature rows map to both target classes",
            )
        )


def _check_same_day_time_order(frame: pd.DataFrame, issues: list[DiagnosticIssue]) -> None:
    if not {"local_date", "snapshot_time_local"} <= set(frame.columns):
        return
    local_dates = pd.to_datetime(frame["local_date"], errors="coerce").dt.date
    snapshot_dates = _date_prefix(frame["snapshot_time_local"])
    bad_rows = int((snapshot_dates != local_dates).sum())
    if bad_rows:
        issues.append(
            DiagnosticIssue(
                "warning",
                "same_day_time_order",
                f"{bad_rows} rows have snapshot local date different from local_date",
            )
        )


def _check_next_day_time_order(frame: pd.DataFrame, issues: list[DiagnosticIssue]) -> None:
    if not {"local_date", "prediction_date", "prediction_snapshot_time_local"} <= set(frame.columns):
        return
    local_dates = pd.to_datetime(frame["local_date"], errors="coerce")
    prediction_dates = pd.to_datetime(frame["prediction_date"], errors="coerce")
    expected = prediction_dates + pd.Timedelta(days=1)
    bad_target_dates = int((local_dates.dt.date != expected.dt.date).sum())
    if bad_target_dates:
        issues.append(
            DiagnosticIssue(
                "error",
                "next_day_time_order",
                f"{bad_target_dates} rows are not prediction_date + 1 calendar day",
            )
        )
    snapshot_dates = _date_prefix(frame["prediction_snapshot_time_local"])
    bad_snapshots = int((snapshot_dates != prediction_dates.dt.date).sum())
    if bad_snapshots:
        issues.append(
            DiagnosticIssue(
                "warning",
                "next_day_time_order",
                f"{bad_snapshots} rows have prediction snapshots outside prediction_date",
            )
        )


def _date_prefix(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str).str[:10], errors="coerce").dt.date


def _check_same_day_threshold_consistency(frame: pd.DataFrame, issues: list[DiagnosticIssue]) -> None:
    if not {"threshold", "target", "final_high_tmpf"} <= set(frame.columns):
        return
    expected = frame["final_high_tmpf"].astype(float) >= frame["threshold"].astype(float)
    mismatches = int((expected.astype(int) != frame["target"].astype(int)).sum())
    if mismatches:
        issues.append(DiagnosticIssue("error", "target_consistency", f"{mismatches} rows disagree with final_high >= threshold"))


def _check_next_day_threshold_consistency(frame: pd.DataFrame, issues: list[DiagnosticIssue]) -> None:
    if not {"threshold", "target", "target_final_high_tmpf"} <= set(frame.columns):
        return
    expected = frame["target_final_high_tmpf"].astype(float) >= frame["threshold"].astype(float)
    mismatches = int((expected.astype(int) != frame["target"].astype(int)).sum())
    if mismatches:
        issues.append(
            DiagnosticIssue(
                "error",
                "target_consistency",
                f"{mismatches} rows disagree with target_final_high >= threshold",
            )
        )


def _build_summary(frame: pd.DataFrame, validation_year: int) -> dict[str, object]:
    summary: dict[str, object] = {"rows": int(len(frame)), "columns": int(len(frame.columns)), "validation_year": validation_year}
    if "station" in frame:
        summary["stations"] = int(frame["station"].nunique())
    if "local_date" in frame:
        dates = pd.to_datetime(frame["local_date"], errors="coerce")
        summary["start_date"] = str(dates.min().date()) if dates.notna().any() else None
        summary["end_date"] = str(dates.max().date()) if dates.notna().any() else None
    if "target" in frame and len(frame):
        summary["event_rate"] = float(frame["target"].astype(int).mean())
    return summary


def _build_column_report(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    rows = []
    for column in feature_columns:
        series = frame[column]
        rows.append(
            {
                "column": column,
                "non_null_rate": float(series.notna().mean()) if len(series) else np.nan,
                "unique_values": int(series.nunique(dropna=True)),
                "is_constant": bool(series.nunique(dropna=True) <= 1),
                "dtype": str(series.dtype),
            }
        )
    return pd.DataFrame(rows)


def _build_split_report(frame: pd.DataFrame, validation_year: int) -> pd.DataFrame:
    if not {"local_date", "target"} <= set(frame.columns):
        return pd.DataFrame(columns=["split", "rows", "start_date", "end_date", "event_rate", "stations"])
    dates = pd.to_datetime(frame["local_date"], errors="coerce")
    splits = {
        "train": frame.loc[dates.dt.year < validation_year],
        "validation": frame.loc[dates.dt.year == validation_year],
        "future_holdout": frame.loc[dates.dt.year > validation_year],
    }
    rows = []
    for name, split in splits.items():
        split_dates = pd.to_datetime(split["local_date"], errors="coerce") if not split.empty else pd.Series(dtype="datetime64[ns]")
        rows.append(
            {
                "split": name,
                "rows": int(len(split)),
                "start_date": str(split_dates.min().date()) if split_dates.notna().any() else None,
                "end_date": str(split_dates.max().date()) if split_dates.notna().any() else None,
                "event_rate": float(split["target"].astype(int).mean()) if not split.empty else np.nan,
                "stations": int(split["station"].nunique()) if "station" in split and not split.empty else 0,
            }
        )
    return pd.DataFrame(rows)


def _build_same_day_policy_report(frame: pd.DataFrame) -> pd.DataFrame:
    policies = {}
    if {"threshold", "max_temp_so_far"} <= set(frame.columns):
        policies["already_hit_threshold"] = (frame["max_temp_so_far"].astype(float) >= frame["threshold"].astype(float)).astype(float)
    if {"threshold", "current_temp"} <= set(frame.columns):
        policies["current_temp_hit_threshold"] = (frame["current_temp"].astype(float) >= frame["threshold"].astype(float)).astype(float)
    if {"threshold", "hrrr_remaining_max"} <= set(frame.columns):
        policies["hrrr_remaining_max_hit_threshold"] = (frame["hrrr_remaining_max"].astype(float) >= frame["threshold"].astype(float)).astype(float)
    return _score_policies(frame, policies)


def _build_next_day_policy_report(frame: pd.DataFrame) -> pd.DataFrame:
    policies = {}
    if {"threshold", "prior_day_high"} <= set(frame.columns):
        policies["prior_day_high_hit_threshold"] = (frame["prior_day_high"].astype(float) >= frame["threshold"].astype(float)).astype(float)
    if {"threshold", "prior_7day_high_mean"} <= set(frame.columns):
        policies["prior_7day_mean_hit_threshold"] = (frame["prior_7day_high_mean"].astype(float) >= frame["threshold"].astype(float)).astype(float)
    if {"target"} <= set(frame.columns):
        event_rate = float(frame["target"].astype(int).mean()) if len(frame) else np.nan
        policies["base_rate"] = pd.Series(event_rate, index=frame.index)
    return _score_policies(frame, policies)


def _score_policies(frame: pd.DataFrame, policies: dict[str, pd.Series]) -> pd.DataFrame:
    if "target" not in frame:
        return pd.DataFrame(columns=["policy", "rows", "coverage", "avg_probability", "brier_score", "log_loss", "roc_auc"])
    y_true = frame["target"].astype(int)
    rows = []
    for name, probabilities in policies.items():
        probabilities = pd.Series(probabilities, index=frame.index).astype(float)
        valid = probabilities.notna() & y_true.notna()
        if not valid.any():
            continue
        y_prob = probabilities.loc[valid].clip(1e-6, 1 - 1e-6)
        y_valid = y_true.loc[valid]
        rows.append(
            {
                "policy": name,
                "rows": int(valid.sum()),
                "coverage": float(valid.mean()),
                "avg_probability": float(y_prob.mean()),
                "brier_score": float(brier_score_loss(y_valid, y_prob)),
                "log_loss": float(log_loss(y_valid, y_prob, labels=[0, 1])),
                "roc_auc": float(roc_auc_score(y_valid, y_prob)) if y_valid.nunique() > 1 and y_prob.nunique() > 1 else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["brier_score", "log_loss"]).reset_index(drop=True)
