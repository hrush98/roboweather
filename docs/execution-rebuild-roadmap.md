# Execution Rebuild Roadmap

This is the living roadmap for turning RoboWeather research signals into measurable, fill-conditioned trading evidence. Update this document when phase status, sequencing, or exit gates change. Detailed economic ideas belong in `docs/hypotheses/`; active feature design belongs in `docs/implementation/`.

Last updated: 2026-08-04

## Objective

Establish whether an exact weather signal can be traded through an exact execution tactic at useful size with positive net, settlement-aligned PnL across the observable market lifecycle.

```text
market listing + causal forecast state
-> calibrated quoteable fair
-> causal market state
-> policy-neutral constrained discovery
-> immutable strategy manifest
-> untouched forward tape
-> quote/taker decision
-> fill or miss
-> markout
-> Polymarket settlement
-> portfolio PnL
```

## Current Phase

The Phase 3 market-tape recorder has retained policy-independent data since July 23 and the repository includes a frozen-portfolio batch taker holdout. Its completed 72-hour run recorded 224.6 million events with zero reconstruction errors and about 96.7% valid eligible-token coverage. Periodic reconnect gaps and late initial discovery are accepted research limitations, not critical-path blockers; causal replay continues to reject each affected decision window rather than filling across missing tape. Recorder heartbeat/reconnect hardening and another full lifecycle run are deferred unless missing coverage materially obstructs candidate evaluation. One real persisted quote/tape reconstruction, passive fill bounds, markouts, and venue settlement remain open. Price Sheet V2a Slices 0-3 are implemented and have nonempty current-database evidence. Slice 3 keeps calibrator selection unfrozen, computes reserves only from prior out-of-fold market dates, and leaves both pilots research-only until a baseline and untouched forward window are declared.

Phase 3D now has a repository implementation for its immutable run contract, broad causal materializer, constrained fixed-rule discovery, complexity penalty, correlated-family collapse, one-winner freeze, and post-activation stable-taker evaluator. No production run or prospective manifest has been frozen. Current ledgers have no venue-resolution rows and Phase 3 markouts remain open, so the forward gate can only continue collecting or reject until those sources exist. The named V2a pilots remain vertical controls and receive no presumption that one must become the deployed strategy.

A full-market-lifecycle forecast/data program is approved as a parallel research build. It extends collection to first listing and builds one continuously updating pricing and inventory engine with separately validated D-1, early-day, intraday, and late information states. It does not change the current critical path: the bounded late Price Sheet V2a pilot remains first, with earlier lifecycle regions progressively enabled after their forecast, tape, inventory, and execution gates pass. Funded trading remains paused.

Canonical records:

- Current assessment: `docs/current-trading-system-audit.md`
- Economic hypothesis: `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`
- Tape implementation/acceptance: `docs/implementation/phase-3-market-tape-replay.md`
- Strategy discovery/freeze implementation: `docs/implementation/tape-strategy-discovery.md`
- Active pricing implementation: `docs/implementation/price-sheet-v2.md`
- Forecast-data implementation: `docs/implementation/forecast-edge-data-program.md`
- Full-lifecycle implementation: `docs/implementation/full-market-lifecycle-trading.md`
- Full-lifecycle hypothesis: `docs/hypotheses/2026-07-17-full-market-lifecycle-trading.md`
- Rolling tape portfolio hypothesis: `docs/hypotheses/2026-07-29-rolling-tape-portfolio-discovery.md`
- Funded operating state: `docs/live-trading-journal.md`

## Phase Status

| Phase | Purpose | Status | Exit condition |
| --- | --- | --- | --- |
| 0. Whole-chain instrumentation | Link candidates, decisions, orders, fills, and settlement. | Prototype complete | Exact live candidate-to-settlement reconstruction exists. |
| 1. Quoteable fair/price sheet | Convert model output into a conservative maximum price. | V2a Slices 0-3 repository complete; no calibrator selected and untouched forward gate open | Positive walk-forward quoted-price EV without extreme uncalibrated fairs. |
| 2. Shadow quote construction | Generate auditable quote intents and cancellation metadata. | Plumbing prototype complete | Deterministic intent construction tests pass. No profitability claim. |
| 3. Shared market tape and replay | Collect pre-signal active-universe market events and replay quote tactics causally. | Research substrate accepted at about 96.7% valid coverage with gap-affected decisions rejected; recorder hardening deferred. Slice 4 real join and passive/markout Slices 5-6 remain open. | Tape validity, deterministic book replay, conservative fill bounds, and forward shadow reporting pass. |
| 3D. Policy-neutral strategy discovery and freeze | Discover simple strategies from broad causal snapshot + tape + settlement rows without preselecting the winner. | D0-D3 repository path implemented; no production winner frozen; D4 blocked on prospective activation, venue settlement, and markouts | Predeclared walk-forward search selects at most one simple winner, freezes it before activation, and produces an untouched future-tape verdict. |
| F. Full-lifecycle forecast/data foundation | Observe weather and market state from first listing and price horizon-specific distributions. | Approved parallel research build; no production consumer | Venue-aligned truth, causal D-1 sources, first-listing tape, horizon calibration, and inventory-aware replay pass. |
| 4. Funded validation | Validate replay fidelity and useful-size fill-conditioned PnL with controlled real orders. | Blocked on a passing immutable Phase 3D manifest with validated V2a + V2b pricing/execution | Plumbing canary passes, then `$50` and `$100` size-specific evidence passes. |
| 5. Learned quote policy and sizing | Select quote/skip/size from calibrated signal and microstructure state. | Future | Sufficient clean Phase 3/4 data and stable out-of-sample improvement. |

## Price Sheet V2 Workstreams

1. V2a outcome pricing is the immediate critical path:
   - freeze the initial late HRRR signal definitions (complete);
   - build causal fit and evaluation datasets (complete, including current remote DB smoke);
   - run expanding-window calibration with a decision-time market reference (complete; fitted baselines did not beat market);
   - produce a conservative outcome fair and maximum economic quote price (complete);
   - freeze one calibrator and untouched forward start before inspecting that window;
   - require positive out-of-fold theoretical quoted-price EV before shadow promotion.
2. V2b execution overlay starts on valid Phase 3 windows:
   - materialize decision-time book, queue, flow, latency, and coverage features for both the broad policy-neutral discovery view and frozen V2 views;
   - reduce V2a price/size or skip based on toxicity and capacity;
   - compare frozen passive and stable-taker arms;
   - require positive base-case fill-conditioned EV before requesting Phase 4.

The detailed contracts, module boundaries, slices, tests, and acceptance gates are in `docs/implementation/price-sheet-v2.md`.

## Policy-Neutral Strategy Discovery And Freeze

Strategy selection is downstream of measurement. Do not hard-code a named MVP,
model family, side, station exception, or clock variant as the final strategy
before the broad causal substrate is available.

```text
market tape + causal forecast/model snapshots + venue settlement
-> constrained date-ordered discovery
-> complexity penalty and correlated-family collapse
-> at most one simple primary winner
-> immutable manifest and activation boundary
-> untouched future tape
-> controlled real-order request
```

The Phase 3D implementation must:

1. Build a reproducible broad discovery view over all eligible causal
   snapshot/token decisions, not only selected V2a pilots.
2. Predeclare source cutoffs, search grammar, complexity budget, folds,
   effective-sample requirements, costs, fill scenario, size, caps, stability
   tests, and winner-selection rule before ranking.
3. Fit calibration and thresholds only on dates before each walk-forward fold,
   with primary uncertainty clustered by market date.
4. Collapse nearby delay, window, price-cap, scope, and model variants into
   correlated families rather than counting them as independent confirmation.
5. Prefer the simplest stable family to the highest in-sample return and select
   at most one primary winner initially.
6. Freeze the exact signal, pricing, execution, size, risk, source, code, and
   activation manifest before any holdout decision is inspected.
7. Evaluate only post-activation tape with venue settlement, fail-closed
   coverage, fill-conditioned markouts, and current portfolio caps.
8. Send only an exact passing manifest to Phase 4; a failed holdout creates no
   permission to add a retrospective rescue filter.

The detailed contract is
`docs/implementation/tape-strategy-discovery.md`.

## Full-Market-Lifecycle Expansion

The intended steady state is one continuous event-driven engine traversing separately versioned and validated lifecycle states:

```text
market listed
-> D-1 open -> D-1 forecast revision -> D0 pre-dawn/morning
-> D0 midday observations -> D0 late remaining heating
-> settlement
```

The engine continues evaluating between named transitions whenever causal
forecast, observation, book, or inventory state changes. Horizon labels provide
calibration, uncertainty, reporting, risk, activation, and rollback boundaries;
they are not intended to create unrelated permanent clock-window strategies.

The expansion follows these gates:

1. Observe current and future-dated weather markets from first listing and measure actual lifecycle liquidity and price discovery.
2. Build causally timestamped, station-specific D-1 distributions from WeatherNext/NBM and eligible short-range sources.
3. Extend the continuous Price Sheet V2 consumer one validated lifecycle region at a time, using horizon-sensitive or explicitly pooled calibration and uncertainty/inventory reserves.
4. Replay quote activation, scheduled-release cancellation/repricing, filled inventory, exit, and hold-to-settlement behavior.
5. Feed eligible lifecycle rows into Phase 3D, freeze at most one simple winner
   before activation, and compare its untouched forward evidence with the late
   control under shared portfolio caps.
6. Request controlled funded validation only for the exact immutable horizon,
   tactic, inventory cap, exit rule, and size that pass.

The complete implementation contract is `docs/implementation/full-market-lifecycle-trading.md`.

## Phase 3 Workstreams

1. Active-universe token discovery independent of current policies.
2. Separate bounded recorder service with dynamic subscriptions and restart supervision.
3. Immutable partitioned raw-event storage plus a compact catalog and coverage ledger.
4. Deterministic book reconstruction with checkpoints, reconnect/resync behavior, and invalid gap intervals.
5. Causal joins from observation/decision availability to token tape.
6. Correct trade-direction, queue, cancellation, book-touch, markout, and settlement labels.
7. Policy-neutral broad discovery materialization across eligible causal snapshots.
8. Constrained walk-forward strategy discovery and immutable winner freezing.
9. Frozen forward quote-policy evaluation, including passive and stable-taker controls.
10. Private order/user-channel capture for later real-canary ground truth.

The detailed schema, module boundaries, sprint slices, and acceptance tests are in `docs/implementation/phase-3-market-tape-replay.md`.

## Phase 3 Exit Gates

- Discovery lag and missing initial coverage are reported explicitly; research may proceed on the actually observed tape without claiming unobserved first-listing coverage.
- A bounded queue and storage process stay within declared memory, disk, and receipt-lag budgets.
- Each event has a stable session, token, feed timestamp when available, local receipt timestamp, and monotonic receipt ordering.
- A book can be reconstructed deterministically from a checkpoint and ordered deltas.
- Reconnects, drops, stale feeds, and invalid intervals are detected and reported. Global lifecycle availability is monitored rather than used as a strategy-discovery blocker; decision replay rejects any gap in its pre-signal-through-termination window.
- A fixed signal timestamp and quote specification produce the same replay result on repeated runs.
- Executed flow uses authoritative trade events; placements and cancellations are not counted as trades.
- Every quote outcome states whether coverage was valid from before placement through termination.
- Conservative, base, and optimistic labels are clearly separated.
- Broad discovery inputs are independent of current policy and V2 pilot selection.
- Discovery cutoffs, grammar, folds, metrics, costs, and complexity rules are immutable before ranking.
- A strategy manifest and activation time are frozen before its forward rows exist.
- Forward reports use immutable hypothesis versions and activation timestamps.

## Phase 4 Validation Ladder

Phase 4 separates two questions that earlier documentation mixed together:

1. Plumbing validation: minimum-risk funded orders may be used to verify public replay against authoritative user-channel placement, fill, partial-fill, and cancellation events. These orders do not count as capacity or promotion evidence.
2. Capacity validation: `$50` is the minimum useful-size test and `$100` is the target-capacity test. Results authorize only the tested tactic and size.

Normal sizing does not follow from an arbitrary count of fills. Counts are smoke gates; promotion also requires uncertainty-aware evidence across independent market dates/regimes, a comparable missed-order sample, non-toxic markouts, and positive settlement-aligned net PnL.

## Parallel Research During Phase 3

- Continue broad causal prediction snapshots and official outcome resolution.
- Extend policy-independent market and forecast collection to first listing/day-before conditions.
- Build D-1 opening and forecast-revision distributions without changing the frozen late control.
- Measure actual lifecycle volume, spread/depth, price response, fill bounds, and exit capacity by horizon.
- Keep the late HRRR-rich tuned dynamic and HRRR-v2 dynamic definitions as V2 vertical controls; do not presume either is the Phase 3D winner.
- Build the policy-neutral discovery substrate and constrained search/freeze path before requesting Phase 4.
- Keep the two-of-four late agreement rule exploratory until its exact definition and activation time are recorded.
- Keep Price Sheet V2a research-only until one calibrator and untouched forward window are frozen and pass the Slice 3 gate.
- Do not add funded strategies or expand normal risk caps.

## Roadmap Update Protocol

- Change this document when a phase starts, passes, fails, or changes scope.
- Put detailed implementation progress in the active implementation plan.
- Put economic evidence and falsification in the hypothesis record.
- Put current funded policy, sizing, and execution state in the live journal.
- Put chronological completed changes in the changelog.
