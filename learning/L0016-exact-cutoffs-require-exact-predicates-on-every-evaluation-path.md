---
id: L0016
title: Exact cutoffs require exact predicates on every evaluation path
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-08-27
origin: T0021
---

# L0016 Exact cutoffs require exact predicates on every evaluation path

## Why This Mattered

A one-hour timing leak can invalidate forward evidence while leaving aggregate acceptance metrics apparently healthy.

## What Happened

F3 declared an exact 14:00 station-local cutoff, but load_identical_cohort admitted every timestamp whose local hour was 14, including a sampled 14:57 decision; only the historical path used the exact selector.

## Concept

A causal contract is only as strict as its least precise implementation path. Equivalent-looking selectors must share one boundary primitive rather than independently approximating it.

## Intuition

Checking the hour label is like closing a gate sometime during the two-o'clock hour; it does not prove the gate closed at 2:00 sharp.

## General Pattern

Any duplicated cutoff logic across historical, forward, pricing, and tape joins can drift in granularity, timezone handling, or inclusivity.

## RoboWeather Application

Centralize station-local exact-as-of selection, assert every selected timestamp is at or before the declared cutoff, and report timing diagnostics separately for every cohort path.

## Questions To Revisit

Where else does RoboWeather duplicate horizon selection, and can one contract-owned selector serve forecast, pricing, and tape evidence?

## Evidence And Sources

- weather_trader/forecasting/nbm_benchmark.py:357 load_identical_cohort local_hour <= horizon_hour_local; reports/forecast-edge/f3-current/result.json exact-cutoff claim; prediction_snapshots id 2160300 at 14:57 local

## Revisit Log

No revisits yet.
