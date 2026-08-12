---
id: L0004
title: Attempt status is not last-known-good state
status: CAPTURED
kind: DESIGN
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-08-26
origin: T0007
---

# L0004 Attempt status is not last-known-good state

## Why This Mattered

A failed discovery attempt must be visible without erasing or impersonating the last complete analytical answer.

## What Happened

The retired scheduler mixed failed attempts with discovery state, while D5 needed a deliberately failed run to leave the prior complete report byte-identical and still emit FAILED_ANALYSIS separately.

## Concept

Operational systems need separate append-only attempt outcomes and a monotonic pointer that advances only after a complete validated artifact is durable.

## Intuition

A broken new measurement should raise an alarm, not replace yesterday's valid measurement with either nothing or a false zero.

## General Pattern

Model training, deployments, data snapshots, backups, forecasts, and batch reports all benefit from publish-after-validate last-known-good pointers.

## RoboWeather Application

RoboWeather run_discovery.py writes each run artifact independently and atomically replaces latest_complete.json only for completed analytical states; the TUI validates that pointer and cache health.

## Questions To Revisit

Should future scheduling add a separate latest-attempt alert record, and what staleness threshold should make an otherwise valid latest-complete report operationally unhealthy?

## Evidence And Sources

- T0007; weather_trader/discovery/operator_status.py; tests/test_discovery_operator_status.py; D5 failed-run production acceptance

## Revisit Log

No revisits yet.
