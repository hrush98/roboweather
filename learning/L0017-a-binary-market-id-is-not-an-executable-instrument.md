---
id: L0017
title: A binary market ID is not an executable instrument
status: CAPTURED
kind: FAILURE
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-13
origin: T0022
---

# L0017 A binary market ID is not an executable instrument

## Why This Mattered

Tape replay can be internally consistent yet price the opposite outcome token when lookup identity omits the selected side.

## What Happened

The initial F6 implementation keyed tape tokens only by market_id and always loaded the YES token. The evaluated cohort happened to contain only BUY_YES rows, so current metrics did not change, but the implementation would silently misprice any BUY_NO row.

## Concept

Executable identity must include every field that changes the traded payoff. For binary markets that means at least market plus outcome token or selected side; market identity alone names a pair of instruments.

## Intuition

Knowing the game is not enough to know which ticket you bought. YES and NO share a market but have opposite payoffs and separate order books.

## General Pattern

The same composite-identity rule applies to option call/put and strike, long/short legs, venue, settlement version, contract horizon, and any replay join where one parent entity owns multiple executable instruments.

## RoboWeather Application

Key tape mappings, caches, replay decisions, and regression fixtures by market_id plus normalized selected side, and assert both BUY_YES and BUY_NO cases even when the current cohort is one-sided.

## Questions To Revisit

Which other market_id-only joins exist in replay or execution paths? Should executable token identity become a shared typed contract rather than ad hoc tuples?

## Evidence And Sources

- T0022; commit cda3965; tests/test_forecast_f6_report.py::test_tape_tokens_are_mapped_to_the_selected_side

## Revisit Log

No revisits yet.
