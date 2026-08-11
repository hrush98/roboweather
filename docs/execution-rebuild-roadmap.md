# Execution Rebuild Roadmap

This is the living roadmap for turning RoboWeather research signals into measurable, fill-conditioned trading evidence. Update this document when phase status, sequencing, or exit gates change. Detailed economic ideas belong in `docs/hypotheses/`; active feature design belongs in `docs/implementation/`.

Last updated: 2026-08-11

## Objective

Establish whether an exact weather signal can be traded through an exact execution tactic at useful size with positive net, settlement-aligned PnL across the observable market lifecycle.

```text
market listing + causal forecast state
-> calibrated quoteable fair
-> causal market state
-> incremental executable-decision cache
-> deterministic historical discovery + untouched holdout
-> exact candidate forward evaluation
-> one operator report
-> quote/taker decision
-> fill or miss
-> markout
-> Polymarket settlement
-> portfolio PnL
```

## Current Phase

The Phase 3 market-tape recorder has retained policy-independent data since July 23 and the repository includes a frozen-portfolio batch taker holdout. Its completed 72-hour run recorded 224.6 million events with zero reconstruction errors and about 96.7% valid eligible-token coverage. Periodic reconnect gaps and late initial discovery are accepted research limitations, not critical-path blockers; causal replay continues to reject each affected decision window rather than filling across missing tape. Recorder heartbeat/reconnect hardening and another full lifecycle run are deferred unless missing coverage materially obstructs candidate evaluation. One real persisted quote/tape reconstruction, passive fill bounds, markouts, and venue settlement remain open. Price Sheet V2a Slices 0-3 are implemented and have nonempty current-database evidence. Slice 3 keeps calibrator selection unfrozen, computes reserves only from prior out-of-fold market dates, and leaves both pilots research-only until a baseline and untouched forward window are declared.

Phase 3D has completed D0-D2 for the versioned causal checkpoint execution contract. The known-failing scheduler is stopped and disabled; research and tape collection remain active. The production cache reduced 146,937 model rows to 19,032 decisions, completed its cold backfill in 32.43 seconds, matched 200/200 stratified direct replays, completed a warm no-op in 0.026 seconds, and refreshed a 9,216-row increment in 2.52 seconds at roughly 320 MiB peak RSS. The C2-C6 registry, evaluator, transition, scheduler, and status code remains compatibility-only. D3-D5 must now move the bounded historical grid, correlated-family collapse, untouched holdout, exact post-activation candidate evaluation, and one report onto this cache before the operator cutover or any scheduler is considered. Current ledgers still lack sufficient venue-resolution and markout evidence, so no result can pass to Phase 4.

A full-market-lifecycle forecast/data program is approved as a parallel research build. It extends collection to first listing and builds one continuously updating pricing and inventory engine with separately validated D-1, early-day, intraday, and late information states. It does not change the current critical path: the bounded late Price Sheet V2a pilot remains first, with earlier lifecycle regions progressively enabled after their forecast, tape, inventory, and execution gates pass. Funded trading remains paused.

Canonical records:

- Current assessment: `docs/current-trading-system-audit.md`
- Economic hypothesis: `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`
- Tape implementation/acceptance: `docs/implementation/phase-3-market-tape-replay.md`
- Deterministic tape-backed strategy discovery: `docs/implementation/tape-strategy-discovery.md`
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
| 3D. Deterministic strategy discovery | Cache each executable tape decision once, search a bounded historical grid, test frozen representatives and existing candidates honestly, and issue one report. | D0-D2 accepted on production for the first-post-ready-checkpoint taker contract; D3 historical grid/report, D4 forward evaluation, and D5 operator cutover remain open. Existing C2-C6 automation is compatibility-only. | Cached replay equals direct replay; warm and incremental runtime gates pass; one command distinguishes emerged, none, incomplete cache, and failed analysis; exact candidates remain activation-bounded. |
| F. Full-lifecycle forecast/data foundation | Observe weather and market state from first listing and price horizon-specific distributions. | Approved parallel research build; no production consumer | Venue-aligned truth, causal D-1 sources, first-listing tape, horizon calibration, and inventory-aware replay pass. |
| 4. Funded validation | Validate replay fidelity and useful-size fill-conditioned PnL with controlled real orders. | Blocked on an explicitly approved Phase 3D candidate version with validated V2a + V2b pricing/execution | Plumbing canary passes, then `$50` and `$100` size-specific evidence passes. |
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

## Deterministic Strategy Discovery

Strategy generation is downstream of measurement. Do not hard-code a named MVP,
model family, side, station exception, or clock variant as the final strategy.
Exact candidate versions remain immutable so later evidence cannot be attributed
to a rule changed after the fact, but lifecycle machinery is not a prerequisite
for obtaining the analytical answer.

```text
market tape + causal forecast/model snapshots + venue settlement
-> incremental executable-decision cache
-> date-ordered historical grid
-> complexity penalty and correlated-family collapse before holdout
-> untouched historical holdout
-> exact existing-candidate post-activation evaluation
-> one report
-> controlled real-order request
```

The Phase 3D implementation must:

1. Derive unique executable-decision keys before tape access and cache successful
   and rejected replay results incrementally.
2. Preserve every model opinion through model-to-decision mappings without
   reconstructing shared market state for each model row.
3. Seal source watermarks, grammar, folds, costs, latency, fill scenario, size,
   caps, uncertainty, family mapping, and candidate activations before ranking.
4. Use chronological folds and collapse correlated variants before opening the
   untouched final historical dates.
5. Evaluate existing exact candidates only on their post-activation decisions.
6. Fail closed on tape gaps and unavailable asks; keep weather, venue, markout,
   public-tape counterfactual, and actual-order evidence distinct.
7. Return one report whose status distinguishes emerged strategies, a valid zero,
   an incomplete cache, and failed analysis.
8. Prove warm and daily-incremental production performance before adding a thin
   scheduler, and send only an explicitly approved passing candidate to Phase 4.

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
5. Feed eligible lifecycle rows into recurring Phase 3D runs, register bounded
   candidate versions, and compare their post-activation evidence with the late
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
8. Recurring constrained walk-forward discovery and versioned candidate registration.
9. Continuous candidate-cohort evaluation, including passive and stable-taker controls.
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
- Each discovery run seals its cutoffs, grammar, folds, metrics, costs, and complexity rules before ranking.
- Every candidate definition and activation time is registered before its forward rows exist.
- Forward scorecards are append-only by candidate version and as-of watermark.
- Recurring runs are idempotent, challenger counts are bounded, and family-level failures survive version churn.

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
- Build the policy-neutral discovery substrate, candidate registry, recurring search, and continuous cohort evaluator before requesting Phase 4.
- Keep the two-of-four late agreement rule exploratory until its exact definition and activation time are recorded.
- Keep Price Sheet V2a research-only until one calibrator and untouched forward window are frozen and pass the Slice 3 gate.
- Do not add funded strategies or expand normal risk caps.

## Roadmap Update Protocol

- Change this document when a phase starts, passes, fails, or changes scope.
- Put detailed implementation progress in the active implementation plan.
- Put economic evidence and falsification in the hypothesis record.
- Put current funded policy, sizing, and execution state in the live journal.
- Put chronological completed changes in the changelog.
