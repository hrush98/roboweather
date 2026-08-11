---
id: L0003
title: Execution timing is part of strategy identity
status: CAPTURED
kind: DESIGN
captured: 2026-08-11
last_revisited: never
revisit_on: 2026-08-25
origin: T0004
---

# L0003 Execution timing is part of strategy identity

## Why This Mattered

An exact-time replay contract was scientifically strict but operationally incapable of meeting the daily cache budget, while a bounded causal delay made the same evidence substrate tractable.

## What Happened

The exact quote-ready backfill slowed to roughly 100 decisions per minute while decompressing five-minute raw partitions. Replacing it with the first full-book checkpoint no more than 30 seconds after readiness, with continuous VALID coverage through execution, completed 19,032 production decisions in 32.43 seconds and cached 17,232 explicit rejections.

## Concept

Execution time is not an incidental implementation detail. A different executable observation time defines a different counterfactual strategy and must be versioned in the decision identity, provenance, and evidence clock.

## Intuition

If we wait for a trustworthy full-book checkpoint, we are studying a slightly later trade—not approximating the earlier one. Naming and bounding that delay keeps the claim honest while avoiding expensive reconstruction that adds little practical value.

## General Pattern

This applies whenever latency, batching, data publication cadence, or state checkpoints change what was knowable and executable at a decision boundary.

## RoboWeather Application

RoboWeather keys cached decisions by a versioned latency and execution contract, records actual checkpoint time and delay, rejects delays over 30 seconds, and never presents the checkpoint arm as exact-time or actual-fill evidence.

## Questions To Revisit

Does the 30-second bound remain economically representative across market families, and should later evidence justify a tighter bound or a raw event index for exact-time studies?

## Evidence And Sources

- T0004; production cache refresh p3d_cache_refresh_394314795815712bbd6869c5; docs/implementation/tape-strategy-discovery.md

## Revisit Log

No revisits yet.
