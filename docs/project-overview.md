# Project Overview

RoboWeather is a research-first system for U.S. same-day daily-high temperature markets. The current priority is discovering robust policy rules from live-collected forecasts, market prices, and official station outcomes before committing to live execution.

## Current Mode

The active research loop is the main source of truth. It:

- collects live prediction snapshots during the station-local entry window;
- writes policy positions for first-eligible strategy hypotheses;
- resolves station/date outcomes from IEM ASOS official max temperatures;
- supports policy leaderboard analysis from the research SQLite database.

The active local research database is usually:

```text
/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Repository SQLite copies under `data/paper/` may be stale and should be treated as snapshots or test artifacts unless explicitly known to be current.

## Paper Execution Status

The standalone paper-policy loop exists, but it is not the preferred long-term execution design. It scans `research_policy_positions` from SQLite, promotes allowlisted policy rows, then attempts simulated execution from those stored rows.

This is useful for auditing persistence, book fetching, order-attempt logging, marks, and settlement mechanics. It is less useful as evidence that the future live system is ready because it can test delayed DB promotion rather than fresh signal execution.

Observed limitations:

- stale allowlisted rows can be promoted after the market/books are no longer live;
- execution timing depends on a separate polling loop;
- paper behavior depends on the promoted-policy allowlist rather than the most current research conclusion;
- the DB-scanning model does not closely match the desired live trading path.

Do not over-invest in the standalone paper loop unless it is needed for diagnostics or a narrow guardrail fix.

## Preferred Execution Direction

When a policy is ready, the production path should be one integrated live loop:

```text
fetch markets/books
fetch latest observations
run models
build policy candidates
apply promoted policy gates
apply risk gates
verify fresh signal and fresh book
submit order through dry_run, paper, or live adapter
write research and execution ledgers
mark and settle existing positions
```

The research ledger should remain valuable as an audit trail and analysis source, but it should not be the queue that drives live order submission. Execution should be driven by fresh in-memory candidates, with SQLite used for dedupe, state, risk, and audit history.

Before real-money trading, run the integrated loop in `dry_run` or `paper_submit` mode. The rehearsal should exercise the same code path intended for live trading, with only the final submit adapter changed.

## Readiness Bar For Live Trading

Before enabling live orders, require:

- an explicitly allowlisted policy or policy family;
- station/date exposure caps;
- per-order and daily risk caps;
- no stale signal execution;
- no stale book execution;
- strict market-date validation;
- duplicate exposure prevention;
- clear order-attempt logging;
- mark and settlement visibility;
- a kill switch.

Research mode remains the right default until a policy has enough resolved live evidence and the integrated execution path exists.
