# Execution Rebuild Roadmap

This is the living roadmap for turning RoboWeather research signals into measurable, fill-conditioned trading evidence. Update this document when phase status, sequencing, or exit gates change. Detailed economic ideas belong in `docs/hypotheses/`; active feature design belongs in `docs/implementation/`.

Last updated: 2026-07-30

## Objective

Establish whether an exact weather signal can be traded through an exact execution tactic at useful size with positive net, settlement-aligned PnL across the observable market lifecycle.

```text
market listing + causal forecast state
-> calibrated quoteable fair
-> causal market state
-> quote/taker decision
-> fill or miss
-> markout
-> Polymarket settlement
-> portfolio PnL
```

## Current Phase

The Phase 3 market-tape recorder has retained policy-independent data since July 23 and the repository now includes a frozen-portfolio batch taker holdout. The first pre-cutoff discovery/post-cutoff replay reconstructed 12 executable positions across six resolved dates and was positive, establishing that later strategy extraction from the shared snapshot/tape data is technically viable. This is not a Slice 2 or Slice 6 pass: the July 29 lifecycle report failed on collector-session errors, receipt lag above 10 seconds, and no eligible complete listing-to-close market, while the batch replay does not model passive fills, markouts, or venue settlement. One real persisted quote/tape reconstruction also remains open. Price Sheet V2a Slices 0-2 are implemented and have nonempty current-database evidence. The first 98-row walk-forward calibration read found that fitted calibrators improved materially over raw model fairs but did not beat the decision-time market baseline, so Slice 3 conservative pricing must remain fail-closed and may reject both pilot signals. V2b will consume only valid Phase 3 tape windows.

A full-market-lifecycle forecast/data program is approved as a parallel research build. It extends collection to first listing and builds distinct D-1, early-day, intraday, and late distributions and tactics. It does not change the current critical path: the bounded late Price Sheet V2a pilot remains first, with earlier horizons added one at a time after their forecast, tape, inventory, and execution gates pass. Funded trading remains paused.

Canonical records:

- Current assessment: `docs/current-trading-system-audit.md`
- Economic hypothesis: `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`
- Tape implementation/acceptance: `docs/implementation/phase-3-market-tape-replay.md`
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
| 1. Quoteable fair/price sheet | Convert model output into a conservative maximum price. | V2a Slices 0-2 repository complete; Slice 3 conservative fair/price next, with no calibrator promoted | Positive walk-forward quoted-price EV without extreme uncalibrated fairs. |
| 2. Shadow quote construction | Generate auditable quote intents and cancellation metadata. | Plumbing prototype complete | Deterministic intent construction tests pass. No profitability claim. |
| 3. Shared market tape and replay | Collect pre-signal active-universe market events and replay quote tactics causally. | Slices 1-4 implemented; first batch taker holdout complete; Slice 2 lifecycle, Slice 4 real join, and passive/markout Slices 5-6 remain open | Tape validity, deterministic book replay, conservative fill bounds, and forward shadow reporting pass. |
| F. Full-lifecycle forecast/data foundation | Observe weather and market state from first listing and price horizon-specific distributions. | Approved parallel research build; no production consumer | Venue-aligned truth, causal D-1 sources, first-listing tape, horizon calibration, and inventory-aware replay pass. |
| 4. Funded validation | Validate replay fidelity and useful-size fill-conditioned PnL with controlled real orders. | Blocked on validated V2a + V2b configuration | Plumbing canary passes, then `$50` and `$100` size-specific evidence passes. |
| 5. Learned quote policy and sizing | Select quote/skip/size from calibrated signal and microstructure state. | Future | Sufficient clean Phase 3/4 data and stable out-of-sample improvement. |

## Price Sheet V2 Workstreams

1. V2a outcome pricing is the immediate critical path:
   - freeze the initial late HRRR signal definitions (complete);
   - build causal fit and evaluation datasets (complete, including current remote DB smoke);
   - run expanding-window calibration with a decision-time market reference (complete; fitted baselines did not beat market);
   - produce a conservative outcome fair and maximum economic quote price;
   - require positive out-of-fold theoretical quoted-price EV before shadow promotion.
2. V2b execution overlay starts on valid Phase 3 windows:
   - materialize decision-time book, queue, flow, latency, and coverage features;
   - reduce V2a price/size or skip based on toxicity and capacity;
   - compare frozen passive and stable-taker arms;
   - require positive base-case fill-conditioned EV before requesting Phase 4.

The detailed contracts, module boundaries, slices, tests, and acceptance gates are in `docs/implementation/price-sheet-v2.md`.

## Full-Market-Lifecycle Expansion

The intended steady state is a sequence of separately versioned horizons:

```text
D-1 open -> D-1 forecast revision -> D0 pre-dawn/morning
-> D0 midday observations -> D0 late remaining heating -> settlement
```

The expansion follows these gates:

1. Observe current and future-dated weather markets from first listing and measure actual lifecycle liquidity and price discovery.
2. Build causally timestamped, station-specific D-1 distributions from WeatherNext/NBM and eligible short-range sources.
3. Extend Price Sheet V2 one horizon at a time with separate calibration and uncertainty/inventory reserves.
4. Replay quote activation, scheduled-release cancellation/repricing, filled inventory, exit, and hold-to-settlement behavior.
5. Freeze one exact early-horizon shadow arm and compare it with the late control under shared portfolio caps.
6. Request controlled funded validation only for the exact horizon, tactic, inventory cap, exit rule, and size that pass.

The complete implementation contract is `docs/implementation/full-market-lifecycle-trading.md`.

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

- The full supported current and future-dated active weather-token universe is discovered near first listing and remains subscribed through a complete market lifecycle; discovery lag is reported explicitly.
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
- Extend policy-independent market and forecast collection to first listing/day-before conditions.
- Build D-1 opening and forecast-revision distributions without changing the frozen late control.
- Measure actual lifecycle volume, spread/depth, price response, fill bounds, and exit capacity by horizon.
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
