---
id: T0009
title: Evaluate forecast model edge
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: 231a14f1094fcfd90b1afd783fefb9e5c4f8ee89b9fcb4e163ec37e24d6a9a8f
closed: 2026-08-12
---

# T0009 Evaluate forecast model edge

## Question

Is the current forecast model stack and training data adequate, and which forecast or climate-data additions should be tested first for durable executable market edge?

## Outcome

The current estimator stack is adequate only as a rich METAR/HRRR research baseline, not as a profitable or production-grade fair-value model. HRRR features improve held-out weather scores, but the 36-name stack contains correlated and duplicate families, raw probabilities are severely overconfident versus realized outcomes and the market, and the latest causal tape-backed untouched holdout produced zero surviving families. Prioritize venue/sensor truth, a coherent remaining-heating distribution, WeatherNext 2 plus NBM ensemble baselines, spatial and radiation residuals, then local coastal water-temperature ablations. Treat RONI/ENSO only as a slow seasonal regime covariate with causal vintages and long-history validation, not as a direct intraday signal; treat Google's cyclone model as a rare-event regime input rather than the core temperature model.

## Evidence

- Active DB through 2026-08-11: 1,772,116 snapshots, 84 resolved dates, 36 models; current artifacts use 2022-2025 data across 17,516 station-days; V2a raw Brier 0.380/0.348 versus market 0.255/0.260; deterministic tape-backed run had 12 passing discovery families and 0 holdout survivors; WeatherNext 2 and NOAA RONI/OISST capabilities verified from current primary documentation.

## Durable Output

Explicit no-canonical-change verdict in closed T0009; learning/L0006-model-diversity-comes-from-information-not-estimator-count.md; existing approved docs/implementation/forecast-edge-data-program.md remains the recommended experiment contract.
