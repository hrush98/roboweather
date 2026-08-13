---
id: L0013
title: A time bucket is not a decision cutoff
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-13
origin: T0018
---

# L0013 A time bucket is not a decision cutoff

## Why This Mattered

The apparent F3 historical advantage was initially measured almost one hour later than the intended live decision, making remaining heating look easier and smaller.

## What Happened

The frozen F0B selector filtered hour_local <= 14, but hour_local described the underlying observation. It selected 4,338 of 4,364 validation rows after 14:00 local, usually routine reports at 14:51-14:58, while forward 14:00 decisions used earlier reports.

## Concept

Causal evaluation requires comparing event timestamps to an exact decision timestamp; categorical clock buckets cannot establish that information was available by the boundary.

## Intuition

Calling something a 14:00 snapshot does not make every observation stamped 14:xx available at 14:00. The final minute of an hour can contain almost another hour of weather.

## General Pattern

The same failure can affect forecast releases, market quotes, tape checkpoints, settlement revisions, and any join implemented with hour or date buckets instead of ordered timestamps.

## RoboWeather Application

RoboWeather now versions the exact-local-cutoff selector, requires station timezone provenance, rejects post-cutoff observations, and regression-tests a 14:52 report against a 14:00 decision.

## Questions To Revisit

Which remaining research and pricing selectors compare coarse buckets instead of exact availability timestamps, and should they be migrated to explicit as-of joins?

## Evidence And Sources

- weather_trader/forecasting/evaluation.py; tests/test_forecast_evaluation.py; reports/forecast-edge/f3-current/result.json; T0018

## Revisit Log

No revisits yet.
