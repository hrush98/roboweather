# Low Temperature Daily Model Expansion Plan

Date: 2026-05-18
Branch/worktree: `research/low-temp-daily-models` at `/home/maxrush/General/roboweather-low-temp`

## Goal

Add a low-temperature market family to the research stack without multiplying thin-slice policy rows. The first milestone is offline model training and diagnostics for daily low markets. Research-loop integration should follow only after the low-target feature set and calibration look sane.

## Working Hypothesis

Lowest-temperature markets should behave like the inverse of highest-temperature markets, but the useful trading window is different. Daily highs usually resolve through late morning and afternoon warming. Daily lows are usually decided overnight or near sunrise, so the first research window should be local early morning, roughly `02:00-09:00`.

The core alpha question is:

> Given current temp, low-so-far, HRRR remaining minimum, station/time/season, bucket prices, and market liquidity, do low-temperature markets show stale pricing during the overnight or early-morning window?

## HRRR Cache Usability Check

The HRRR v2 cache generated in the main checkout is usable for low-temperature models.

Checked file:

`/home/maxrush/General/roboweather/data/cache/hrrr_v2.sqlite`

Current cache facts:

- Tables: `hrrr_extract_tasks`, `hrrr_point_forecasts`
- Point forecast rows: `722274`
- Stations: `14`
- Cycle coverage: `2022-01-01T13:00:00+00:00` through `2025-12-30T19:00:00+00:00`
- Forecast-hour coverage: `1` through `17`
- `tmpf` rows: `722274` of `722274`
- Task completion: `51591` done, `26` failed
- Failures are sparse by year: 2022 has 4, 2023 has 19, 2024 has 1, 2025 has 2

The cache stores raw point forecast `tmpf` by station/cycle/forecast hour, so low features can be derived by taking the minimum over remaining forecast rows. The existing summarizer already emits `hrrr_remaining_min`.

The high-model processed CSV in the main checkout:

`/home/maxrush/General/roboweather/data/processed/dataset_2022_2025_initial5_hrrr_v2.csv`

already includes `hrrr_remaining_min`, but it is still high-target-shaped:

- It has `final_high_tmpf`, `max_temp_so_far`, and high-threshold targets.
- It does not contain low-target labels such as `final_low_tmpf`.
- It does not contain low-progress features such as `min_temp_so_far` / `low_so_far`.

Conclusion from the initial inspection: reuse the HRRR v2 cache and summarization path, but build a new low-target dataset rather than trying to train low models directly from the current high-target CSV.

Follow-up after training the first low-threshold models:

- The observation-only low dataset built successfully with `525555` rows.
- The HRRR materialized dataset also built, but only `13329` rows had non-null `hrrr_remaining_min`.
- Unique low snapshots: `58359`
- Unique low snapshots with HRRR: `1481`
- Coverage is concentrated around `08:00` local and overwhelmingly at `KORD`.

That means the current HRRR v2 cache is technically usable for a smoke-test low HRRR model, but it is not yet broad enough for a fair production-quality low-market comparison. The cache was likely extracted around high-temperature snapshot plans, so the low `02:00-09:00` window needs its own HRRR extraction plan.

Initial low-threshold artifacts trained from this branch:

- Obs-only model: `data/models/low_mvp_obs_2022_2025.joblib`
- HRRR model: `data/models/low_mvp_hrrr_v2_obs_2022_2025.joblib`

Initial validation metrics:

| model | train rows | validation rows | accuracy | brier | log loss | ROC AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| obs-only low threshold | 394524 | 131031 | 0.878 | 0.0857 | 0.2649 | 0.9476 |
| HRRR low threshold | 9918 | 3411 | 0.937 | 0.0452 | 0.2373 | 0.9805 |

Treat the HRRR metrics as a coverage-biased smoke result until early-morning HRRR coverage is expanded.

## Data Contract

Introduce a market-family distinction in model and research data:

- `HIGH_TEMP`: existing daily high market behavior.
- `LOW_TEMP`: new daily low market behavior.

Low-temperature training rows should include:

- `final_low_tmpf`: official station-date minimum observed temperature.
- `min_temp_so_far` or `low_so_far`: minimum observed temperature up to the snapshot.
- `hrrr_remaining_min`: minimum HRRR forecast temp over the remaining local-day window.
- `hrrr_current_temp`: current HRRR forecast temp at/as near the snapshot.
- `threshold`: candidate low threshold.
- `target`: for threshold models, `final_low_tmpf <= threshold`.
- bucket target: for bucket models, `lower_f <= final_low_tmpf <= upper_f`, with left/right tails handled consistently.

## Feature Plan

Mirror the same-day high feature builder with low-specific semantics:

- Daily aggregate: `min(tmpf)` as `final_low_tmpf`.
- Snapshot aggregate: `min(tmpf)` through the snapshot as `min_temp_so_far`.
- Threshold distances:
  - `threshold_minus_current_temp`
  - `threshold_minus_min_so_far`
  - consider adding `current_temp_minus_threshold` and `min_so_far_minus_threshold` if sign clarity helps diagnostics.
- HRRR distances:
  - `hrrr_remaining_min`
  - `hrrr_remaining_min_minus_threshold`
  - `hrrr_current_temp_minus_current_temp`
- Bucket distances:
  - `lower_minus_current_temp`
  - `upper_minus_current_temp`
  - `lower_minus_min_so_far`
  - `upper_minus_min_so_far`
  - `hrrr_remaining_min_minus_lower`
  - `hrrr_remaining_min_minus_upper`

Do not remove or reinterpret existing high-temperature columns. Low models should have separate artifact names and feature lists.

## Model Plan

Train low-specific model families instead of reusing high-temperature artifacts:

1. Low threshold classifier
   - Target: `final_low_tmpf <= threshold`
   - Purpose: direct mirror of the existing threshold classifier.
   - Candidate artifact: `low_mvp_hrrr_v2_obs_2022_2025.joblib`

2. Low dynamic bucket model
   - Target: bucket contains `final_low_tmpf`.
   - Purpose: generalizable bucket-level market pricing.
   - Candidate artifact: `low_dynamic_bucket_hrrr_v2_obs_2022_2025.joblib`

3. Low regression/distribution model
   - Target: `final_low_tmpf`.
   - Purpose: distributional fair values over buckets.
   - Candidate artifact: `low_regression_hrrr_v2_obs_2022_2025.joblib`

Start with the smallest set that gives useful diagnostics. Avoid training every high-temperature variant until the low target proves useful.

## Diagnostics

Before wiring into the research loop, compare low models against simple baselines:

- Bucket containing `min_temp_so_far`.
- Bucket containing `hrrr_remaining_min`.
- Prior-day low threshold baseline.
- Station and season calibration.
- Local-hour calibration, especially `02:00-09:00`.
- Bucket type: range vs `<=` left tail vs `>=` right tail.
- Side: BUY_YES vs BUY_NO after market-price integration.

Minimum acceptance criteria for research-loop integration:

- Target construction passes leakage checks.
- Feature columns are low-specific and do not accidentally train on `final_low_tmpf`.
- Calibration is not obviously inverted.
- The HRRR-min baseline is present in diagnostics.
- Performance is summarized by local-hour window, not just aggregate score.

## Research Loop Integration Plan

After model diagnostics are acceptable, add low-market collection and scoring behind an explicit market-family flag.

Collector defaults for low markets:

- Local entry window: `02:00-09:00`.
- Delay buckets can reuse the existing set initially: `instant`, `5m`, `10m`, `15m`, `30m`.
- Weather state should include both high and low fields, but low-market code should read low fields.

Initial policy rows should stay coarse:

- `low_pm_us12_consensus_hc_first`
- `low_pm_us12_consensus_hc_by_bucket_side_delay_first`
- optionally `low_min_so_far_first`

Avoid materializing entry-band, station-regime, early/mid/late, and other thin slices as separate policies. Store enough metadata in snapshots and policy rows so reports can group by:

- local hour
- entry price band
- station regime
- bucket type
- selected side
- delay bucket
- model family

## Implementation Notes

- Keep generated runtime state and bulky artifacts out of commits unless explicitly requested.
- The HRRR cache currently lives as an untracked artifact in the main checkout, not this worktree. Reference it by path or copy it locally only for experimentation, but do not commit it.
- Prefer adding low-specific modules/functions where that keeps semantics clear. Avoid over-generalizing high/low behavior until both paths are proven.
- If we later generalize, use names like `temperature_metric` or `market_family` rather than implicit sign flips.

## Open Questions

- Does Polymarket expose low-temperature market slugs/questions with consistent parseable phrasing and station mapping?
- Are low markets listed early enough and liquid enough during `02:00-09:00` local time?
- Should low-market “day” resolve over calendar local day, event-specified local date, or a Polymarket-specific window?
- Are official low settlements based on ASOS calendar-day min, or does Polymarket use a different source/window?
