# Modeling And Trading Decisions

This is the technical reference for the temperature-market modeling work in `roboweather`.
It is meant to explain the choices, tradeoffs, and current conclusions without reading the whole codebase.

## Problem Framing

The target market is a binary or bucketed contract on the same-day daily high temperature.
The useful outputs are not just point forecasts; they are probabilities that can be priced against market quotes.

There are three relevant framings:

1. Binary threshold pricing.
1. Ordered bucket pricing.
1. Continuous temperature regression with bucket integration.

The repo currently supports all three, but they do not perform equally.

## What We Learned

### Threshold model

The threshold model predicts `P(final_high >= threshold)`.
That is the cleanest fit for binary questions and remains a strong baseline.

It is not, by itself, a good exact-bucket model.
When we derive bucket probabilities by differencing cumulative probabilities, the result is sensitive to monotonicity, boundary conventions, and calibration quality.

### Dynamic bucket model

The dynamic bucket model trains directly on synthetic bucket ladders and normalizes probabilities within each ladder.
This is currently the best exact-bucket model we have tested.

Current result on the rebuilt 2022-2025 dataset:

- Grouped log loss: `1.4931`
- Grouped Brier score: `0.0768`
- Top-bucket accuracy: `32.9%`

It is also the strongest model in the early trading window, where max-so-far heuristics are weakest and the price is least anchored by near-resolution behavior.

### Regression and NGBoost

We tested two distributional baselines:

1. `HistGradientBoostingRegressor` with empirical residual bucket probabilities.
1. NGBoost with a Normal distribution and CRPS-style training.

Both improved after the timestamp-based temperature trend fix, but neither beat the dynamic bucket classifier for exact-bucket pricing.

### Bucket semantics mattered

We fixed bounded buckets to be half-open so exact integer boundaries do not overlap.
That change removed a subtle bias and made the validation results more honest.

## Data And Feature Notes

The main same-day dataset is built from METAR/ASOS observations, not a fixed 5-minute grid.
Snapshot rows are mostly hourly local observations around `:51` and `:52`.

The old implementation of `temp_change_1h` and `temp_change_3h` assumed row shifts of 12 and 36, which only makes sense for a 5-minute cadence.
We replaced that with timestamp-based lookbacks.

This matters because the observations used in live trading are METAR-like snapshots, so trend features need to reflect actual time gaps.

## Current Ranking

For exact bucket pricing:

1. Dynamic bucket classifier.
1. Threshold-derived buckets.
1. Regression residual buckets.
1. NGBoost Normal buckets.

For binary threshold pricing:

1. Threshold model.

## Trading Implications

This is a betting problem, not a pure forecasting problem.

The model must do more than identify the most likely bucket:

- It must be calibrated.
- It must stay stable by entry window.
- It must survive all-or-nothing settlement.
- It must respect ladder-level exposure.

The most important trading question is not "Which bucket wins?"
It is "Is the market price wrong enough to justify risking capital under the current uncertainty?"

## Current Trading Position

The current best candidate for pricing exact buckets is the dynamic bucket classifier.
That does not mean it should be traded blindly.

The next evaluation layer should simulate:

- market edge thresholds,
- ladder-level exposure caps,
- window-stratified entry rules,
- realized P/L,
- drawdown,
- turnover.

## Decision Log

Use this as the place to record future calls on:

- whether exact buckets stay in scope,
- whether distributional regression replaces synthetic bucket ladders,
- how much HRRR should influence trading,
- what edge thresholds are acceptable by window.

### HRRR model family

The HRRR v2 cache now supports a parallel HRRR model family. Keep these models
separate from observation-only models until validation and research-policy replay
show clear improvement. See [HRRR model family plan](./hrrr-model-family-plan-2026-05-18.md).

## Reference Files

- [Changelog](./changelog.md)
- [Bucket distribution findings](./bucket_distribution_model_findings.md)
- [HRRR model family plan](./hrrr-model-family-plan-2026-05-18.md)
