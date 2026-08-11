---
id: T0002
title: Simplify deterministic discovery
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-11
last_touched: 2026-08-11
facts_fingerprint: a50f72529e8dd2b56e02d295bb98b2ae0aaab600826c64546175861a256d9d30
closed: 2026-08-11
---

# T0002 Simplify deterministic discovery

## Question

What minimum architecture and acceptance gates should replace the current Phase 3D operator workflow with an incremental cached single-command discovery system?

## Outcome

Replace the failed Phase 3D multi-command operator workflow with an incremental executable-decision cache and one deterministic command that performs historical discovery, untouched holdout, exact post-activation candidate evaluation, and one report; defer scheduling and lifecycle automation until production performance passes.

## Evidence

- phase3d_status reported repeated approximately 901-second recurring-discovery failures, zero completed runs, and zero candidates; canonical documentation passes git diff --check and now defines D0-D6 with exactness, determinism, crash-resume, holdout, forward-integrity, and runtime gates

## Durable Output

docs/implementation/tape-strategy-discovery.md; docs/execution-rebuild-roadmap.md; docs/current-trading-system-audit.md; agent_loop/STATE.md; learning/L0002-prove-the-kernel-before-automating-the-control-plane.md
