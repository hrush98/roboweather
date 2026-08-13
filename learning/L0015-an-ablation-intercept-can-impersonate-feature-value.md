---
id: L0015
title: An ablation intercept can impersonate feature value
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-13
origin: T0019
---

# L0015 An ablation intercept can impersonate feature value

## Why This Mattered

The first F4 spatial model could change every forecast through a learned intercept, so its score mixed spatial information with generic predecessor recalibration.

## What Happened

The initial asos_upwind_residual_exact_cutoff_v1 ridge learned a roughly -0.56 F average correction even though F4's question was whether cross-row spatial residual variation added information. The audit caught the confound before closure; v2 disabled the intercept and the full untouched evaluation remained negative.

## Concept

An incremental feature ablation must hold the baseline calibration surface fixed. Any free constant, station effect, or refitted component that can improve predictions without the candidate feature creates an alternate causal path and invalidates attribution.

## Intuition

If the candidate is unplugged and the challenger can still move the forecast, the experiment is not isolating the candidate. The model may win by repairing an old bias rather than using the new information.

## General Pattern

This applies to residual models, calibration layers, market-aware blends, station heads, regime intercepts, and any challenger trained on predecessor errors.

## RoboWeather Application

For RoboWeather source ablations, require a no-feature identity property: missing or zeroed candidate inputs leave the frozen predecessor unchanged, and any correction learner must be unable to express an unconditional shift unless that shift is a separately scored control.

## Questions To Revisit

Should every future feature report include an explicit zero-feature identity test and an intercept-only control? When is separately versioned recalibration itself the research question?

## Evidence And Sources

- T0019; weather_trader/forecasting/spatial_residual.py; reports/forecast-edge/f4-current/result.json

## Revisit Log

No revisits yet.
