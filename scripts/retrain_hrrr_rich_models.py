#!/usr/bin/env python3
"""Retrain all 12 HRRR-rich and METAR-HRRR-rich models from the base dataset + HRRR cache.

Produces:
  data/models/*_hrrr_rich_pm_active_us12_obs_2022_2025.joblib       (6 models)
  data/models/*_metar_hrrr_rich_pm_active_us12_obs_2022_2025.joblib (6 models)

Compares training metrics to the hrrr_v2 baseline models already on disk.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from weather_trader.config import RAW_DIR, MODELS_DIR, CACHE_DIR
from weather_trader.forecasts.hrrr_v2 import materialize_hrrr_v2_features
from weather_trader.models.bucket_classifier import (
    train_bucket_classifier,
    train_catboost_bucket_classifier,
    tune_bucket_model_configs,
    save_bucket_artifacts,
    save_catboost_bucket_artifacts,
    BucketTrainingArtifacts,
)
from weather_trader.models.high_regressor import (
    train_high_regressor,
    train_ngboost_high_regressor,
    save_high_regression_artifacts,
    save_ngboost_artifacts,
)
from weather_trader.models.train_classifier import train_and_calibrate, save_artifacts

# ── config ────────────────────────────────────────────────────────────────
DATASET_PATH = RAW_DIR / "dataset_2022-01-01_2025-12-31_pm_active_us12.csv"
VALIDATION_YEAR = 2025
MODELS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_FAMILIES = [
    {
        "suffix": "hrrr_rich",
        "description": "HRRR-rich features (HRRR shortwave, gust, cloud cover, dewpoint, RH + METAR obs)",
        # HRRR-rich uses the enrichment but NOT the METAR-rich columns.
        # The dataset already has METAR columns; we keep them.
    },
    {
        "suffix": "metar_hrrr_rich",
        "description": "METAR + HRRR rich features (all available enriched columns)",
    },
]

MODEL_TYPES = [
    "mvp",
    "dynamic_bucket",
    "dynamic_bucket_tuned",
    "catboost_bucket",
    "high_regression",
    "ngboost_normal",
]


def model_path(suffix: str, model_type: str) -> Path:
    return MODELS_DIR / f"{model_type}_{suffix}_pm_active_us12_obs_2022_2025.joblib"


def load_or_enrich_dataset() -> pd.DataFrame:
    """Load base dataset and enrich with HRRR features from v2 cache."""
    enriched_path = RAW_DIR / "dataset_2022-01-01_2025-12-31_pm_active_us12_hrrr_enriched.csv"
    if enriched_path.exists():
        print(f"Loading cached enriched dataset: {enriched_path} ({enriched_path.stat().st_size / 1e6:.1f} MB)", flush=True)
        return pd.read_csv(enriched_path, low_memory=False)

    print(f"Loading base dataset: {DATASET_PATH} ({DATASET_PATH.stat().st_size / 1e6:.1f} MB)", flush=True)
    base = pd.read_csv(DATASET_PATH, low_memory=False)
    print(f"Base dataset: {len(base)} rows, {len(base.columns)} columns", flush=True)

    print("Enriching with HRRR features from v2 cache (SQLite reads only)...")
    start = time.monotonic()
    enriched = materialize_hrrr_v2_features(
        dataset=base,
        cache_path=CACHE_DIR / "hrrr_v2.sqlite",
        progress_every=5000,
    )
    elapsed = time.monotonic() - start
    print(f"Enrichment complete: {len(enriched)} rows, {len(enriched.columns)} columns in {elapsed:.0f}s")

    hrrr_cols = [c for c in enriched.columns if c.startswith("hrrr_")]
    print(f"HRRR columns added: {len(hrrr_cols)} -> {hrrr_cols}")

    enriched.to_csv(enriched_path, index=False)
    print(f"Cached enriched dataset: {enriched_path}")
    return enriched


def train_mvp(dataset: pd.DataFrame, suffix: str) -> dict:
    """Train MVP threshold classifier."""
    path = model_path(suffix, "mvp")
    print(f"\n  Training MVP threshold classifier -> {path.name}")
    start = time.monotonic()
    artifacts = train_and_calibrate(
        dataset=dataset,
        validation_year=VALIDATION_YEAR,
        temperature_metric="high",
    )
    save_artifacts(artifacts, path)
    elapsed = time.monotonic() - start
    metrics = artifacts.metrics or {}
    print(f"  MVP done in {elapsed:.0f}s  accuracy={metrics.get('accuracy', '?'):.4f}  "
          f"log_loss={metrics.get('log_loss', '?'):.4f}  roc_auc={metrics.get('roc_auc', '?'):.4f}  "
          f"train_rows={artifacts.train_rows}  val_rows={artifacts.validation_rows}")
    return {
        "model": "mvp",
        "suffix": suffix,
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "accuracy": metrics.get("accuracy"),
        "log_loss": metrics.get("log_loss"),
        "roc_auc": metrics.get("roc_auc"),
        "brier_score": metrics.get("brier_score"),
        "duration_s": elapsed,
    }


def train_dynamic_bucket(dataset: pd.DataFrame, suffix: str, tuned: bool = False) -> dict:
    """Train dynamic bucket classifier (optionally tuned)."""
    label = "dynamic_bucket_tuned" if tuned else "dynamic_bucket"
    path = model_path(suffix, label)
    print(f"\n  Training {'tuned ' if tuned else ''}dynamic bucket -> {path.name}")
    start = time.monotonic()

    if tuned:
        # Tune first, then train with best config
        from weather_trader.models.bucket_classifier import BucketModelConfig
        results_df = tune_bucket_model_configs(
            dataset=dataset,
            validation_year=VALIDATION_YEAR,
            temperature_metric="high",
        )
        best_row = results_df.iloc[0]
        print(f"  Best tuned config: {best_row['config']}  gll={best_row['grouped_log_loss']:.4f}", flush=True)
        best = BucketModelConfig(
            name=best_row["config"],
            learning_rate=best_row["learning_rate"],
            max_depth=int(best_row["max_depth"]),
            max_iter=int(best_row["max_iter"]),
            min_samples_leaf=int(best_row["min_samples_leaf"]),
            l2_regularization=float(best_row["l2_regularization"]),
            calibration_method=best_row["calibration_method"],
        )
        artifacts = train_bucket_classifier(
            dataset=dataset,
            validation_year=VALIDATION_YEAR,
            model_config=best,
            temperature_metric="high",
        )
    else:
        artifacts = train_bucket_classifier(
            dataset=dataset,
            validation_year=VALIDATION_YEAR,
            temperature_metric="high",
        )

    save_bucket_artifacts(artifacts, path)
    elapsed = time.monotonic() - start
    metrics = artifacts.metrics or {}
    print(f"  {'Tuned ' if tuned else ''}Bucket done in {elapsed:.0f}s  "
          f"grouped_log_loss={metrics.get('grouped_log_loss', '?'):.4f}  "
          f"top_bucket_acc={metrics.get('top_bucket_accuracy', '?'):.4f}  "
          f"train_rows={artifacts.train_rows}  val_rows={artifacts.validation_rows}")
    return {
        "model": label,
        "suffix": suffix,
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "grouped_log_loss": metrics.get("grouped_log_loss"),
        "top_bucket_accuracy": metrics.get("top_bucket_accuracy"),
        "log_loss": metrics.get("log_loss"),
        "brier_score": metrics.get("brier_score"),
        "duration_s": elapsed,
    }


def train_catboost(dataset: pd.DataFrame, suffix: str) -> dict:
    """Train CatBoost bucket classifier."""
    path = model_path(suffix, "catboost_bucket")
    print(f"\n  Training CatBoost bucket -> {path.name}")
    start = time.monotonic()
    artifacts = train_catboost_bucket_classifier(
        dataset=dataset,
        validation_year=VALIDATION_YEAR,
        temperature_metric="high",
    )
    save_catboost_bucket_artifacts(artifacts, path)
    elapsed = time.monotonic() - start
    metrics = artifacts.metrics or {}
    print(f"  CatBoost done in {elapsed:.0f}s  "
          f"grouped_log_loss={metrics.get('grouped_log_loss', '?'):.4f}  "
          f"top_bucket_acc={metrics.get('top_bucket_accuracy', '?'):.4f}  "
          f"train_rows={artifacts.train_rows}  val_rows={artifacts.validation_rows}")
    return {
        "model": "catboost_bucket",
        "suffix": suffix,
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "grouped_log_loss": metrics.get("grouped_log_loss"),
        "top_bucket_accuracy": metrics.get("top_bucket_accuracy"),
        "log_loss": metrics.get("log_loss"),
        "brier_score": metrics.get("brier_score"),
        "duration_s": elapsed,
    }


def train_high_regression(dataset: pd.DataFrame, suffix: str) -> dict:
    """Train high regression empirical residual model."""
    path = model_path(suffix, "high_regression")
    print(f"\n  Training high regression -> {path.name}")
    start = time.monotonic()
    artifacts = train_high_regressor(
        dataset=dataset,
        validation_year=VALIDATION_YEAR,
    )
    save_high_regression_artifacts(artifacts, path)
    elapsed = time.monotonic() - start
    metrics = artifacts.metrics or {}
    print(f"  High regression done in {elapsed:.0f}s  "
          f"mae={metrics.get('mae', '?'):.4f}  rmse={metrics.get('rmse', '?'):.4f}  "
          f"train_rows={artifacts.train_rows}  val_rows={artifacts.validation_rows}")
    return {
        "model": "high_regression",
        "suffix": suffix,
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "mae": metrics.get("mae"),
        "rmse": metrics.get("rmse"),
        "duration_s": elapsed,
    }


def train_ngboost(dataset: pd.DataFrame, suffix: str) -> dict:
    """Train NGBoost normal CRPS high regressor."""
    path = model_path(suffix, "ngboost_normal")
    print(f"\n  Training NGBoost normal -> {path.name}")
    start = time.monotonic()
    artifacts = train_ngboost_high_regressor(
        dataset=dataset,
        validation_year=VALIDATION_YEAR,
    )
    save_ngboost_artifacts(artifacts, path)
    elapsed = time.monotonic() - start
    metrics = artifacts.metrics or {}
    print(f"  NGBoost done in {elapsed:.0f}s  "
          f"mae={metrics.get('mae', '?'):.4f}  rmse={metrics.get('rmse', '?'):.4f}  "
          f"train_rows={artifacts.train_rows}  val_rows={artifacts.validation_rows}")
    return {
        "model": "ngboost_normal",
        "suffix": suffix,
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "mae": metrics.get("mae"),
        "rmse": metrics.get("rmse"),
        "avg_predicted_sigma": metrics.get("avg_predicted_sigma"),
        "duration_s": elapsed,
    }


def load_baseline_metrics(baseline_suffix: str, model_type: str) -> dict | None:
    """Load metrics from an existing baseline model for comparison."""
    path = MODELS_DIR / f"{model_type}_{baseline_suffix}_obs_2022_2025.joblib"
    if not model_type.startswith("catboost") and not model_type.startswith("dynamic"):
        path = MODELS_DIR / f"{model_type}_{baseline_suffix}_obs_2022_2025.joblib"
    if not path.exists():
        # Try pm_active_us12 variant
        path = MODELS_DIR / f"{model_type}_{baseline_suffix}_pm_active_us12_obs_2022_2025.joblib"
    if not path.exists():
        return None
    try:
        bundle = joblib.load(path)
        metrics = bundle.get("metrics") or {}
        return {
            "path": str(path.name),
            "train_rows": bundle.get("train_rows", "?"),
            "validation_rows": bundle.get("validation_rows", "?"),
            **{k: v for k, v in metrics.items()},
        }
    except Exception:
        return None


def main() -> None:
    overall_start = time.monotonic()

    # 1. Load enriched dataset
    print("=" * 70)
    print("STEP 1: Load/enrich dataset")
    print("=" * 70)
    dataset = load_or_enrich_dataset()

    # 2. Train all models
    all_results = []

    for family in MODEL_FAMILIES:
        suffix = family["suffix"]
        print(f"\n{'=' * 70}")
        print(f"FAMILY: {suffix} — {family['description']}")
        print(f"{'=' * 70}")

        for model_type in MODEL_TYPES:
            path = model_path(suffix, model_type)
            if path.exists():
                print(f"  SKIP {model_type}_{suffix}: already exists", flush=True)
                continue
            if model_type == "mvp":
                result = train_mvp(dataset, suffix)
            elif model_type == "dynamic_bucket":
                result = train_dynamic_bucket(dataset, suffix, tuned=False)
            elif model_type == "dynamic_bucket_tuned":
                result = train_dynamic_bucket(dataset, suffix, tuned=True)
            elif model_type == "catboost_bucket":
                result = train_catboost(dataset, suffix)
            elif model_type == "high_regression":
                result = train_high_regression(dataset, suffix)
            elif model_type == "ngboost_normal":
                result = train_ngboost(dataset, suffix)
            else:
                continue
            all_results.append(result)

    # 3. Compare to baselines
    print(f"\n{'=' * 70}")
    print("COMPARISON: New models vs HRRR-v2 baselines")
    print(f"{'=' * 70}")

    comparison_rows = []
    for result in all_results:
        model_type = result["model"]
        # Map to baseline name
        baseline_map = {
            "mvp": "mvp",
            "dynamic_bucket": "dynamic_bucket",
            "dynamic_bucket_tuned": "dynamic_bucket_tuned",
            "catboost_bucket": "catboost_bucket",
            "high_regression": "high_regression",
            "ngboost_normal": "ngboost_normal",
        }
        baseline_type = baseline_map.get(model_type, model_type)
        baseline = load_baseline_metrics("hrrr_v2", baseline_type)

        row = {
            "model": f"{model_type}_{result['suffix']}",
            "train_rows": result.get("train_rows"),
            "val_rows": result.get("validation_rows"),
            "duration_s": result.get("duration_s"),
        }

        # Key metrics
        for key in ["accuracy", "log_loss", "roc_auc", "brier_score",
                     "grouped_log_loss", "top_bucket_accuracy", "mae", "rmse"]:
            row[key] = result.get(key)
            if baseline:
                row[f"baseline_{key}"] = baseline.get(key)

        comparison_rows.append(row)

    # Print comparison table
    print(f"\n{'model':<50} {'train':>8} {'val':>8} {'key_metric':>24} {'baseline':>12}")
    print("-" * 110)
    for row in comparison_rows:
        # Determine primary metric
        if row.get("grouped_log_loss") is not None:
            key_metric = f"gll={row['grouped_log_loss']:.4f}"
            base_key = f"gll={row.get('baseline_grouped_log_loss', '?'):.4f}" if row.get('baseline_grouped_log_loss') is not None else "N/A"
        elif row.get("log_loss") is not None:
            key_metric = f"ll={row['log_loss']:.4f}"
            base_key = f"ll={row.get('baseline_log_loss', '?'):.4f}" if row.get('baseline_log_loss') is not None else "N/A"
        elif row.get("mae") is not None:
            key_metric = f"mae={row['mae']:.4f}"
            base_key = f"mae={row.get('baseline_mae', '?'):.4f}" if row.get('baseline_mae') is not None else "N/A"
        else:
            key_metric = "?"
            base_key = "?"

        print(f"{row['model']:<50} {row.get('train_rows', '?'):>8} {row.get('val_rows', '?'):>8} {key_metric:>24} {base_key:>12}")

    overall_elapsed = time.monotonic() - overall_start
    print(f"\n{'=' * 70}")
    print(f"All training complete in {overall_elapsed:.0f}s ({overall_elapsed / 60:.1f} min)")
    print(f"Models written to: {MODELS_DIR}")
    print(f"{'=' * 70}")

    # Save results
    results_path = ROOT / "data" / "reports" / "retrain_hrrr_rich_2026-06-15.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w") as f:
        json.dump({"results": all_results, "comparison": comparison_rows, "elapsed_s": overall_elapsed}, f, indent=2, default=str)
    print(f"Results saved to: {results_path}")


if __name__ == "__main__":
    main()
