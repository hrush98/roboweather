# Forecast Edge Data Program Implementation Plan

Status: Approved for research implementation; not approved for production pricing or funded trading

Last updated: 2026-08-12

## Feature Goal

Build a causal, versioned forecast-research layer that estimates a coherent probability distribution for the resolution-source-reported daily high at a specific station from day-before/first-listing conditions through settlement.

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
high-frequency target-station ASOS ----------------------+--> exact high-so-far
                                                         |
MADIS neighbor observations + model-at-neighbor ---------+--> spatial residual update
                                                         |
GOES cloud/radiation observations + HRRR expectation ----+--> heating-surprise update
                                                         |
                                                         v
                                    coherent latent final-high distribution
                                                         |
                                                         v
                                  resolution-source reported-high distribution
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
| F3 | Information | READY | F0 and F0B settled | Does a peak-passed plus conditional-additional-heating distribution outperform the frozen absolute/bucket baseline? | Coherent remaining-heating model, chronological ablation report, and acceptance or rejection verdict. | — |
| F4 | Information | BLOCKED | F3 settled | Do frozen high-frequency upwind station residuals add information beyond target-station observations and model point features? | MADIS/ASOS spatial residual implementation and controlled ablation. | — |
| F5 | Information | BLOCKED | F3 settled | Does causally observed cloud/radiation surprise add broad or predeclared cloud-regime skill? | GOES heating-surprise implementation and controlled ablation. | — |
| F5A | Information | GATED | F4 or other frozen evidence identifies a coastal residual mechanism | Does local sea, bay, or lake temperature improve the affected coastal or lake-regime forecast after core sources are known? | Local-water-temperature causal dataset and predeclared regime ablation. | — |
| F5B | Information | GATED | Long-history causal corpus spans multiple ENSO events and F0B evaluation is available | Does vintage-correct RONI improve seasonal D-1 calibration after forecast-model information is known? | RONI/ENSO incremental calibration ablation or explicit no-change verdict. | — |
| F6 | Cross-pillar | BLOCKED | At least one forecast version from F2-F5 is accepted | Does one frozen forecast version pass Price Sheet V2 selected, quoted-price, market-relative, and tape-backed research gates at one lifecycle horizon? | Versioned Price Sheet V2 candidate report and pricing-research acceptance or rejection verdict. | — |

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

- `forecast_fixed_support_weather_date_v1`, fingerprint
  `40963e8fb40b481498c112e59be5495168a66dcaa26bb7f189dbc539479f1172`,
  freezes Fahrenheit support at `-20..130`, selects the latest causal snapshot
  at or before 14:00 station-local time, scores one forecast per station/date,
  averages station rows within weather date, and bootstraps whole weather
  dates rather than threshold rows.
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

### Slice 4: Spatial Nowcast

- Freeze neighbor network and dynamic upwind aggregation.
- Collect/model neighbor residuals.
- Run controlled ablation.

### Slice 5: GOES Heating Surprise

- Add causal cloud/radiation extraction.
- Validate cloud-sensitive regimes and broad fallback.

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

### Slice 6: Price Sheet Candidate

- Freeze one accepted forecast version.
- Join it to Price Sheet V2a at one frozen lifecycle horizon without changing signal selection.
- Re-run selected, quoted-price, and market-relative gates before any execution experiment.

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
- [ ] Additional-heating distribution validated.
- [ ] MADIS/upwind residual ablation completed.
- [ ] GOES cloud/radiation ablation completed.
- [ ] One forecast version passes pricing-research acceptance.
- [ ] Price Sheet V2 integration reviewed separately.

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
