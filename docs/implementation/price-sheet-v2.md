# Price Sheet V2 Implementation Plan

Status: Approved for implementation

Current workstream: V2a outcome pricing

V2b dependency: validated Phase 3 tape windows and replay features

Last updated: 2026-07-17

## Feature Goal

Replace the hand-capped Phase 1 price sheet with a versioned pricing system that answers two separate questions:

1. V2a outcome pricing: what is a conservative, causally calibrated probability that this token wins, and what is the highest price that preserves a required profit margin?
2. V2b execution overlay: given the current book and recent tape, how should fill risk, toxicity, latency, capacity, TTL, and cancellation behavior reduce that price or cause the system to skip?

The output is not a general model score. It is an auditable trading contract for one exact candidate at one decision time.

```text
signal policy
-> V2a conservative outcome fair
-> V2a maximum economic price
-> V2b execution adjustment
-> final quote/taker cap, size, TTL, and cancel rules
```

## Relationship To The Roadmap

- V2a is the reopened Phase 1 pricing gate and is the current implementation priority.
- Phase 3 market-tape collection continues while V2a is built.
- V2b consumes valid Phase 3 book/tape windows and can begin incrementally as those features are available.
- Phase 4 funded validation remains blocked until one exact V2a + V2b configuration passes shadow replay.
- Phase 5 learned quote policy remains out of scope; V2b starts as a conservative, interpretable overlay.
- The approved full-market-lifecycle program will extend this contract one horizon at a time after the initial late pilot; it is specified in `docs/implementation/full-market-lifecycle-trading.md`.

## Why V1 Is Being Replaced

The current `phase1_price_maker_v1`:

- averages available model fairs;
- calls a fixed `0.05-0.90` clamp a calibrated fair;
- subtracts hand-set uncertainty and adverse-selection haircuts;
- records the market reference without using it as a true pricing prior;
- applies only to the old US consensus no-tiny BUY_NO sleeve;
- has no validated fill/toxicity model;
- failed its updated theoretical replay gate.

V2 must learn outcome calibration causally, preserve the market reference as information, expose every reserve separately, and remain conservative when the evidence is sparse.

## Initial Pilot Scope

Keep signal selection and pricing separate during the first implementation. Do not search for a new policy while fitting its price sheet.

Initial frozen signal families:

- late HRRR-v2 dynamic;
- late HRRR-rich tuned dynamic as a separate challenger, not an ensemble duplicate.

Initial trading scope:

- US high-temperature markets;
- late local window matching the frozen signal definitions;
- BUY_NO first;
- current no-tiny entry eligibility retained for signal comparability;
- one first eligible opportunity per predeclared policy scope;
- no funded execution.

The exact model IDs, station allow-list, decision window, strategy bucket, side, entry eligibility, dedupe key, and activation timestamp must be stored in immutable signal-spec records before forward evaluation begins.

## Full-Lifecycle Extension

The initial late-window pilot remains unchanged and is the immediate critical path. It becomes the control for later day-before and early-day research.

An earlier horizon is not enabled by widening the pilot's clock window. Before it enters Price Sheet V2, it needs:

- a frozen `lifecycle_horizon` and source-vintage/forecast-revision lineage;
- a horizon-specific or explicitly pooled calibrator with held-out evidence;
- a horizon-appropriate uncertainty reserve;
- an inventory-risk reserve and aggregate capacity interaction;
- frozen quote activation, update, cancellation, replacement, and exit rules;
- valid market tape from first listing through quote termination;
- a separate shadow activation and promotion decision.

Potential horizons are `d1_open`, `d1_update`, `d0_predawn`, `d0_morning`, `d0_midday`, and `d0_late`. They must be added and evaluated one at a time. Passing one does not authorize the others.

## System Schematic

```text
resolved causal model snapshots --------------------+
                                                     |
decision-time market reference ---------------------+----> V2a dataset
                                                     |          |
venue-aligned outcome labels -----------------------+          v
                                                     walk-forward calibrator
                                                               |
                                                               v
                                             conservative outcome fair
                                                               |
                                    profit + cost reserves -----+
                                                               |
                                                               v
                                                    V2a max quote
                                                               |
Phase 3 valid book/tape window ----------------------+----------+
                                                     |
                                                     v
                                V2b fill/toxicity/capacity overlay
                                                     |
                                                     v
                              final max price + size + TTL + cancels
                                                     |
                                                     v
                                       frozen shadow quote specification
```

## Common Contracts

### Signal Specification

Required fields:

- stable `signal_spec_id` and version;
- activation timestamp;
- exact model artifact ID or consensus members;
- market family, station/regime scope, side, and bucket eligibility;
- local decision window and observation-delay rules;
- lifecycle horizon, predecessor forecast distribution, and source-revision rule;
- strategy bucket and edge/entry eligibility;
- first-entry/dedupe scope;
- outcome-label source;
- training and evaluation date boundaries.

Changing any selection field creates a new signal-spec version. It must not silently refit the existing price sheet.

### Price Sheet Output

Every generated sheet must carry:

```text
price_sheet_version
signal_spec_id
decision_id
decision_time and quote_ready_time
model artifact/version
raw token fair
market reference and source
calibrator artifact/version
calibrator training cutoff
calibrated outcome fair
uncertainty reserve
conservative outcome fair
minimum profit reserve
cost reserve
V2a maximum quote price
V2b tape/session/coverage references
fill scenario or fill model version
toxicity reserve
latency reserve
execution-adjusted maximum quote price
size/capacity limit
TTL/GTD expiry
cancellation rules
inventory state and inventory-risk reserve
eligible/skip reason
```

Do not overload `calibrated_fair` to mean capped, shrunk, or execution-adjusted fair. Store the probability estimate and each price reserve as separate concepts.

## V2a: Outcome Pricing

### Objective

Estimate `P(token wins | causal weather signal, decision-time market information)` without using future observations, same-day outcomes, post-decision prices, or policy results from the evaluation date.

V2a decides whether the token is worth owning and sets the maximum economic price before execution-specific adjustments.

### Dataset Layers

Build two distinct datasets:

1. Calibration fit corpus:
   - causal model candidate probabilities available at decision time;
   - contemporaneous market bid/ask/mid or another explicitly defined market reference;
   - station, market family, side, model family, local time/window, and observation freshness;
   - venue-aligned final token outcome;
   - sample weights that prevent repeated intraday snapshots from dominating independent station/date outcomes.
2. Frozen-policy evaluation corpus:
   - only opportunities selected by an immutable signal specification;
   - first-entry/dedupe applied before scoring;
   - no use in fitting the calibrator for the same or later evaluation row.

The fit corpus may use broader candidate-distribution information than the selected policy, but evaluation must remain on the exact frozen policy.

### Causal Split And Weighting

- Use expanding-window, date-ordered folds.
- For evaluation date `D`, train only on outcomes strictly before `D`.
- Persist the training cutoff with every out-of-fold prediction.
- Group diagnostics and uncertainty by market date/regime; do not treat repeated snapshots as independent trades.
- Cap each station/date or market-date cluster's total training weight so high-frequency snapshots do not manufacture sample size.
- Keep a final forward period untouched after the signal and price-sheet versions are frozen.

### Calibration Candidates

Start with simple pooled models and require evidence before adding granularity:

1. Raw model fair baseline.
2. Decision-time market probability baseline.
3. Pooled Platt/logistic calibration of raw model probability.
4. Regularized market-aware calibration using model and market logits.

Conceptual model:

```text
logit(P(win)) = intercept
              + beta_model * logit(raw_model_fair)
              + beta_market * logit(market_reference)
              + small predeclared context terms
```

Do not initially fit per-station, narrow entry-band, per-TTL, or per-quote-offset calibrators. Add pooled station/regime deviations only when enough independent dates exist and the out-of-fold result improves.

For the initial selected-token pilot, calibrate the probability of the frozen selected token winning. Do not reselect the bucket using calibrated probabilities in the same experiment. Full coherent ladder recalibration is a separate future scope.

### Conservative Fair And Maximum Price

V2a output must separate probability from the price we are willing to pay:

```text
p_calibrated = walk_forward_calibrator(raw_model_fair, market_reference, context)

uncertainty_reserve = conservative buffer from out-of-fold date-cluster error

p_conservative = clamp(p_calibrated - uncertainty_reserve, 0, 1)

V2a_max_quote = floor_to_tick(
    p_conservative
    - minimum_profit_reserve
    - known_cost_reserve
)
```

The initial uncertainty method should be transparent, such as a date-clustered out-of-fold residual buffer or lower confidence bound. Do not use an opaque confidence score that cannot be reproduced.

The minimum profit and cost reserves must be versioned inputs. Evaluate a small predeclared set; do not optimize a dense grid on the same rows used to select the signal.

### Market Reference Rules

- Use only book state available at the recorded decision/quote-ready time.
- Record whether the reference is midpoint, same-side best price, opposite ask, or fallback.
- Skip or use an explicitly conservative fallback when the book is crossed, missing, stale, or outside valid coverage.
- Treat the market as information, not ground truth. V2a may deviate from it only when the walk-forward residual evidence supports the deviation.

### V2a Report

The report must compare, out of fold:

- raw model fair;
- market baseline;
- calibration-only fair;
- market-aware calibrated fair;
- conservative fair;
- V1 quote price where comparable;
- V2a maximum quote price.

Required metrics:

- binary Brier score and log loss;
- calibration-in-the-large and calibration slope;
- reliability bands;
- average raw, calibrated, conservative, market, and quoted prices;
- theoretical quoted-price PnL/R/R;
- effective market dates and station/dates;
- results by model family, side, station/regime, and time window as diagnostics;
- current, broader, and untouched forward windows.

Diagnostic slices do not become filters without a new hypothesis version.

## V2b: Execution Overlay

### Objective

Reduce or reject the V2a price and size based on causal market microstructure. V2b does not change the weather-outcome probability; it changes whether and how that probability is accessible through the venue.

V2b begins with conservative interpretable rules. Learned quote/skip/size optimization remains Phase 5.

### Tape Eligibility

Use only Phase 3 intervals that:

- have valid coverage from before quote readiness through fill, cancellation, or expiry;
- have a reconstructable initial book and explicit gap status;
- distinguish authoritative trade flow from placements, cancellations, and price changes;
- carry feed and local receipt timing needed for the configured latency assumption;
- have the required markout window or a clearly pending status.

Invalid or ambiguous tape windows must return `SKIP_INVALID_TAPE`, not an optimistic fill label.

### Decision-Time Inputs

- V2a maximum quote and conservative outcome fair;
- bid/ask, spread, tick size, and depth by level;
- same-price and better-price queue ahead;
- recent authoritative trade flow and direction;
- recent adds/cancels and top-level depth decay;
- book age, receipt lag, gap/stale status;
- time to expiry/resolution and next expected weather update;
- intended size and current portfolio risk;
- frozen quote rule, TTL, and cancellation rule.

No feature may use events after the simulated quote-ready timestamp.

### V2b Outputs

```text
postable/reachable
coverage_validity
fill_scenario probabilities or bounds
expected adverse markout reserve
latency reserve
capacity bound
execution-adjusted maximum quote
final size cap
TTL/GTD expiry
cancellation triggers
skip reason
```

Initial formula:

```text
V2b_max_quote = floor_to_tick(
    V2a_max_quote
    - toxicity_reserve
    - latency_reserve
)

final_size = min(
    signal_size_cap,
    conservative_tape_capacity,
    portfolio_risk_capacity
)
```

V2b may only reduce V2a price/size or skip. It must never raise the maximum economic price merely to improve fill rate.

### Passive And Taker Arms

Evaluate from the same V2a sheet:

1. Passive price-maker arm:
   - post-only at or below V2b maximum quote;
   - predeclared TTL and cancel rules;
   - conservative/base/optimistic public-tape fill bounds kept separate.
2. Stable-taker control:
   - take only when the reachable ask after the configured latency remains at or below V2a/V2b maximum price;
   - record disappearance, partial depth, and realized VWAP;
   - never chase above the sheet.

The comparison determines whether forecast edge is more accessible as maker, taker, both, or neither.

### Fill And Toxicity Calibration

Public tape initially provides scenario labels, not exact fill truth. Track separately:

- conservative fill;
- base fill;
- optimistic touch;
- actual funded fill when available.

Once controlled canaries exist, calibrate:

```text
P(actual fill | public scenario, queue, offset, TTL, size)
E(markout | actual fill, book state, quote rule)
P(adverse move before fill | book state, quote rule)
```

Minimum-risk canaries validate replay fidelity only. `$50` and `$100` execution/capacity claims require direct evidence at those sizes.

### V2b Report

For each frozen signal + V2a version + execution arm:

- selected and tape-valid opportunities;
- postable/reachable rate;
- fill rate/bounds by size and price offset;
- missed versus filled outcome PnL;
- markouts at required windows;
- adverse movement before/after fill;
- cancellation attribution;
- settlement PnL under conservative/base/actual fills;
- capacity and opportunity frequency;
- effective independent market dates;
- invalid/gapped/stale coverage exclusions.

## Artifact And Persistence Design

Recommended boundaries:

```text
weather_trader/pricing/contracts.py          signal and sheet contracts
weather_trader/pricing/dataset.py            causal V2a materialization
weather_trader/pricing/calibration.py        walk-forward fit/apply
weather_trader/pricing/price_sheet_v2.py     V2a pricing
weather_trader/pricing/execution_overlay.py  V2b reductions/skips
scripts/build_price_sheet_v2.py              versioned artifact build
scripts/price_sheet_v2_report.py             V2a out-of-fold report
scripts/price_sheet_v2_tape_report.py        V2b replay report
```

The current `weather_trader.execution.price_maker` should remain as the V1 compatibility path until V2 passes. Do not mutate V1 behavior in place.

Calibration artifacts and generated reports are runtime/research outputs and remain outside commits. Commit source, tests, schemas, and documentation only.

Persistence may extend `live_price_sheets` or introduce a versioned successor, but V2 fields must be queryable without parsing ambiguous overloaded names. Schema migration must remain backward compatible with V1 rows.

## Sprint Slices

### Slice 0: Freeze Contracts And Pilot Signals

- Define immutable signal specifications for the two pilot families.
- Define V2a/V2b sheet contracts and skip reasons.
- Declare market-reference, label-source, split, weighting, and dedupe rules.
- Define V1 compatibility and rollback behavior.

Exit: the same source row always maps to the same signal/decision ID and versioned sheet inputs.

### Slice 1: V2a Dataset Materializer

- Build calibration-fit and frozen-policy evaluation datasets.
- Enforce timestamp availability and training cutoffs.
- Add station/date and market-date cluster weights.
- Persist data-quality and label-source diagnostics.

Exit: leak tests pass and sampled rows can be reconstructed to raw snapshots, market state, and outcomes.

### Slice 2: Walk-Forward Calibration Baselines

- Implement raw-model, market, pooled calibration, and market-aware regularized baselines.
- Generate expanding-window out-of-fold predictions.
- Persist calibrator versions and training cutoffs.
- Produce probability-quality and reliability comparisons.

Exit: every evaluation prediction is demonstrably trained only on earlier dates.

### Slice 3: Conservative Fair And V2a Price

- Implement uncertainty, profit, and cost reserves as separate versioned components.
- Generate V2a maximum quote and skip reasons.
- Build the current/broad/forward price-sheet report.
- Compare against V1 and no-trade baselines.

Exit: at least one frozen signal has positive out-of-fold theoretical quoted-price EV without depending on extreme raw fairs or post-hoc slices; otherwise remain research-only or reject the signal.

### Slice 4: Shadow Integration

- Persist V2 sheets beside V1 without changing funded behavior.
- Generate a small frozen quote-spec set from V2a.
- Join V2 decisions to Phase 3 tape IDs and coverage states.
- Add reconstruction to the operational report.

Exit: a random V2 quote can be traced from raw signal through calibration artifact, maximum price, tape interval, and pending replay state.

### Slice 5: V2b Tape Feature Materializer

- Materialize decision-time book, queue, flow, latency, stale/gap, and capacity features.
- Enforce quote-ready cutoffs.
- Create passive and stable-taker replay inputs from the same V2a sheet.

Exit: repeated feature materialization is deterministic and invalid intervals fail closed.

### Slice 6: Interpretable Execution Overlay

- Implement conservative toxicity and latency reserves.
- Implement capacity sizing and explicit skips.
- Replay predeclared passive and taker arms.
- Report filled/missed markouts and settlement under each fill scenario.

Exit: an exact V2a + V2b configuration has positive base-case shadow filled EV, non-toxic markouts, and useful-size capacity evidence sufficient to request Phase 4 plumbing validation.

### Slice 7: Actual-Order Calibration

- Join controlled canary orders to public replay results.
- Measure public-label versus actual fill calibration.
- Re-estimate fill and toxicity reserves only with prior-date actual data.
- Advance through minimum-risk plumbing, `$50`, then `$100` validation gates.

Exit: the exact tactic and tested size meet the Phase 4 promotion standard or are rejected.

## Acceptance Gates

### V2a

- [ ] Frozen signal versions and activation timestamps exist.
- [ ] Fit and evaluation datasets are distinct and reconstructable.
- [ ] Every out-of-fold prediction uses only earlier resolved dates.
- [ ] Repeated snapshots do not inflate effective sample size.
- [ ] Market references are causal, typed, and stale-aware.
- [ ] Raw, market, calibrated, and conservative probability metrics are reported.
- [ ] Probability, uncertainty, profit, and cost components remain separate.
- [ ] V2a theoretical quoted-price EV is positive in predeclared evaluation windows or the signal remains research-only.
- [ ] No diagnostic station/regime slice silently becomes a trading filter.
- [ ] V1 remains available as a non-funded comparison/rollback path.

### V2b

- [ ] Only valid Phase 3 coverage intervals receive execution labels.
- [ ] All decision features stop at quote readiness.
- [ ] Placements, cancellations, price changes, and trades remain distinct.
- [ ] Conservative, base, optimistic, and actual fills remain separate.
- [ ] V2b can only reduce V2a price/size or skip.
- [ ] Passive and stable-taker arms use the same economic price ceiling.
- [ ] Markouts, settlement PnL, capacity, and invalid exclusions are reported.
- [ ] Public fill predictions are checked against actual canary outcomes before promotion.
- [ ] `$50/$100` claims use evidence at the claimed size.
- [ ] Negative base-case filled EV kills the tactic even when selected replay is positive.

## Testing Strategy

Required unit tests:

- time-split and training-cutoff leakage;
- cluster weighting and dedupe;
- probability clipping/logit stability;
- calibrator fallback and missing-market behavior;
- tick rounding and reserve arithmetic;
- version/hash stability;
- V2b never raises V2a price or size;
- invalid tape coverage fails closed;
- taker reachability and passive post-only behavior;
- actual/public fill-label separation.

Required integration tests:

- raw snapshot to V2a sheet reconstruction;
- V2a sheet persistence and V1 coexistence;
- V2a decision to Phase 3 tape join;
- deterministic V2b replay;
- report generation with pending, invalid, shadow-filled, missed, and actual-filled rows.

## Kill And Rollback Rules

- If market-aware calibration cannot improve reliability or positive quote EV out of sample, keep the raw signal research-only.
- If a signal is only positive after station/window/entry tuning on the evaluation set, reject that version and refreeze.
- If V2a is positive but V2b base-case filled EV is negative, kill the execution tactic, not the probability calibration.
- If passive fails and the stable-taker control passes, advance only the taker configuration.
- If both passive and taker fail, stop the signal/venue combination instead of expanding the retrospective grid.
- V1 remains disabled for funding and available only as a comparison until explicitly removed after V2 validation.

## Decision Log

- 2026-07-17: Approved a future full-market-lifecycle extension one frozen horizon at a time. Kept the initial late pilot unchanged as the immediate critical path and required separate forecast, calibration, inventory, quote-update/cancel, exit, and tape evidence for every earlier horizon.
- 2026-07-16: Approved a two-part Price Sheet V2 plan. V2a outcome pricing is the immediate critical path while Phase 3 tape collection runs; V2b execution overlay begins on valid tape windows and remains conservative/interpretable until Phase 4 actual-order evidence exists.
