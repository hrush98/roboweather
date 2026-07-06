# 2026-06-18 US High-Temp Consensus Candidate Policies

## Status

Research

## Hypothesis

Several broad US high-temperature consensus policy families have strong positive EV at their recorded snapshot entry prices. They may be live candidates if cap-aware replay, calibration, and live execution tests show the edge survives actual fill constraints.

## Expected Mechanism

The strongest candidates require agreement across enriched model families or across bucket-classifier and MVP-style models. This should filter noisy single-model bucket picks and leave high-conviction opportunities where the market ask is materially below the model-implied fair.

## Scope

- Market family: `HIGH_TEMP`
- Stations/regimes: PM-active US high-temperature stations; broad station scope until regime-specific replay says otherwise.
- Side: mixed `BUY_YES` / `BUY_NO`
- Entry band: `0.00-0.50`
- Local window: unrestricted in this initial screen.
- Model/source: consensus families from raw `prediction_snapshots`
- Policy/sleeve names:
  - `metar_hrrr_rich_catboost_mvp_hc_entry_00_50_by_bucket_side_delay_first`
  - `hrrr_v2_three_model_consensus_hc_entry_00_50_by_bucket_side_delay_first`
  - `hrrr_v2_bucket_consensus_hc_entry_00_50_by_bucket_side_delay_first`
  - `pm_us12_catboost_mvp_hc_entry_00_50_by_bucket_side_delay_first`

## Evidence Required

- Replay gate: cap-aware incremental replay behind the current live stack using current risk caps and recorded depth.
- Recent-window requirement: positive in both last-7 and last-30 resolved windows, or a clearly explained regime split.
- Minimum resolved sample: at least 30 independent resolved rows before full promotion; lower samples can only justify a canary.
- Fillability/depth requirement: demonstrate executable size at or near recorded entry. Do not promote from entry-price paper PnL alone.
- Live canary requirement: if promoted, use a small canary with entry-anchored execution, no broad chasing above recorded entry, and whole-chain review after settlement.

## Current Evidence

Commands run on 2026-06-18 against:

- Research DB: `/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
- Snapshot coverage: fresh through `2026-06-18T17:25Z`
- Outcomes resolved through market date `2026-06-17`

Primary sweep:

```bash
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/snapshot_opportunity_sweep.py \
  --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite \
  --market-family HIGH_TEMP \
  --us-high-temp-only \
  --rolling-summary \
  --min-policy-n 20 \
  --top-n 12
```

Entry-price replay scoring uses the selected ask at snapshot time:

- Correct position: `1 - entry`
- Wrong position: `-entry`

| Policy | Resolved | Win rate | Avg entry | Entry risk | Entry PnL | Entry R/R | Avg $50 sweep fillable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `metar_hrrr_rich_catboost_mvp_hc_entry_00_50_by_bucket_side_delay_first` | 22 | 86.4% | 0.108 | 2.387 | 16.613 | 6.960 | $25.03 |
| `hrrr_v2_three_model_consensus_hc_entry_00_50_by_bucket_side_delay_first` | 20 | 85.0% | 0.193 | 3.859 | 13.141 | 3.405 | $16.10 |
| `hrrr_v2_bucket_consensus_hc_entry_00_50_by_bucket_side_delay_first` | 30 | 66.7% | 0.182 | 5.474 | 14.526 | 2.654 | $24.93 |
| `pm_us12_catboost_mvp_hc_entry_00_50_by_bucket_side_delay_first` | 37 | 64.9% | 0.184 | 6.801 | 17.199 | 2.529 | $24.68 |

Interpretation: these policies are profitable at the registered entry price if fills are available there. The common `EXECUTION_WEAK` flag matters: average recorded fillability for a $50 sweep was only about $16-$25, so execution capacity and adverse selection are the central unknowns.

Related checks from the same review:

- `scripts/live_policy_promotion_report.py --limit 20` classified the live US consensus no-tiny family and several HRRR/METAR+HRRR dynamic candidates as `CANARY`, not full promotion.
- `scripts/calibrated_candidate_replay.py --grid` found the best calibrated selected-row variant at edge `>= 0.20`: 28 rows, 42.9% win rate, 1.000 R/R. Candidate-universe calibration at edge `>= 0.20`: 75 rows, 40.0% win rate, 0.626 R/R.
- `scripts/trading_retrospective_report.py --start-date 2026-06-11` showed recent live execution remains weak despite positive model-implied EV, so promotion decisions must reconcile replay with fills and settlement.

## Risks And Failure Modes

- Thin books: the signal may exist only for small notional at the recorded ask.
- Adverse fill selection: fills may be the subset where the market is correctly moving against the model.
- Slippage/chasing: paying above recorded entry can erase the edge.
- Overlap: candidates may duplicate the current US consensus no-tiny sleeve rather than add independent capacity.
- Calibration drift: raw fairs are overconfident; calibrated candidate replay is materially less explosive than raw entry-price replay.
- Sample size: some strongest R/R rows have only 20-22 resolved observations.

## Kill Conditions

- Cap-aware incremental replay behind the current stack is non-positive after recorded depth and current caps.
- Whole-chain live canary review shows filled-at-actual R/R <= 0 after at least 10 resolved fills.
- Filled subset replay materially underperforms unfilled subset replay, indicating adverse selection.
- The policy only wins through sub-$10 effective capacity that cannot scale under live order constraints.
- Calibrated edge-filtered replay falls below 0.25 R/R with at least 30 resolved observations.

## Gates Added Or Required

- Required: add these four candidate families to a cap-aware incremental replay report or extend `scripts/portfolio_promotion_report.py` to include them as named candidate sleeves.
- Required: compare entry-price replay, recorded-depth replay, and actual-live execution if canaried.
- Required: calibration-aware replay with edge floors, starting with calibrated edge `>= 0.20`.
- Required: overlap report versus current live US consensus no-tiny and HRRR inland disagreement sleeves.

## Review Trigger

Review after the next resolved settlement batch, or before any new US high-temperature live promotion. Do not promote before a cap-aware current-stack replay has been run.

## Decision Log

- 2026-06-18: Initial research record created. Entry-price replay is strong, but all four candidates remain research-only because recorded sweep depth is weak and recent live execution reports show replay-to-fill degradation.
