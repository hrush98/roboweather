---
id: T0016
title: Build causal forecast source catalog
status: CLOSED
pillar: information
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: 27d4092673cee21729cc515c0ee9c53378127bde90f5a3fe3d3c9989a0bd9b32
closed: 2026-08-12
---

# T0016 Build causal forecast source catalog

## Question

Can the scoped WeatherNext, NBM or GLMP, HRRR or RRFS, and observation sources be recorded with immutable source vintages and replayed only after their actual causal availability?

## Outcome

F1 established a versioned causal forecast-source collection and replay substrate, not information edge: NBM v5, HRRR v4, and IEM routine/special artifacts were captured and decoded under first-observed availability; WeatherNext requires provider ingestion_time and approved access; RRFS remains fail-closed until an operational contract is frozen.

## Evidence

- Full repository pytest passed (469 tests); focused forecast/source/HRRR suite passed 28 tests; bounded host capture persisted 3 NBM, 3 HRRR, and 1 IEM artifacts; pygrib decoded intended fields; F2 is now READY.

## Durable Output

weather_trader/forecasting/source_catalog.py; scripts/forecast_source_catalog.py; tests/test_forecast_source_catalog.py; docs/implementation/forecast-edge-data-program.md; docs/hypotheses/2026-07-17-station-specific-forecast-edge.md; docs/current-trading-system-audit.md; docs/execution-rebuild-roadmap.md; docs/changelog.md; AGENTS.md; learning/L0011-a-forecast-has-more-than-one-clock.md; generated uncommitted reports/forecast-edge/f1-source-catalog-current/
