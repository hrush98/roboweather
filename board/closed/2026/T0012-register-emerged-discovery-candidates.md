---
id: T0012
title: Register emerged discovery candidates
status: CLOSED
pillar: execution
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: 699aba2e2181b753cc8b3097af0947f910be961fb029426d328b0f1c1f055ad4
closed: 2026-08-12
---

# T0012 Register emerged discovery candidates

## Question

Can the sole discovery command losslessly freeze each surviving historical rule as one immutable activation-bounded candidate for later exact forward evaluation?

## Outcome

The sole discovery command can now losslessly freeze every surviving D3 rule, atomically register the completed outcome and immutable future-activated candidate only after report artifacts exist, and evaluate that exact version only on post-activation rows; completed-none behavior and funded authority remain unchanged.

## Evidence

- Focused discovery/registry suite: 16 passed; full repository suite: 482 passed; compileall and git diff --check passed.

## Durable Output

weather_trader/discovery/emergence.py; weather_trader/discovery/contracts.py; weather_trader/discovery/registry.py; weather_trader/discovery/forward_analysis.py; scripts/run_discovery.py; tests/test_discovery_emergence.py; docs/implementation/tape-strategy-discovery.md; docs/current-trading-system-audit.md; docs/changelog.md
