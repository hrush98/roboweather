---
id: L0012
title: Discrete forecast bins inherit the label's rounding semantics
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-13
origin: T0017
---

# L0012 Discrete forecast bins inherit the label's rounding semantics

## Why This Mattered

A half-degree boundary error can shift an otherwise normalized probability distribution and change every proper-score comparison without producing an obvious runtime failure.

## What Happened

The initial F2 NBM benchmark projected a continuous Gaussian forecast onto integer Fahrenheit support using whole-degree cut points; review caught the mismatch before a resumed run published new evidence, and the projection was corrected to half-degree rounding boundaries.

## Concept

A continuous predictive distribution becomes a discrete forecast only after applying the same measurement and rounding map that creates the evaluated label. Normalization alone does not make the mapping semantically correct.

## Intuition

If 70F means values that round to 70, its probability cell runs from 69.5F to 70.5F. Cutting at 70F assigns half of the wrong neighboring interval even though all probabilities still sum to one.

## General Pattern

The same failure appears in price ticks, bucket contracts, censored endpoints, quantized sensors, and any model evaluated against rounded or interval-valued labels.

## RoboWeather Application

Freeze outcome transformation and bin boundaries together; test symmetric distributions at exact label centers; let endpoint support cells absorb tails only after interior half-step boundaries are correct.

## Questions To Revisit

Which other RoboWeather probability transforms implicitly assume whole-unit rather than half-unit boundaries, and do venue bucket semantics ever differ from numeric-label rounding semantics?

## Evidence And Sources

- weather_trader/forecasting/nbm_benchmark.py normal_fixed_support; tests/test_nbm_benchmark.py::test_normal_fixed_support_uses_half_degree_rounding_boundaries; T0017

## Revisit Log

No revisits yet.
