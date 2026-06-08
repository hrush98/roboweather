# Changelog

Keep this file up to date for notable data, model, and trading changes.

## 2026-06-08

- Replaced the live core dynamic-tuned BUY_NO policy with the bucket-consensus high-conviction 15m late `<= 0.50` replay winner (`pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first`) while preserving the existing core notional slot.

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
