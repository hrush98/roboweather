---
id: L0010
title: The tradable target may be a reporting process, not the physical quantity
status: CAPTURED
kind: CONCEPT
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-12
origin: T0015
---

# L0010 The tradable target may be a reporting process, not the physical quantity

## Why This Mattered

F0 showed that choosing the meteorologically official high would have made the training target less settlement-compatible.

## What Happened

Across 220 resolved June station-dates, the IEM routine/special report maximum matched every venue-winning bucket, while NWS CLI conflicted on 56 of 176 comparable rows and NCEI one-minute ASOS on 115 of 196.

## Concept

A contract can settle on a public reporting pipeline whose rounding, cadence, calendar, and revision semantics differ from the latent physical measurement. Prediction truth and physical truth must therefore be modeled as separate linked variables.

## Intuition

Predict what the referee will write on the scoreboard, while separately tracking what happened on the field. A more precise thermometer can be a worse settlement predictor when the venue follows a different report.

## General Pattern

This applies to any market resolved from an index, publication, rounded display, revised statistic, or named data vendor rather than direct physical reality.

## RoboWeather Application

Keep venue bucket authoritative; version the IEM report maximum as the numeric proxy; retain CLI, one-minute ASOS, and Weather Underground as distinct evidence; fail closed on missing or revised source rows.

## Questions To Revisit

Does the 220/220 mapping survive later seasons and venue rule changes? Can historical venue buckets and source capture times be backfilled immutably? What uncertainty should live pricing assign before all daily reports arrive?

## Evidence And Sources

- reports/forecast-edge/f0-truth-current/result.json; weather_trader/forecasting/truth.py; docs/implementation/forecast-edge-data-program.md F0 accepted contract

## Revisit Log

No revisits yet.
