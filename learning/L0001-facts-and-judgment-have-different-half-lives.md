---
id: L0001
title: Facts and judgment have different half-lives
status: REVISIT
kind: DESIGN
captured: 2026-08-11
last_revisited: 2026-08-11
revisit_on: 2026-08-25
origin: T0003
---

# L0001 Facts and judgment have different half-lives

## Why This Mattered

The user wants agents to preserve both trustworthy current context and the deeper intuition learned from failures and design work.

## What Happened

RoboWeather had strong canonical documents, but their review dates and volatile runtime facts aged at different speeds, forcing each agent to reconcile several partially stale views.

## Concept

Temporal decomposition separates regenerated facts, reviewed state, in-flight work, settled history, and personal learning because each kind of knowledge has a different lifetime and writer.

## Intuition

A sensor reading should be replaced when the world changes; a decision should be reviewed; an unfinished thought should be handed off; and a lesson should be revisited. Treating all four as ordinary prose makes freshness indistinguishable from authority.

## General Pattern

The same idea appears in event sourcing versus projections, telemetry versus incident reviews, caches versus configuration, and observations versus scientific conclusions.

## RoboWeather Application

Generate runtime facts mechanically, keep strategic state human-approved, bound work in resumable threads, preserve closed history, and mature learning cards without granting them trading authority.

## Questions To Revisit

Which freshness warnings should trigger mandatory review, and what evidence shows that a learning has become integrated rather than merely familiar?

## Evidence And Sources

- T0001 agent-coordination bootstrap and T0003 learning-memory extension.
- docs/project-overview.md and agent_loop/facts.json exposed different document ages and runtime watermarks.

## Revisit Log



### 2026-08-11

The key distinction is not simply mutable versus immutable; it is matching the update mechanism to the epistemic role of the information.

- New connection: Event-sourced systems retain observations while rebuilding projections, just as the board retains closed threads while regenerating current indexes.
- Practice or action: When adding a new record, declare its lifetime, writer, authority, and regeneration or supersession rule before choosing its folder.
- Maturity after review: REVISIT
