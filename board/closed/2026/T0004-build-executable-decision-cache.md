---
id: T0004
title: Build executable decision cache
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-11
last_touched: 2026-08-11
facts_fingerprint: 616072c114c4e08c557e7fbbdab468edd9a1784f14627dc2286685d112f833b5
closed: 2026-08-11
---

# T0004 Build executable decision cache

## Question

Can the Phase 3D executable-decision cache satisfy deterministic replay equality and production runtime gates?

## Outcome

The versioned causal checkpoint executable-decision cache satisfies D0-D2: deterministic identities and cached rejections are crash-resumable, production cold/warm/incremental resource gates pass, and direct replay matches exactly.

## Evidence

- Production scheduler disabled while research/tape stayed active; 146,937 mappings -> 19,032 decisions; cold 32.43s at 325,480 KiB; warm 0.026s and zero replay; 9,216-row increment 2.52s; 200/200 stratified direct replay hashes; focused tests pass.

## Durable Output

weather_trader/discovery/decision_cache.py; scripts/refresh_executable_decision_cache.py; tests/test_decision_cache.py; docs/implementation/tape-strategy-discovery.md; commits e35f0f6 and 6b9ee80; learning/L0003-execution-timing-is-part-of-strategy-identity.md
