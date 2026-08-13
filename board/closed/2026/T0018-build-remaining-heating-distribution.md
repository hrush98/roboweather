---
id: T0018
title: Build remaining-heating distribution
status: CLOSED
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: bb5fae7316d60ae35f6e86c0289dd8450f730fae6b7508895500b5ae39c054b4
closed: 2026-08-13
---

# T0018 Build remaining-heating distribution

## Question

Does a peak-passed plus conditional-additional-heating distribution outperform the frozen absolute/bucket baseline under the F0B chronological identical-coverage contract?

## Outcome

Accepted for the information pillar and Price Sheet V2 research: exact-cutoff remaining_heating_hurdle_multinomial_exact_cutoff_v3 plus conditioned HRRR improved corrected historical, untouched 22-date forward, and recent 14-date log loss and RPS. Market-relative point estimates improved but intervals cross zero; no funded or tradable-edge claim.

## Evidence

- 15 focused tests passed; compileall passed; generated result verdict ACCEPT_F3_FOR_PRICE_SHEET_V2_RESEARCH with 17/17 checks, exact-cutoff post-cutoff rows 0/4,364, and untouched 22-date deltas logloss -0.17879 and RPS -0.29365 with both clustered intervals below zero.

## Durable Output

weather_trader/forecasting/remaining_heating.py; scripts/forecast_remaining_heating_report.py; reports/forecast-edge/f3-current/report.md; docs/implementation/forecast-edge-data-program.md; learning/L0013-a-time-bucket-is-not-a-decision-cutoff.md; commit a95d82b
