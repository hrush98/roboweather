---
id: T0023
title: Test causal GOES heating surprise
status: WAITING
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: d35f31f7b68316b8de3be275f2aaa0a6c890e4c8cb2ffc6fe01b02ebf49f0daa
---

# T0023 Test causal GOES heating surprise

## Question

Conditional on the existing exact-cutoff HRRR-based forecast and contemporaneous market probability, does causally observed cloud/radiation surprise improve US-high settlement-token probability skill on predeclared regimes, thresholds, horizons, and abstention levels?

## Current Answer

Independent exact-12 and exact-14 forward-causal F5 arms now have separate frozen predecessor and GOES versions, fingerprints, output directories, calibrator lifecycles, and untouched clocks. Incremental settlement-token skill remains unestablished because both 2026-08-14 evidence clocks have zero eligible resolved dates.

## Evidence

- Commit fae901d adds horizon-isolated exact-12 and exact-14 contracts, an immutable exact-12 predecessor builder, generic predecessor semantics, cross-horizon rejection, and 34 passing focused tests.
- The exact-12 predecessor trained on 13,152 rows across 1,096 dates, fit its blend on 218 dates, and improved over HRRR on a later 146-date 2025 diagnostic by -0.06733 log loss and -0.13697 RPS; artifact SHA-256 10704409c1f313a01a0f72157eab64eca7d763dc3932847de6f7f34d8cc6ca48 reproduced byte-identically.
- Production exact-12 and exact-14 reports both return ACCUMULATING_CALIBRATION / NOT_EVALUATED_FORWARD_EVIDENCE_INCOMPLETE with zero eligible rows and dates, no frozen calibrator, and no information or execution claim; the latest official resolved date is 2026-08-13, before the 2026-08-14 activation.

## Next Action

After both arms have at least 20 resolved post-activation dates, freeze each independently with scripts/forecast_goes_heating_report.py --horizon-hour-local HOUR --freeze-calibrator --untouched-forward-start-date YYYY-MM-DD using a strictly future date, then continue outcome-blind collection until each has 20 untouched dates.

## Closure Output

GOES heating-surprise source contract, implementation, exact selected-token calibration and HRRR/market-relative controlled ablation, station/regime/threshold/abstention report, verdict, and explicit executable-edge follow-on gate if accepted
