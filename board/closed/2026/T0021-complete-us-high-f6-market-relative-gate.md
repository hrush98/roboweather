---
id: T0021
title: Complete US-high F6 market-relative gate
status: CLOSED
pillar: cross-pillar
priority: normal
owner: Codex
opened: 2026-08-13
last_touched: 2026-08-13
facts_fingerprint: 81d291a9fe2490969da8149e7605af499d95e88bedeb0ab62a866386d5d9f3df
closed: 2026-08-13
---

# T0021 Complete US-high F6 market-relative gate

## Question

Does frozen US-high F3 pass the complete Price Sheet V2 selected, quoted-price, market-relative, executable edge-half-life, and tape-backed research contract at one lifecycle horizon?

## Outcome

Rejected: centralized exact-cutoff revalidation rejects F3 on market-relative log loss, and F6 fails selected-market, preactivation-calibration, quoted-price, and useful-size tape gates.

## Evidence

- 51 focused tests passed; F3 historical/forward post-cutoff rows=0; corrected F3 holdout market logloss delta=+0.00258 and recent=+0.03028; F6 selected logloss delta=+0.14519; t0 2/18 fillable at 5 with median reserved net edge=-0.16308

## Durable Output

scripts/forecast_f6_report.py; reports/forecast-edge/f3-current/report.md; reports/forecast-edge/f4-current/report.md; reports/forecast-edge/f6-current/report.md; canonical forecast plan/audit/changelog updates
