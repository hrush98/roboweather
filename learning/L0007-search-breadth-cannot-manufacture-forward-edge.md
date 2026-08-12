---
id: L0007
title: Search breadth cannot manufacture forward edge
status: CAPTURED
kind: CONCEPT
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-15
origin: T0010
---

# L0007 Search breadth cannot manufacture forward edge

## Why This Mattered

The system needed to distinguish a genuinely broad discovery attempt from a narrow fixed-policy search, without mistaking thousands of correlated in-sample winners for independent strategies.

## What Happened

A behavior-normalized grid represented 20,167,999,488,000 syntactic rules and scored 49,620,192 distinct discovery behavior/risk combinations. 29,900 passed strict discovery gates, collapsed to 15 correlated representatives, and every representative lost money on the untouched five-date holdout.

## Concept

Multiple testing grows much faster than independent evidence. Exhaustive search is useful for ruling through a declared grammar, but only outcome-blind behavioral normalization, family collapse, and untouched forward dates keep the search from converting noise into apparent strategy abundance.

## Intuition

A wider fishing net catches more shapes in the historical water; it does not make the fish real. The larger the net, the more aggressively we must collapse look-alike catches and demand that the final few swim in water the search never saw.

## General Pattern

This applies to hyperparameter tuning, feature selection, portfolio sleeve discovery, subgroup analysis, and any process that ranks many correlated alternatives on a short history.

## RoboWeather Application

Keep the broad causal grammar and efficient cache-backed enumeration, but treat discovery passers only as family candidates. Freeze one representative per correlated semantic family before holdout, require genuinely new resolved dates for further evidence, and never widen the same grammar in response to a completed zero-survivor result.

## Questions To Revisit

As resolved history grows, how fast does the number of independent market dates grow relative to grammar breadth? Which semantic family definition best captures correlated model/data variants without hiding genuinely different behavior?

## Evidence And Sources

- reports/discovery/absolute-wide-2026-08-12/result.json; T0010

## Revisit Log

No revisits yet.
