# International Low-Temp Policy Sweep - 2026-06-01

Source DB: `/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`

Scope: international stations, `LOW_TEMP`, market dates `2026-05-29` through `2026-05-31`, scored from `prediction_results` after all-scope resolver backfill.

Resolved low-temp coverage is still small: 577 model snapshot rows, 662 rows including consensus, 6 stations, 2 market dates. Treat this as candidate discovery, not promotion evidence.

## Baseline

| Mode | Rows | WR | R/R | Sharpe | Avg Entry | Avg Edge | Avg Fair | Avg Sweep 50 | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| all snapshots | 662 | 0.310 | -0.134 | -0.074 | 0.357 | 0.154 | 0.512 | 32.6 | too noisy |
| first opportunity | 331 | 0.344 | 0.311 | 0.143 | 0.263 | 0.187 | 0.450 | 28.6 | promising but execution thin |
| station-date first | 94 | 0.287 | 0.296 | 0.134 | 0.222 | 0.212 | 0.434 | 29.8 | lower duplication, still positive |
| edge improve 50 | 350 | 0.346 | 0.265 | 0.125 | 0.273 | 0.186 | 0.460 | 29.3 | similar to first opportunity |

## Best Tradeable Slice

The strongest non-moonshot pattern is `HIGH_CONVICTION`, `BUY_NO`, entry `0.50-0.75`.

| Candidate | Rows | WR | R/R | Sharpe | Avg Entry | Avg Edge | Avg Fair | Avg Sweep 50 | Scope |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| global_low_dynamic_hc_buy_no_50_75_bucket_side_delay | 10 | 1.000 | 0.471 | 6.452 | 0.680 | 0.164 | 0.844 | n/a | station/date/bucket/side/delay |
| global_low_consensus_hc_buy_no_50_75_bucket_side_delay | 10 | 1.000 | 0.431 | 7.458 | 0.699 | 0.217 | 0.916 | 34.9 | station/date/bucket/side/delay |
| global_low_mvp_hc_buy_no_50_75_bucket_side_delay | 18 | 0.944 | 0.421 | 1.136 | 0.664 | 0.199 | 0.864 | 38.6 | station/date/bucket/side/delay |
| global_low_mvp_hc_buy_no_50_75_station_date | 6 | 1.000 | 0.449 | 6.041 | 0.690 | 0.199 | 0.889 | 30.7 | one per station/date |
| global_low_consensus_hc_buy_no_50_75_station_date | 4 | 1.000 | 0.394 | 65.241 | 0.718 | 0.220 | 0.938 | 34.9 | one per station/date |

Per-station/date performance for the broader MVP slice:

| Station Date | Rows | WR | R/R | Avg Entry |
|---|---:|---:|---:|---:|
| EGLC 2026-05-30 | 3 | 1.000 | 0.429 | 0.700 |
| LFPB 2026-05-31 | 2 | 1.000 | 0.389 | 0.720 |
| RJTT 2026-05-31 | 2 | 1.000 | 0.562 | 0.640 |
| RKSI 2026-05-31 | 4 | 1.000 | 0.527 | 0.655 |
| VHHH 2026-05-31 | 5 | 0.800 | 0.316 | 0.608 |
| ZSPD 2026-05-31 | 2 | 1.000 | 0.351 | 0.740 |

## Moonshot-Like Slices

Tiny-price `BUY_YES` low-temp slices produce very high R/R when they hit, but they are less suitable as a core policy because the hit rate is low and results are dominated by very few wins.

| Candidate | Rows | WR | R/R | Sharpe | Avg Entry | Avg Edge | Avg Fair | Avg Sweep 50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| consensus global low max-so-far BUY_YES entry 0.00-0.05 | 19 | 0.105 | 8.615 | 0.316 | 0.011 | 0.280 | 0.291 | 35.7 |
| consensus global low tail, 5m, entry 0.00-0.10 | 5 | 0.400 | 16.857 | 0.800 | 0.022 | 0.096 | 0.119 | n/a |

## Recommendation

Do not promote a global low-temp policy to live yet. The resolved window is only two low-temp market dates.

Add these to research/paper tracking first:

1. `global_low_mvp_hc_buy_no_50_75_by_bucket_side_delay_first`
2. `global_low_dynamic_hc_buy_no_50_75_by_bucket_side_delay_first`
3. `global_low_consensus_hc_buy_no_50_75_by_bucket_side_delay_first`

Policy shape:

- market family: `LOW_TEMP`
- station scope: international stations only
- model/model-group:
  - `mvp_international_celsius_low_obs_2022_2025`
  - `dynamic_bucket_international_celsius_low_obs_2022_2025`
  - `global_low_dynamic_mvp`
- strategy: `HIGH_CONVICTION`
- side: `BUY_NO`
- entry: `0.50 <= entry < 0.75`
- uniqueness: `station_date_bucket_side_obs_delay`
- paper/live sizing: keep small until at least 20-30 resolved trades across more than 2 market dates.

Implementation note: current `ResearchPolicySpec` does not have a selected-side filter. To materialize these exactly as research policies, add a `selected_side` field to policy specs and filter it in `_passes_policy_filters`.
