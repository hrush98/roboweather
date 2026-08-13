---
id: T0023
title: Test causal GOES heating surprise
status: ACTIVE
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: ab9403774004c90ff7ce2d65321dae5eca4b72d925e9b907156c7e125fe15c35
---

# T0023 Test causal GOES heating surprise

## Question

Conditional on the existing exact-cutoff HRRR-based forecast and contemporaneous market probability, does causally observed cloud/radiation surprise improve US-high settlement-token probability skill on predeclared regimes, thresholds, horizons, and abstention levels?

## Current Answer

The forward-causal GOES source and the complete immutable `goes_dsr_market_relative_logit_v2` calibration/untouched-evaluation lifecycle are implemented, but incremental settlement-token skill is not established because the 2026-08-14 evidence clock has zero eligible resolved dates.

## Evidence

- Commit 0f461ca implements the initial source/model/report contracts; the current v2 lifecycle adds immutable earliest-20-date fitting, row-hash replay validation, strict future activation, a second 20-date untouched gate, and every predeclared diagnostic before outcomes exist.
- Runtime catalog query on 2026-08-13 observed 11 `goes_abi_dsr` artifacts totaling 402406682 bytes with latest causal availability at 2026-08-13T20:46:23.952580+00:00; `roboweather-goes-dsr.timer` remains enabled and active.
- The production report remains `ACCUMULATING_CALIBRATION` / `NOT_EVALUATED_FORWARD_EVIDENCE_INCOMPLETE` with zero eligible rows, zero eligible dates, and no frozen calibrator; generated reports remain uncommitted and funded authority is unchanged.

## Next Action

After at least 20 resolved post-activation dates exist, run `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/forecast_goes_heating_report.py --freeze-calibrator --untouched-forward-start-date YYYY-MM-DD` with a strictly future date, then continue collection without inspecting post-boundary outcomes until 20 untouched dates exist.

## Closure Output

GOES heating-surprise source contract, implementation, exact selected-token calibration and HRRR/market-relative controlled ablation, station/regime/threshold/abstention report, verdict, and explicit executable-edge follow-on gate if accepted
