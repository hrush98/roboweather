---
id: T0019
title: Implement frozen spatial residual nowcast
status: ACTIVE
pillar: information
priority: normal
owner: codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: c1d94d23c15c936dfe0521800dffcc2106f8564c448aa074472dad7cc1f55144
---

# T0019 Implement frozen spatial residual nowcast

## Question

Do frozen high-frequency upwind station residuals add information beyond target-station observations and model point features?

## Current Answer

Not established yet.

## Evidence

- Canonical plan: docs/implementation/forecast-edge-data-program.md
- Queue slice: F4
- Dependency evidence: F3 closed as COMPLETE in T0018 with the accepted exact-cutoff remaining-heating distribution.

## Next Action

Audit available causal neighbor-observation and model-at-neighbor data, then freeze the F4 station network, weighting rules, QC, and identical-row ablation design before scoring outcomes.

## Closure Output

MADIS/ASOS spatial residual implementation, controlled chronological ablation report, and acceptance or rejection verdict
