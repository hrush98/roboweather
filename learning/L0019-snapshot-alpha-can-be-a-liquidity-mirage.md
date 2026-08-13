---
id: L0019
title: Snapshot alpha can be a liquidity mirage
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-15
origin: T0024
---

# L0019 Snapshot alpha can be a liquidity mirage

## Why This Mattered

A highly positive snapshot replay could have been mistaken for a neglected-longshot edge.

## What Happened

The global-low top-1 YES rule returned +0.450 R/R on 85 earlier snapshot selections and +0.914 on 10 later viewed selections, but accepted causal tape reduced 95 selections to three fully executable $25 decisions, all losses, with no later-window execution.

## Concept

Signal selection and execution observability are jointly identifying. A price is not an opportunity unless it was available after the signal under continuous valid coverage and at the declared size.

## Intuition

A cheap label in a database may be the last visible crumb of a dead or thin book. Backtests can buy it repeatedly; a causal desk cannot.

## General Pattern

Applies to stale quotes, indicative prices, sparse longshot books, last-trade signals, and any analysis that joins decisions to asynchronous market state.

## RoboWeather Application

Require the accepted quote-ready checkpoint contract, explicit coverage rejection, capped full-size sweeps, and a later untouched executable sample before treating snapshot-return anomalies as strategy candidates.

## Questions To Revisit

When fresh global-low settlement and tape accumulate, does any predeclared cheap-YES rule retain both causal executable frequency and positive venue-settled economics?

## Evidence And Sources

- reports/net-edge/t0024-current-data-opportunity-map/report.md

## Revisit Log

No revisits yet.
