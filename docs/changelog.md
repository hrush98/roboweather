# Changelog

Keep this file up to date for notable data, model, and trading changes.

## 2026-06-12

- Integrated current-stack promotion/candidate replay into `scripts/trading_retrospective_report.py` so the weekly review packet shows live sleeves, candidate sleeves, empirical PnL/R/R, fill/sample counts, and watch/review status in one run.
- Renamed weekly retrospective EV labels so entry-edge EV is explicitly treated as uncalibrated model-implied EV, while current-stack resolved replay PnL is presented as the empirical EV proxy until calibration improves.
- Enhanced `scripts/trading_retrospective_report.py` to separate uncalibrated model-implied intended EV from filled model-implied EV, estimate missed model-implied EV from unfilled exposure, classify execution outcomes as terminal rejects vs child FAK misses/resting TTL/order-construction issues, and support `--start-timestamp`/`--end-timestamp` for post-deployment reviews.
- Added `scripts/trading_retrospective_report.py` for manual Sunday/Monday weekly live retrospectives covering uncalibrated model-implied EV, empirical replay EV/PnL, realized PnL, fills vs intended notional, rejects by reason, current-stack replay comparison, and policy review/kill threshold flags.
- Added `docs/continuous-improvement-loop.md` and `docs/hypotheses/README.md` to formalize the hypothesis-to-replay-to-live-canary-to-gate workflow for recursive trading-system improvement.
- Raised the live global low-temperature MVP add-on default target from $25 to $50 after cap-aware portfolio replay showed the size-up remained positive behind the current live stack using current caps and recorded ask-sweep depth.
- Added `scripts/portfolio_promotion_report.py` as the cap-aware current-stack replay gate for live sizing and promotion decisions; it reports each sleeve's incremental risk/PnL/RR after current live plan order, risk caps, and recorded ask-sweep depth.
- Added `docs/roadmap-to-1000-ev-day.md` as the strategic scaling roadmap, including portfolio-promotion requirements, calibration/regime sizing, HRRR specialist overlay replay results, breadth expansion, and execution-attribution milestones.

## 2026-06-11

- Fixed live resting fallback accounting so a parent position is marked `PARTIAL`, not `FILLED`, when GTC child orders fill only part of the parent target; filled shares, cost, and average entry are now persisted from cumulative parent-level execution before returning from the fallback path.

## 2026-06-10

- Raised the live resting fallback TTL from 180 seconds to 360 seconds so accepted passive GTC ladder children have more time to fill before refresh/cancel.
- Changed live resting fallback parent summaries so GTC ladders that are posted, left unfilled for the TTL, and then cancelled are reported as `RESTING_TTL_EXPIRED` instead of the misleading `RESTING_LADDER_SKIPPED_AFTER_INSUFFICIENT_DEPTH`.
- Fixed depth-limited live entries so selected sweep depth caps only the initial FAK child, not the full intended risk target; filled sweep children now continue through retry and resting GTC ladder for the remaining risk-capped notional.
- Fixed live Polymarket v2 resting fallback submission so passive GTC/GTD child orders use explicit limit-order args with tick-resolved price and share size, avoiding invalid signed prices from market-order amount/price reconstruction.
- Routed live entries blocked only by insufficient ask-sweep depth directly into the resting $25 GTC ladder, preserving risk caps while avoiding no-op FAK rejects when passive fills are acceptable.
- Restricted the live `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first` add-on to the global low-temperature station allow-list after KLGA LOW_TEMP fills showed the MVP sleeve was missing the station filter used by the other global low sleeves.

## 2026-06-09

- Updated the Textual live dashboard for the US plus global live stack: Live exposure now aggregates all open positions across market dates instead of filtering to only the newest date, station/contract/strategy rows show market family, strategy rows show caps and decision windows, the Performance tab adds per-strategy recent/live/historical R/R and Sharpe columns, and the Config tab was trimmed to operational execution/sizing/risk settings.
- Raised main live sizing to $100 targets for the US no-tiny consensus core and global low-temperature consensus canary, with risk caps raised to max order/exact bucket-side $100, station/date/side $200, station/date $300, daily new risk $750, and total open risk $1,125. Resting fallback now ladders leftover notional into $25 penny-stepped GTC child orders under one shared 180-second TTL before refresh/cancel.
- Added a $25 live `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first` BUY_NO add-on after live-style portfolio replay showed positive incremental value behind the current stack; exposed `--global-low-mvp-notional-usd` for independent sizing.
- Deactivated the live US high-temperature 15m consensus overlay after cap-aware live-style replay showed the policy was negative incrementally behind the no-tiny consensus core; the active US high-temperature stack now keeps the canonical no-tiny consensus core plus the small moonshot sleeve.
- Deactivated live NGBoost BUY_YES after raw-snapshot replay showed weak overall and poor recent performance; raised the global low-temperature BUY_NO canary from $25 to $50 for the $0.05-$0.75 band and added a $5 `global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first` tiny-tail BUY_NO sleeve.
- Fixed live global low-temperature weather collection by routing non-US station IDs through the Celsius/global weather feature service. Recent live cycles were admitting EGLC/LFPB/RJTT/RKSI/VHHH/ZSPD markets but logging Unknown station errors from the US-only weather service before this change.

## 2026-06-08

- Added `scripts/live_policy_promotion_report.py` as the standardized raw-snapshot gatekeeper for US high-temperature live policy promotion. It replays from `prediction_snapshots`, ignores stale `research_policy_positions`, scores exact live-like opportunity scopes, and classifies policies as PROMOTE/CANARY/DEACTIVATE/RESEARCH_ONLY using predictive profitability/stability gates; fillability remains diagnostic-only because live execution uses FAK retries plus resting fallback.
- Replaced the live core dynamic-tuned BUY_NO policy with the bucket-consensus high-conviction 15m late `<= 0.50` replay winner (`pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first`) while preserving the existing core notional slot.
- Added a $25 live canary for global low-temperature `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`: BUY_NO-only, EGLC/LFPB/RJTT/RKSI/VHHH/ZSPD only, station-local `00:30-05:00`, `<= 0.75` entry cap, existing FAK/retry/GTC resting fallback execution, and no added depth gate. Live market discovery now runs with `market_scope=all` by default and admits markets by active strategy plan rather than a hard-coded US high-temp filter.

## 2026-06-07

- Extended `scripts/snapshot_opportunity_sweep.py` for raw `prediction_snapshots` policy replay: direct weather-outcome scoring fallback, HRRR-rich and METAR+HRRR-rich PM-active US12 model/consensus aliases, US high-temperature filtering, and a compact rolling 7-day/30-day/all-time summary mode for live-style high-conviction overlays.

## 2026-06-04

- Changed the research runner default to snapshot-only policy capture (`EVALUATE_POLICIES=0`) so TUI-started research loops collect broad `prediction_snapshots` without expanding `research_policy_positions`; set `EVALUATE_POLICIES=1` to materialize fixed policies.
- Added HRRR-rich and METAR+HRRR-rich PM-active US12 high-temperature model artifacts to the default US/all research-loop model set.
- Extended live/research HRRR inference feature assembly for the rich model families: remaining min/max, next-3h temperature summaries, dewpoint, relative humidity, wind/gust, cloud cover, shortwave, and forecast count now flow into model feature rows and snapshot raw JSON.

## 2026-06-03

- Added METAR-rich ASOS enrichment fields to dataset construction, model feature rows, and live/research fair-value feature assembly: relative humidity, wet-bulb approximation, pressure, pressure tendency, visibility, hourly precipitation, altimeter, feels-like temperature, min-so-far, range-so-far, and threshold/bucket distances from min-so-far.
- Retrained PM-active US12 high-temperature model families for METAR-rich, HRRR-rich, and combined METAR+HRRR-rich feature sets; saved model artifacts and report directories under `data/models` and `data/reports`.
- Added `scripts/model_registry.py`, `data/reports/model_registry.csv`, and `docs/model-performance-log.md` as the canonical model-performance registry/log, including a focused PM-active US12 enrichment comparison.

- Added the US high-temperature HRRR v2 model family to the default research-loop model set for MARKET_SCOPE=us and MARKET_SCOPE=all: dynamic bucket, tuned dynamic bucket, CatBoost bucket, MVP, high regression, and NGBoost. This activates HRRR research snapshot collection while leaving live execution on the existing obs-family strategy stack until HRRR replay is reviewed.

## 2026-05-28

- Added `docs/live-trading-journal.md` as the current live trading state and rationale tracker.
- Linked the journal from `AGENTS.md` so future agents update it when live strategy, sizing, risk, execution, or material trading assumptions change.

## 2026-05-06

- Added backfilled changelog and a modeling/trading decision reference doc.
- Rebuilt same-day datasets so `temp_change_1h` and `temp_change_3h` use timestamp-based lookbacks instead of row shifts.
- Retrained the dynamic bucket classifier, regression residual model, and NGBoost baseline on the rebuilt dataset.
- Fixed synthetic bucket semantics so bounded buckets are half-open and no longer overlap at integer boundaries.
- Added bucket-distribution experiment reports and stratified window comparisons.

## 2026-05-05

- Added the dynamic bucket candidate model and grouped ladder evaluation.
- Added the headless research collector and training-data diagnostics.
- Added HRRR-enriched model reporting and live scanner support improvements.

## 2026-05-04

- Added the baseline paper trader, station/date grouping, and initial same-day threshold model work.

## Notes

- Treat this as the running history for meaningful user-facing changes in the repo.
- Prefer adding a short entry here when a change affects modeling, trading, data generation, or operator workflow.
