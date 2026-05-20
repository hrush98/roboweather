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

## Terminology and DB Labels

Use these names consistently when discussing research results:

| Concept | Meaning | Primary DB labels |
| --- | --- | --- |
| Model | One trained forecasting artifact that emits probabilities for a station/date market ladder. Examples include `dynamic_bucket_pm_active_us12_obs_2022_2025`, `mvp_pm_active_us12_obs_2022_2025`, and `catboost_bucket_pm_active_us12_obs_2022_2025`. | `prediction_snapshots.model_name`; copied to `research_policy_positions.model_group` for single-model policies |
| Model family | A comparable feature/training universe for a set of models. Current broad high-temperature families are `obs` and `hrrr_v2`. | Encoded in `policy_name` prefixes such as `broad_obs_*` and `broad_hrrr_v2_*`; defined in `weather_trader/research/policies.py` as `MODEL_FAMILIES` |
| Consensus group | A fixed combination of models. A consensus row exists only when every model in the group independently selects the same station, market date, observation-delay bucket, strategy bucket, side, market id, and bucket. | `raw_json.source_prediction_snapshot_ids`; `raw_json.raw_policy.model_group`; `research_policy_positions.model_group` is `consensus` for these rows |
| Strategy | The rule used to choose a candidate from a model's priced ladder. This is not a model and not a policy. Current broad strategies are `HIGH_CONVICTION`, `TAIL`, and `BEST_BUCKET`; `MAX_SO_FAR` is rule-based. | `prediction_snapshots.strategy_bucket`; `research_policy_positions.strategy_bucket` |
| Opportunity | The selected tradable unit: station, market date, market family, selected bucket, selected side, and often observation-delay bucket. This is the level used for overlap and duplicate-exposure analysis. | `station`, `market_date`, `market_family`, `selected_bucket`, `selected_side`, `obs_delay_bucket`, `selected_market_id` |
| Policy | A named research ledger rule that filters and de-duplicates candidates into hypothetical positions. Policies are analysis objects, not forecasting models. | `research_policy_positions.policy_name`; full policy settings are nested under `raw_json.raw_policy.policy` |
| Policy scope | The uniqueness key that decides how many rows one policy may insert. `station_date` means first eligible row per station/date/family. `station_date_bucket_side` also separates bucket and side. `station_date_bucket_side_obs_delay` also separates observation-delay bucket. | `research_policy_positions.scope_key`; configured by `raw_json.raw_policy.policy.uniqueness_key_mode` for newer rows |
| Gates | Optional filters applied by a policy, such as model, consensus group, strategy, observation delay, entry-price band, fair-probability band, edge band, bucket type, station allow/exclude set, or decision-time window. | Policy columns are materialized as position fields where relevant; configured under `raw_json.raw_policy.policy` |

The `broad_*` high-temperature policies are intended as broad comparison policies across model family, model or consensus group, and strategy. They currently leave edge, entry-price, fair-probability, observation-delay, station, bucket-type, and decision-time gates open. Their names follow:

```
broad_{model_family}_{model_alias_or_consensus_group}_{strategy}_first
```

Examples:

- `broad_obs_dynamic_tuned_high_conviction_first`: one `obs` family model, one strategy.
- `broad_obs_dynamic_tuned_mvp_high_conviction_first`: consensus between the `obs` dynamic-tuned and MVP models, one strategy.
- `broad_hrrr_v2_catboost_mvp_best_bucket_first`: consensus between the `hrrr_v2` catboost and MVP models, one strategy.

Policy scopes should be chosen by analysis purpose:

- `station_date` is the clean scorecard layer. It keeps one first eligible position per station/date/family/policy, which makes model, consensus-group, and strategy comparisons less sensitive to correlated multi-bucket fanout.
- `station_date_bucket_side_obs_delay` is the preferred opportunity-capture research layer. It keeps one first eligible position per station/date/family/bucket/side/obs-delay/policy, which preserves enough breadth to later break results down by bucket, side, observation timing, model, consensus group, and strategy without repeatedly inserting the same position every loop.
- `station_date_bucket_side` is useful when observation timing should be collapsed but bucket and side still need to be preserved.

The generated `broad_*` specs currently use the default `station_date` scope. For deeper research on what is actually trading best, add or replay a parallel diagnostic family with `uniqueness_key_mode="station_date_bucket_side_obs_delay"` rather than trying to infer all opportunity-level behavior from the scorecard layer.

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
