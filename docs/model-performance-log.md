# Model Performance Log

Generated: 2026-06-03T22:31:39.198410+00:00

Machine-readable registry: `data/reports/model_registry.csv`

## PM-Active US12 Enrichment Comparison

| model | type | feature set | validation rows | log loss | grouped log loss | top bucket acc | MAE | RMSE |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| mvp_pm_active_us12_obs_2022_2025 | threshold_classifier | obs | 235656 | 0.3286 |  |  |  |  |
| mvp_metar_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | metar_rich | 235656 | 0.3246 |  |  |  |  |
| mvp_hrrr_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | hrrr_rich | 212175 | 0.2511 |  |  |  |  |
| mvp_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | metar_rich+hrrr_rich | 212175 | 0.2509 |  |  |  |  |
| dynamic_bucket_pm_active_us12_obs_2022_2025 | dynamic_bucket | obs | 235224 |  | 1.4707 | 0.3414 |  |  |
| dynamic_bucket_tuned_pm_active_us12_obs_2022_2025 | dynamic_bucket | obs | 235224 |  | 1.4580 | 0.3489 |  |  |
| dynamic_bucket_metar_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich | 235224 |  | 1.4696 | 0.3463 |  |  |
| dynamic_bucket_tuned_metar_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich | 235224 |  | 1.4579 | 0.3515 |  |  |
| dynamic_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | hrrr_rich | 211797 |  | 1.4318 | 0.3795 |  |  |
| dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | hrrr_rich | 211797 |  | 1.4204 | 0.3819 |  |  |
| dynamic_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich+hrrr_rich | 211797 |  | 1.4314 | 0.3781 |  |  |
| dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich+hrrr_rich | 211797 |  | 1.4204 | 0.3824 |  |  |
| catboost_bucket_pm_active_us12_obs_2022_2025 | catboost_bucket | obs | 235224 |  | 1.4579 | 0.3456 |  |  |
| catboost_bucket_metar_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | metar_rich | 235224 |  | 1.4598 | 0.3453 |  |  |
| catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | hrrr_rich | 211797 |  | 1.4162 | 0.3861 |  |  |
| catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | metar_rich+hrrr_rich | 211797 |  | 1.4156 | 0.3883 |  |  |
| high_regression_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | obs | 26136 |  |  |  | 1.5367 | 2.2120 |
| high_regression_metar_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | metar_rich | 26136 |  |  |  | 1.5365 | 2.2145 |
| high_regression_hrrr_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | hrrr_rich | 23533 |  |  |  | 1.1828 | 1.6451 |
| high_regression_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | metar_rich+hrrr_rich | 23533 |  |  |  | 1.1823 | 1.6453 |
| ngboost_normal_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | obs | 26136 |  |  |  | 1.6772 | 2.4207 |
| ngboost_normal_metar_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | metar_rich | 26136 |  |  |  | 1.7313 | 2.4773 |
| ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | hrrr_rich | 23533 |  |  |  | 1.2848 | 1.7781 |
| ngboost_normal_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | metar_rich+hrrr_rich | 23533 |  |  |  | 1.2816 | 1.7745 |

## Best Loaded Artifacts

| model | type | feature set | validation rows | primary metric | top bucket acc | report |
|---|---|---:|---:|---:|---:|---|
| catboost_bucket_hrrr_v2_obs_2022_2025 | catboost_bucket | hrrr_basic | 98127 | grouped_log_loss=1.3983 | 0.3948 | data/reports/catboost_bucket_hrrr_v2_obs_2022_2025 |
| dynamic_bucket_tuned_hrrr_v2_obs_2022_2025 | dynamic_bucket | hrrr_basic | 98127 | grouped_log_loss=1.4007 | 0.3914 | data/reports/dynamic_bucket_tuned_hrrr_v2_obs_2022_2025 |
| dynamic_bucket_hrrr_v2_obs_2022_2025 | dynamic_bucket | hrrr_basic | 98127 | grouped_log_loss=1.4092 | 0.3908 | data/reports/dynamic_bucket_hrrr_v2_obs_2022_2025 |
| low_mvp_hrrr_v2_obs_2022_2025 | low_threshold | hrrr_basic | 3411 | log_loss=0.2373 |  | data/reports/low_mvp_hrrr_v2_obs_2022_2025 |
| mvp_hrrr_v2_obs_2022_2025 | threshold_classifier | hrrr_basic | 98280 | log_loss=0.2533 |  | data/reports/mvp_hrrr_v2_obs_2022_2025 |
| high_regression_hrrr_v2_obs_2022_2025 | high_regression_empirical_residual | hrrr_basic | 10903 | mae=1.2350 |  | data/reports/high_regression_hrrr_v2_obs_2022_2025 |
| ngboost_normal_hrrr_v2_obs_2022_2025 | ngboost_normal_crps | hrrr_basic | 10903 | mae=1.3373 |  | data/reports/ngboost_normal_hrrr_v2_obs_2022_2025 |
| catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | hrrr_rich | 211797 | grouped_log_loss=1.4162 | 0.3861 | data/reports/catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | hrrr_rich | 211797 | grouped_log_loss=1.4204 | 0.3819 | data/reports/dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | hrrr_rich | 211797 | grouped_log_loss=1.4318 | 0.3795 | data/reports/dynamic_bucket_hrrr_rich_pm_active_us12_obs_2022_2025 |
| mvp_hrrr_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | hrrr_rich | 212175 | log_loss=0.2511 |  | data/reports/mvp_hrrr_rich_pm_active_us12_obs_2022_2025 |
| high_regression_hrrr_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | hrrr_rich | 23533 | mae=1.1828 |  | data/reports/high_regression_hrrr_rich_pm_active_us12_obs_2022_2025 |
| ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | hrrr_rich | 23533 | mae=1.2848 |  | data/reports/ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_tuned_metar_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich | 235224 | grouped_log_loss=1.4579 | 0.3515 | data/reports/dynamic_bucket_tuned_metar_rich_pm_active_us12_obs_2022_2025 |
| catboost_bucket_metar_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | metar_rich | 235224 | grouped_log_loss=1.4598 | 0.3453 | data/reports/catboost_bucket_metar_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_metar_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich | 235224 | grouped_log_loss=1.4696 | 0.3463 | data/reports/dynamic_bucket_metar_rich_pm_active_us12_obs_2022_2025 |
| mvp_metar_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | metar_rich | 235656 | log_loss=0.3246 |  | data/reports/mvp_metar_rich_pm_active_us12_obs_2022_2025 |
| high_regression_metar_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | metar_rich | 26136 | mae=1.5365 |  | data/reports/high_regression_metar_rich_pm_active_us12_obs_2022_2025 |
| ngboost_normal_metar_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | metar_rich | 26136 | mae=1.7313 |  | data/reports/ngboost_normal_metar_rich_pm_active_us12_obs_2022_2025 |
| catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | catboost_bucket | metar_rich+hrrr_rich | 211797 | grouped_log_loss=1.4156 | 0.3883 | data/reports/catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich+hrrr_rich | 211797 | grouped_log_loss=1.4204 | 0.3824 | data/reports/dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | dynamic_bucket | metar_rich+hrrr_rich | 211797 | grouped_log_loss=1.4314 | 0.3781 | data/reports/dynamic_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| mvp_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | threshold_classifier | metar_rich+hrrr_rich | 212175 | log_loss=0.2509 |  | data/reports/mvp_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| high_regression_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | high_regression_empirical_residual | metar_rich+hrrr_rich | 23533 | mae=1.1823 |  | data/reports/high_regression_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| ngboost_normal_metar_hrrr_rich_pm_active_us12_obs_2022_2025 | ngboost_normal_crps | metar_rich+hrrr_rich | 23533 | mae=1.2816 |  | data/reports/ngboost_normal_metar_hrrr_rich_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_international_celsius_low_obs_2022_2025 | dynamic_bucket | obs | 262395 | grouped_log_loss=1.0156 | 0.5983 | data/reports/dynamic_bucket_international_celsius_low_obs_2022_2025 |
| low_dynamic_bucket_obs_2022_2025 | dynamic_bucket | obs | 130842 | grouped_log_loss=1.1517 | 0.5409 | data/reports/low_dynamic_bucket_obs_2022_2025 |
| catboost_bucket_international_celsius_high_obs_2022_2025 | catboost_bucket | obs | 196848 | grouped_log_loss=1.1992 | 0.4763 | data/reports/catboost_bucket_international_celsius_high_obs_2022_2025 |
| dynamic_bucket_international_celsius_high_obs_2022_2025 | dynamic_bucket | obs | 196848 | grouped_log_loss=1.2682 | 0.4589 | data/reports/dynamic_bucket_international_celsius_high_obs_2022_2025 |
| catboost_bucket_pm_active_us12_obs_2022_2025 | catboost_bucket | obs | 235224 | grouped_log_loss=1.4579 | 0.3456 | data/reports/catboost_bucket_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_tuned_pm_active_us12_obs_2022_2025 | dynamic_bucket | obs | 235224 | grouped_log_loss=1.4580 | 0.3489 | data/reports/dynamic_bucket_tuned_pm_active_us12_obs_2022_2025 |
| dynamic_bucket_pm_active_us12_obs_2022_2025 | dynamic_bucket | obs | 235224 | grouped_log_loss=1.4707 | 0.3414 | data/reports/dynamic_bucket_pm_active_us12_obs_2022_2025 |
| catboost_bucket_obs_2022_2025 | catboost_bucket | obs | 98127 | grouped_log_loss=1.4805 | 0.3387 | data/reports/catboost_bucket_obs_2022_2025 |
| mvp_international_celsius_low_obs_2022_2025 | threshold_classifier | obs | 262584 | log_loss=0.1715 |  | data/reports/mvp_international_celsius_low_obs_2022_2025 |
| mvp_international_celsius_high_obs_2022_2025 | threshold_classifier | obs | 197082 | log_loss=0.1906 |  | data/reports/mvp_international_celsius_high_obs_2022_2025 |
| low_mvp_obs_2022_2025 | low_threshold | obs | 131031 | log_loss=0.2649 |  | data/reports/low_mvp_obs_2022_2025 |
| mvp_pm_active_us12_obs_2022_2025 | threshold_classifier | obs | 235656 | log_loss=0.3286 |  | data/reports/mvp_pm_active_us12_obs_2022_2025 |
| mvp_next_day_obs_2022_2025 | next_day_threshold | obs | 16380 | log_loss=0.6210 |  | data/reports/mvp_next_day_obs_2022_2025 |
| high_regression_international_celsius_high_obs_2022_2025 | high_regression_empirical_residual | obs | 21872 | mae=0.7812 |  | data/reports/high_regression_international_celsius_high_obs_2022_2025 |
| ngboost_normal_international_celsius_high_obs_2022_2025 | ngboost_normal_crps | obs | 21872 | mae=0.8711 |  | data/reports/ngboost_normal_international_celsius_high_obs_2022_2025 |

## Notes

- `grouped_log_loss` is the primary metric for bucket-distribution models; lower is better.
- `log_loss`/`brier_score` apply to threshold classifier artifacts; lower is better.
- `mae`/`rmse` apply to final-high distribution/regression artifacts; lower is better.
- Artifacts with load errors are retained in the CSV so old training history remains visible even when sklearn/joblib compatibility prevents metric extraction.

## Load Errors

| model | inferred type | error |
|---|---|---|
| dynamic_bucket_early_obs_2022_2025 | dynamic_bucket | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| dynamic_bucket_early_pm_active_us12_obs_2022_2025 | dynamic_bucket | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| dynamic_bucket_obs_2022_2025 | dynamic_bucket | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| dynamic_bucket_tuned_obs_2022_2025 | dynamic_bucket | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| high_regression_obs_2022_2025 | high_regression_empirical_residual | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| mvp | threshold_classifier | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| mvp_hrrr_sample | threshold_classifier | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| mvp_obs_2022_2025 | threshold_classifier | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| mvp_obs_2022_2025_report_refresh | threshold_classifier | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
| mvp_obs_corrected | threshold_classifier | AttributeError: Can't get attribute '_RemainderColsList' on <module 'sklearn.compose._column_transformer' from '/home/maxrush/miniconda3/envs/roboweather/lib/python3.11/site-packages/sklearn/compose/_column_transformer.py'> |
