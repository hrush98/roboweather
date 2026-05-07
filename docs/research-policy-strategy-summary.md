# Research Policy Strategy Summary

Date discussed: 2026-05-07

## Current Research Setup

The live research loop stores model snapshots and market books, but it does not submit trades. The May 7 run used two active same-day models:

- `dynamic_bucket_obs_2022_2025`: dynamic bucket classifier that prices each bucket directly and normalizes across the ladder.
- `mvp_obs_corrected`: corrected threshold classifier that derives bucket probabilities from threshold-crossing probabilities.

Other trained artifacts exist in `data/models`, but were not active in the May 7 research loop:

- `mvp.joblib`
- `mvp_hrrr_sample.joblib`
- `mvp_obs_2022_2025.joblib`
- `mvp_obs_2022_2025_report_refresh.joblib`
- `mvp_next_day_obs_2022_2025.joblib`
- `high_regression_obs_2022_2025.joblib`
- `ngboost_normal_obs_2022_2025.joblib`

The current loop stores each model independently in `prediction_snapshots`. It does not blend model probabilities live. Consensus analysis means post-processing rows where both active models independently chose the same station/date/delay/strategy/side/bucket.

## Strategy Definitions

`BEST_BUCKET` chooses the bucket with the highest model `fair_yes` for a station/date. It is a `BUY_YES` strategy and can act at lower probability thresholds.

`HIGH_CONVICTION` chooses the best YES or NO side by edge, but requires stronger probability and edge. It can produce either `BUY_YES` or `BUY_NO`.

`MAX_SO_FAR` chooses the bucket containing the already-observed high so far. It should be tracked separately because it is a rule-based strategy, not normal model alpha.

## Policies Added

The policy ledger tracks hypothetical live entries with one insert per policy scope. These policies are now registered:

- `consensus_hc_first`
- `consensus_hc_10m_first`
- `consensus_hc_15m_first`
- `consensus_best_15m_first`
- `consensus_per_strategy_first`
- `mvp_hc_first`
- `mvp_hc_10m_first`
- `mvp_hc_15m_first`
- `mvp_best_15m_first`
- `dynamic_hc_first`
- `dynamic_hc_10m_first`
- `dynamic_hc_15m_first`
- `dynamic_best_15m_first`
- `max_so_far_first`
- `max_so_far_10m_first`
- `max_so_far_15m_first`

Consensus policies require both active models to agree on:

- station
- market date
- obs delay bucket
- strategy bucket
- selected side
- selected market/bucket

For consensus entries, the stored edge is the average of the two model edges, and both source prediction snapshot IDs are retained.

## May 7 Exploratory Results

These were provisional live marks from the research DB, not official resolved outcomes. The provisional rule was:

- selected-side live bid `>= 0.90`: effectively won
- selected-side live bid `<= 0.10`: effectively lost
- no bid with selected-side ask `<= 0.10`: treated as loss
- otherwise open and marked to the live bid

Using latest stored books around `2026-05-07T20:51Z`, the strongest exploratory policies were:

| Policy | Picks | Wins | Losses | Open | $1 P&L | ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `consensus_hc_10m_first` | 4 | 0 | 0 | 4 | +0.807 | +20.2% |
| `consensus_hc_first` | 5 | 1 | 0 | 4 | +0.809 | +16.2% |

Weaker policies under the same mark rule included:

| Policy | Picks | Wins | Losses | Open | $1 P&L | ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `consensus_hc_15m_first` | 4 | 0 | 0 | 4 | -0.252 | -6.3% |
| `dynamic_hc_first` | 5 | 0 | 1 | 4 | -0.507 | -10.1% |
| `mvp_hc_first` | 7 | 1 | 2 | 4 | -1.923 | -27.5% |
| `consensus_per_strategy_first` | 10 | 1 | 4 | 5 | -2.191 | -21.9% |

Earlier in the day, broader one-share and mark-to-bid views looked better for `mvp_obs_corrected` and for looser consensus policies. Once no-bid/near-zero-ask markets were counted as losses, consensus high-conviction policies were the cleanest.

## Caveats

The May 7 sample is tiny and should not be treated as statistically meaningful.

Several analyses were exploratory and could be hindsight-biased unless a policy is defined as "first eligible signal" or tied to a fixed obs delay bucket. The added policy ledger is intended to reduce this problem by inserting hypothetical positions as the research loop runs.

Official outcomes were not available during the analysis. Final scoring should use `station_date_outcomes` and `prediction_results` once resolved.

The research loop crashed on a Polymarket book `404`. The book client was updated to skip unavailable individual books instead of crashing the whole loop.

## Recommended Next Run

For the next live research run, collect all registered policies for the full day and compare:

- provisional marks during the day
- final settled outcomes after station highs resolve
- 1-share P&L
- $1-per-position P&L
- policy-level ROI
- station/date exposure conflicts

The primary policies to watch are `consensus_hc_first` and `consensus_hc_10m_first`, with the rest kept for comparison.
