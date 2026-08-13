---
id: T0022
title: Finalize F6 closure state
status: CLOSED
pillar: cross-pillar
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: e6dcfc710deef6cfe739b63efe2b7d0f2a57833806cf86f26c387b9838860183
closed: 2026-08-13
---

# T0022 Finalize F6 closure state

## Question

Does the current repository and remote state faithfully and reproducibly close F6 after T0021, including exact evidence labels, current machine facts, commits, and branch publication?

## Outcome

Yes. F6 is reproducibly closed as rejected. T0021 evidence text "t0 2/18 fillable at 5" is explicitly superseded by "t0 2/18 rows fully sweepable at the $25 cost target"; closed T0021 remains append-only. Side-aware tape lookup is now enforced, and because all 18 frozen rows are BUY_YES, corrected replay leaves the numerical verdict unchanged.

## Evidence

- 21 focused F3/F4/F6 tests passed; production F6 rerun COMPLETE/REJECT_F6_CORRECTED_F3_PREREQUISITE_AND_PRICING_GATES with 18 selected rows over 14 dates, 14 tape mappings, 2/18 t0 rows fully sweepable at the $25 cost target, median reserved net edge -0.16308; selected log-loss delta +0.14519; all acceptance gates remain fail-closed; git push 02934bb..cda3965 succeeded

## Durable Output

commit cda3965; scripts/forecast_f6_report.py; tests/test_forecast_f6_report.py; reports/forecast-edge/f6-current/result.json sha256 a3288149e76feb93c638da083484e50e2e91e21b94e6e5f99c4aeaa6c1117045; learning/L0017-a-binary-market-id-is-not-an-executable-instrument.md; branch execution-engine-2026-06-15 published through cda3965
