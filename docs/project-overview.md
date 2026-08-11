# Project Overview

RoboWeather is a research-first weather prediction-market system. The current priority is determining whether promising forecast signals can be converted into positive fill-conditioned PnL through causal market-data collection, conservative pricing, and controlled execution evidence.

Last reviewed: 2026-08-11

## Documentation Map

Use these documents as the canonical entry points:

| Question | Canonical document |
| --- | --- |
| What direction and authority has the human approved? | `agent_loop/STATE.md` |
| What do the repository and runtime report now? | Generated `agent_loop/facts.json` |
| What bounded work is active, parked, or waiting? | Generated `board/INDEX.md`, then the selected thread |
| What concepts, failures, and design intuition can I revisit? | Generated `learning/INDEX.md`, then the selected learning card |
| What is the current financial/systems verdict? | `docs/current-trading-system-audit.md` |
| What phase are we in and what gates come next? | `docs/execution-rebuild-roadmap.md` |
| What is currently funded or paused? | `docs/live-trading-journal.md` |
| Why might a strategy or infrastructure idea work? | Dated record under `docs/hypotheses/` |
| How is the active feature being built and accepted? | Plan under `docs/implementation/` |
| What changed chronologically? | `docs/changelog.md` |
| Where do generated tables and ad hoc analysis go? | `reports/`, treated as non-canonical evidence |

Use the fixed agent read order in `AGENTS.md`. Update living documents in place. Do not create a new standalone audit report when new evidence can update the current audit, roadmap, hypothesis decision log, or live journal. Never hand-edit generated facts or the board index.

## Current Mode

Funded trading is paused. The Phase 3 shared active-universe market-tape collector is reported built and running, with replay and validity evidence accumulating. Price Sheet V2a is the current implementation priority; V2b will consume valid tape windows. The active research loop remains the signal and outcome source of truth, while the validated market tape becomes the execution-evidence source of truth.

The research loop:

- collects live prediction snapshots during the station-local entry window;
- writes policy positions for first-eligible strategy hypotheses;
- resolves station/date outcomes from IEM ASOS official max temperatures;
- supports policy leaderboard analysis from the research SQLite database.

The active local research database is usually:

```text
/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Repository SQLite copies under `data/paper/` may be stale and should be treated as snapshots or test artifacts unless explicitly known to be current.

## Research Data Model

Use the research tables as separate layers, not as interchangeable sources:

| Layer | Role | Primary use |
| --- | --- | --- |
| `prediction_snapshots` | Raw opportunity tape from the research loop. Stores model, strategy, selected bucket/side, edge, market price, liquidity, execution diagnostics, and timing for each observed signal. | Broad discovery and retrospective analysis. |
| `prediction_results` | Resolved outcome tape keyed by `prediction_snapshot_id`. Stores official weather outcome, selected-side correctness, entry price, and hypothetical snapshot PnL. | Snapshot-level scoring. |
| `research_policy_positions` | Materialized policy views derived from snapshots or consensus rows after applying policy gates and a de-duplication scope. | Candidate policy replay and scorecards. |
| `paper_policy_positions` | Paper execution ledger for allowlisted policies. | Execution rehearsal, order logging, fills/marks/settlement mechanics. |

The active research loop should keep collecting broad `prediction_snapshots`. That is the most valuable source of truth and should not be narrowed prematurely. `research_policy_positions` are useful, but they are not the full research universe; they are saved hypotheses or scorecard views over the broader snapshot tape.

Recommended analysis sequence:

1. Collect broad snapshots and liquidity/execution diagnostics in the research loop.
2. Resolve snapshots through `prediction_results` using official station outcomes.
3. Analyze snapshots first by model, consensus group, strategy, side, bucket, obs-delay bucket, entry band, edge band, liquidity, station, and local time.
4. Define candidate policy rules only after the broad snapshot analysis shows a stable pattern.
5. Replay those rules into `research_policy_positions` with an explicit scope such as `station_date` for scorecards or `station_date_bucket_side_obs_delay` for opportunity-level diagnostics.
6. Promote only a small number of explicit candidate policies to paper execution.
7. Treat raw replay as hypothesis evidence only.
8. Require causal tape replay and controlled real-order validation before normal funded execution.

`station_date` policy scope is a clean comparison layer: one first eligible position per station/date/family/policy. `station_date_bucket_side_obs_delay` is the broader opportunity-capture layer: one first eligible position per station/date/family/bucket/side/obs-delay/policy. Both can be rebuilt retroactively from `prediction_snapshots` for current policy families, including consensus groups when the required model snapshots exist.

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

The research ledger should remain valuable as an audit trail and analysis source, but it should not be the queue that drives live order submission. Execution should be driven by fresh candidates joined to causal market state, with durable storage used for dedupe, state, risk, replay, and audit history.

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

Research mode remains the right default until the exact signal, fair-value version, quote/cancellation rule, and size pass the gates in `docs/current-trading-system-audit.md` and `docs/execution-rebuild-roadmap.md`.
