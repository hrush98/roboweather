# Bucket Model Experiment: Tuning, CatBoost, And Early Specialist

Date: 2026-05-08

## Summary

We ran three offline bucket-model improvement experiments against the same chronological 2025 validation split:

1. Dynamic bucket hyperparameter and calibration sweep.
2. CatBoost dynamic bucket classifier with native categorical handling for `station`.
3. Early-window specialist trained only on `hour_local < 11`.

The tuned dynamic bucket model is the cleanest conservative promotion candidate because it improves both grouped log loss and grouped Brier versus the current production dynamic bucket model. CatBoost is also worth keeping for research because it has the best grouped log loss and top-bucket accuracy, but its grouped Brier is slightly worse than the tuned isotonic model. The early-window specialist is not a probability-quality promotion candidate because it does not improve early-window Brier, despite improving early top-bucket accuracy.

## Dataset And Split

```text
dataset: data/raw/dataset_2022-01-01_2025-12-31_initial5.csv
validation_year: 2025
validation groups: 10,903 ladders
```

All models used the same synthetic bucket candidate dataset and grouped probability normalization.

## Artifacts

```text
data/reports/bucket_tuning_obs_2022_2025.csv
data/models/dynamic_bucket_tuned_obs_2022_2025.joblib
data/reports/dynamic_bucket_tuned_obs_2022_2025/
data/models/catboost_bucket_obs_2022_2025.joblib
data/reports/catboost_bucket_obs_2022_2025/
data/models/dynamic_bucket_early_obs_2022_2025.joblib
data/reports/dynamic_bucket_early_obs_2022_2025/
```

Each report directory includes candidate predictions, ladder predictions, calibration, heuristic comparison, window metrics, and data diagnostics.

## Overall Results

| Model | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy | Read |
|---|---:|---:|---:|---|
| current dynamic bucket | 1.493053 | 0.076753 | 32.92% | Baseline |
| tuned dynamic bucket: `deeper_leaf50_isotonic` | 1.484623 | 0.076514 | 33.07% | Best conservative general model |
| CatBoost bucket | 1.480469 | 0.076526 | 33.87% | Best log loss and top-pick accuracy, slightly worse Brier than tuned |
| early specialist | 1.767226 | 0.087986 | 21.20% | Early-window only; not comparable overall |

## Window Results

### Tuned Dynamic Bucket

| Window | Groups | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|---:|
| early_09_10 | 3,632 | 1.768526 | 0.087934 | 19.19% |
| midday_11_12 | 3,632 | 1.595100 | 0.082101 | 25.77% |
| late_13_plus | 3,639 | 1.091001 | 0.059539 | 54.47% |

### CatBoost Bucket

| Window | Groups | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|---:|
| early_09_10 | 3,632 | 1.765871 | 0.087954 | 18.97% |
| midday_11_12 | 3,632 | 1.591381 | 0.082136 | 28.19% |
| late_13_plus | 3,639 | 1.084918 | 0.059522 | 54.41% |

### Early Specialist

| Window | Groups | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|---:|
| early_09_10 | 3,632 | 1.767226 | 0.087986 | 21.20% |

## Interpretation

The tuning sweep shows that calibration mattered more than changing model family. The best dynamic bucket config was `deeper_leaf50_isotonic`, which improved both primary probability metrics over `current_sigmoid`:

```text
grouped_log_loss:    1.493053 -> 1.484623
grouped_brier_score: 0.076753 -> 0.076514
top_bucket_accuracy: 32.92%   -> 33.07%
```

CatBoost is promising but mixed. It has the best grouped log loss and the strongest top-bucket accuracy, which means it is better at ranking or concentrating mass on the eventual winner. Its grouped Brier is slightly worse than the tuned isotonic model, suggesting the probability distribution may be a little less smooth or less Brier-calibrated even while log loss improves.

The early specialist does not meet the probability-quality promotion rule. It improves early top-bucket accuracy materially, but early grouped Brier is worse than the tuned general model:

```text
tuned early Brier:      0.087934
specialist early Brier: 0.087986
```

That makes it more plausible as a ranking auxiliary than as a standalone fair-value probability model.

## Promotion Decision

Recommended:

- Promote `data/models/dynamic_bucket_tuned_obs_2022_2025.joblib` as the conservative general dynamic bucket candidate after final manual review.
- Keep `data/models/catboost_bucket_obs_2022_2025.joblib` for offline review and possibly live research as an `--extra-model`; do not blend probabilities yet.
- Do not promote `data/models/dynamic_bucket_early_obs_2022_2025.joblib` as a probability model.

## Commands Run

```bash
python -m weather_trader.cli tune-bucket-model \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --output data/reports/bucket_tuning_obs_2022_2025.csv \
  --validation-year 2025

python -m weather_trader.cli train-bucket-model \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --output data/models/dynamic_bucket_tuned_obs_2022_2025.joblib \
  --validation-year 2025 \
  --report-dir data/reports/dynamic_bucket_tuned_obs_2022_2025 \
  --bucket-config deeper_leaf50_isotonic

python -m weather_trader.cli train-catboost-bucket-model \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --output data/models/catboost_bucket_obs_2022_2025.joblib \
  --validation-year 2025 \
  --report-dir data/reports/catboost_bucket_obs_2022_2025

python -m weather_trader.cli train-bucket-model \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --output data/models/dynamic_bucket_early_obs_2022_2025.joblib \
  --validation-year 2025 \
  --report-dir data/reports/dynamic_bucket_early_obs_2022_2025 \
  --hour-local-max 10
```

CatBoost was installed in the experiment Python environment before running the CatBoost command.

## Verification

```text
pytest tests/test_bucket_classifier.py tests/test_cli_research_loop.py tests/test_execution_harness.py
pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_bucket_classifier.py::test_catboost_bucket_predictions_normalize_and_match_metric_schema
```

Results:

```text
targeted pytest: 30 passed, 1 skipped
full pytest: 50 passed, 1 skipped
CatBoost-specific Python 3.11 test: 1 passed
```

Note: running `python -m pytest` without disabling plugin autoload in the Python 3.11 environment failed before test collection because of an unrelated `web3` pytest plugin import error involving `eth_typing.ContractName`. The repository's normal `pytest` command passes under Python 3.10.
