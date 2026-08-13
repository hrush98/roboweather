# Full-Market-Lifecycle Trading Implementation Plan

Status: Approved for research implementation; not approved for production pricing or funded trading

Last updated: 2026-08-13

## Feature Goal

Build one causal, continuously operating research and execution framework that observes each supported weather market from first listing through settlement, updates station high-or-low distributions as information changes, and evaluates complete quote and inventory lifecycles at useful size.

This plan coordinates five existing programs:

- forecast and observation sources: `docs/implementation/forecast-edge-data-program.md`;
- shared market tape and replay: `docs/implementation/phase-3-market-tape-replay.md`;
- conservative price construction: `docs/implementation/price-sheet-v2.md`;
- policy-neutral strategy discovery and freezing:
  `docs/implementation/tape-strategy-discovery.md`;
- phase sequencing: `docs/execution-rebuild-roadmap.md`.

The economic claim and falsification live in `docs/hypotheses/2026-07-17-full-market-lifecycle-trading.md`. The strategy rationale is preserved in `reports/full-market-lifecycle-trading-strategy-2026-07-17.md`.

The runtime may observe all supported markets, but forecast and promotion evidence is partitioned into `US_HIGH`, `GLOBAL_HIGH`, `US_LOW`, and `GLOBAL_LOW`. Every immutable record carries cohort, station, market family, settlement-mapping version, and station-scope version. One cohort passing never authorizes another.

## Non-Goals

- Do not restart funded trading.
- Do not extend the current late signal backward in time without horizon-specific training and calibration.
- Do not delay the initial bounded Price Sheet V2a late-window pilot.
- Do not infer D-1 fills or volume from displayed intraday snapshots.
- Do not let an opening sleeve consume later risk capacity without explicit portfolio replay.
- Do not average down automatically after adverse forecast revisions.
- Do not promote all lifecycle horizons when one exact horizon/tactic passes.
- Do not preselect a lifecycle horizon, model family, or quote tactic as the
  winner before constrained policy-neutral discovery.
- Do not store bulky raw forecasts, tape segments, model artifacts, or runtime databases in Git.

## Lifecycle Schematic

```text
market first listed
    |
    v
D-1 opening prior ---> D-1 source revision ---> D0 overnight convergence
    |                         |                         |
    +---------- quote / cancel / fill / inventory -----+
                                                      |
                                                      v
      D0 morning observations -> midday regime -> late remaining heating
                  |                    |                    |
                  +------ reprice / exit / add only if independently valid
                                                               |
                                                               v
                                                  close and settlement
```

At every arrow, the system must distinguish an unfilled quote that can be canceled from filled inventory that needs a new hold, reduce, exit, or independently qualified add decision.

## Continuous Operating Architecture

The target runtime is one event-driven lifecycle loop, not a collection of
unrelated clock-window strategies:

```text
market / forecast / observation / book / inventory event
-> rebuild the causal weather distribution
-> apply lead- and information-state calibration and uncertainty
-> construct the conservative price sheet
-> quote, cancel, replace, resize, hold, exit, or skip
-> persist the decision and continue until settlement
```

The engine may pool statistical strength across the lifecycle using continuous
features and hierarchical calibration. At minimum, decision state includes
forecast lead, time to settlement, source vintage, time since the latest
forecast or observation, observation freshness, market state, and inventory.

Named horizons remain mandatory as stable reporting and risk boundaries. They
allow leakage-safe evaluation, horizon-appropriate uncertainty, progressive
activation, and rapid rollback when one region fails. They do not imply
separate production processes, a forced pause between horizons, or a
requirement to ignore opportunities between named forecast releases.

## Horizon Contract

The initial fixed research labels are:

| Code | Description | Initial role |
| --- | --- | --- |
| `d1_open` | First eligible day-before distribution after market listing | Small passive opening arm |
| `d1_update` | Frozen distribution following a specified D-1 source revision | Passive revision arm |
| `d0_predawn` | Overnight/early short-range convergence | Passive early-day arm |
| `d0_morning` | Observed heating path begins | Confirmation and inventory review |
| `d0_midday` | Spatial/cloud/radiation regime is observable | Intraday update |
| `d0_late` | Peak nearly known and remaining heating is bounded | Existing high-confidence control |
| `settlement` | Market close through final resolution | Exit/hold reconciliation |

Every signal, price sheet, quote, position decision, and evaluation row must carry `lifecycle_horizon`. Boundaries must be station-local, immutable within a hypothesis version, and reported in UTC as well. The label identifies the decision's validated information state; it is not by itself a runtime trading schedule.

## Shared Immutable Records

### Market Lifecycle Record

Required fields include:

- venue event, market, token, station, market date, and four-cohort ID;
- market family, geography class, venue units/bucket syntax, and settlement-mapping version;
- listing/discovery time, accepting-orders time, close time, and settlement time;
- local station timezone and lifecycle horizon transitions;
- token subscription generations and valid/invalid tape intervals;
- settlement winner, venue source text, revision state, and provenance.

Discovery must include future-dated active weather markets. The first known discovery time is not assumed to equal venue listing time; late discoveries must be flagged.

### Forecast Revision Record

Required fields include:

- immutable forecast-distribution identifier;
- source vintages and local receipt times;
- decision-ready time after parsing and feature computation;
- lifecycle horizon and forecast lead;
- predecessor distribution and revision magnitude;
- latent physical-high and reported-high distributions;
- calibration, target-mapping, and uncertainty-reserve versions;
- missing-source/fallback flags.

### Pricing Decision Record

Required fields include:

- signal and forecast-distribution identifiers;
- decision-time market reference and valid tape boundary;
- calibrated weather fair and conservative lower confidence bound;
- model, settlement, execution, and inventory reserves;
- maximum quote price, selected side/token, and skip reasons;
- lifecycle horizon and exact hypothesis version.

### Quote Lifecycle Record

Required fields include:

- quote specification and activation timestamp after latency;
- price, size, post-only/taker behavior, and time in force;
- declared termination event and maximum lifetime;
- cancel, replace, expire, touch, fill, and partial-fill states;
- continuous tape validity from pre-activation through termination;
- conservative/base/optimistic fill labels;
- subsequent markouts and settlement outcome.

### Inventory Decision Record

Required fields include:

- station/date/token/side inventory before and after the action;
- originating and current lifecycle horizons;
- average entry price and reserved notional;
- latest weather fair, executable exit price, and unrealized markout;
- action: hold, cancel remaining, reduce, exit, or independently qualified add;
- action reason and versioned threshold;
- aggregate station/date and exact-bucket capacity remaining.

## Workstream 1: First-Listing Coverage

### Tasks

- Extend policy-independent discovery to future-dated active weather events.
- Record discovery/listing lag and do not silently claim pre-discovery coverage.
- Subscribe supported tokens at first discovery and retain them through closure.
- Preserve reconnect/resync/invalid intervals and exact raw segment coverage.
- Compute lifecycle liquidity sheets: depth, spreads, trades, executed notional, and price movement by horizon.
- Establish retention and disk budgets for multi-day token lifecycles.

### Exit Gate

- At least one complete market lifecycle is validly recorded for every promoted station family.
- The coverage ledger distinguishes actual first-listing coverage from late discovery.
- Resource measurements support the declared active universe and lifecycle duration.
- Actual D-1 volume and price discovery can be reported without relying on static snapshots.

## Workstream 2: Horizon-Specific Forecast Distributions

### Tasks

- Collect causally timestamped WeatherNext, NBM, HRRR/RRFS, observation, and outcome-source data.
- Produce a D-1 baseline from actual forecast vintages, not the current lightweight next-day scaffold.
- Localize ensemble members to the station and learn resolution-source mapping.
- Use peak-passed/remaining-heating models for highs and separately versioned trough-passed/remaining-cooling models for lows.
- Fit frozen hierarchical cohort, climate-region, and eligible station effects; require child heads to beat their pooled parent on untouched data.
- Emit coherent distributions for every eligible horizon and retain revision lineage.
- Calibrate separately by cohort and horizon where sample size supports it, with explicit pooled shrinkage otherwise.
- Preserve continuous forecast-lead and information-freshness features so pooled models can learn across horizon boundaries without treating all lifecycle states as interchangeable.
- Benchmark climatology, deterministic conversion, individual sources, and combined models.

### Exit Gate

- D-1 and D0 distributions have identical-row walk-forward evaluation.
- Every scored row passes a conservative source-availability check.
- Probability metrics, calibration, and uncertainty are reported by horizon.
- Source additions survive predeclared ablation; a lower weather MAE alone does not pass.

## Workstream 3: Price Sheet Horizon Extension

### Tasks

- Add lifecycle horizon and forecast-revision lineage to the immutable signal contract.
- Fit separate or hierarchically pooled calibration and uncertainty reserves by horizon.
- Feed validated horizons into one continuous event-driven price-sheet consumer rather than cloning independent runtime strategies.
- Add an inventory-risk reserve to the economic maximum quote.
- Keep the initial late Price Sheet V2a pilot unchanged as the control.
- Add earlier horizons one at a time after their forecast gate passes.
- Record the public/market baseline available at the exact decision time.
- For every F6 candidate, retain the quote-ready forecast while sampling executable market prices at +30 seconds, +2 minutes, +5 minutes, and +15 minutes; report later forecast revisions separately.
- Report net-edge half-life, time to nonpositive edge, available size, and right-censoring from gaps or unavailable books.

### Exit Gate

- Each horizon emits a conservative price without changing another horizon's frozen definition.
- Early quotes carry wider uncertainty when evidence warrants it.
- Quoted-price theoretical EV passes out of fold before execution replay.
- No raw model fair is treated as an executable price.

## Workstream 4: Forecast-Release Repricing

### Initial Frozen Tactics

For each scheduled source update, compare:

1. cancel before the update and replace after the new forecast is ready;
2. retain a deliberately wider quote through the update;
3. stay out during the release window;
4. after receipt, take only revisions exceeding a frozen robust threshold.

### Requirements

- Update schedules are treated as hints; actual receipt controls causality.
- Cancellation latency and possible fills before cancel acknowledgement are modeled.
- Replacement quotes receive new identifiers and price-sheet versions.
- Markouts are measured from activation, touch/fill, update receipt, and termination.
- Results separate unfilled quote behavior from already-filled inventory.

### Exit Gate

- A tactic has positive fill-conditioned results around source releases under conservative latency.
- Stale fills and missed favorable quotes are both represented.
- The selected rule is immutable for the forward shadow window.

## Workstream 5: Inventory And Exit Replay

### Initial Comparisons

- hold to settlement;
- exit after an adverse forecast revision threshold;
- exit when conservative weather fair falls below executable liquidation economics;
- reduce inventory as later evidence invalidates the opening thesis;
- add only when a later frozen horizon independently passes at the new price.

### Portfolio Constraints

- aggregate station/date cap;
- station/date/side cap;
- exact bucket/side cap;
- lifecycle-horizon opening budget;
- maximum inventory duration;
- reserve capacity for later horizons;
- no loss-chasing or automatic averaging down.

### Exit Gate

- Round-trip and hold-to-settlement results include entry and exit capacity.
- Early sleeves add incremental value after existing sleeves consume capacity.
- Tail loss, inventory duration, and concentration are within predeclared bounds.
- A later add is scored as a new decision, not retroactive validation of the opening trade.

## Workstream 6: Frozen Replay And Forward Shadow

### Initial Arms

- `d1_open_passive`;
- `d1_post_update_passive`;
- `d0_early_passive`;
- `d0_late_control`;
- `no_trade_market_reference`.

Every arm freezes:

- activation rule and date;
- signal, forecast, calibration, and target versions;
- side and token selection;
- quote price, size, latency, and lifetime;
- cancellation/replacement and exit rules;
- inventory interactions and capacity priority;
- tape-validity and fill-label requirements.

### Exit Gate

- Repeated replay is deterministic from immutable forecast, tape, and hypothesis inputs.
- Results report forecast, execution, inventory, and settlement metrics together.
- A predeclared forward window completes without rule retuning.
- Promotion is limited to the exact horizon/tactic/size tested.

## Initial Sizing Principles

The implementation should support configuration and replay; it should not encode funded values until Phase 4 authorization.

Research defaults should express the following principles:

- D-1 inventory is smaller than the aggregate station/date cap.
- Risk is reserved for D0 rather than consumed entirely at opening.
- Wider forecast uncertainty produces a lower maximum quote or a skip.
- Quote size is capped by observed executable capacity, not desired notional.
- Exit capacity is considered before opening size is accepted.
- All horizons share aggregate concentration caps.

## Measurement Sheet

### Forecast

- log loss, ranked probability score, Brier score, calibration, sharpness;
- forecast revision magnitude and accuracy;
- source contribution and ablation;
- station, horizon, lead, regime, and recent/all-history slices.

### Market

- lifecycle listing/discovery/close timing;
- spread, depth, trades, and executed notional by horizon;
- price response around forecast receipts;
- continuous valid coverage and replay eligibility.

### Execution

- quote touches, conservative/base/optimistic fills, time to fill;
- comparable misses and adverse selection;
- activation/fill/release/termination markouts;
- taker VWAP and passive capacity at `$50` and `$100`.

### Portfolio

- incremental risk, PnL, and return on risk by sleeve;
- exit costs and hold-to-settlement comparison;
- capacity consumed versus reserved;
- inventory duration, concentration, and tail loss.

## Build Slices

### Slice 0: Freeze Contracts

- Add lifecycle horizon and revision lineage to the data definitions.
- Freeze initial horizon boundaries and experimental arms.
- Define listing, forecast receipt, quote activation, and termination semantics.

### Slice 1: Observe From First Listing

- Extend discovery to future-dated markets.
- Run bounded multi-day tape probes and lifecycle health reports.
- Produce a no-trade lifecycle liquidity and price-discovery report.

### Slice 2: D-1 Forecast Baseline

- Build venue-aligned labels and causal forecast-source catalog.
- Compare WeatherNext, NBM, HRRR/RRFS where eligible, and climatology.
- Freeze the first D-1 distribution and calibration version.

### Slice 3: D-1 Price Sheet

- Add horizon-specific uncertainty and inventory reserves.
- Evaluate theoretical quoted-price EV without fill claims.
- Reject or freeze one D-1 price construction.

### Slice 4: Quote And Inventory Replay

- Replay opening, update, early, and late arms.
- Add cancellation latency, fill bounds, markouts, exits, and shared caps.
- Report useful-size and incremental portfolio economics.
- Feed valid causal rows into the Phase 3D broad discovery view without
  privileging the current late control or a named early arm.

### Slice 5: Continuous Forward Shadow From Candidate Registry

- Consume bounded active Phase 3D candidate versions registered before activation.
- Update each candidate's post-activation lifecycle cohort as outcomes resolve.
- Hold each version's rules fixed, compare aligned champion/challenger dates,
  and publish pass/continue/retire evidence.

### Slice 6: Controlled Funded Validation

- Request a minimum-risk plumbing canary only after prior gates pass.
- Reconcile replay with private order truth.
- Request useful-size testing only for the exact tactic and size supported.

## Initial Acceptance Checklist

- [ ] Horizon definitions and source-release rules are frozen.
- [ ] Future-dated market discovery and listing lag are measured.
- [ ] At least one complete valid lifecycle is recorded per supported station family.
- [ ] D-1 forecast vintages and venue-aligned outcomes are causally joined.
- [ ] D-1 distribution beats predeclared baselines out of sample.
- [ ] Price Sheet produces a horizon-specific conservative quote.
- [ ] Source-release cancel/reprice tactics are replayed with latency.
- [ ] Filled inventory has explicit exit and settlement paths.
- [ ] Actual volume and fill opportunity replace snapshot inference.
- [ ] `$50` and `$100` capacity results are separated.
- [ ] Portfolio replay preserves later-horizon capacity.
- [ ] Policy-neutral recurring discovery registers bounded simple lifecycle
      candidate versions before their activation boundaries.
- [ ] Candidate cohorts update continuously and champion/challenger comparisons
      retain family-level failures.
- [ ] No funded behavior changes without Phase 4 authorization.

## Decision Log

- 2026-08-04: Replaced the one-winner lifecycle handoff with the continuous
  Phase 3D candidate registry. Lifecycle rows now feed recurring discovery and
  candidate-specific cohorts without freezing the strategy program.
- 2026-07-30: Routed full-lifecycle candidate selection through the Phase 3D
  policy-neutral discovery/freeze gate. Lifecycle replay now feeds the broad
  substrate, while Slice 5 consumes only an immutable pre-activation winner.
- 2026-07-30: Clarified that the steady-state system is one continuously operating event-driven lifecycle engine. Named horizons are leakage-safe calibration, reporting, risk, activation, and rollback boundaries inside that engine rather than permanent standalone clock-window strategies.
- 2026-07-17: Approved full-market-lifecycle collection and research.
- 2026-07-17: Defined D-1 as a separate passive, uncertainty-aware sleeve rather than an earlier invocation of the late strategy.
- 2026-07-17: Required first-listing tape, forecast-revision lineage, inventory/exit replay, and horizon-specific promotion.
- 2026-07-17: Kept the current late Price Sheet V2a pilot and Phase 3 acceptance work on the immediate critical path.
- 2026-07-17: Funded trading remains paused.
