# Forecast Edge Data Program Implementation Plan

Status: Proposed research implementation; not approved for production pricing or funded trading

Last updated: 2026-07-17

## Feature Goal

Build a causal, versioned forecast-research layer that estimates a coherent probability distribution for the resolution-source-reported daily high at a specific station and decision time.

The economic rationale and falsification criteria live in `docs/hypotheses/2026-07-17-station-specific-forecast-edge.md`. The full source review is preserved in `reports/forecast-edge-data-source-strategy-2026-07-17.md`. Execution phase sequencing remains in `docs/execution-rebuild-roadmap.md`; this proposed work does not change the current Price Sheet V2a critical path.

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

## Workstream 1: Public Probabilistic Baselines

### WeatherNext 2

Retain ensemble members rather than only mean/max summaries. Extract a small grid stencil around every station and derive:

- member local-day temperature traces;
- ensemble threshold probabilities, quantiles, spread, skew, and upper tail;
- spatial gradients and elevation-aware interpolation candidates;
- forecast-cycle revisions;
- WeatherNext-versus-HRRR disagreement.

Because WeatherNext is six-hourly and coarse relative to an airport maximum, learn a causal localization/diurnal correction rather than treating its nearest-grid maximum as truth.

### NBM

Collect deterministic, standard-deviation, percentile, and available maximum-temperature probability products. Record operational version changes and use NBM as both a candidate input and a public probabilistic benchmark.

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

## Proposed Implementation Slices

### Slice 0: Truth Audit

- Build read-only outcome comparison and mismatch report.
- Decide canonical target and high-so-far semantics.
- Add tests for timezone, rolling maximum, rounding, and bucket mapping.

### Slice 1: Forecast Source Catalog

- Add source-vintage contracts and separate runtime catalog/cache.
- Implement bounded forward collectors for NBM/GLMP/RRFS metadata and selected fields.
- Establish raw retention, version-change, and failure telemetry.

### Slice 2: WeatherNext/NBM Benchmark

- Backfill WeatherNext historical members for the scoped stations/fields.
- Materialize causal station distributions.
- Run identical-coverage baseline and market-relative reports.

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

### Slice 6: Price Sheet Candidate

- Freeze one accepted forecast version.
- Join it to Price Sheet V2a without changing signal selection.
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

- [ ] Settlement/sensor truth audit implemented and run.
- [ ] US station target-source mismatches quantified.
- [ ] Canonical target/high-so-far semantics decided.
- [ ] Source-vintage contract implemented.
- [ ] Separate runtime catalog/cache selected and tested.
- [ ] WeatherNext historical station distributions materialized.
- [ ] NBM probabilistic baseline collected and scored.
- [ ] Identical-coverage and market-relative report implemented.
- [ ] Additional-heating distribution validated.
- [ ] MADIS/upwind residual ablation completed.
- [ ] GOES cloud/radiation ablation completed.
- [ ] One forecast version passes pricing-research acceptance.
- [ ] Price Sheet V2 integration reviewed separately.
