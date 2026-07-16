# Execution Rebuild Roadmap

This is the living roadmap for turning RoboWeather research signals into measurable, fill-conditioned trading evidence. Update this document when phase status, sequencing, or exit gates change. Detailed economic ideas belong in `docs/hypotheses/`; active feature design belongs in `docs/implementation/`.

Last updated: 2026-07-16

## Objective

Establish whether an exact weather signal can be traded through an exact execution tactic at useful size with positive net, settlement-aligned PnL.

```text
forecast signal
-> calibrated quoteable fair
-> causal market state
-> quote/taker decision
-> fill or miss
-> markout
-> Polymarket settlement
-> portfolio PnL
```

## Current Phase

The Phase 3 market-tape recorder now has repository-validated rotation, reconnect/gap semantics, resource telemetry, strict health checks, deterministic arbitrary-time book reconstruction, and a generic latency-aware causal decision join. Complete-lifecycle capacity evidence, real decision integration, and fill replay remain open. Price Sheet V2a can continue independently; V2b will consume only valid Phase 3 tape windows. Funded trading remains paused.

Canonical records:

- Current assessment: `docs/current-trading-system-audit.md`
- Economic hypothesis: `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`
- Tape implementation/acceptance: `docs/implementation/phase-3-market-tape-replay.md`
- Active pricing implementation: `docs/implementation/price-sheet-v2.md`
- Funded operating state: `docs/live-trading-journal.md`

## Phase Status

| Phase | Purpose | Status | Exit condition |
| --- | --- | --- | --- |
| 0. Whole-chain instrumentation | Link candidates, decisions, orders, fills, and settlement. | Prototype complete | Exact live candidate-to-settlement reconstruction exists. |
| 1. Quoteable fair/price sheet | Convert model output into a conservative maximum price. | V2a current implementation priority | Positive walk-forward quoted-price EV without extreme uncalibrated fairs. |
| 2. Shadow quote construction | Generate auditable quote intents and cancellation metadata. | Plumbing prototype complete | Deterministic intent construction tests pass. No profitability claim. |
| 3. Shared market tape and replay | Collect pre-signal active-universe market events and replay quote tactics causally. | Recorder/book slices validated; long-run collection and decision/fill replay remain open | Tape validity, deterministic book replay, conservative fill bounds, and forward shadow reporting pass. |
| 4. Funded validation | Validate replay fidelity and useful-size fill-conditioned PnL with controlled real orders. | Blocked on validated V2a + V2b configuration | Plumbing canary passes, then `$50` and `$100` size-specific evidence passes. |
| 5. Learned quote policy and sizing | Select quote/skip/size from calibrated signal and microstructure state. | Future | Sufficient clean Phase 3/4 data and stable out-of-sample improvement. |

## Price Sheet V2 Workstreams

1. V2a outcome pricing is the immediate critical path:
   - freeze the initial late HRRR signal definitions;
   - build causal fit and evaluation datasets;
   - run expanding-window calibration with a decision-time market reference;
   - produce a conservative outcome fair and maximum economic quote price;
   - require positive out-of-fold theoretical quoted-price EV before shadow promotion.
2. V2b execution overlay starts on valid Phase 3 windows:
   - materialize decision-time book, queue, flow, latency, and coverage features;
   - reduce V2a price/size or skip based on toxicity and capacity;
   - compare frozen passive and stable-taker arms;
   - require positive base-case fill-conditioned EV before requesting Phase 4.

The detailed contracts, module boundaries, slices, tests, and acceptance gates are in `docs/implementation/price-sheet-v2.md`.

## Phase 3 Workstreams

1. Active-universe token discovery independent of current policies.
2. Separate bounded recorder service with dynamic subscriptions and restart supervision.
3. Immutable partitioned raw-event storage plus a compact catalog and coverage ledger.
4. Deterministic book reconstruction with checkpoints, reconnect/resync behavior, and invalid gap intervals.
5. Causal joins from observation/decision availability to token tape.
6. Correct trade-direction, queue, cancellation, book-touch, markout, and settlement labels.
7. Frozen forward quote-policy evaluation, including passive and stable-taker controls.
8. Private order/user-channel capture for later real-canary ground truth.

The detailed schema, module boundaries, sprint slices, and acceptance tests are in `docs/implementation/phase-3-market-tape-replay.md`.

## Phase 3 Exit Gates

- The full supported active weather-token universe remains subscribed through a complete market lifecycle.
- A bounded queue and storage process stay within declared memory, disk, and receipt-lag budgets.
- Each event has a stable session, token, feed timestamp when available, local receipt timestamp, and monotonic receipt ordering.
- A book can be reconstructed deterministically from a checkpoint and ordered deltas.
- Reconnects, drops, stale feeds, and invalid intervals are detected and reported.
- A fixed signal timestamp and quote specification produce the same replay result on repeated runs.
- Executed flow uses authoritative trade events; placements and cancellations are not counted as trades.
- Every quote outcome states whether coverage was valid from before placement through termination.
- Conservative, base, and optimistic labels are clearly separated.
- Forward reports use immutable hypothesis versions and activation timestamps.

## Phase 4 Validation Ladder

Phase 4 separates two questions that earlier documentation mixed together:

1. Plumbing validation: minimum-risk funded orders may be used to verify public replay against authoritative user-channel placement, fill, partial-fill, and cancellation events. These orders do not count as capacity or promotion evidence.
2. Capacity validation: `$50` is the minimum useful-size test and `$100` is the target-capacity test. Results authorize only the tested tactic and size.

Normal sizing does not follow from an arbitrary count of fills. Counts are smoke gates; promotion also requires uncertainty-aware evidence across independent market dates/regimes, a comparable missed-order sample, non-toxic markouts, and positive settlement-aligned net PnL.

## Parallel Research During Phase 3

- Continue broad causal prediction snapshots and official outcome resolution.
- Freeze the late HRRR-rich tuned dynamic and HRRR-v2 dynamic signal definitions for forward shadow evaluation.
- Keep the two-of-four late agreement rule exploratory until its exact definition and activation time are recorded.
- Implement Price Sheet V2a around walk-forward calibration and market-aware shrinkage.
- Do not add funded strategies or expand normal risk caps.

## Roadmap Update Protocol

- Change this document when a phase starts, passes, fails, or changes scope.
- Put detailed implementation progress in the active implementation plan.
- Put economic evidence and falsification in the hypothesis record.
- Put current funded policy, sizing, and execution state in the live journal.
- Put chronological completed changes in the changelog.
