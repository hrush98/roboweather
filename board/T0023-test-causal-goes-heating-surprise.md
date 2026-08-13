---
id: T0023
title: Test causal GOES heating surprise
status: ACTIVE
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: 42a9b5a1f27f38d8bf7cd94e1e676a6d42a758e59d5b797388697bcbe99921bf
---

# T0023 Test causal GOES heating surprise

## Question

Conditional on the existing exact-cutoff HRRR-based forecast and contemporaneous market probability, does causally observed cloud/radiation surprise improve US-high settlement-token probability skill on predeclared regimes, thresholds, horizons, and abstention levels?

## Current Answer

The causal GOES source, station decoder, normalized-radiation-surprise transform, and frozen market-relative model contract are established. Incremental settlement-token skill is not established: the forward evidence clock starts on 2026-08-14 and currently has zero eligible resolved dates.

## Evidence

- `goes_abi_dsr` uses public NOAA GOES-18/19 ABI Level 2 DSR, retains archive clocks as provenance only, and makes each artifact causally visible at its first successful local observation.
- The frozen `goes_dsr_market_relative_logit_v1` challenger adds radiation surprise and predeclared mixed/cloudy interactions to the corrected F3 and market baseline; it declares station/regime groups, surprise thresholds, and abstention thresholds before evaluation.
- Twenty-three focused tests pass. The user timer is enabled, the first bounded run completed, and the catalog reached eight valid artifacts (282,602,424 bytes). The initial report is `ACCUMULATING_CALIBRATION` with zero eligible resolved dates and no trading authority.

## Next Action

Keep the five-minute collector healthy until at least 20 resolved post-activation dates; run the report, freeze the fitted calibrator and a strictly future activation boundary, then continue collecting without inspecting post-boundary outcomes.

## Closure Output

GOES heating-surprise source contract, implementation, exact selected-token calibration and HRRR/market-relative controlled ablation, station/regime/threshold/abstention report, verdict, and explicit executable-edge follow-on gate if accepted
