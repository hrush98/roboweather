# Changelog

Keep this file up to date for notable data, model, and trading changes.

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
