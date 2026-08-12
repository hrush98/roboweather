---
id: T0014
title: Freeze minimal forecast baseline
status: CLOSED
pillar: information
priority: high
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: a2bd2a2949c3c392671b6eecb118d568599d5f257cf1e6cd24d6cf35e4e9bb50
closed: 2026-08-12
---

# T0014 Freeze minimal forecast baseline

## Question

What is the smallest genuinely distinct current forecast baseline, and can it be scored without outcome-conditioned ladders or correlated-row inflation?

## Outcome

F0B froze an outcome-independent, weather-date-aware full-distribution evaluation contract and reduced 18 current PM-active artifact names to three outcome-blind control roles; this repairs baseline validity but does not prove market-relative information edge.

## Evidence

- 489 tests passed; the 18-artifact report scored 4,364 identical station/date rows across 364 weather dates and two complete runs produced byte-identical result, pairwise, pruning, and Markdown artifacts.

## Durable Output

weather_trader/forecasting/evaluation.py; scripts/forecast_edge_report.py; tests/test_forecast_evaluation.py; docs/implementation/forecast-edge-data-program.md; docs/hypotheses/2026-07-17-station-specific-forecast-edge.md; docs/current-trading-system-audit.md; docs/changelog.md; learning/L0009-evaluation-geometry-can-leak-the-answer.md; generated uncommitted reports/forecast-edge/f0b-current/
