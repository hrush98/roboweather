---
id: T0018
title: Build remaining-heating distribution
status: PARKED
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: 5a0197556aec3922883ea411cc344dab2b4d415fff3df40945b785c161766014
---

# T0018 Build remaining-heating distribution

## Question

Does a peak-passed plus conditional-additional-heating distribution outperform the frozen absolute/bucket baseline under the F0B chronological identical-coverage contract?

## Current Answer

F3 is not settled. The versioned hurdle/ordinal remaining-heating model is coherent and beats the frozen HRRR-rich baseline on the 2025 historical holdout, but it fails the untouched 2026 complete-ladder cohort and therefore has no accepted information edge.

## Evidence

- weather_trader/forecasting/remaining_heating.py implements remaining_heating_hurdle_ordinal_v1; tests/test_remaining_heating.py plus tests/test_forecast_evaluation.py pass 9/9.
- Frozen 2025 holdout: 4,364 rows/364 dates; candidate log loss 0.86974 and RPS 0.33503 versus HRRR-rich 0.92872 and 0.45220.
- Untouched 2026 research cohort at /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite: 541 complete-ladder rows/55 dates through 2026-08-12; candidate log loss 1.79320 and RPS 0.78288 versus HRRR-rich 1.56085/0.78135 and market 1.42646/0.68666.
- Season-matched realized additional heating shifted from mean 0.7548F on 840 June-August 2025 rows to 1.4229F on 558 June-August 2026 rows; this is diagnostic, not permission to tune on forward outcomes.

## Next Action

Build a reproducible F3 report that compares historical and forward cohort feature availability, observation timing, high-so-far provenance, and station/date delta distributions, then predeclare any new model version before evaluating it on later untouched dates.

## Closure Output

Coherent remaining-heating model, chronological ablation report, and acceptance or rejection verdict.
