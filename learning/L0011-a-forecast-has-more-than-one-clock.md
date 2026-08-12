---
id: L0011
title: A forecast has more than one clock
status: CAPTURED
kind: DESIGN
captured: 2026-08-12
last_revisited: never
revisit_on: 2026-09-12
origin: T0016
---

# L0011 A forecast has more than one clock

## Why This Mattered

Causal forecast evaluation can look excellent while leaking information if cycle time or server modification time is mistaken for the moment the artifact was actually usable.

## What Happened

F1 integrated WeatherNext, NBM, HRRR, RRFS, and IEM under one catalog. WeatherNext exposes a provider ingestion timestamp, NOAA responses expose HTTP provenance but no historical receipt proof, and an IEM live probe initially returned rate limits before the first successful capture.

## Concept

Initialization, valid, provider publication, first local observation, and later modification are distinct timestamps. Replay visibility must be derived from the strongest source-specific availability evidence, never from the earliest convenient clock.

## Intuition

A forecast labeled 12Z was not in anyone's hands at 12Z. It becomes tradable information only after dissemination and receipt. If we did not witness that receipt and the provider does not preserve ingestion time, we cannot travel backward and claim we had it.

## General Pattern

The same distinction applies to market listings, official observations, settlement revisions, news releases, model artifacts, and any mutable API response used in backtests.

## RoboWeather Application

WeatherNext replays from provider ingestion_time; NOAA and IEM replay from first successful local observation; Last-Modified is provenance only; missing availability fails closed and raw revisions remain immutable.

## Questions To Revisit

Can later provider archives supply authoritative historical availability for NBM/HRRR? Do collection outages bias identical-coverage cohorts? Which source revisions matter economically?

## Evidence And Sources

- T0016; weather_trader/forecasting/source_catalog.py; reports/forecast-edge/f1-source-catalog-current/

## Revisit Log

No revisits yet.
