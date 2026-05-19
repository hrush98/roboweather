# HRRR Model Family Plan

The HRRR v2 cache is now good enough to support a parallel model family alongside
the current observation-only models.

## Current Cache Status

Verified cache:

```bash
data/cache/hrrr_v2.sqlite
```

Validation summary from May 18, 2026:

- SQLite integrity check: `ok`
- completed extraction tasks: `51,591`
- failed extraction tasks: `26`
- point forecast rows: `722,274`
- cached stations: `14`
- valid-time span: `2022-01-01T15:00:00+00:00` through `2025-12-31T07:00:00+00:00`

Exporting the initial-five dataset produced:

- input rows: `394,200`
- output rows: `394,200`
- rows with HRRR features: `394,101`
- coverage: `99.97489%`

The missing `99` rows all occur on `2023-05-29`, caused by transient NOAA/S3
fetch failures in the cache build. That is small enough for `--require-hrrr`
training, but it should be recorded in model reports.

## Export Dataset

Materialize the HRRR-enriched initial-five dataset:

```bash
python -m weather_trader.cli hrrr-v2-cache \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --cache data/cache/hrrr_v2.sqlite \
  --mode export \
  --output data/processed/dataset_2022_2025_initial5_hrrr_v2.csv
```

For the PM-active US12 dataset, use the same cache and export path:

```bash
python -m weather_trader.cli hrrr-v2-cache \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_pm_active_us12.csv \
  --cache data/cache/hrrr_v2.sqlite \
  --mode export \
  --output data/processed/dataset_2022_2025_pm_active_us12_hrrr_v2.csv
```

If the US12 export has large HRRR gaps, inspect whether the raw dataset contains
stations outside the HRRR cache station list before training.

## Model Naming

Keep HRRR models as a separate family by adding `_hrrr_v2` before the dataset
suffix:

- `mvp_hrrr_v2_obs_2022_2025.joblib`
- `dynamic_bucket_hrrr_v2_obs_2022_2025.joblib`
- `dynamic_bucket_tuned_hrrr_v2_obs_2022_2025.joblib`
- `catboost_bucket_hrrr_v2_obs_2022_2025.joblib`
- `high_regression_hrrr_v2_obs_2022_2025.joblib`
- `ngboost_normal_hrrr_v2_obs_2022_2025.joblib`

For PM-active US12 variants, mirror the existing names:

- `mvp_hrrr_v2_pm_active_us12_obs_2022_2025.joblib`
- `dynamic_bucket_hrrr_v2_pm_active_us12_obs_2022_2025.joblib`
- `dynamic_bucket_tuned_hrrr_v2_pm_active_us12_obs_2022_2025.joblib`
- `high_regression_hrrr_v2_pm_active_us12_obs_2022_2025.joblib`
- `ngboost_normal_hrrr_v2_pm_active_us12_obs_2022_2025.joblib`

## Training Commands

Threshold model:

```bash
python -m weather_trader.cli train-model \
  --dataset data/processed/dataset_2022_2025_initial5_hrrr_v2.csv \
  --output data/models/mvp_hrrr_v2_obs_2022_2025.joblib \
  --report-dir data/reports/mvp_hrrr_v2_obs_2022_2025 \
  --require-hrrr
```

Dynamic bucket model:

```bash
python -m weather_trader.cli train-bucket-model \
  --dataset data/processed/dataset_2022_2025_initial5_hrrr_v2.csv \
  --output data/models/dynamic_bucket_hrrr_v2_obs_2022_2025.joblib \
  --report-dir data/reports/dynamic_bucket_hrrr_v2_obs_2022_2025 \
  --require-hrrr
```

Tuned dynamic bucket model:

```bash
python -m weather_trader.cli train-bucket-model \
  --dataset data/processed/dataset_2022_2025_initial5_hrrr_v2.csv \
  --output data/models/dynamic_bucket_tuned_hrrr_v2_obs_2022_2025.joblib \
  --report-dir data/reports/dynamic_bucket_tuned_hrrr_v2_obs_2022_2025 \
  --bucket-config deeper_leaf50_isotonic \
  --require-hrrr
```

Regression residual model:

```bash
python -m weather_trader.cli train-high-regression-model \
  --dataset data/processed/dataset_2022_2025_initial5_hrrr_v2.csv \
  --output data/models/high_regression_hrrr_v2_obs_2022_2025.joblib \
  --report-dir data/reports/high_regression_hrrr_v2_obs_2022_2025 \
  --require-hrrr
```

NGBoost model, in the NGBoost environment:

```bash
python -m weather_trader.cli train-ngboost-model \
  --dataset data/processed/dataset_2022_2025_initial5_hrrr_v2.csv \
  --output data/models/ngboost_normal_hrrr_v2_obs_2022_2025.joblib \
  --report-dir data/reports/ngboost_normal_hrrr_v2_obs_2022_2025 \
  --require-hrrr
```

## Why This Should Work

The training code already treats HRRR columns as optional active features. On
observation-only datasets those columns are absent or all-null, so they are
dropped. On HRRR-enriched datasets with `--require-hrrr`, the rows without HRRR
coverage are filtered out and the HRRR columns become active model features.

Important current feature paths:

- Threshold classifier uses `hrrr_current_temp`, `hrrr_remaining_max`,
  `hrrr_remaining_max_minus_threshold`, and
  `hrrr_current_temp_minus_current_temp`.
- Dynamic bucket classifier adds bucket-relative HRRR features such as
  `hrrr_remaining_max_minus_lower` and `hrrr_remaining_max_minus_upper`.
- Regression and NGBoost use HRRR current temp, remaining max, and current-temp
  delta as high-temperature predictors.

## Evaluation Gate

Do not replace observation-only models just because HRRR is available. Treat HRRR
models as a new family and compare them against matching obs-only baselines on
the same chronological validation year.

For exact-bucket pricing, compare:

- grouped log loss
- grouped Brier score
- top-bucket accuracy
- station-level validation summaries
- entry-window reports

Promotion rule:

1. HRRR must beat the matching obs-only model on grouped Brier and grouped log
   loss, or clearly improve one without damaging the other.
2. HRRR cannot create a large station-specific regression, especially for PM
   active stations.
3. HRRR must improve research-policy realized returns after replay, not just
   offline probability metrics.
4. HRRR models should initially run as `--extra-model` research models before
   they replace production defaults.

## Research Policy Integration

After training, add HRRR model paths to the research loop as extra models first.
That lets the existing snapshot and policy tables record HRRR-family predictions
without changing live decision defaults.

The first policy family to add should mirror the existing high-conviction
architecture:

- `dynamic_hrrr_hc_first`
- `mvp_hrrr_hc_first`
- `consensus_hrrr_dynamic_mvp_hc_first`

When evaluating retrospective policy buckets, replay from `prediction_snapshots`
rather than filtering only already-materialized policy rows. That keeps the HRRR
comparison from inheriting first-eligible-row bias from the older obs-only
policies.
