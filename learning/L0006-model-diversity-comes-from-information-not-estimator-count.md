---
id: L0006
title: Model diversity comes from information, not estimator count
status: CAPTURED
kind: CONCEPT
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-12
origin: T0009
---

# L0006 Model diversity comes from information, not estimator count

## Why This Mattered

RoboWeather was collecting 36 model names, so model count could be mistaken for independent forecast confirmation.

## What Happened

The active stack applied threshold, dynamic-bucket, CatBoost, regression, and NGBoost estimators to heavily shared observation and HRRR inputs; HRRR-rich and METAR-plus-HRRR-rich tuned models were behaviorally identical, raw fairs remained overconfident, and no correlated strategy family survived the latest untouched tape holdout.

## Concept

Estimator diversity is useful only when it produces stable, incrementally informative predictions. Genuine forecast diversity usually comes from distinct causal measurements, forecast systems, scales, or error mechanisms, not from retraining several algorithms on the same feature matrix.

## Intuition

Five judges reading the same flawed evidence can agree confidently and still be wrong. Adding a new sensor or genuinely different forecast may change what is knowable; changing only the voting rule often does not.

## General Pattern

Applies to ensembles, alpha factors, medical tests, anomaly detectors, and any decision system where correlated errors make nominal component count overstate effective evidence.

## RoboWeather Application

Collapse RoboWeather variants by prediction correlation, retain a small set of controls, and admit WeatherNext, NBM, spatial residuals, radiation surprise, or ocean-regime data only through identical-coverage incremental-skill tests against the existing forecast and market baselines.

## Questions To Revisit

Which current families are truly distinct after correlation and disagreement analysis, and which new source first adds market-relative skill across independent weather regimes?

## Evidence And Sources

- T0009; active research DB through 2026-08-11; Price Sheet V2a calibration reports; deterministic tape-backed discovery report; docs/current-trading-system-audit.md

## Revisit Log

No revisits yet.
