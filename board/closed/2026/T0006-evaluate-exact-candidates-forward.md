---
id: T0006
title: Evaluate exact candidates forward
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: b13977cd8a564c0d40872efc8b57661a7d6e528a3be41d4817256fa3d80cf720
closed: 2026-08-12
---

# T0006 Evaluate exact candidates forward

## Question

Can every immutable existing Phase 3D candidate be evaluated only on post-activation cached decisions with aligned, cap-aware, provenance-honest results?

## Outcome

Exact existing-candidate evaluation is activation-bounded, cap-aware, deterministic, and promotion-fail-closed; production contained zero candidate versions and no candidate passed.

## Evidence

- 28 focused tests passed; synthetic candidates exclude all pre-activation rows and preserve exact registered execution/cap semantics; two production cutoff-2026-08-12 reports had content hash 3e868620b941eab8a07c80ffd0a3237d04ff5a78e14533756abd037b414532bc and byte-identical JSON/Markdown/CSV; production registry candidate count was zero and report status was NO_EXISTING_CANDIDATES.

## Durable Output

weather_trader/discovery/forward_analysis.py; scripts/run_discovery.py; tests/test_forward_analysis.py; docs/implementation/tape-strategy-discovery.md; commit c6d99cb
