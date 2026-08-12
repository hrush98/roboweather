# Agent Loop State

Last reviewed: 2026-08-12
Human approval: initial coordination layer and corrected deterministic-discovery direction approved on 2026-08-11; four-pillar edge governance approved on 2026-08-12; later strategic changes require human approval.

## Current Verdict

RoboWeather is a four-pillar net-edge research system with funded trading paused. The shared market tape and deterministic discovery path are valid research substrates, but no pillar has yet produced sufficiently robust forward profitability. Promotion still requires an exact candidate version with venue-authoritative settlement, valid markouts, causal tape coverage, conservative positive economics, concentration controls, and controlled useful-size evidence.

The authoritative financial and systems assessment remains `docs/current-trading-system-audit.md`. This file is a compact orientation layer, not a replacement for that audit.

## Four-Pillar Edge Governance

```text
net trading edge = information advantage + settlement advantage
                 + execution advantage - costs and adverse selection
```

| Pillar | Proof target | Current direction |
| --- | --- | --- |
| Information | A causal outcome estimate or deterministic signal that beats the contemporaneous executable market on untouched data. | Treat the zero-survivor broad causal holdout as negative evidence, repair target/evaluation truth, then test genuinely distinct forecast information. |
| Settlement | More accurate contract-resolution mapping or faster settlement-compatible truth than the market. | Reconcile venue, Weather Underground, CLI, IEM, and high-frequency ASOS before trusting labels or high-so-far rules. |
| Execution | Better conversion of a valid fair into filled PnL through timing, price, quoting, cancellation, and inventory behavior. | Preserve causal tape/replay and require actual markout/fill evidence; working plumbing is not yet a proven advantage. |
| Costs and adverse selection | Positive net economics after fees, spread, slippage, toxicity, capacity, and concentration. | Keep reserves explicit and prove fill-conditioned useful-size economics before promotion. |

The pillars may progress independently, but funded promotion is an integrated gate: strength in one pillar cannot silently compensate for missing evidence in another. Every new board thread declares one primary pillar. `cross-pillar` is reserved for integration or governance and creates no additional source of edge. Existing plans remain where they are.

## Critical Path

1. Keep policy-neutral snapshot, tape, outcome, and settlement evidence collection healthy.
2. Preserve the completed bounded deterministic discovery and immutable-candidate path. Its wide untouched holdout produced zero survivors; do not keep widening the grammar without a new causal mechanism.
3. Shift new research capacity toward settlement truth and honest information-edge evaluation through the F0/F0B queue in `docs/implementation/forecast-edge-data-program.md`.
4. Integrate any surviving signal through separately versioned pricing, execution, cost, adverse-selection, concentration, and useful-size gates.
5. Package a Phase 4 request only after all four pillars pass for one exact candidate and the D6 gates in `docs/implementation/tape-strategy-discovery.md` pass.

## Approved Parallel Work

- Forecast-edge research may continue under `docs/implementation/forecast-edge-data-program.md` without changing funded authority or bypassing the execution critical path.
- Full-market-lifecycle collection and horizon research may continue under `docs/implementation/full-market-lifecycle-trading.md`.
- Price Sheet V2 work remains research-only until its causal calibration and untouched-forward gates pass.

## Funded Authority

- Funded trading is paused.
- Research roles such as champion or challenger confer no funded authority.
- No agent may infer promotion from snapshot prices, optimistic queue assumptions, weather-only settlement, a stale materialized policy table, or a generated report alone.
- Any change to funded status, live policy mix, sizing, entry caps, risk caps, or execution authority requires explicit human approval and an update to `docs/live-trading-journal.md`.

## Current Promotion Blockers

- Venue-authoritative settlement evidence is not yet sufficient.
- Required post-fill markouts remain unavailable or incomplete.
- No exact candidate has passed the complete conservative/base Phase 3D-to-Phase-4 evidence gate.
- Useful-size fill-conditioned evidence remains open.

Machine-observed status and freshness belong in `agent_loop/facts.json`; do not copy volatile counts or timestamps into this file.

## Board Adoption

The board began empty at initialization. New bounded work must start through `$start-thread` or `scripts/agent_loop.py start-thread`. Current thread status belongs in generated `board/INDEX.md`; do not copy volatile thread IDs here. Do not retroactively convert every historical implementation plan into an open thread.

## Learning Memory

Transferable concepts, failure mechanisms, lived experiences, and system-design intuition belong in `learning/L####-*.md`. Their generated revisit queue is `learning/INDEX.md`. Learning cards preserve the concrete incident separately from its interpretation and mature from `CAPTURED` through `REVISIT` to `INTEGRATED`, or remain historically visible as `SUPERSEDED`.

Learning cards are for the human learner. They do not replace tests, gates, canonical conclusions, or funded authority. Capture them with `$capture-learning`; append reflection with `$revisit-learning` rather than rewriting the original observation.

## Do Not Do

- Do not start or bypass a second writer against a locked runtime database.
- Do not treat unavailable runtime inspection as evidence of failure.
- Do not hand-edit `agent_loop/facts.json` or `board/INDEX.md`.
- Do not hand-edit `learning/INDEX.md` or treat a provisional learning card as system truth.
- Do not open more than seven work threads or keep more than three active.
- Do not put implementation detail, command catalogs, or historical evidence into this state file.
- Do not rewrite settled history silently; append a dated decision or supersede it explicitly.

## Read Order

1. `AGENTS.md`
2. `agent_loop/STATE.md`
3. `agent_loop/facts.json`
4. `board/INDEX.md`
5. The selected board thread
6. Only the canonical documents linked by that thread
7. Code and runtime evidence

## State Update Rule

Agents may propose a complete replacement of this file when strategy, critical-path priority, accepted evidence, or funded authority changes. The human approves those judgment-bearing changes. Routine machine facts must be regenerated instead. Keep this file below 1,500 words.
