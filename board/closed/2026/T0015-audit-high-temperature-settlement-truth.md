---
id: T0015
title: Audit high-temperature settlement truth
status: CLOSED
pillar: settlement
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: 701ff98a218cd991c4e2988d28e2325561d7a2dbe03de1c94e5facc7edf761b4
closed: 2026-08-12
---

# T0015 Audit high-temperature settlement truth

## Question

Do IEM, Weather Underground, CLI, high-frequency ASOS, and venue settlement agree sufficiently to define the US high-temperature training target and exact high-so-far?

## Outcome

F0 established a cohort-bounded settlement mapping, not a settlement advantage: venue bucket is authoritative; the IEM routine/special report maximum matched 220/220 resolved June venue buckets and is the versioned numeric proxy/report-stream high-so-far, while CLI, one-minute ASOS, and localized Weather Underground remain distinct diagnostics; provenance and venue-bucket backfill are required without matched-cohort numeric relabeling.

## Evidence

- 494 tests passed; production audit covered 230 station-dates across 10 stations, 220 fully resolved venue chains, and 220/220 IEM routine/special maximum bucket matches; CLI mismatched 56/176, NCEI one-minute ASOS 115/196, interval-aware WU 21/220; 10 unresolved June 18 chains failed closed.

## Durable Output

weather_trader/forecasting/truth.py; scripts/forecast_truth_audit.py; tests/test_forecast_truth.py; docs/implementation/forecast-edge-data-program.md; docs/hypotheses/2026-07-17-station-specific-forecast-edge.md; docs/current-trading-system-audit.md; docs/changelog.md; learning/L0010-the-tradable-target-may-be-a-reporting-process-not-the-physical.md; generated uncommitted reports/forecast-edge/f0-truth-current/
