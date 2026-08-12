---
id: T0010
title: Run maximally broad causal constraint search
status: CLOSED
pillar: information
priority: high
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: f9f4007159d3f393d4b401d8a933cfc5945b4d65ca23caee3783affbd57e0cc9
closed: 2026-08-12
---

# T0010 Run maximally broad causal constraint search

## Question

Does an efficiently enumerated maximally broad causal grammar over the current executable-decision cache reveal any robust executable strategy family?

## Outcome

A maximally broad finite causal grammar was searched efficiently from the accepted decision cache. The run represented 20,167,999,488,000 syntactic rules, scored 49,620,192 behavior-normalized risk variants, found 29,900 strict discovery passers, collapsed them outcome-blind to 15 correlated representatives, and every representative lost money on the untouched 2026-08-07 through 2026-08-11 holdout; therefore zero strategy family emerged at this cutoff.

## Evidence

- Production cache contract ab785d646a2143c0db0aa6ca164d4fc64d410fe06b97a5ae5219f6a79bb2afcd had 16,015 eligible rows, zero invalid rows, and zero pending decisions; run hash 03cb8e7ad149d2d1f125908e6a5152a5ecae3a0bbd5f509568e3de47a650da9e; all 15 holdout PnLs were negative from -50.00 to -460.93; 6 focused/regression tests passed.

## Durable Output

weather_trader/discovery/wide_analysis.py; scripts/wide_constraint_search.py; tests/test_wide_analysis.py; commit 3392ebd; generated uncommitted report reports/discovery/absolute-wide-2026-08-12/result.json; learning/L0007-search-breadth-cannot-manufacture-forward-edge.md
