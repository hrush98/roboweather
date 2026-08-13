---
id: T0017
title: Benchmark NBM and WeatherNext forecast skill
status: CLOSED
pillar: information
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-13
facts_fingerprint: e00c823be792bc3df07baea649ad431497b94e690e2986fa70c37c2c8d5b3ebe
closed: 2026-08-13
---

# T0017 Benchmark NBM and WeatherNext forecast skill

## Question

Does causally available NBM or WeatherNext 2 improve held-out high-temperature probability forecasts beyond the frozen minimal HRRR-rich baseline and contemporaneous market on identical rows?

## Outcome

F2 rejects the exact nbm_v5_archive_cycle_plus_2h_v1 transform: on 541 identical D0 rows it failed the joint held-out log-loss/RPS and market-relative gates; WeatherNext remains unavailable rather than rejected, so the information pillar remains unproven.

## Evidence

- 28 focused forecast tests passed; compileall passed; corrected production benchmark completed on 541 rows, 55 weather dates, 10 stations, 100% NBM D0 coverage, and 22 untouched holdout dates with verdict REJECT_NBM_FOR_F2_WEATHERNEXT_UNAVAILABLE.

## Durable Output

weather_trader/forecasting/nbm_benchmark.py; scripts/forecast_source_benchmark.py; tests/test_nbm_benchmark.py; docs/implementation/forecast-edge-data-program.md; docs/hypotheses/2026-07-17-station-specific-forecast-edge.md; docs/current-trading-system-audit.md; docs/changelog.md; learning/L0012-discrete-forecast-bins-inherit-the-label-s-rounding-semantics.md; generated uncommitted reports/forecast-edge/f2-current/
