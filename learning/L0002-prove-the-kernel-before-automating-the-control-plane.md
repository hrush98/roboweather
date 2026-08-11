---
id: L0002
title: Prove the kernel before automating the control plane
status: CAPTURED
kind: DESIGN
captured: 2026-08-11
last_revisited: never
revisit_on: 2026-09-11
origin: T0002
---

# L0002 Prove the kernel before automating the control plane

## Why This Mattered

Phase 3D had strong causal governance but could not produce a completed production discovery result.

## What Happened

The scheduler repeatedly reconstructed historical tape at model-row grain, reached its 900-second timeout, and left zero completed runs and zero candidates while downstream evaluator and transition machinery had nothing to evaluate.

## Concept

Choose the smallest correct expensive-work identity, cache it incrementally, and prove production cardinality before adding lifecycle orchestration.

## Intuition

A perfect filing system around a machine that never finishes still produces no answer; make the machine finish once, reuse its work, then automate it.

## General Pattern

Applies to replay pipelines, feature materialization, model evaluation, ETL, schedulers, registries, and any system where many logical records share one expensive underlying computation.

## RoboWeather Application

RoboWeather will key tape replay by executable decision and replay version, map model opinions separately, require direct-replay equivalence and cold/warm/incremental performance gates, and add scheduling only after the single discovery command passes production cycles.

## Questions To Revisit

Did the decision key capture every causal distinction without duplicate replay? Do warm and daily incremental runs meet their budgets? Has any lifecycle feature returned before the single report proved necessary?

## Evidence And Sources

- T0002; docs/implementation/tape-strategy-discovery.md; docs/current-trading-system-audit.md; Phase 3D scheduler failures near 901 seconds on 2026-08-10 and 2026-08-11

## Revisit Log

No revisits yet.
