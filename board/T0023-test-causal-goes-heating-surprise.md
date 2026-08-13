---
id: T0023
title: Test causal GOES heating surprise
status: WAITING
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: b11f54d2c50b9f6523ce303fb7651176cade0d9b75af3c28b37e05830e3d894f
---

# T0023 Test causal GOES heating surprise

## Question

Conditional on the existing exact-cutoff HRRR-based forecast and contemporaneous market probability, does causally observed cloud/radiation surprise improve US-high settlement-token probability skill on predeclared regimes, thresholds, horizons, and abstention levels?

## Current Answer

The complete forward-causal goes_dsr_market_relative_logit_v2 lifecycle is implemented and frozen before outcomes, but incremental settlement-token skill remains unestablished because the 2026-08-14 evidence clock has zero eligible resolved dates.

## Evidence

- Commit 5d0fe97 adds immutable earliest-20-date fitting, calibration-row replay hashes, strictly future activation enforcement, a second 20-date untouched gate, date-clustered log-loss comparisons versus the conditional no-surprise baseline/F3/market, exact-token calibration, station/regime/signed-threshold diagnostics, and a displayed-ask abstention curve; 28 focused tests pass.
- Production scripts/forecast_goes_heating_report.py returned ACCUMULATING_CALIBRATION / NOT_EVALUATED_FORWARD_EVIDENCE_INCOMPLETE with 11 artifacts, 0 eligible rows, 0 eligible dates, no frozen calibrator, and 0 untouched dates.
- The research database latest resolved market date remains 2026-08-12; the GOES catalog reached 11 artifacts / 402406682 bytes through causal_available_at_utc 2026-08-13T20:46:23.952580+00:00, and roboweather-goes-dsr.timer is enabled and active.

## Next Action

After at least 20 resolved post-activation dates exist, run /home/maxrush/miniconda3/envs/roboweather/bin/python scripts/forecast_goes_heating_report.py --freeze-calibrator --untouched-forward-start-date YYYY-MM-DD with a strictly future date, then continue collection without inspecting post-boundary outcomes until 20 untouched dates exist.

## Closure Output

GOES heating-surprise source contract, implementation, exact selected-token calibration and HRRR/market-relative controlled ablation, station/regime/threshold/abstention report, verdict, and explicit executable-edge follow-on gate if accepted
