---
id: T0023
title: Test causal GOES heating surprise
status: WAITING
pillar: information
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: 53ff693b5fc7db109f93822a94c289c76ad71abacc6e394ab22621121afb2768
---

# T0023 Test causal GOES heating surprise

## Question

Conditional on the existing exact-cutoff HRRR-based forecast and contemporaneous market probability, does causally observed cloud/radiation surprise improve US-high settlement-token probability skill on predeclared regimes, thresholds, horizons, and abstention levels?

## Current Answer

The forward-causal GOES source, station decoder, normalized radiation-surprise transform, and frozen market-relative model contract are implemented, but incremental settlement-token skill is not established because the 2026-08-14 evidence clock has zero eligible resolved dates.

## Evidence

- Commit 0f461ca implements the source/model/report contracts and 23 focused tests pass.
- Runtime catalog query on 2026-08-13 observed 8 goes_abi_dsr artifacts totaling 282602424 bytes with latest causal_available_at_utc 2026-08-13T20:20:36.847562+00:00; roboweather-goes-dsr.timer is enabled and active (waiting).
- scripts/forecast_goes_heating_report.py returned ACCUMULATING_CALIBRATION / NOT_EVALUATED_FORWARD_EVIDENCE_INCOMPLETE with 0 eligible rows and 0 eligible dates; generated reports remain uncommitted and no funded authority changed.

## Next Action

After at least 20 resolved post-activation dates exist, run /home/maxrush/miniconda3/envs/roboweather/bin/python scripts/forecast_goes_heating_report.py, freeze its fitted calibrator with a strictly future activation boundary, and continue collection without inspecting post-boundary outcomes.

## Closure Output

GOES heating-surprise source contract, implementation, exact selected-token calibration and HRRR/market-relative controlled ablation, station/regime/threshold/abstention report, verdict, and explicit executable-edge follow-on gate if accepted
