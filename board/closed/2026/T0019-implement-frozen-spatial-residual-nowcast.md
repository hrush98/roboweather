---
id: T0019
title: Implement frozen spatial residual nowcast
status: CLOSED
pillar: information
priority: normal
owner: codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: 8e8c983ef3bc9897923da8bfb18da3d93d934e34c549d7b2786d527c7b7a280a
closed: 2026-08-13
---

# T0019 Implement frozen spatial residual nowcast

## Question

Do frozen high-frequency upwind station residuals add information beyond target-station observations and model point features?

## Outcome

Rejected asos_upwind_residual_exact_cutoff_v2: the causal five-neighbor spatial correction had complete controlled-row coverage but worsened untouched and recent log loss and RPS versus F3.

## Evidence

- 541/541 eligible rows across 55 weather dates; untouched 22-date spatial-minus-F3 log loss +0.14235 and RPS +0.01225; recent 14-date metrics also worse; 38 relevant tests passed; authoritative 5,000-bootstrap verdict REJECT_F4_SPATIAL_RESIDUAL.

## Durable Output

Commit 01f75a0 adds the F4 implementation, report command, and tests; canonical plan/audit/hypothesis/roadmap/changelog updates record rejection; L0015 preserves the zero-intercept ablation lesson; generated reports remain uncommitted.
