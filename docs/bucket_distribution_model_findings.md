# Bucket Distribution Model Findings

Date: 2026-05-06

## Summary

We tested several ways to price Polymarket-style daily high temperature buckets from same-day weather snapshots. The best current approach is the direct dynamic bucket classifier trained over synthetic bucket candidates with grouped probability normalization.

After fixing bucket semantics, the dynamic bucket model is clearly ahead of the tested alternatives on exact-bucket probability scoring.

## Bucket Semantics Fix

The original synthetic bucket logic treated bounded buckets as inclusive on both ends. For example:

```text
79-80F: 79 <= final_high <= 80
80-81F: 80 <= final_high <= 81
```

That made exact boundary values overlap. A final high of `80.0F` matched both buckets and the implementation picked the first match, biasing labels downward and making the `max_so_far` heuristic look stronger than it really was.

The current semantics are non-overlapping:

```text
79-80F: 79 <= final_high < 80
80-81F: 80 <= final_high < 81
right tail: >= lower
left tail: < upper
```

Example ladder:

```text
<76F, 76-77F, 77-78F, 78-79F, 79-80F, 80-81F, 81-82F, 82-83F, >=83F
```

## Models Tested

1. `dynamic_bucket_classifier`
   - HistGradientBoostingClassifier over synthetic bucket candidates.
   - Candidate features include weather state plus bucket geometry.
   - Candidate probabilities are normalized within each ladder.

2. `cumulative_threshold_derived_bucket`
   - Uses the existing threshold classifier.
   - Bucket probabilities are derived from survival probabilities.
   - Per-ladder monotonic repair is applied before differencing.

3. `regression_empirical_residual_bucket`
   - HistGradientBoostingRegressor predicts `final_high_tmpf`.
   - Bucket probabilities are derived from empirical training residuals by entry window.

4. `ngboost_normal_crps_bucket`
   - NGBoost Normal distribution trained with CRPS.
   - Bucket probabilities are computed with Normal CDF differences.
   - Trained in a separate conda env at `.conda/roboweather-ngboost` because NGBoost requires newer sklearn than the main project env.

5. Baselines
   - Uniform over ladder.
   - Bucket containing `max_temp_so_far`.

## Overall Validation Results

Dataset:

```text
data/raw/dataset_2022-01-01_2025-12-31_initial5.csv
validation_year: 2025
validation groups: 10,903 ladders
```

| Model | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|
| dynamic_bucket_classifier | 1.4971 | 0.0768 | 33.14% |
| cumulative_threshold_derived_bucket | 2.3481 | 0.0890 | 30.37% |
| regression_empirical_residual_bucket | 2.1526 | 0.1022 | 21.07% |
| ngboost_normal_crps_bucket | 2.2468 | 0.1032 | 20.22% |
| uniform_over_ladder | 2.1972 | 0.0988 | n/a |
| bucket_containing_max_so_far | n/a | n/a | 29.34% |

The dynamic bucket classifier is best on log loss, Brier score, and top-bucket accuracy.

## Window-Stratified Results

### Early Window: 09:00-10:59

| Model | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|
| dynamic_bucket_classifier | 1.7763 | 0.0881 | 20.81% |
| cumulative_threshold_derived_bucket | 3.2512 | 0.1124 | 10.44% |
| regression_empirical_residual_bucket | 2.5455 | 0.1165 | 8.92% |
| ngboost_normal_crps_bucket | 2.5998 | 0.1196 | 5.12% |
| uniform_over_ladder | 2.1972 | 0.0988 | n/a |
| bucket_containing_max_so_far | n/a | n/a | 11.54% |

The early window is the cleanest trading-relevant test because the high is usually not set yet. Dynamic bucket is the only tested model that clearly beats uniform probabilistically here.

### Midday Window: 11:00-12:59

| Model | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|
| dynamic_bucket_classifier | 1.6076 | 0.0824 | 24.34% |
| cumulative_threshold_derived_bucket | 2.2189 | 0.0924 | 24.89% |
| regression_empirical_residual_bucket | 2.1718 | 0.1018 | 20.73% |
| ngboost_normal_crps_bucket | 2.2213 | 0.1020 | 20.79% |
| uniform_over_ladder | 2.1972 | 0.0988 | n/a |
| bucket_containing_max_so_far | n/a | n/a | 22.47% |

Dynamic bucket has the best probabilistic score. Threshold-derived narrowly has the best top-pick accuracy, but its log loss is much worse.

### Late Window: 13:00+

| Model | Grouped Log Loss | Grouped Brier | Top Bucket Accuracy |
|---|---:|---:|---:|
| dynamic_bucket_classifier | 1.1083 | 0.0601 | 54.22% |
| cumulative_threshold_derived_bucket | 1.5758 | 0.0622 | 55.73% |
| regression_empirical_residual_bucket | 1.7412 | 0.0883 | 33.53% |
| ngboost_normal_crps_bucket | 1.9200 | 0.0881 | 34.73% |
| uniform_over_ladder | 2.1972 | 0.0988 | n/a |
| bucket_containing_max_so_far | n/a | n/a | 53.97% |

Late-window top-pick accuracy is less informative because `max_temp_so_far` often already equals or nearly equals the final high. Dynamic bucket remains best by log loss.

## Regression And NGBoost Notes

The histogram regression model produced stronger point forecasts than NGBoost:

| Model | MAE | RMSE |
|---|---:|---:|
| HistGradientBoostingRegressor | 1.696F | 2.367F |
| NGBoost Normal CRPS | 1.792F | 2.517F |

NGBoost predicted an average sigma of `2.17F`, but the resulting Normal bucket probabilities were not competitive. The likely issue is not that distributional regression is impossible; this first pass used a simple Normal distribution and the existing feature set. Temperature bucket probabilities may need better uncertainty features, station climatology, HRRR inputs, or a different distribution/calibration layer.

## Current Read

The dynamic bucket HistGradientBoosting classifier is the strongest current bucket-pricing model. It has useful signal in the early window, where the `max_so_far` heuristic is weak, and it beats uniform by a large margin on grouped log loss.

This does not prove it is tradeable. The next gate should be a simulated trading layer:

```text
for each station/day/snapshot ladder:
  compute fair bucket probabilities
  compare against market YES/NO prices
  trade only when edge exceeds threshold
  cap exposure per station/day ladder
  stratify edge thresholds by entry window
  score realized P/L, drawdown, hit rate, and turnover
```

## Artifacts

Models:

```text
data/models/dynamic_bucket_obs_2022_2025.joblib
data/models/high_regression_obs_2022_2025.joblib
data/models/ngboost_normal_obs_2022_2025.joblib
```

Reports:

```text
data/reports/dynamic_bucket_obs_2022_2025/
data/reports/high_regression_obs_2022_2025/
data/reports/ngboost_normal_obs_2022_2025/
data/reports/bucket_model_comparison_2025/
```

Most useful summary CSVs:

```text
data/reports/bucket_model_comparison_2025/model_comparison_with_ngboost.csv
data/reports/bucket_model_comparison_2025/model_comparison_by_window_with_ngboost.csv
```
