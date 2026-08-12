# Continuous Improvement Loop

This document defines the closed-loop process for improving RoboWeather trading performance without repeatedly rediscovering the same failure modes.

The goal is recursive improvement: every meaningful live or research lesson should either become a better hypothesis, a better replay, a better gate, or a better operating rule.

Last updated: 2026-08-12

## Storage Model

Use these canonical layers, each with a different job:

| Layer | Location | Purpose |
| --- | --- | --- |
| Approved direction | `agent_loop/STATE.md` | Compact human-approved priorities, authority boundaries, and promotion blockers. |
| Generated facts | `agent_loop/facts.json` | Regenerated repository/runtime truth and freshness warnings; never hand-edited. |
| In-flight questions | `board/` | Bounded resumable work with one question, one next action, and a named closure output. |
| Human learning memory | `learning/` | Revisitable concepts, failure mechanisms, experiences, and design intuition; never a substitute for canonical evidence. |
| Current audit | `docs/current-trading-system-audit.md` | Living financial/systems verdict, durable evidence, open risks, and current promotion blockers. |
| Current roadmap | `docs/execution-rebuild-roadmap.md` | Living phase sequence, status, and exit gates. |
| Live state and rationale | `docs/live-trading-journal.md` | What is live now, why it is live, current sizing/risk/execution rules, and material live lessons. |
| Hypothesis and decision records | `docs/hypotheses/` | Proposed strategy, sizing, model, execution, or risk changes before or during evaluation. |
| Implementation plans | `docs/implementation/` | Active feature architecture, sprint slices, and acceptance tests after a hypothesis is approved. |
| Gates | Code/tests/scripts/docs, listed in this file | Durable checks that prevent known bad decisions from recurring. |
| Generated evidence | `reports/` | Reproducible or ad hoc tables and analysis; never the canonical conclusion. |

Do not put every hypothesis directly into the live trading journal. The journal should stay focused on live state and material lessons. Use `docs/hypotheses/` for structured hypotheses and decision records; link the relevant record from the journal when a hypothesis becomes live or materially changes live assumptions.

Do not create a new one-off narrative audit for every review. Update the living audit and preserve detailed tables in a report or script. When an approved hypothesis becomes a build, keep its economic rationale in the hypothesis and move its engineering plan to `docs/implementation/`.

The coordination layer is temporal, not another source of research conclusions. `STATE.md` orients the agent, generated facts report machine truth, and board threads preserve unfinished work. Learning cards preserve what the human wants to understand and revisit. Settled financial or architectural conclusions still belong in the canonical documents above.

## Four-Pillar Edge Governance

All substantive work must advance and measure at least one component of:

```text
net trading edge = information advantage + settlement advantage
                 + execution advantage - costs and adverse selection
```

| Primary pillar | Question it must answer | Existing canonical routes |
| --- | --- | --- |
| Information | Do we know something about the outcome or price response that the executable market does not already price correctly? | `docs/implementation/forecast-edge-data-program.md`; `docs/implementation/tape-strategy-discovery.md` |
| Settlement | Do we map the exact contract-resolution source and rules more accurately or sooner than the market? | Forecast-edge F0 truth audit; settlement provenance and resolver evidence in the living audit |
| Execution | Can we convert a valid fair into fills with better timing, pricing, cancellation, or inventory behavior? | `docs/implementation/phase-3-market-tape-replay.md`; `docs/implementation/full-market-lifecycle-trading.md` |
| Costs and adverse selection | Is the result still positive after fees, spread, slippage, toxicity, capacity, concentration, and uncertainty reserves? | `docs/implementation/price-sheet-v2.md`; Phase 4 promotion gates |

Every new board thread declares one primary pillar. A hypothesis record declares its primary pillar and may name supporting pillars. Use `cross-pillar` only for an end-to-end integration gate or governance change whose output genuinely changes multiple pillars; it is not a fifth pillar and must not hide an unbounded question. Close a thread with a pillar-scoped answer. Do not claim that working infrastructure proves an advantage.

The pillars can develop independently. Promotion cannot: the exact signal, settlement mapping, execution rule, and size must clear the combined net-economic gate on the same causal evidence contract.

## Improvement Loop

The required loop is:

```text
Hypothesis -> Causal Replay -> Controlled Canary -> Settlement Review -> Lesson -> Gate
```

Each step should answer a different question:

| Step | Question |
| --- | --- |
| Hypothesis | What mechanism do we believe creates EV? |
| Causal Replay | Did it add EV after current stack order, caps, causal market state, and conservative fill assumptions? |
| Controlled Canary | Does the signal survive actual market timing, authoritative order lifecycle, fills, and settlement mechanics? |
| Settlement Review | Was the miss or win due to forecast, policy selection, execution, risk caps, or settlement mismatch? |
| Lesson | What should we change or remember? |
| Gate | How do we make sure we do not repeat the error? |

A lesson that does not become a gate, a test, a script, a checklist item, or an explicit kill rule is not yet fully captured.

## Hypothesis Records

Hypothesis records live under:

```text
docs/hypotheses/
```

Recommended filename format:

```text
YYYY-MM-DD-short-slug.md
```

Examples:

```text
docs/hypotheses/2026-06-12-global-low-mvp-size-up.md
docs/hypotheses/2026-06-12-hrrr-inland-late-overlay.md
```

A hypothesis record should be created when any of these are true:

- a new policy or overlay is being considered for live or paper;
- a current live sleeve may be resized;
- a model family may be activated or deactivated;
- execution behavior changes expected fills, slippage, or capacity;
- risk caps, station allow-lists, entry bands, or side filters may change;
- a lesson is important enough that future agents should not rediscover it.

Use the template in `docs/hypotheses/README.md`.

## Gate Locations

Gates are not all stored in one file. They should live where they are enforceable.

| Gate type | Location | Examples |
| --- | --- | --- |
| Unit/integration tests | `tests/` | station allow-list tests, tick-size order tests, partial-fill accounting tests |
| Replay gates | `scripts/` | `scripts/portfolio_promotion_report.py`, `scripts/live_policy_promotion_report.py`, `scripts/snapshot_opportunity_sweep.py` |
| Operator workflow gates | `AGENTS.md`, `docs/live-trading-journal.md` | required commands before promotion, current live-state checklist |
| Strategy/risk rules | source modules | live strategy defaults, risk caps, policy specs, station allow-lists |
| Reference rationale | `docs/` | roadmap, model-performance log, policy retrospectives |

Every gate should have an owner and a trigger:

```text
Gate: cap-aware portfolio replay before live size-up
Owner: scripts/portfolio_promotion_report.py
Trigger: any promotion, deactivation, or sizing change
Originating lesson: standalone 15m replay looked good but was negative incrementally behind the core
```

## Current Mandatory Gates

These checks are required before live policy promotion or size increase:

1. Run the cap-aware portfolio replay:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/portfolio_promotion_report.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Use `--start-date YYYY-MM-DD` for a recent-window review. Use `--no-depth` only as an upper-bound capacity diagnostic, not as promotion evidence.

2. Verify the candidate is incremental behind current stack order, caps, and recorded depth.

3. Check recent and all-loaded windows. A candidate that only wins in one window needs a clear regime explanation.

4. Confirm station allow-lists, market family, side, entry cap, and local-time filters match the intended hypothesis.

5. For live execution changes, run the focused execution tests and verify live ledger accounting assumptions.

6. Update `docs/live-trading-journal.md` when live policy mix, sizing, risk caps, entry bands, execution behavior, or material live lessons change.

7. Update `docs/changelog.md` for meaningful system or workflow changes.

8. Reconcile the conclusion into `docs/current-trading-system-audit.md`. Update `docs/execution-rebuild-roadmap.md` if phase status, sequence, or exit gates changed.

## Failure Mode Register

Known failure modes that should be checked during review:

| Failure mode | Gate or mitigation |
| --- | --- |
| Stale `research_policy_positions` used as current truth | Replay from raw `prediction_snapshots`; use `scripts/portfolio_promotion_report.py`. |
| Standalone leaderboard bias | Require cap-aware incremental portfolio replay. |
| Earlier live sleeves consume all good rows | Apply current live plan order before candidates. |
| Same station/date or bucket/side overconcentration | Apply station/date, station/date/side, exact bucket/side caps. |
| Book-depth replay mismatch | Use recorded ask-sweep fillable fields; treat `--no-depth` as upper bound only. |
| Snapshot depth treated as deterministic execution | Require fill-conditioned evidence from selected, filled, and unfilled live candidates before funded promotion. |
| Candidate-scoped event collection treated as a causal market tape | Require policy-independent pre-signal token coverage under the Phase 3 implementation plan. |
| Model rows treated as distinct tape decisions | Derive stable executable-decision keys first and reconstruct shared tape state once per replay version. |
| Lifecycle automation built before production analysis viability | Require direct-replay equivalence plus cold/resume, warm no-op, and new-data performance gates before scheduling. |
| Price changes or book touches treated as executed flow | Authoritative trade-direction tests and deterministic tape replay; fail closed on ambiguous or gapped intervals. |
| Adverse live fill selection | Run the whole-chain truth report and reject or pause sleeves where filled-subset replay materially underperforms unfilled selected replay. |
| Station allow-list leakage | Add tests around policy specs and live strategy plans. |
| Market-family leakage | Assert `HIGH_TEMP` vs `LOW_TEMP` filters in tests and replay specs. |
| Polymarket settlement differs from weather-outcome scoring | Track live settlement separately from research weather outcomes. |
| Partial fills overstated as complete | Execution tests and live ledger review. |
| Tick-size or order-version execution failures | Quantization tests and order-attempt rejection summaries. |
| Recent model family has too little sample | Canary sizing only until minimum resolved sample is met. |
| Coastal/inland or other regime transfer failure | Regime-specific replay before broad promotion. |
| Funded live execution without current fill-conditioned edge | Keep live paused or at tiny smoke-test size until actual filled R/R, filled-at-entry replay, settlement alignment, and current-window checks pass. |

## Current Phase

The Phase 3 shared market tape remains the accepted causal execution-evidence substrate. Phase 3D D0-D5 are accepted: the incremental executable-decision cache and `scripts/run_discovery.py` are the sole discovery workflow, and the TUI observes its atomically published latest-complete report/cache status. The failed multi-command scheduler remains retired and no replacement scheduler is enabled. Funded trading remains paused behind the separate Phase 4 gate.

Current phase status and acceptance gates are canonical in `docs/execution-rebuild-roadmap.md`, `docs/implementation/phase-3-market-tape-replay.md`, and `docs/implementation/tape-strategy-discovery.md`. The current financial and systems verdict is `docs/current-trading-system-audit.md`.

Before any new funded sleeve, promotion, or size-up:

1. Raw snapshot replay may identify candidates, but cannot approve funding by itself.
2. The review must compare live-selected replay, filled-at-entry replay, filled-at-actual replay, unfilled selected replay, and actual settlement.
3. The filled subset must not materially underperform the unfilled selected subset.
4. Any replay capacity claim must be downgraded by observed fill behavior or a documented fill-probability model.
5. If the recent raw replay is negative, the sleeve stays stopped regardless of execution improvements.
6. Existing candidate-scoped shadow labels do not count as fill evidence until their semantics are rebuilt and validated against the shared tape.
7. Minimum-risk funded orders may validate plumbing after shadow reconstruction passes, but normal promotion requires direct useful-size evidence.

## Scheduled Review Cadence

### Weekly Retrospective Report

Run the live trading retrospective manually on Sunday after markets resolve or Monday before sizing/promotion decisions:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/trading_retrospective_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date YYYY-MM-DD --end-date YYYY-MM-DD --out reports/trading-retrospectives/weekly-YYYY-Www.md
```

The report compares uncalibrated model-implied EV from entry edge, empirical replay EV/PnL, live realized PnL, fills vs intended notional, rejects by reason, current-stack research replay for the same window, policy review/kill threshold flags, and integrated promotion/candidate replay review.

### Daily or after each resolved batch

Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/portfolio_promotion_report.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date YYYY-MM-DD
```

Review:

- current live sleeves: incremental R/R, risk, PnL, skipped caps, skipped depth;
- candidate sleeves: whether they are additive or duplicate;
- live order attempts: rejected reasons, partial fills, TTL-expired ladders;
- live settlements: where live PnL diverged from replay assumptions.

### Weekly

Review open hypothesis records in `docs/hypotheses/`:

- close hypotheses that have enough evidence;
- update kill conditions if replay or live settlement changed;
- promote durable lessons into tests, scripts, or operator checklist gates;
- prune stale research-only ideas.

### Before any sizing increase

Required checklist:

- portfolio replay all-loaded and recent windows are positive;
- replay uses current caps and recorded depth;
- live execution path has no unresolved accounting bug for that sleeve;
- candidate does not rely on a known-bad station/regime transfer;
- hypothesis record includes a kill condition and review trigger;
- journal/changelog updates are prepared.

## Lesson-To-Gate Rule

Use this mapping:

| If the lesson is... | Convert it into... |
| --- | --- |
| A code behavior bug | A regression test in `tests/`. |
| A promotion/evaluation mistake | A replay script rule or required report. |
| An operator workflow miss | An `AGENTS.md` instruction or journal checklist. |
| A sizing/risk mistake | A risk cap, sizing rule, or portfolio replay check. |
| A model/regime insight | A hypothesis record and replay filter. |
| A live execution issue | An execution test plus order-attempt diagnostic query/report. |

## Current Improvement Backlog

| Primary pillar | Item | Status |
| --- | --- | --- |
| Cross-pillar governance | Living audit and execution roadmap | Implemented in `docs/current-trading-system-audit.md` and `docs/execution-rebuild-roadmap.md`. |
| Information | Cap-aware policy selection and causal discovery | Implemented as research gates; no robust exact candidate has passed. |
| Information | Forecast-edge F0B and distinct-source program | Ready behind target/evaluation truth; existing fairs remain overconfident. |
| Settlement | Forecast-edge F0 venue/source/sensor truth audit | Ready and prioritized before trusting training or resolution labels. |
| Execution | Shared market tape, exact replay, fill/queue/cancellation/markout truth | Tape substrate accepted; fill-conditioned execution advantage remains unproven. |
| Costs and adverse selection | [Price Sheet V2a/V2b](implementation/price-sheet-v2.md), useful-size capacity, and concentration | Research-only; combined net economics have not passed. |
| Cross-pillar governance | `scripts/trading_retrospective_report.py` | Implemented for manual Sunday/Monday weekly review. |

## Closing A Hypothesis

A hypothesis should be closed with one of these outcomes:

| Outcome | Meaning |
| --- | --- |
| Promote | Meets replay, live canary, execution, and risk requirements. |
| Canary | Positive but sample/capacity/execution evidence is incomplete. |
| Research only | Interesting but not live-ready. |
| Reject | Replay or live evidence is negative. |
| Superseded | Replaced by a better formulation. |

When a hypothesis closes, update its record and, if live behavior changed, update the journal and changelog.
