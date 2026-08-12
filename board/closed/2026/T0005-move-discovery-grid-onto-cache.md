---
id: T0005
title: Move discovery grid onto cache
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-11
last_touched: 2026-08-12
facts_fingerprint: d8be5a15d84a1a1f612d57b8cae0bcee56e0563a9c06e3180ef325b93591e293
closed: 2026-08-12
---

# T0005 Move discovery grid onto cache

## Question

Can the bounded Phase 3D historical grid, family collapse, untouched holdout, and four-state report run deterministically from the accepted decision cache only?

## Outcome

The bounded historical grid now runs only from the accepted decision cache, freezes correlated representatives before holdout access, and emits deterministic four-state JSON/Markdown/CSV; the production holdout produced a valid completed-none result.

## Evidence

- Production cutoff 2026-08-12 loaded 4,281 eligible cache rows, ranked 20,736 rules, collapsed 252 passers to 12 pre-holdout representatives, found zero five-date holdout survivors, and repeated with identical analytical hash 46b61545682ea4fca95adb7f0db7a61ae2b39adae6004c9e09c5b62977917ab5 plus byte-identical JSON/Markdown/CSV; focused tests pass.

## Durable Output

weather_trader/discovery/cache_analysis.py; scripts/run_discovery.py; tests/test_cache_analysis.py; docs/implementation/tape-strategy-discovery.md; commits 40f7855 and d130b4d
