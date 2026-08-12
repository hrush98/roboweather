---
id: L0009
title: Evaluation geometry can leak the answer
status: CAPTURED
kind: FAILURE
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-12
origin: T0014
---

# L0009 Evaluation geometry can leak the answer

## Why This Mattered

Clean feature lists created false confidence while the realized high silently selected the probability ladder being scored.

## What Happened

RoboWeather's historical grouped bucket metrics built each validation ladder around final_high_tmpf, so every model was evaluated on outcome-conditioned support and repeated threshold rows inflated apparent sample size.

## Concept

Leakage can enter through cohort construction, support selection, normalization, or metric weighting even when no future column is passed to the estimator.

## Intuition

Hiding the answer from the model is insufficient if the exam itself is rearranged around the answer before scoring.

## General Pattern

Any evaluation that chooses labels, bins, candidates, joins, horizons, or missing-data subsets after observing outcomes can manufacture favorable or incomparable evidence.

## RoboWeather Application

Freeze outcome-independent support and horizon selectors, require identical rows, score one station/date forecast, cluster by weather date, and fail closed when a complete causal market ladder is missing.

## Questions To Revisit

Where else do policy selection, bucket construction, or coverage filters depend on realized outcomes? Should automated diagnostics hash the evaluation cohort and reject outcome-derived selectors?

## Evidence And Sources

- T0014; weather_trader/models/bucket_classifier.py build_synthetic_bucket_dataset; scripts/forecast_edge_report.py; reports/forecast-edge/f0b-current/result.json

## Revisit Log

No revisits yet.
