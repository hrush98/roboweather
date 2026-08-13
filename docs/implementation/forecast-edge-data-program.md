# Forecast Edge Data Program Implementation Plan

Status: Approved for research implementation; not approved for production pricing or funded trading

Last updated: 2026-08-13

## Feature Goal

Build a causal, versioned forecast-research layer that estimates a coherent probability distribution for the resolution-source-reported daily high or low at a specific station from day-before/first-listing conditions through settlement.

## Information Thesis And Cohort Boundary

RoboWeather is not attempting to replace public numerical weather prediction
globally. It tests whether specialized interpretation of causally available
public weather information produces a better probability estimate for one
settlement-specific station contract at one exact decision time, and whether
that advantage remains after contemporaneous executable market prices are
considered.

Treat these as four separate evidence cohorts:

| Cohort ID | Market family | Geography | Current evidence |
| --- | --- | --- | --- |
| `US_HIGH` | `HIGH_TEMP` | United States | F0 settlement mapping is established; corrected exact-cutoff F3, F4, and F6 are rejected, so no forecast currently enters Price Sheet V2. |
| `GLOBAL_HIGH` | `HIGH_TEMP` | Non-US | Collection exists; settlement, units/day semantics, localization, and probability skill are unvalidated. |
| `US_LOW` | `LOW_TEMP` | United States | Collection exists; settlement truth and remaining-cooling model are unvalidated. |
| `GLOBAL_LOW` | `LOW_TEMP` | Non-US | Collection exists; settlement, units/day semantics, localization, and remaining-cooling model are unvalidated. |

Acceptance in one cohort never transfers automatically to another. A model may
share source transforms or a hierarchical backbone, but each cohort retains a
separate settlement mapping, support/unit transform, horizon contract,
calibration head, holdout, version, and evidence clock. High and low markets
must use distinct physical state decompositions:

```text
daily high: peak-passed hurdle + conditional additional heating
daily low:  trough-passed hurdle + conditional additional cooling
```

The default modeling strategy is partial pooling, not four unrelated models
and not one universal model. Share only effects whose cross-cohort transfer
beats cohort-only controls on untouched data; otherwise retain cohort- or
station-specific heads.

The economic rationale and falsification criteria live in `docs/hypotheses/2026-07-17-station-specific-forecast-edge.md`. The full source review is preserved in `reports/forecast-edge-data-source-strategy-2026-07-17.md`. The full-lifecycle consumer and horizon contract live in `docs/implementation/full-market-lifecycle-trading.md`. Execution phase sequencing remains in `docs/execution-rebuild-roadmap.md`; this approved research work does not change the current Price Sheet V2a critical path.

## Non-Goals

- Do not restart funded trading.
- Do not register a new WeatherNext, NBM, MADIS, or GOES live strategy merely because collection exists.
- Do not interrupt Price Sheet V2 or Phase 3 tape acceptance work.
- Do not add model families solely to enlarge a leaderboard.
- Do not treat weather MAE improvement as sufficient trading evidence.
- Do not mix market-aware pricing, settlement mapping, and execution haircuts into one opaque model score.
- Do not write bulky raw forecast fields, members, satellite imagery, or runtime databases into Git.
- Do not build a graph neural network or large end-to-end neural model before simple spatial residuals pass ablation.

## System Schematic

```text
authoritative venue settlement -------------------------+
Weather Underground / CLI / ASOS comparison ------------+--> truth and settlement mapping
                                                         |
WeatherNext / NBM / HRRR source vintages ----------------+--> multi-model prior
                                                         |
high-frequency target-station observations ---------------+--> exact high/low-so-far
                                                         |
MADIS neighbor observations + model-at-neighbor ---------+--> spatial residual update
                                                         |
GOES cloud/radiation observations + HRRR expectation ----+--> heating-surprise update
                                                         |
                                                         v
                                    coherent latent final-high/low distribution
                                                         |
                                                         v
                                  resolution-source reported-high/low distribution
                                                         |
                              +--------------------------+------------------+
                              |                                             |
                              v                                             v
                     weather-only evaluation                    Price Sheet V2 consumer
                     versus public/market baseline               only after acceptance
```

## Common Data Contracts

### Source Vintage

Every forecast or observation record must carry, where applicable:

- `source_id` and source family;
- source/model version;
- initialization or observation time;
- publication/dissemination time;
- local UTC receipt time;
- valid time and forecast lead;
- lifecycle horizon and station-local decision bucket;
- predecessor source vintage or forecast distribution when the record is a revision;
- station or grid coordinates and elevation;
- member identifier;
- raw field/variable name and units;
- quality-control flags;
- immutable payload or payload checksum;
- collector/parser version.

Historical replay may use only data available by the simulated quote-ready timestamp. When publication time cannot be established, use a conservative documented availability lag or mark the row ineligible for causal evaluation.

### Outcome Truth

Keep distinct fields for:

- venue-settled winning token/bucket;
- Weather Underground displayed daily high and capture time;
- official NWS/CLI daily high where available;
- routine/special METAR maximum;
- five-minute ASOS maximum;
- reconstructed official-style rolling-five-minute maximum where supportable;
- source-local day and timezone;
- revision status and retrieval provenance.

Never overwrite one source with another under a generic `final_high_tmpf` label. A selected canonical training target must record the source and mapping version.

### Forecast Distribution

Every scored forecast must produce one normalized distribution over an integer temperature support, plus:

- cohort ID, market family, geography class, station identity, and frozen
  station-scope version;
- forecast version and training cutoff;
- decision timestamp and horizon/local-time bucket;
- immutable predecessor distribution and revision magnitude;
- latent physical-high probabilities;
- resolution-source-reported-high probabilities;
- expected high, quantiles, spread, and peak-already-occurred probability;
- per-source contribution/availability;
- exact high-so-far used;
- calibration version;
- missing-source and fallback flags.

Bucket probabilities must be derived from this distribution rather than trained independently without an ordering/coherence constraint.

Venue units and displayed bucket syntax remain part of settlement mapping.
Models may use a canonical internal scale, but conversion, rounding, endpoint
inclusion, local-day definition, and daylight-saving behavior must be
versioned and tested before probabilities are mapped to tokens.

## Storage And Collection Design

Use separate generated/runtime storage for forecast-source data. Do not add raw model members or satellite rasters to the live trading ledger or the current research SQLite database.

Proposed layers:

1. Immutable or checksummed raw source cache/partitions for reproducibility.
2. Compact source catalog for vintages, coverage, versions, and parse status.
3. Materialized station/time features and probability distributions for evaluation.
4. Small versioned model/calibration artifacts.

Generated source caches, model artifacts, reports, and runtime databases remain uncommitted under the repository rules.

## Workstream 0: Settlement And Sensor Truth Audit

### Objective

Determine what temperature each US high-temperature market actually settles from and whether the current IEM maximum is a valid proxy.

### Deliverables

- Reproducible station/date comparison across venue, Weather Underground, CLI, routine METAR, and high-frequency ASOS sources.
- Mismatch counts by station, source pair, degree difference, and whether the market-winning bucket changes.
- Availability/revision notes for each outcome source.
- Recommendation for canonical historical training label, live high-so-far, and settlement mapping.
- Explicit unresolved cases rather than silent fallback.

### Exit Gate

- Every promoted US station has a documented target source or a documented probabilistic mapping to venue settlement.
- Material one-degree and bucket-changing mismatches are quantified.
- The audit determines whether existing training/outcome rows require backfill or only provenance clarification.

F0 accepted contract and current evidence:

- `us_high_temperature_truth_v1` keeps venue settlement, Weather Underground,
  NWS CLI, routine/special METAR, and NCEI one-minute ASOS values separate and
  records source URL, capture time, day semantics, exactness, and failures.
- `scripts/forecast_truth_audit.py` audited 230 station-dates across all 10 US
  high-temperature stations from 2026-06-01 through 2026-06-23. Polymarket
  exposed one fully resolved winning bucket for 220 rows; all 10 June 18 chains
  remained unresolved and were excluded rather than inferred.
- The civil-local-day IEM routine/special METAR maximum classified into the
  venue-winning bucket on all 220/220 comparable rows. NWS CLI conflicted on
  56/176 and NCEI one-minute ASOS on 115/196. Weather Underground was rendered
  in rounded Celsius on this host; interval-aware comparison still conflicted
  on 21/220 and therefore cannot be treated as an immutable exact-F archive.
- Venue-winning bucket is the authoritative settlement label. The accepted
  numeric training proxy is the separately versioned IEM routine/special
  report maximum, not physical/CLI daily high. Existing matched-cohort numeric
  labels do not require relabeling, but provenance and venue-bucket backfill do.
- Live high-so-far means the causal maximum of available IEM routine/special
  reports with availability and revision flags. It is exact for that report
  stream, not the latent ASOS rolling-five-minute physical maximum. Missing
  source or venue evidence fails closed with no silent substitution.

## Workstream 1: Public Probabilistic Baselines

### WeatherNext 2

Retain ensemble members rather than only mean/max summaries. Extract a small grid stencil around every station and derive:

- member local-day temperature traces;
- ensemble threshold probabilities, quantiles, spread, skew, and upper tail;
- spatial gradients and elevation-aware interpolation candidates;
- forecast-cycle revisions;
- WeatherNext-versus-HRRR disagreement.

WeatherNext is initially a D-1/opening probabilistic prior and forecast-revision source, not a direct airport-maximum oracle. Because it is six-hourly and coarse relative to an airport maximum, learn a causal localization/diurnal correction rather than treating its nearest-grid maximum as truth.

### NBM

Collect deterministic, standard-deviation, percentile, and available maximum-temperature probability products. Record operational version changes and use NBM as both a candidate input and a public probabilistic benchmark, especially for D-1 and D0 pre-dawn horizons.

### Exit Gate

- Identical station/date/timestamp coverage comparison against the existing HRRR-rich baseline.
- Report full-ladder and threshold probability metrics, not just temperature MAE.
- Show all-history, recent, station, horizon, and regime slices with weather-date clustered uncertainty.
- No Price Sheet V2 integration unless incremental skill survives the predeclared evaluation.

## Workstream 2: Exact High-So-Far And Remaining Heating

### Objective

Replace the implicit absolute-high framing with a structurally valid remaining-heating distribution:

```text
H(t) = exact resolution-compatible high-so-far
Delta(t) = final reported high - H(t), where Delta(t) >= 0
```

Estimate:

1. probability the peak has already occurred;
2. conditional distribution of additional degrees when more heating remains.

### Required Features

- exact high-so-far and its source/averaging semantics;
- local time, solar time, and time since sunrise/solar noon;
- recent warming/cooling slopes;
- recent record-high cadence;
- current wind/dewpoint/cloud/pressure regime;
- model remaining maximum distributions;
- observed/model residual at the target station.

### Exit Gate

- Distribution respects the high-so-far lower bound for every row.
- It improves ranked probability score/log loss over the existing absolute/bucket baseline on identical held-out rows.
- Calibration is reported by local-time/horizon bucket, including `Delta=0` reliability.

## Workstream 3: Spatial Observation Residuals

### Objective

Use high-frequency MADIS/ASOS neighbors to estimate model error and boundary movement approaching the target station.

### Initial Feature Family

- neighbor observed temperature minus model temperature valid at the same location/time;
- dynamically upwind weighted residual;
- neighbor warming-rate residual;
- temperature/dewpoint/wind gradients;
- estimated advection travel time;
- elevation-adjusted residual;
- boundary/wind-shift indicators;
- coastal versus inland divergence.

Start with deterministic engineered aggregations and transparent station maps. Do not use unrestricted nearest-neighbor search after viewing outcomes.

### Exit Gate

- Station networks and weighting rules are frozen before held-out scoring.
- Quality-control filters and missing-neighbor behavior are explicit.
- The spatial update adds skill beyond target-station observations and model point features on identical rows.

## Workstream 4: Observed Cloud And Radiation Surprise

### Objective

Measure realized heating relative to forecast using GOES cloud/radiation products.

### Initial Feature Family

- observed minus HRRR downward-shortwave radiation over 15/30/60/120 minutes;
- cumulative radiation deficit since sunrise;
- clear-sky duration and clearing timestamp;
- cloud optical depth and motion in an upwind stencil;
- observed-versus-HRRR cloud category/probability;
- aerosol/smoke flag where available;
- temperature response following clearing or cloud arrival.

### Exit Gate

- Satellite observation and product availability are causally timestamped.
- Cloud/radiation coverage and quality flags are retained.
- Incremental skill is demonstrated overall or in a frozen cloud-sensitive regime without degrading the broad forecast materially.

## Workstream 5: Secondary Regime Sources

Only begin after the core gates identify a residual failure mode. Candidate sources include:

- coastal water and sea-surface temperature;
- MRMS recent precipitation and soil-wetness proxies;
- snow cover;
- smoke/aerosol optical depth;
- SMAP or land-model soil moisture;
- aircraft ascent/descent profiles;
- regional solar generation as a secondary radiation proxy.

Power load, traffic, and pedestrian data remain out of scope unless a predeclared residual mechanism and reliable causal data feed are demonstrated.

## Modeling Design

Begin with interpretable, regularized methods appropriate to the effective sample size:

- ensemble model output statistics or quantile mapping;
- hierarchical residual/ordinal models with station partial pooling;
- gradient-boosted residual models with horizon and regime interactions;
- Bayesian/model averaging with weights learned only from past outcomes;
- a zero-inflated additional-heating component for `Delta=0`.

### Hierarchical Bayesian Trees

Hierarchical Bayesian additive regression trees are a plausible later
challenger, especially for nonlinear station, horizon, forecast-disagreement,
cloud, radiation, and recent-heating interactions. They should not be the
initial core model.

The hierarchy and the trees solve different problems:

- station, climate-region, lifecycle-horizon, season, and regime effects need
  partial pooling so thin groups borrow strength without being treated as
  identical;
- trees may capture residual interactions that remain after the physical and
  probabilistic structure is represented explicitly;
- a posterior predictive distribution may support a reproducible uncertainty
  reserve, but only after its coverage is validated on independent weather
  dates and regional events.

Arguments against using a hierarchical tree ensemble as the primary model:

- the effective sample is independent station/dates and weather regimes, not
  the much larger count of correlated intraday snapshots;
- Bayesian regularization cannot create information where few independent
  outcomes exist, and an iid likelihood can still produce falsely narrow
  posteriors;
- vanilla per-token trees do not enforce an ordered, normalized temperature
  ladder and can assign mutually inconsistent bucket probabilities;
- tree partitions extrapolate poorly into record temperatures, unseen source
  versions, and weather regimes outside their training support;
- hierarchy, tree structure, priors, likelihood, feature selection, and
  probability transformation introduce many research degrees of freedom;
- posterior uncertainty does not include settlement-source error, model/source
  drift, market regime change, or adverse execution unless those are modeled
  and validated separately;
- a flexible market-aware tree can imitate historical market deviations
  without demonstrating meteorological information that persists forward;
- a better weather posterior does not establish that its edge is fillable or
  survives fill-conditioned markouts.

The preferred initial architecture is a structured hierarchical
distributional ensemble:

```text
NBM + WeatherNext + HRRR distributions
-> past-outcome-only horizon-specific ensemble weights
-> station/climate-region partial pooling and localization
-> peak-passed plus conditional additional-heating distribution
-> coherent integer final-high distribution
-> separately versioned settlement-source mapping
-> separately versioned market-aware Price Sheet V2 calibration/reserves
-> separately versioned execution overlay
```

Use a hierarchical logistic or additive model first for
`P(peak already occurred)` and a hierarchical ordinal or distributional model
for additional heating conditional on the peak not having occurred. This is
more data-efficient, auditable, and naturally compatible with the
`final_high >= high_so_far` constraint.

After that structured baseline is frozen, compare gradient-boosted and
hierarchical Bayesian tree models only as residual challengers on identical
chronological coverage. A tree challenger advances only if it improves proper
probability scores, calibration, recent and regime stability, and
market-relative information after weather-date clustering. Its forecast must
then pass Price Sheet V2 quoted-price and tape-backed execution gates; a direct
tree model of historical trade/no-trade or PnL is out of scope because it would
entangle forecast quality, policy selection, and execution selection.

Keep these stages separately versioned:

```text
raw source forecasts
-> weather localization/update
-> latent high distribution
-> settlement-source mapping
-> outcome calibration
-> market-aware Price Sheet V2 shrinkage/reserves
-> execution overlay
```

Do not allow market price into the weather-only forecast used to claim meteorological edge. It may enter the separately reported trade fair and Price Sheet V2 calibration layer.

## Evaluation Contract

### Baselines

- current observation-only model;
- current HRRR-rich model on identical rows;
- raw HRRR-derived distribution where defined;
- NBM probabilistic MaxT;
- normalized contemporaneous market-implied distribution.

### Metrics

- multiclass log loss;
- ranked probability score or CRPS;
- threshold Brier score and reliability;
- top-bucket accuracy as a secondary diagnostic;
- sharpness conditional on calibration;
- market-relative log score/Brier improvement;
- entry-price paper value as a separate trading diagnostic, never a substitute for probability scoring.

### Sampling And Uncertainty

- Split chronologically and preserve model/source versions.
- Weight or deduplicate repeated intraday rows so they do not dominate independent outcomes.
- Cluster confidence intervals by weather date and relevant regional event.
- Report identical-coverage ablations; never compare models on different easy/hard subsets without a coverage decomposition.
- Freeze source transforms, station maps, horizons, and regimes before held-out evaluation.

### Station Specialization

- Every probability row is station-specific, but station effects should be
  estimated with hierarchical partial pooling across predeclared climate and
  settlement groups.
- Freeze a station-scope registry before viewing holdout outcomes. Allowed
  scopes begin with whole cohort and named meteorological/settlement groups.
  An individual-station head becomes eligible only after a declared minimum of
  independent resolved dates and must beat its pooled parent on a later
  untouched window.
- Exact calendar dates are identities for causal joins, chronological splits,
  deduplication, and caps. They are never discovery predicates. Season or
  regime features require an ex ante definition.
- Report cohort-wide skill, station dispersion, leave-one-station-out transfer,
  maximum station contribution, and shrinkage toward the parent.
- A station-specific improvement may authorize only that frozen station scope;
  it cannot be presented as cohort-wide confirmation.

### Acceptance

A candidate forecast version may be proposed to Price Sheet V2 only when:

- target/settlement provenance is established;
- causal source availability is reproducible;
- identical-coverage held-out probability metrics improve;
- the gain is not explained by a few correlated weather dates;
- recent performance is not materially negative;
- market-relative information remains after contemporaneous price is considered;
- failure regimes and fallback behavior are documented.

Passing this gate authorizes pricing research only. It does not authorize live funding, sizing, or execution changes.

## Explicit Execution Queue

This queue is the durable backlog for the forecast-edge program. It preserves
future work without creating board threads before the work begins. The board
owns volatile execution state; this table owns slice order, dependencies,
launch questions, and closure outputs.

Queue states have these meanings:

- `READY`: dependencies are settled and a continuation session may start the
  slice.
- `IN_PROGRESS`: one open board thread owns the slice.
- `BLOCKED`: a named predecessor or required artifact is unresolved.
- `GATED`: do not start until the row's evidence condition is met.
- `COMPLETE`: the closure output passed the slice gate.
- `REJECTED`: the question was answered defensibly but the proposed source or
  model failed its gate. Rejection is a valid completed investigation.
- `SUPERSEDED`: a later contract replaced this slice before or after execution.

| ID | Pillar | State | Depends on | Thread-launch question | Named closure output | Thread |
| --- | --- | --- | --- | --- | --- | --- |
| F0 | Settlement | COMPLETE | — | Do IEM, Weather Underground, CLI, high-frequency ASOS, and venue settlement agree sufficiently to define the US high-temperature training target and exact high-so-far? | Reproducible truth-audit command/report, station mismatch table, and canonical target/backfill decision. | [T0015](../../board/closed/2026/T0015-audit-high-temperature-settlement-truth.md) |
| F0B | Information | COMPLETE | — | What is the smallest genuinely distinct current forecast baseline, and can it be scored without outcome-conditioned ladders or correlated-row inflation? | Prediction-correlation/pruning report and frozen fixed-support, weather-date-aware evaluation contract. | [T0014](../../board/closed/2026/T0014-freeze-minimal-forecast-baseline.md) |
| F1 | Information | COMPLETE | F0 settled | Can WeatherNext, NBM, HRRR/RRFS, and observation vintages be collected and replayed from their actual causal availability times? | Source-vintage contracts, separate runtime catalog/cache, bounded collectors, tests, and coverage report. | [T0016](../../board/closed/2026/T0016-build-causal-forecast-source-catalog.md) |
| F2 | Information | REJECTED | F0, F0B, F1 settled | Does WeatherNext 2 or NBM add held-out probability skill beyond the minimal HRRR-rich baseline and contemporaneous market on identical rows? | WeatherNext/NBM identical-coverage and market-relative benchmark with a source acceptance or rejection verdict. | [T0017](../../board/closed/2026/T0017-benchmark-nbm-and-weathernext-forecast-skill.md) |
| F3 | Information | REJECTED | F0 and F0B settled | Does a peak-passed plus conditional-additional-heating distribution outperform the frozen absolute/bucket baseline for US high temperature? | Coherent remaining-heating model, chronological ablation report, and acceptance or rejection verdict. | [T0018](../../board/closed/2026/T0018-build-remaining-heating-distribution.md), superseded by [T0021](../../board/closed/2026/T0021-complete-us-high-f6-market-relative-gate.md) |
| F4 | Information | REJECTED | F3 settled | Do frozen high-frequency upwind station residuals add information beyond target-station observations and model point features for the US-high control? | MADIS/ASOS spatial residual implementation and controlled ablation. | [T0019](../../board/closed/2026/T0019-implement-frozen-spatial-residual-nowcast.md) |
| F6 | Cross-pillar | REJECTED | F3 exact-cutoff revalidation settled | Does frozen US-high F3 pass Price Sheet V2 selected, quoted-price, market-relative, edge-half-life, and tape-backed research gates at one lifecycle horizon? | US-high Price Sheet V2 report with executable edge-decay curve and acceptance or rejection verdict. | [T0021](../../board/closed/2026/T0021-complete-us-high-f6-market-relative-gate.md) |
| FC0 | Cross-pillar | READY | F0 settled | What immutable cohort, station, unit, local-day, and eligibility registry defines US/global high/low research without transferring evidence between them? | Four-cohort registry and dependency matrix with explicit unsupported stations and source gaps. | — |
| F0GH | Settlement | BLOCKED | FC0 settled | What source and mapping define venue-authoritative outcomes and causal high-so-far for each eligible non-US high-temperature station? | Global-high settlement/source audit and versioned station mappings. | — |
| F0UL | Settlement | BLOCKED | FC0 settled | What source and mapping define venue-authoritative outcomes and causal low-so-far for each eligible US low-temperature station? | US-low settlement/source audit and versioned station mappings. | — |
| F0GL | Settlement | BLOCKED | FC0 settled | What source and mapping define venue-authoritative outcomes and causal low-so-far for each eligible non-US low-temperature station? | Global-low settlement/source audit and versioned station mappings. | — |
| F3S | Information | BLOCKED | FC0, F0GH, F0UL, and F0GL settled | Do predeclared hierarchical station and climate-region effects improve probability skill without arbitrary station or date filtering? | Frozen station-scope registry, partial-pooling model, eligibility thresholds, and controlled ablation. | — |
| F3GH | Information | BLOCKED | F0GH and F3S settled | Does a localized global-high model improve public and market baselines under its own causal cohort contract? | Global-high coherent distribution, chronological ablation, and verdict. | — |
| F6GH | Cross-pillar | BLOCKED | F3GH or later global-high forecast accepted | Does one frozen global-high version pass the complete F6 contract? | Global-high Price Sheet V2, edge-half-life, tape, and verdict report. | — |
| F3UL | Information | BLOCKED | F0UL and F3S settled | Does a trough-passed plus conditional-additional-cooling model improve US-low public and market baselines? | US-low coherent remaining-cooling distribution, chronological ablation, and verdict. | — |
| F6UL | Cross-pillar | BLOCKED | F3UL or later US-low forecast accepted | Does one frozen US-low version pass the complete F6 contract? | US-low Price Sheet V2, edge-half-life, tape, and verdict report. | — |
| F3GL | Information | BLOCKED | F0GL, F3UL, and F3S settled | Does global localization add skill to the low-temperature model beyond US-low and public baselines? | Global-low localized distribution, transfer ablation, and verdict. | — |
| F6GL | Cross-pillar | BLOCKED | F3GL or later global-low forecast accepted | Does one frozen global-low version pass the complete F6 contract? | Global-low Price Sheet V2, edge-half-life, tape, and verdict report. | — |
| F4X | Information | BLOCKED | F4, F3S, and relevant cohort model settled | Do frozen spatial residual features transfer or require cohort/station-specific refits outside US high temperature? | Four-cohort spatial transfer matrix and accepted/rejected scoped versions. | — |
| F5 | Information | IN_PROGRESS | F3 settled | Does causally observed cloud/radiation surprise add broad or predeclared cloud-regime skill for US high temperature? | GOES heating-surprise implementation and controlled ablation. | [T0023](../../board/T0023-test-causal-goes-heating-surprise.md) |
| F5X | Information | BLOCKED | F5 and relevant cohort model settled | Does observed cloud/radiation surprise transfer to other eligible high/low cohorts under identical-row ablation? | Cohort-specific GOES transfer report and scoped versions. | — |
| F5A | Information | GATED | F4 or other frozen evidence identifies a coastal residual mechanism | Does local sea, bay, or lake temperature improve the affected coastal or lake-regime forecast after core sources are known? | Local-water-temperature causal dataset and predeclared regime ablation. | — |
| F5B | Information | GATED | Long-history causal corpus spans multiple ENSO events and F0B evaluation is available | Does vintage-correct RONI improve seasonal D-1 calibration after forecast-model information is known? | RONI/ENSO incremental calibration ablation or explicit no-change verdict. | — |

`Settled` means the predecessor thread closed with `COMPLETE`, `REJECTED`, or
an explicit no-change answer that resolves the dependency. It does not mean a
source passed. A downstream row that requires an accepted artifact remains
blocked when its predecessor was rejected; F6 is the explicit example.

## Threading And Continuation Rules

The operator may start a new session with a request such as:

```text
Continue executing docs/implementation/forecast-edge-data-program.md.
```

The agent must then apply this selection algorithm:

1. Complete the repository's fixed orientation read through
   `board/INDEX.md`.
2. Search open board threads for a canonical-plan reference to this file and
   an `F*` queue ID.
3. If a linked `ACTIVE` thread exists, resume the earliest queue row with an
   actionable next action. Do not create another thread for the same slice.
4. Otherwise, resume a linked `PARKED` or `WAITING` thread only when its exact
   recorded unblock condition is now satisfied.
5. If no linked thread is resumable, inspect this queue from top to bottom and
   select the first `READY` row whose dependencies are demonstrably settled.
6. Start exactly one board thread with `$start-thread`, using the queue row's
   pillar as its `--pillar` value. Its question and closure output must preserve
   the queue row's meaning while narrowing the immediate work. Add these lines
   to the new thread's evidence section:

   ```text
   - Canonical plan: docs/implementation/forecast-edge-data-program.md
   - Queue slice: F#
   ```

7. In the same change, mark the selected row `IN_PROGRESS` and link its
   `T####`. Do not open threads for later rows.
8. Before handoff, close or park the thread through the repository skill. On
   closure, update the row to `COMPLETE`, `REJECTED`, or `SUPERSEDED`, link the
   closed thread, update the checklist and decision log when applicable, and
   expose newly eligible rows as `READY`. On parking, leave the row
   `IN_PROGRESS` and retain the open thread link.
9. If no row is eligible, report the earliest unmet dependency. Do not widen a
   gate, silently skip a row, or manufacture a task merely to keep working.

One ordinary continuation request advances one slice. Parallel execution
requires an explicit request or a clearly independent active thread, and must
still respect the board's global active-thread cap. Queue position grants no
production-pricing, funded-trading, sizing, or execution authority.

## Proposed Implementation Slices

The detailed slices below are the architecture and acceptance contracts behind
the queue. The queue ID is the stable handoff identifier; implementation names
may evolve inside a slice without changing its question.

### Slice 0: Truth Audit

- Build read-only outcome comparison and mismatch report.
- Decide canonical target and high-so-far semantics.
- Add tests for timezone, rolling maximum, rounding, and bucket mapping.

### Slice 0B: Baseline And Evaluation Repair

- Measure prediction correlation and disagreement across current model names
  on identical causal rows; collapse behaviorally duplicate families.
- Retain a minimal observation-only control, HRRR control, and any demonstrably
  distinct distributional control rather than treating estimator count as
  independent evidence.
- Replace outcome-conditioned synthetic ladder evaluation with fixed integer
  support or actual causally observed market ladders.
- Freeze chronological, weather-date-clustered full-distribution metrics and a
  contemporaneous normalized market baseline before testing new sources.


F0B accepted contract and current baseline:

- `forecast_fixed_support_exact_cutoff_weather_date_v2` supersedes the
  flawed v1 selector. V1 filtered an observation-derived `hour_local <= 14`
  and admitted 4,338/4,364 validation rows after the intended 14:00 decision,
  usually at 14:51-14:58. V2 requires station timezone provenance, compares
  the full observation timestamp to the exact local cutoff, preserves the
  frozen Fahrenheit `-20..130` support, scores one station/date row, and
  bootstraps whole weather dates. The 5,000-bootstrap F3 report fingerprint is
  `04c45519df5d5cffb2ce4817e9fe04f56a8ed6e3fbee26e306a2d69fbe17f669`.
- The minimal current controls are
  `mvp_pm_active_us12_obs_2022_2025`,
  `mvp_hrrr_rich_pm_active_us12_obs_2022_2025`, and the behaviorally distinct
  `ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025`. Other estimators
  over those same information sets remain diagnostics, not independent
  evidence.
- Existing grouped metrics from synthetic ladders centered on
  `final_high_tmpf` are invalid for promotion comparisons and are retired.
  New weather-only comparisons use the fixed support. Market-relative scoring
  must use an actually observed, causally timestamped, complete normalized
  ladder on identical rows and fails closed when that ladder is unavailable.
- Reproduce the current 18-artifact audit with
  `scripts/forecast_edge_report.py`; generated detail remains uncommitted under
  `reports/forecast-edge/f0b-current/`.

### Slice 1: Forecast Source Catalog

- Add source-vintage contracts and separate runtime catalog/cache.
- Implement bounded forward collectors for WeatherNext, NBM/GLMP, HRRR/RRFS metadata, and selected fields beginning by first supported market listing.
- Establish raw retention, version-change, and failure telemetry.

F1 accepted contract and current evidence:

- `forecast_source_vintage_v1` stores immutable source contracts, collection
  attempts, content-addressed raw revisions, market targets, provider and local
  availability clocks, and replay-visible artifacts in a separate runtime
  catalog under `~/.local/state/roboweather/forecast_sources/`.
- WeatherNext 2 requires provider `ingestion_time`; NBM, HRRR, RRFS, and IEM
  use first successful local observation unless a stronger frozen provider
  field is explicitly contracted. HTTP `Last-Modified` is provenance only
  and never backdates causal replay.
- `scripts/forecast_source_catalog.py` plans station-bounded NBM and HRRR
  GRIB subsets plus IEM routine/special observation snapshots only after the
  first supported market listing. It imports bounded WeatherNext/RRFS
  manifests, validates raw formats, records failures and revisions, and
  enforces artifact/byte ceilings.
- The bounded host probe captured and decoded three NBM artifacts, three HRRR
  artifacts, and one IEM observation vintage. WeatherNext has no host artifact
  because approved Google access is not configured; RRFS remains fail-closed
  until an operational version and endpoint are frozen. These are explicit
  source limitations, not fallbacks.
- Reproduce current coverage with
  `scripts/forecast_source_catalog.py --report-only --report-out reports/forecast-edge/f1-source-catalog-current`.
  Generated catalog, raw artifacts, and report remain uncommitted.

### Slice 2: WeatherNext/NBM Benchmark

- Backfill WeatherNext historical members for the scoped stations/fields.
- Materialize causal D-1 opening, D-1 revision, and D0 station distributions.
- Run identical-coverage baseline and market-relative reports.

F2 settled contract and evidence:

- `nbm_v5_archive_cycle_plus_2h_v1` freezes historical NBM v5 eligibility at
  cycle initialization plus two hours, retains archive modification time as
  provenance only, and materializes nearest-grid 12-hour TMAX mean and
  ensemble-standard-deviation distributions for D-1 08:00, D-1 20:00, and
  the frozen D0 latest-at-or-before-14:00 station-local horizons.
- `scripts/forecast_source_benchmark.py` scored 541 D0 station/date rows over
  55 weather dates and 10 stations with complete same-snapshot HRRR-rich and
  normalized ask-ladder distributions. NBM archive materialization covered
  100% of that cohort; 17 otherwise eligible rows failed closed for incomplete
  observed ladders.
- The untouched 22-date holdout gave NBM only a 2.09% fit-period weight over
  HRRR-rich. The blend improved RPS by `0.01135` but worsened log loss by
  `0.00340`; its clustered log-loss interval crossed zero. Against the market,
  fitted NBM weight was effectively zero. Raw NBM was materially worse than
  HRRR-rich overall and on the recent 14-date slice in both scores.
- Reject this exact NBM transform for F2 and do not pass it to Price Sheet V2.
  WeatherNext is unavailable, not rejected: approved access and provider
  ingestion-time history remain absent. A future localized NBM transform or
  WeatherNext contract is a new version with a new evidence clock, not a
  reinterpretation of this holdout.
- D-1 NBM rows were materialized but not scored because the ledger lacks
  identical D-1 baseline and complete market ladders. F2 therefore settles the
  tested D0 source contract without claiming D-1 skill.

### Slice 3: Remaining-Heating Distribution

- Build the exact-high-so-far state.
- Train and validate peak-passed plus conditional-additional-heating models.
- Produce coherent integer distributions.

F3 corrected contract and rejection evidence:

- `remaining_heating_hurdle_multinomial_exact_cutoff_v3` separates
  peak-passed probability from a regularized positive-additional-heating
  distribution and assigns no mass below the exact integer high-so-far.
  Independent ordinal v1/v2 variants are retired because crossed survival
  curves could collapse learned outcomes to zero probability.
- T0021 found that the original forward loader used `local_hour <= 14` and
  admitted rows throughout the 14:00 hour. The loader now reuses the exact
  timezone-aware selector; both historical and forward diagnostics prove zero
  rows after 14:00 local.
- On the corrected 535-row, 55-date cohort, the frozen weather forecast uses
  45.14% remaining-heating and 54.86% conditioned HRRR-rich. It still improves
  the untouched 22-date holdout versus HRRR-rich by `0.09641` log loss and
  `0.23931` RPS, with both clustered intervals below zero.
- The separately fitted 59.78% weather / 40.22% market combination worsens
  holdout log loss by `0.00258` and recent log loss by `0.03028`. F3 therefore
  fails its joint market-relative gate and no longer authorizes Price Sheet V2.
- Reproduce the generated, uncommitted report and versioned model artifact with
  `scripts/forecast_remaining_heating_report.py`; outputs live under
  `reports/forecast-edge/f3-current/`.

### Slice 3S: Hierarchical Station Specialization

- Freeze cohort membership, climate/settlement parent groups, station eligibility,
  and minimum independent-date thresholds before holdout scoring.
- Fit pooled, group, and eligible station heads with explicit shrinkage.
- Compare each child against its pooled parent and report leave-one-station-out
  transfer; never search arbitrary station combinations or exact dates.

### Slices 3GH, 3UL, And 3GL: Cohort Models

- Global high temperature starts from the accepted remaining-heating structure
  but receives its own localization, units/day mapping, calibration, and
  evidence clock.
- US low temperature uses a separately versioned trough-passed hurdle and
  conditional remaining-cooling distribution.
- Global low temperature tests transfer from the US-low structure only after
  global settlement/local-day mapping passes.
- Compare shared-backbone, cohort-head, and cohort-only controls. Keep separate
  models wherever partial pooling fails untouched transfer tests.

### Slice 4: Spatial Nowcast

- Keep the active F4 experiment frozen to the US-high control.
- Freeze neighbor network and dynamic upwind aggregation.
- Collect/model neighbor residuals.
- Run controlled ablation.
- Use F4X later to test transfer or cohort-specific refits; F4 success alone
  does not authorize global or low-temperature use.

F4 settled contract and evidence:

- `asos_upwind_residual_exact_cutoff_v2` freezes five outcome-blind ASOS
  neighbors within 150 km, ten-minute availability lag, explicit QC/fallback,
  causal HRRR interpolation, and distance/upwind/elevation weights.
- On the corrected exact-cutoff cohort, its zero-intercept spatial correction
  had only 35.51% eligible coverage on 535 rows and worsened untouched 22-date
  log loss by `0.12294` and RPS by `0.00659`; the recent 14-date slice also
  worsened both. Reject this exact transform and do not pass it to Price Sheet
  V2 or F4X.

### Slice 5: GOES Heating Surprise

- Freeze NOAA GOES-18/19 ABI-L2-DSRF v02r00 as the observed-radiation source. Use GOES-18 west of -105 degrees and GOES-19 otherwise; sample a DQF-good 3x3 station neighborhood. Embedded creation time and S3 Last-Modified are provenance only: replay visibility begins at first successful local observation.
- At the exact 14:00-local F3 horizon, derive trailing observed-radiation state without using files first observed after the decision. Condition the challenger on the frozen HRRR-rich forecast and contemporaneous selected-token market probability.
- Freeze broad and cloud-sensitive regimes, surprise thresholds, and abstention levels before outcomes. Report exact selected-token calibration, market-relative log loss, station/regime slices, and an abstention curve.
- Earlier decision horizons require separately frozen forecast versions and evidence clocks. If the information gate passes, open a separate cross-pillar executable-ask and t0/+30s/+2m/+5m/+15m edge-decay gate before any trading claim.
- The frozen statistical contract is `goes_dsr_market_relative_logit_v1`: a regularized selected-token logit conditioned on F3, the normalized contemporaneous market distribution, and clear/mixed/cloudy regime controls; the challenger adds radiation surprise plus only its predeclared regime interactions. Calibration requires 20 resolved weather dates, then the fitted artifact and a future activation boundary must be persisted before any date can count as untouched. Abstention diagnostics use predicted same-side ask edges of 0.00, 0.05, 0.10, and 0.15.

### Slice 5A: Local Water-Temperature Regimes

- Start only after a frozen residual analysis identifies a coastal, bay, or
  lake-air interaction worth testing.
- Use causally available local water temperature, anomaly, trend, air-water
  difference, and onshore-flow interactions rather than a high-dimensional
  global ocean field.
- Reject the source if its gain disappears outside the predeclared station and
  season regime or after the core forecast sources are known.

### Slice 5B: ENSO/RONI Regime Calibration

- Treat RONI as a slow seasonal background covariate, not a direct intraday
  signal.
- Preserve publication vintages and revisions; do not use final historical
  index values before they would have been available.
- Start only with a long-history corpus spanning multiple ENSO events, then
  test season, region, and horizon interactions after the main forecast stack
  is known.
- Reject the feature if apparent skill is explained by trend, a single event,
  or correlated weather dates.

### Slice 6: Price Sheet Candidate And Edge Half-Life

- Freeze one accepted forecast version and cohort.
- Join it to Price Sheet V2a at one frozen lifecycle horizon without changing signal selection.
- Re-run selected, quoted-price, and market-relative gates before any execution experiment.
- Set `t0` to the first causal quote-ready time after the frozen forecast
  update and processing latency. Hold that forecast fixed while sampling
  executable market prices at `t0`, `+30s`, `+2m`, `+5m`, and
  `+15m`; separately report later forecast revisions.
- Define side-aligned net edge as conservative forecast probability minus
  executable VWAP/price and all declared costs/reserves. Report the first time
  edge falls below half its initial value, becomes nonpositive, or is
  right-censored by missing, gapped, or unfillable tape.
- Classify an edge that disappears before the first realistic execution
  checkpoint as unusable latency evidence, not information edge. Require
  useful-size availability and later settlement/markout evidence for any
  trading claim.

## Proposed Module Boundaries

Names may change during implementation, but responsibilities should remain separated:

```text
weather_trader/forecast_data/contracts.py      source-vintage and raw-field contracts
weather_trader/forecast_data/catalog.py        coverage/version/runtime catalog
weather_trader/forecast_data/weathernext.py    WeatherNext access and station stencil
weather_trader/forecast_data/nbm.py            NBM access and probability products
weather_trader/forecast_data/madis.py          high-frequency station network
weather_trader/forecast_data/goes.py           cloud/radiation extraction
weather_trader/forecasting/truth.py             settlement/sensor truth mapping
weather_trader/forecasting/spatial.py           upwind residual features
weather_trader/forecasting/distribution.py      coherent final-high distribution
weather_trader/forecasting/evaluation.py        causal ablation and metrics
scripts/forecast_truth_audit.py                 reproducible outcome comparison
scripts/forecast_source_report.py               coverage and source-health report
scripts/forecast_edge_report.py                 identical-coverage skill report
```

Reuse existing feature, station metadata, and model infrastructure where contracts align; do not duplicate the current HRRR client or outcome store blindly.

## Documentation And Promotion Rules

- Update the hypothesis decision log at each evidence gate.
- Update this plan when architecture, slice status, or acceptance changes.
- Update `docs/current-trading-system-audit.md` only when evidence changes the system verdict.
- Update `docs/execution-rebuild-roadmap.md` only if this work changes phase sequencing or an exit gate.
- Update `docs/live-trading-journal.md` only if a forecast version changes funded operating assumptions.
- Record completed source/model/workflow changes in `docs/changelog.md`.

## Initial Checklist

- [x] Settlement/sensor truth audit implemented and run.
- [x] US station target-source mismatches quantified.
- [x] Canonical target/high-so-far semantics decided.
- [x] Current model families correlated and behaviorally duplicate controls collapsed.
- [x] Fixed-support, weather-date-aware baseline evaluation contract frozen.
- [x] Source-vintage contract implemented.
- [x] Separate runtime catalog/cache selected and tested.
- [x] Forecast collection begins by first supported market listing.
- [ ] WeatherNext historical station distributions materialized.
- [x] NBM probabilistic baseline collected and scored.
- [ ] D-1 opening and revision distributions pass causal horizon-specific evaluation.
- [x] Identical-coverage and market-relative report implemented.
- [x] Additional-heating distribution validated.
- [x] MADIS/upwind residual ablation completed and rejected.
- [ ] GOES cloud/radiation ablation completed.
- [ ] One US-high forecast version passes pricing-research acceptance.
- [ ] Four-cohort registry and three remaining settlement mappings frozen.
- [ ] Hierarchical station specialization passes controlled ablation.
- [ ] US-low remaining-cooling distribution evaluated.
- [ ] Global-high and global-low localized distributions evaluated.
- [x] Price Sheet V2 integration and executable edge half-life reviewed separately; corrected F6 rejected.

## Decision Log

- 2026-07-17: Approved the forecast-data program for research implementation while keeping all new sources out of production pricing and funded trading.
- 2026-07-17: Extended the output contract from a single intraday decision window to causally versioned D-1-through-settlement distributions and forecast revisions.
- 2026-07-17: Positioned WeatherNext and NBM as day-before probabilistic priors, with short-range models and observations carrying more weight as the lifecycle advances.
- 2026-07-17: Kept the late Price Sheet V2a pilot as the immediate pricing critical path and required earlier horizons to enter one at a time.
- 2026-07-30: Positioned hierarchical Bayesian trees as residual challengers rather than the initial core model. Preferred a structured hierarchical distributional ensemble with coherent remaining-heating probabilities, simple partial pooling first, and separate market-aware pricing and execution gates.
- 2026-08-12: Added the explicit just-in-time execution queue and deterministic continuation rules. Future sessions resume a plan-linked thread or open only the first eligible slice; deferred work remains in the plan rather than being pre-created on the board.
- 2026-08-12: Added baseline/evaluation repair before new-source comparison, gated local water-temperature research on a demonstrated residual mechanism, and deferred vintage-correct RONI evaluation until a long-history multi-event corpus exists.
- 2026-08-12: Completed F0B. Retired outcome-centered synthetic-ladder validation, froze full-distribution scoring on fixed support with weather-date uncertainty, audited 18 current artifacts on 4,364 identical station/date rows, and reduced the baseline to three information-set controls. This is an evaluation repair, not evidence of market-relative information edge.
- 2026-08-12: Completed F0. Across 220 venue-resolved station-dates, the existing IEM routine/special METAR maximum matched every winning bucket while CLI, one-minute ASOS, and interval-aware rendered Weather Underground did not. Froze venue bucket as authoritative, IEM report maximum as numeric proxy/high-so-far semantics, fail-closed source handling, and provenance-plus-venue backfill without matched-cohort numeric relabeling. No information-edge or funded authority changed.
- 2026-08-12: Completed F1. Froze `forecast_source_vintage_v1`, a separate content-addressed runtime catalog, listing-bounded NBM/HRRR/IEM collectors, strict WeatherNext/RRFS manifest imports, source-format validation, bounded retry/size behavior, immutable revision and failure telemetry, and causal replay queries. The host probe captured and decoded NBM, HRRR, and IEM artifacts; WeatherNext access and an operational RRFS contract remain explicit limitations. This establishes an information-research substrate, not forecast skill or funded authority.
- 2026-08-13: Rejected the exact F2 NBM v5 archive transform after a 541-row,
  55-weather-date identical-coverage D0 benchmark. A 2.09% fit-period NBM
  blend improved held-out RPS but worsened log loss, raw NBM was materially
  worse overall and recently, and the contemporaneous market assigned it
  effectively zero weight. WeatherNext remained unavailable rather than
  rejected because approved ingestion-time history is absent. No forecast
  entered pricing research and funded authority did not change.
- 2026-08-13: Completed F3 after correcting two structural evaluation/model
  failures. The legacy F0B hour bucket selected post-14:00 reports on
  4,338/4,364 rows, so `forecast_fixed_support_exact_cutoff_weather_date_v2`
  now performs an exact timezone-aware as-of selection. Independent ordinal
  curves also produced zero-mass learned bins, so
  `remaining_heating_hurdle_multinomial_exact_cutoff_v3` uses a peak-passed
  hurdle plus regularized multinomial positive heating. A coherent 43.99%
  remaining-heating / 56.01% HRRR-rich ensemble improved both log loss and RPS
  with clustered intervals below zero on the untouched 22-date holdout and
  recent 14-date slice. Market-relative point estimates also improved, but
  their intervals cross zero; acceptance authorizes F6 pricing research only,
  not funded trading.
- 2026-08-13: Expanded the execution queue to four explicit evidence cohorts.
  Preserved active F4 as US-high only; added FC0 and cohort settlement/model
  dependencies, hierarchical station specialization, distinct remaining
  cooling for lows, gated F4/F5 transfer slices, cohort-specific F6 gates, and
  executable edge-half-life measurement. The accepted discovery v1 grammar
  remains unchanged; a station-scope v2 requires FC0/F3S. No funded authority
  changed.
- 2026-08-13: Rejected `asos_upwind_residual_exact_cutoff_v2` for F4. The
  outcome-blind five-neighbor ASOS network, causal HRRR interpolation, frozen
  lag/QC/weighting rules, and zero-intercept correction were revalidated on
  the corrected exact-cutoff cohort. Coverage was 35.51%, and both holdout and
  recent metrics worsened versus corrected F3. F4X remains blocked, the
  version does not enter Price Sheet V2, and F5 is the next ready US-high
  information slice. Funded authority did not change.
- 2026-08-13: T0021 superseded the original F3 acceptance after finding that
  its forward path treated the full 14:00 hour as eligible. The shared loader
  now reuses the exact timezone-aware cutoff and has a database regression
  test. Corrected F3 still beat conditioned HRRR-rich but failed holdout and
  recent market-relative log-loss gates, so F3 is rejected.
- 2026-08-13: Started F5 under T0023. Froze NOAA GOES-18/19 ABI-L2-DSRF v02r00, first-successful-local-observation availability, the -105-degree satellite split, DQF-good 3x3 station sampling, solar-normalized observed-minus-HRRR surprise, clear/mixed/cloudy regimes, 0.10/0.20 surprise thresholds, and `goes_dsr_market_relative_logit_v1`. The five-minute timer is collecting forward evidence. At least 20 resolved calibration dates are required before freezing the fit and its future untouched activation boundary; no cloud/radiation skill is established yet.

- 2026-08-13: Completed and rejected F6. The frozen baseline selection yielded
  18 post-activation rows across 14 dates; F3 was worse than the same-side
  market ask in Brier and log loss, no calibrator had been frozen before
  activation, diagnostic quote-cap economics were negative, and only 2/18 t0
  rows had continuously valid, $25-fillable tape with negative median net
  edge. The emitted curve is diagnostic only and funded authority is unchanged.
