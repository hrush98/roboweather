# Research Policy Expansion - 2026-05-13

## Purpose

This phase adds a conservative research layer for testing whether the active `pm_us12_*` signals survive stricter controls. It does not change execution behavior or the SQLite schema. All new diagnostics are derived from existing snapshot, outcome, and research-policy-position tables.

## New Policy Controls

`ResearchPolicySpec` now supports optional report/research filters for:

- station allow and exclude sets
- entry-price min/max
- fair-probability min/max
- edge min/max
- bucket type: `range`, `tail`, or `missing`
- local decision-time windows
- uniqueness key modes:
  - `station_date`
  - `station_date_bucket_side`
  - `station_date_bucket_side_obs_delay`

The uniqueness modes are stored through `scope_key`, so repeated evaluation remains idempotent through the existing `unique(policy_name, station, market_date, scope_key)` constraint.

## New Experimental Policies

The first variants are deliberately limited to the active `pm_us12` consensus high-conviction family:

| Policy | Experiment | Intended Question |
|---|---|---|
| `pm_us12_consensus_hc_15m_entry_25_75_first` | Consensus HC at 15m, only entries from 0.25 to 0.75 | Are results still robust after excluding tiny long-shot and near-certain entries? |
| `pm_us12_consensus_hc_15m_no_tiny_first` | Consensus HC at 15m, entry at least 0.05 | Are sub-5-cent entries adding noise or false confidence? |
| `pm_us12_consensus_hc_late_first` | Consensus HC, local decision time 12:00-15:00 | Do later-day signals improve once more observations are known? |
| `pm_us12_consensus_hc_early_first` | Consensus HC, local decision time 10:00-12:00 | Do early signals carry enough edge before the day is more resolved? |

These are research policies only. They do not alter live trading, sizing, risk checks, market discovery, or model inference.

## Banded Entry Experiments

The next layer breaks entry price into fixed bands so the research can identify where the edge is actually monetizable:

- `entry_00_10`: 0.00 to 0.10
- `entry_10_25`: 0.10 to 0.25
- `entry_25_50`: 0.25 to 0.50
- `entry_50_75`: 0.50 to 0.75
- `entry_75_100`: 0.75 to 1.00

These bands are registered for the three most promising or useful comparator families:

| Family | Why Included |
|---|---|
| `pm_us12_consensus_hc_15m` | Core candidate: consensus, high conviction, 15m observation delay. |
| `pm_us12_consensus_hc_late` | Tests whether the strong late-window signal survives entry-band controls. |
| `pm_us12_dynamic_hc_15m` | Best active single-model comparator for the consensus 15m family. |

Example generated policy names:

- `pm_us12_consensus_hc_15m_entry_25_50_first`
- `pm_us12_consensus_hc_late_entry_25_50_first`
- `pm_us12_dynamic_hc_15m_entry_25_50_first`

The purpose is not to create many deployable candidates immediately. The purpose is to collect outcomes in parallel because resolved samples arrive slowly. If adjacent bands perform well within the same family, a later phase can add combined-band candidates such as `entry_10_50` or `entry_25_75`.

Current resolved data suggests:

- `0.25-0.50` is the cleanest band so far.
- `0.10-0.25` is promising but has fewer observations.
- `0.50-0.75` has much weaker economics despite a decent hit rate.
- `0.00-0.05` has been poor and should not be mixed into the main strategy.

Long-shot entries should be treated as a separate convex-sleeve experiment, not as part of the core high-conviction policy. In the current official resolved data, sub-10-cent entries are weak overall; the only positive-looking sub-10 result is concentrated in `max_so_far_15m_first` and appears driven by one winner. If tested further, the cleaner long-shot experiment is likely `0.05-0.10`, 15m only, and max-so-far only.

## Station Constants

Station regime and stress groups are static code constants, not DB state. This keeps them explicit and easy to revise:

- coastal: `KBOS`, `KDCA`, `KHOU`, `KLAX`, `KLGA`, `KMIA`, `KSEA`, `KSFO`
- inland: `KATL`, `KDEN`, `KDFW`, `KORD`
- manual: currently used for stations outside the two primary groups
- stress exclude: intentionally empty until the report evidence is strong enough to justify a named exclusion

## Reporting Additions

`policy_leaderboard.py` continues to score positions from `station_date_outcomes.final_high_tmpf`, independent of PM settlement. It now also reports:

- raw positions versus unique opportunities
- duplicate opportunity exposure
- effective independent opportunity count
- policy contribution by absolute return share
- overlap-adjusted R/R and Sharpe
- station-regime diagnostics with LOW_N gates under 20 resolved positions
- calibration by fair band, edge band, entry band, side, station, and obs-delay bucket
- top overlapping policy pairs

`status_report.py` now surfaces the same exposure layer in the terminal monitor, so the daily operational view can show whether apparent breadth is real or mostly duplicated policy exposure.

## How To Read The Experiments

Treat the new policy variants as falsification tests:

- If the 0.25-0.75 entry policy remains strong, the signal is less dependent on extreme odds.
- If the 0.25-0.50 band remains stronger than 0.50-0.75, the useful edge is probably in moderate-priced entries rather than all non-extreme entries.
- If the same entry band wins across consensus and dynamic families, the band effect is more likely structural.
- If the no-tiny policy materially improves calibration, tiny entries are probably inflating noisy exposure.
- If late beats early after enough samples, observation maturity is likely important.
- If early remains competitive, the model may be finding pre-noon structure worth preserving.
- If overlap-adjusted returns collapse, multiple policy names are mostly restating the same opportunity.

LOW_N gates should be respected. Under 20 resolved station-level observations, station effects are report hints, not conclusions. Under roughly 30 resolved policy observations, policy confidence should remain weak even if R/R looks attractive.
