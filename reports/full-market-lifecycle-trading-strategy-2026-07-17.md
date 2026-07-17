# Full-Market-Lifecycle Trading Strategy

Date: 2026-07-17

Status: Strategy report. The research and build direction is approved; it does not authorize funded trading.

Canonical follow-through:

- hypothesis and falsification: `docs/hypotheses/2026-07-17-full-market-lifecycle-trading.md`;
- implementation and acceptance: `docs/implementation/full-market-lifecycle-trading.md`;
- forecast-data program: `docs/implementation/forecast-edge-data-program.md`;
- execution sequencing: `docs/execution-rebuild-roadmap.md`;
- current system verdict: `docs/current-trading-system-audit.md`.

## Executive Decision

RoboWeather should expand its research and data collection from the current intraday window to the entire observable market lifecycle, beginning when a market is first listed and continuing through settlement. The trading system should ultimately be able to express an opinion throughout that lifecycle.

This is not a decision to run the current late-day strategy on the prior day. It is a decision to build a sequence of distinct, horizon-aware strategies sharing one causal record:

1. day-before opening prior;
2. day-before forecast-revision trading;
3. same-day pre-dawn and morning repricing;
4. midday spatial and radiation updating;
5. late-day remaining-heating trading;
6. inventory management and settlement.

Each horizon needs its own forecast distribution, calibration, uncertainty reserve, quote rules, inventory cap, cancellation/repricing rules, and evidence. The immediate execution critical path remains the bounded late-window Price Sheet V2a pilot. Expansion proceeds one frozen horizon at a time after the common forecast and market-tape infrastructure is credible.

## Why Extend Earlier

The current intraday focus has two structural limitations:

- useful displayed depth appears to decline as the day progresses; and
- waiting for the most precise weather state may mean arriving after much of the market's risk transfer and price discovery.

A deduplicated diagnostic from current research snapshots since 2026-06-01 shows that representative displayed `$50` ask-sweep fillability was materially greater in the morning than late afternoon:

| Station-local hour | Mean displayed `$50` fillable notional | Full `$50` rate |
| ---: | ---: | ---: |
| 07 | `$46.94` | `26.7%` |
| 09 | `$47.00` | `26.7%` |
| 12 | `$39.61` | `16.4%` |
| 15 | `$30.21` | `6.3%` |
| 17 | `$27.84` | `1.1%` |

This is suggestive, not conclusive. It measures static displayed ask depth in snapshots, not actual traded volume, passive fills, queue position, or day-before conditions. The current snapshot regime mostly covers station-local hours 07 through 18 and therefore cannot answer the D-1 question. The new shared tape must measure the full market lifecycle directly.

The economic opportunity is a trade-off:

- earlier markets may offer more time, depth, spread, and disagreement;
- earlier weather estimates have wider uncertainty and more forecast-revision risk;
- later forecasts are more precise, but capacity may already be gone.

The goal is to learn the best risk-adjusted use of each phase, not to assume that earlier is automatically better.

## What Changes Conceptually

### From One Decision Window To A Forecast Process

The object being estimated is no longer only:

```text
P(final reported high = k | information at a late intraday timestamp)
```

It becomes a versioned process:

```text
P_t(final reported high = k | every source available by t)
```

Every distribution must preserve:

- the decision timestamp;
- forecast source vintages and receipt times;
- the market lifecycle horizon;
- the preceding forecast version it supersedes;
- the exact resolution-source mapping;
- the calibration and uncertainty-reserve version.

The revisions themselves become economically important. A source update can change the forecast distribution before the market fully incorporates it. That possibility must be tested causally against the actual release and receipt sequence.

### From Entry-Only To Position Lifecycle

Opening a position on D-1 creates obligations that a late one-shot policy largely avoids. The system needs to decide whether to:

- leave a resting quote active;
- cancel it ahead of a scheduled forecast release;
- replace it after the new forecast is received;
- reduce or add to already-filled inventory;
- cross the spread to exit;
- hedge through another temperature bucket where economically coherent;
- hold the position to settlement.

An unfilled quote can be canceled. Filled inventory cannot. Therefore full-lifecycle trading requires an explicit inventory and exit model, not only a better entry model.

## Horizon Taxonomy

The first research taxonomy should be fixed before results are reviewed:

| Horizon | Weather state | Primary information | Expected trading role |
| --- | --- | --- | --- |
| D-1 open | Broad, uncertain prior | WeatherNext ensemble, NBM, global/regional forecasts, climatology | Small passive discovery quotes; learn opening disagreement and capacity |
| D-1 post-update | Revision-sensitive | New source cycles, ensemble shifts, cross-model disagreement | Cancel/reprice around scheduled releases; trade durable revisions |
| D0 pre-dawn | Short-range convergence | Overnight HRRR/RRFS/NBM cycles, overnight observations | Recalibrate opening inventory; passive/taker comparison |
| D0 morning | Heating path begins | HRRR/RRFS, ASOS, neighbors, clouds | Increase conviction only when observations confirm the path |
| D0 midday | Realized regime visible | Exact high-so-far, MADIS/ASOS residuals, GOES radiation/cloud surprise | Spatial and remaining-heating update |
| D0 late | Peak nearly known | Exact high-so-far, remaining heating, late HRRR-rich features | Current high-confidence control and bounded pilot |
| Close/settlement | Forecast risk largely resolved | Resolution-source state and venue rules | Exit-versus-hold and settlement reconciliation |

Clock boundaries should ultimately be station-local and source-aware. The initial fixed buckets are research labels, not presumed optimal policy boundaries.

## The New Data Regime

### Weather Forecast State

The day-before phase should emphasize public probabilistic priors:

- WeatherNext ensemble members and revisions;
- NBM deterministic, percentile, standard-deviation, and probability products;
- HRRR/RRFS when their lead and availability become appropriate;
- a simple climatological and persistence reference;
- cross-model disagreement and revision magnitude.

WeatherNext is most naturally useful here. Its value would not come from being novel or inaccessible; other traders can use it. Its possible value comes from retaining ensemble structure, localizing it to the exact station and reporting source, calibrating its tails by lead time and regime, and combining it with later observations in a causally versioned distribution.

During D0, the balance shifts toward:

- exact target-station high-so-far;
- high-frequency ASOS and MADIS neighbor residuals;
- HRRR/RRFS point and stencil fields;
- GOES cloud and radiation surprise;
- local warming rate, wind shift, boundary movement, and peak-already-occurred probability.

The forecast program must output one coherent distribution over the final resolution-source-reported high at every eligible decision time. Independent bucket classifiers are not sufficient because they can violate ordering and total-probability constraints.

### Market State

The shared market tape must begin at first listing, including future-dated active weather markets, and continue through closure or settlement. It must preserve:

- token discovery and subscription generations;
- full books, ordered deltas, trades, and local receipt timestamps;
- reconnect, resync, stale, and invalid intervals;
- spreads, depth, imbalance, queue proxies, and executed flow;
- the complete interval from quote activation through cancellation, expiry, fill, exit, or settlement;
- authoritative private order events during later funded canaries.

This changes the research question from “was there depth at the signal?” to “when did liquidity appear, how did price discovery unfold, and could the exact tactic have entered and exited at useful size?”

### Causal Joining

Forecast and market records must be joined by what was received locally, not by nominal model initialization or feed event time alone:

```text
source initialized
-> source published
-> collector received and parsed it
-> forecast distribution frozen
-> market state observed at quote-ready time
-> quote activated after declared latency
```

No forecast update may be paired with a market book that existed before the update could have been processed. Unknown publication times require a conservative lag or exclusion.

## Day-Before Quote Design

Day-before entry should initially be passive and small. Wide uncertainty is not a reason to avoid quoting entirely; it is a reason to demand a larger margin and carry less inventory.

A horizon-specific maximum quote should have the form:

```text
max_quote_price_t
= calibrated_weather_fair_t
- model_uncertainty_reserve_t
- settlement_mapping_reserve_t
- execution_cost_reserve_t
- inventory_risk_reserve_t
```

The early reserve should normally be larger than the late reserve. A quote is valid only for the exact forecast vintage, market state, inventory state, and planned termination rule used to construct it.

Initial D-1 research rules should include:

- post-only quotes only;
- small per-station/date and per-bucket inventory caps;
- price distance determined by the lower confidence bound, not raw model fair;
- automatic cancel or suspension before known source-release windows unless a frozen experiment explicitly tests resting through them;
- no automatic averaging down after an adverse forecast revision;
- reserved risk capacity for later, more informed horizons;
- no claim that posted notional was filled without tape-derived evidence.

## Forecast Releases And Stale-Quote Risk

Scheduled model releases create predictable information hazards. A quote resting during a release can be selectively filled when stale and ignored when favorable. This is classic adverse selection.

The system should compare frozen tactics:

1. cancel before the release and replace after the new distribution is frozen;
2. keep a deliberately wider quote active through the release;
3. do not quote around the release;
4. cross only after a large, robust revision and sufficient remaining edge.

Required measurements include:

- fill or touch frequency around release windows;
- immediate and later markouts;
- forecast revision magnitude and direction;
- price response latency;
- spread/depth changes;
- net settlement-aligned PnL after entry and exit costs.

The preferred tactic must be selected out of sample. A visually compelling example of beating a market update is not promotion evidence.

## Inventory And Exit Policy

Every opening sleeve must have a declared terminal behavior. At minimum, replay should compare:

- hold to settlement;
- exit when weather fair crosses below a conservative liquidation threshold;
- exit after adverse revision beyond a frozen threshold;
- scale down as uncertainty collapses against the position;
- retain or add only when a later horizon independently requalifies at the new price.

Later qualification must not be mislabeled as “defending” the original position. It is a new decision consuming the same station/date and bucket risk caps.

Portfolio controls must reserve capacity across time. An early sleeve that consumes the full station/date cap prevents the system from exploiting a better late signal. Replays should test horizon budgets and an aggregate cap, for example a small D-1 allocation plus capacity reserved for D0. Exact values remain an experimental parameter, not a live recommendation.

## Frozen Initial Experiment Arms

The first forward shadow comparison should retain simple, interpretable arms:

1. `d1_open_passive`: quote from the first eligible D-1 distribution and terminate at a frozen time or forecast revision;
2. `d1_post_update_passive`: quote only after a specified D-1 source update is received;
3. `d0_early_passive`: quote after the overnight short-range update and before the current intraday window;
4. `d0_late_control`: preserve the accepted late Price Sheet V2a definition;
5. `no_trade_market_reference`: record price changes without hypothetical action.

Each arm must freeze:

- forecast and pricing versions;
- horizon and activation rule;
- quote side, price, size, latency, and time in force;
- cancel/reprice conditions;
- inventory interaction and exit rule;
- tape coverage requirement;
- activation date and evaluation window.

Only one variable should change in the cleanest comparisons. For example, an opening-versus-late comparison should not simultaneously change model family, size, and fill convention.

## What Must Be Measured

### Forecast Metrics

- log loss, ranked probability score, and Brier scores across the entire bucket ladder;
- calibration and sharpness by horizon;
- threshold reliability around the quoted token;
- forecast revision skill versus price revision;
- station/date clustered confidence intervals;
- performance by station, season, cloud regime, and cross-model disagreement.

### Market And Execution Metrics

- listing time and lifecycle duration;
- hourly displayed depth, spread, imbalance, and executed notional;
- trade counts and price discovery by horizon;
- conservative/base/optimistic passive fill bounds;
- taker VWAP at useful size;
- time to touch/fill and comparable missed-order samples;
- markouts around forecast releases and quote activation;
- exit capacity and round-trip economics;
- settlement-aligned net PnL by exact tactic and size.

### Portfolio Metrics

- incremental PnL after earlier sleeves consume capacity;
- inventory duration and maximum adverse markout;
- station/date, side, and exact-bucket concentration;
- opportunity cost of reserving versus consuming risk early;
- tail loss under forecast reversal;
- all-history and recent walk-forward results.

## Acceptance Gates

### Data Gate

- Active and future-dated weather tokens are discovered at first listing.
- Valid tape coverage spans listing through quote termination and ultimately the complete market lifecycle.
- Forecast source vintages have conservative causal availability timestamps.
- Venue-aligned outcome truth and revision provenance are explicit.

### Forecast Gate

- A D-1 distribution beats simple climatology, public deterministic conversion, and the existing next-day scaffold on identical held-out rows.
- Calibration remains usable by horizon and relevant station/regime slices.
- Incremental data-source claims survive ablation and weather-date clustered uncertainty.

### Execution Gate

- Actual lifecycle volume and fill opportunity are measured, not inferred from a few snapshots.
- Repricing and cancellation are replayed against continuous valid tape.
- Early passive fills do not show unacceptable adverse markouts around forecast releases.
- Useful-size results are reported separately for `$50` and `$100` targets.

### Portfolio Gate

- The early sleeve adds positive net PnL after the current stack, aggregate caps, and exit costs.
- Results are not merely duplicate exposure or an unpriced size-up of a later sleeve.
- Tail loss and inventory duration remain within predeclared bounds.
- A plumbing canary precedes any request for useful-size funded validation.

## Recommended Build Sequence

### Stage 1: Observe The Whole Lifecycle

- Extend token discovery and tape subscription to first listing.
- Record actual lifecycle liquidity, flow, spreads, price moves, and coverage.
- Start causal D-1 forecast-source collection and venue-aligned truth work.
- Keep funded trading paused.

### Stage 2: Build D-1 Forecast Baselines

- Replace the current lightweight next-day scaffold with true D-1 source vintages.
- Benchmark WeatherNext, NBM, HRRR/RRFS where available, and simple climatology.
- Emit coherent station-specific distributions and forecast revisions.
- Do not tune quote rules until forecast baselines are frozen.

### Stage 3: Replay Opening And Revision Tactics

- Join frozen distributions to valid tape.
- Compare D-1 open, D-1 post-update, D0 early, and late control arms.
- Add conservative fill bounds, markouts, exit rules, and portfolio capacity.
- Select one exact horizon/tactic only if it survives walk-forward evidence.

### Stage 4: Forward Shadow One Horizon

- Activate one immutable shadow specification.
- Continue collection without retuning through the evaluation window.
- Review forecast, fill, markout, and settlement metrics together.
- Reject or revise the exact specification; do not generalize a pass to all early trading.

### Stage 5: Controlled Validation

- Run minimum-risk plumbing canaries only after tape and replay acceptance.
- Test useful size only after canary reconciliation.
- Authorize no more than the exact horizon, tactic, inventory cap, exit rule, and size tested.

## Principal Risks

1. Early uncertainty: apparent edge can be a miscalibrated sharp forecast.
2. Stale quotes: scheduled source updates can turn passive liquidity into toxic fills.
3. Inventory lock-in: entry may be easy while exit is expensive or unavailable.
4. Lifecycle overfitting: many times, models, and quote rules create a large search surface.
5. Causal leakage: nominal model times can precede actual dissemination and receipt.
6. Resolution mismatch: physical high skill may not map to the venue's reported high.
7. Capacity double-counting: multiple horizons may be the same station/date exposure.
8. Infrastructure distraction: broadening the strategy before the current execution pilot works could delay the most informative test.

These risks argue for shared data and sequential gates, not for staying permanently late.

## Bottom Line

Extending to the day before is strategically sensible because it may expose more liquidity and more gradual price discovery. It also creates a harder problem: uncertainty is wider, forecast revisions matter, quotes can become stale, and inventory must be managed for much longer.

The correct design is therefore a full-lifecycle forecasting and execution system. It observes the market and weather state from first listing, produces a new calibrated distribution whenever information changes, and evaluates separately defined tactics at each horizon. The late intraday strategy remains the control and the current implementation priority; D-1 becomes the first major expansion once causal data, replay, and inventory evidence are ready.
