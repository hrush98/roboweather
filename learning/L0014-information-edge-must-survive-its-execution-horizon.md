---
id: L0014
title: Information edge must survive its execution horizon
status: CAPTURED
kind: DESIGN
captured: 2026-08-13
last_revisited: never
revisit_on: 2026-09-13
origin: T0020
---

# L0014 Information edge must survive its execution horizon

## Why This Mattered

A public-data forecast can be more accurate yet economically useless if the market absorbs the update before RoboWeather can trade at useful size.

## What Happened

After F3 established forecast improvement over HRRR but uncertain market-relative skill, the strategy review distinguished predictive information from information freshness and microsecond latency and found that the current plan did not explicitly measure forecast-to-market edge decay.

## Concept

Information edge is conditional on time and executability: freeze the causal forecast at quote-ready time, measure its side-aligned conservative gap to later executable prices, and distinguish market absorption from a later forecast revision.

## Intuition

An edge visible only in a photograph taken before the system could act is not ours. A usable information advantage must still be on the book when a realistic order can arrive.

## General Pattern

This applies to forecast releases, news, alternative data, model revisions, settlement updates, and any strategy whose signal and market response occur on different clocks.

## RoboWeather Application

F6 now measures executable net edge at quote-ready, 30 seconds, 2 minutes, 5 minutes, and 15 minutes; missing or unfillable tape is right-censored, and pre-execution disappearance is labeled unusable latency evidence.

## Questions To Revisit

What minimum edge lifetime and useful-size depth are operationally acceptable, and does that threshold vary by taker versus passive tactic or cohort?

## Evidence And Sources

- T0020; docs/implementation/forecast-edge-data-program.md; docs/implementation/full-market-lifecycle-trading.md; docs/execution-rebuild-roadmap.md

## Revisit Log

No revisits yet.
