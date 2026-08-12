---
id: L0005
title: A zero-result run does not prove the emerged path
status: CAPTURED
kind: FAILURE
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-12
origin: T0008
---

# L0005 A zero-result run does not prove the emerged path

## Why This Mattered

Phase 3D could honestly produce a completed-none report while the unexercised path from an emerged historical rule to an immutable forward candidate remained disconnected.

## What Happened

D3 production had zero holdout survivors and D4 production had zero registered candidates. All tests passed, but historical DiscoveryRule includes edge and spread thresholds absent from CandidateRule, and run_discovery.py opens the registry read-only, so no emerged rule can be registered exactly for later activation-bounded evaluation.

## Concept

Acceptance must exercise every semantically different terminal branch, especially the branch that creates durable state for later stages.

## Intuition

Proving that an empty conveyor belt stops cleanly does not prove it can carry a package through the handoff.

## General Pattern

Applies to nomination pipelines, job schedulers, payment systems, deployment promotion, event workflows, and any system where the common no-op path bypasses state creation.

## RoboWeather Application

Add a synthetic end-to-end discovery run whose family survives holdout, is converted without loss into an immutable candidate version, receives a future activation, and is evaluated only on later cached decisions. Keep the real production no-result as separate honest evidence.

## Questions To Revisit

Can every field of an emerged rule be represented in candidate identity? Does a complete emerged run append exactly one idempotent version? Does the next run exclude all pre-activation rows?

## Evidence And Sources

- T0008; weather_trader/discovery/cache_analysis.py DiscoveryRule; weather_trader/discovery/contracts.py CandidateRule; scripts/run_discovery.py read-only registry; production reports with zero survivors and zero candidates

## Revisit Log

No revisits yet.
