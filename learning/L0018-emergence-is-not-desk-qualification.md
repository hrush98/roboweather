---
id: L0018
title: Emergence is not desk qualification
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-15
origin: T0024
---

# L0018 Emergence is not desk qualification

## Why This Mattered

A completed discovery status can be mistaken for evidence of economically defensible edge even when its uncertainty and concentration are unacceptable.

## What Happened

The 2026-08-13 current-watermark run registered two high-regression candidates after four holdout trades on three dates earned +0.2288 R/R, although both holdout 5% bounds were negative, the discovery clustered lower bounds were negative, both candidates shared the same holdout trades, and removing the best holdout date made the result negative.

## Concept

Workflow emergence and capital-quality qualification are different gates. A deterministic nomination gate answers whether a rule passed its declared mechanics; a desk gate must additionally demand uncertainty-aware robustness, mechanism, settlement, markouts, useful size, and best-trade stress.

## Intuition

A strategy can win a tournament because the entry rule says a positive median is enough, while still being a bad bet for capital. The label 'emerged' describes the tournament state, not confidence in the economic edge.

## General Pattern

Applies to model leaderboards, hyperparameter searches, A/B winners, candidate registries, and any automated status whose minimum passing rule is weaker than the downstream decision standard.

## RoboWeather Application

Preserve immutable emerged versions for attribution, but add an explicit desk-qualification overlay that fails candidates with negative clustered bounds, too few independent dates, best-date dependence, duplicate exposure, missing venue settlement, missing markouts, or unproven useful-size economics.

## Questions To Revisit

After sufficient post-activation dates, do either version develop positive lower-bound venue-settled economics; should the D3 emergence threshold itself require a positive clustered lower bound or should that remain a separate desk gate?

## Evidence And Sources

- reports/discovery/t0024-current-2026-08-13/result.json; result hash 668ed01f199e9c2d74922eefbedf2eb45eb44d07229428522dc71f37131481bd

## Revisit Log

No revisits yet.
