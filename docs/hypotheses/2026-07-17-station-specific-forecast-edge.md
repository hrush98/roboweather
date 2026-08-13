# 2026-07-17 Station-Specific Probabilistic Forecast Edge

## Status

Research implementation approved. No new forecast source is approved for production pricing or funded use.

The full strategic analysis is preserved in `reports/forecast-edge-data-source-strategy-2026-07-17.md`. Approved research architecture and acceptance work live in `docs/implementation/forecast-edge-data-program.md`. Its full-market-lifecycle consumer is specified in `docs/implementation/full-market-lifecycle-trading.md`.

## Hypothesis

RoboWeather can improve the settlement-bucket probability for one station,
market family, and exact decision timestamp by specializing causally available
public weather information to the venue's reporting process. For highs this
combines resolution-compatible high-so-far and remaining heating; for lows it
uses low-so-far and remaining cooling. Localized multi-model guidance,
hierarchically pooled station effects, spatially advected observation
residuals, and observed cloud/radiation surprise may add information beyond
public baselines and the contemporaneous market.

## Expected Mechanism

The current model families already contain rich station METAR and HRRR point features. Additional model variants over the same fields are therefore likely to be correlated rather than independently informative.

The proposed edge comes from information the current stack does not represent well:

1. **Measurement and settlement fidelity.** The physical high, ASOS rolling-average high, Weather Underground daily high, and Polymarket-settled bucket may differ near a boundary.
2. **Probabilistic model structure.** WeatherNext 2 and NBM can provide ensemble distributions rather than one remaining-day maximum.
3. **Spatial nowcasting.** Upwind surface stations can reveal model errors and boundary movement before they reach the settlement station.
4. **Observed heating surprise.** GOES cloud and radiation observations can show whether realized solar heating is diverging from HRRR.
5. **Better target geometry.** Predicting additional heating above the exact high-so-far produces a coherent, non-negative remaining-high distribution.

Public data are not assumed to be alpha merely because they are added. The hypothesis requires incremental skill after the existing HRRR-rich baseline and contemporaneous market probability are known.

The intended advantage may combine predictive information and operational
freshness, but it is not a microsecond-latency thesis. A discrepancy must
survive to a realistic executable checkpoint and retain useful-size net edge.
F6 therefore measures the forecast-to-market edge-decay curve rather than
crediting a price visible only before the system could act.

## Scope

- Evidence cohorts: `US_HIGH`, `GLOBAL_HIGH`, `US_LOW`, and
  `GLOBAL_LOW`; evidence and versions never transfer silently between them.
- Initial accepted control: `US_HIGH` only. Global and low-temperature
  cohorts remain collected but unvalidated until their settlement and model
  gates close.
- Stations/regimes: station-specific probabilities with hierarchical pooling
  across frozen climate/settlement groups. Individual-station promotion
  requires sufficient independent dates and an untouched comparison against
  its pooled parent; arbitrary station subsets and exact-date rules are out of
  scope.
- Side: full integer outcome distribution supporting both `BUY_YES` and `BUY_NO`
- Entry band: not a signal-selection constraint during forecast evaluation
- Local window: D-1 open, D-1 revision, D0 pre-dawn, morning, midday, late, and peak-passed horizons; the current late window remains the initial pricing/execution control
- Model/source: existing METAR/HRRR baseline plus settlement truth, WeatherNext 2, NBM, high-frequency ASOS/MADIS, upwind residuals, and GOES radiation/cloud observations
- Policy/sleeve name: none; this is a forecast-layer hypothesis, not a live sleeve

## Evidence Required

- Target gate: reconcile venue settlement, Weather Underground, official climate/CLI where available, IEM routine METAR maximum, and high-frequency ASOS maximum by station/date.
- Causal-data gate: every model or observation row carries source version, initialization/source time, publication time when available, local receipt time, and valid time.
- Forecast gate: controlled same-row, same-timestamp ablation improves full-ladder ranked probability score or multiclass log loss over the existing HRRR-rich baseline.
- Market gate: the weather-only distribution adds log-score or Brier skill after contemporaneous normalized market probabilities are known; market-aware shrinkage remains a separate pricing step.
- Recent-window requirement: positive incremental skill in both a held-out recent window and all loaded out-of-sample history, with regime failures shown rather than averaged away.
- Minimum resolved sample: evaluate by independent weather date and regional event, not threshold-row count. Research may proceed earlier, but live dependency requires repeated performance across at least 60 resolved weather dates and more than one material regime/season slice.
- Freshness requirement: measure side-aligned forecast-to-executable-market
  edge at quote-ready time and frozen later checkpoints; distinguish market
  absorption from later forecast revisions, and right-censor missing or
  unfillable tape rather than treating it as zero edge.
- Fillability/depth requirement: not required to establish weather-signal skill, but the complete signal + price + quote rule still must pass current execution promotion gates before funded use.
- Live canary requirement: none until Price Sheet V2 consumes a frozen forecast version and the exact integrated trading contract passes shadow and canary gates.

## Current Evidence

- `weather_trader/models/bucket_classifier.py` shows a rich point-feature baseline: current/max/min temperature, trends, enriched METAR fields, and HRRR temperature, humidity, wind, cloud, and shortwave summaries.
- `weather_trader/stations/iem_asos_client.py` currently requests routine/special IEM ASOS report types rather than an explicitly settlement-compatible high-frequency series.
- `weather_trader/features/build_same_day_features.py` and `weather_trader/research/resolver.py` currently define the US high as the maximum observed IEM `tmpf` row.
- F0 audited 230 June 1-23 station-dates across all 10 US stations. Of 220 venue-resolved rows, the current civil-day IEM routine/special METAR maximum landed in the winning bucket 220 times. NWS CLI conflicted on 56/176 comparable rows and NCEI one-minute ASOS on 115/196; interval-aware rendered Weather Underground conflicted on 21/220 and is mutable/localized rather than an immutable exact-F archive. Venue bucket is now authoritative and the IEM report maximum is the versioned numeric proxy, not a claim about physical ASOS high.
- The July 15 research review found the fresh configured portfolio negative and broad recent fairs overconfident. Better prediction and calibration are economically relevant even if execution mechanics improve.
- WeatherNext 2 offers a 64-member 0.25-degree, six-hourly ensemble with historical forecasts from 2022. NBM publishes calibrated probabilistic maximum-temperature guidance. The exact F2 NBM archive transform failed incremental-skill gates; WeatherNext remains unavailable for causal evaluation.

- F0B rejected the existing outcome-centered synthetic-ladder metrics and froze fixed-support, weather-date-clustered scoring with three minimal controls. F3 later found that the v1 hour-bucket selector admitted post-cutoff reports on 4,338/4,364 rows; `forecast_fixed_support_exact_cutoff_weather_date_v2` supersedes only that flawed selector with a timezone-aware exact as-of rule while retaining the outcome-independent support and control roles.
- F1 froze `forecast_source_vintage_v1` and a separate content-addressed runtime catalog. WeatherNext replay requires provider `ingestion_time`; NOAA and IEM replay starts at first successful local observation; HTTP modification time cannot backdate availability. Bounded host captures decoded NBM and HRRR fields and retained one IEM observation response. WeatherNext approved access is not configured and RRFS has no frozen operational contract, so those paths remain explicit rather than substituted. No source has passed incremental-skill evaluation.
- F2 scored `nbm_v5_archive_cycle_plus_2h_v1` on 541 identical D0 station/date rows across 55 weather dates, with complete same-snapshot HRRR-rich and market ladders and 100% NBM coverage. On the untouched 22-date holdout, the 2.09% NBM/HRRR blend improved RPS by `0.01135` but worsened log loss by `0.00340`; the market fitted effectively zero NBM weight. Raw NBM was materially worse than HRRR-rich overall and over the recent 14 dates. This exact transform is rejected. WeatherNext was not scored and remains access-gated.
- F3 accepted `remaining_heating_hurdle_multinomial_exact_cutoff_v3` for Price Sheet V2 research. Its peak-passed hurdle and positive-heating distribution remain coherent above exact high-so-far; a 43.99% remaining-heating / 56.01% conditioned-HRRR ensemble improved untouched 22-date holdout log loss by `0.17879` and RPS by `0.29365`, with both clustered intervals below zero and positive recent results. A separately evaluated market-aware combination improved holdout and recent point estimates in both metrics, although market-relative intervals cross zero. This is forecast-layer evidence, not tradable or funded edge.

## Risks And Failure Modes

- WeatherNext, NBM, HRRR, and market participants may share enough underlying information that another forecast source adds no independent skill.
- Coarse or six-hourly model output may miss airport microclimates and the true diurnal maximum.
- A settlement-source audit may find that historical authoritative labels are unavailable or inconsistently reproduced.
- High-frequency personal/mesonet stations may contain siting and quality biases that overwhelm their spatial value.
- Forecast coverage differences can make a candidate source look better by selecting easier station/dates.
- Reconstructing historical data without true publication/receipt times can leak future information.
- Repeated thresholds and stations can create a large apparent sample from very few independent weather events.
- A more accurate temperature distribution may still fail to create tradable edge after market price, calibration, execution, and capacity.

## Kill Conditions

- Reject a source if controlled identical-coverage ablation does not improve out-of-sample ranked probability score/log loss or a predeclared regime-specific metric.
- Reject a source if its apparent gain disappears after clustering uncertainty by weather date or controlling for source coverage.
- Do not integrate a forecast into Price Sheet V2 if it cannot be reproduced causally from source-vintage metadata.
- Do not treat a weather-only gain as a trading edge if contemporaneous market probability already contains the information.
- Do not promote a forecast version whose venue-aligned outcome mapping is unknown or materially inconsistent.
- Stop expanding model count when new predictions are behaviorally identical or highly correlated with an existing family.

## Gates Added Or Required

- Add a reproducible settlement/sensor truth audit before retraining the US high-temperature stack around a new target.
- Add a source registry and causal forecast-vintage contract.
- Add full-distribution forecast scoring with ranked probability score, multiclass log loss, threshold Brier scores, calibration, and market-relative skill.
- Add identical-coverage ablation and weather-date clustered uncertainty to forecast-source promotion reports.
- Keep `p_weather`, settlement mapping, market-aware calibration, and execution adjustments separately versioned.
- Keep all new forecast sources research-only until the implementation contract's gates pass.

## Review Trigger

Review after the settlement-source audit, after the first WeatherNext/NBM identical-coverage benchmark, and before Price Sheet V2 consumes any new forecast version.

## Decision Log

- 2026-07-17: Created the forecast-edge hypothesis from the data-source strategy review. Prioritized target fidelity, exact high-so-far, multi-model probabilistic guidance, spatial residuals, and observed radiation surprise; explicitly deprioritized power load and pedestrian proxies.
- 2026-07-17: Split the proposed build and acceptance sequence into `docs/implementation/forecast-edge-data-program.md`; no production or funded-trading change was authorized.
- 2026-07-17: Approved research implementation and extended the forecast process from first listing/D-1 through settlement. Each lifecycle horizon still requires separate causal calibration and trading evidence.
- 2026-08-12: Completed F0B baseline/evaluation repair. Froze outcome-independent support, one snapshot per station/date/horizon, weather-date clustered full-distribution scoring, actual-complete-ladder-only market comparison, and three minimal control roles. No forecast passed the market gate and no pricing or funded authority changed.
- 2026-08-12: Completed the F0 target gate with `us_high_temperature_truth_v1`. Venue settlement is authoritative; the IEM routine/special report maximum is the cohort-supported numeric target and live report-stream high-so-far. CLI and one-minute ASOS remain physical/sensor diagnostics, source failures remain explicit, and provenance/venue backfill is required before retraining. No forecast or funded authority changed.
- 2026-08-12: Completed F1 causal source-vintage collection and replay. The separate catalog preserves raw revisions and all availability clocks, bounded NOAA/IEM collectors begin after supported market listing, WeatherNext requires provider ingestion metadata, and RRFS fails closed while unfrozen. Host probes validate NBM/HRRR/IEM plumbing only; F2 must still prove identical-row market-relative skill.
- 2026-08-13: Rejected `nbm_v5_archive_cycle_plus_2h_v1` for F2 after its identical-row D0 holdout failed joint log-loss/RPS and market-relative gates. WeatherNext remains unavailable rather than rejected. A future localized NBM or WeatherNext version must start a new evidence clock; no pricing or funded authority changed.
- 2026-08-13: Accepted F3 for Price Sheet V2 research after superseding the leaky hour-bucket selector with exact local as-of evaluation and replacing zero-mass independent ordinal curves with a coherent multinomial positive-heating component. The frozen weather ensemble improved corrected historical, untouched forward, and recent probability scores versus HRRR-rich; market-relative point estimates also improved but remain statistically uncertain. F6 pricing/tape gates and funded authority remain unchanged.

- 2026-08-13: Expanded the approved thesis to four separately versioned cohorts and froze hierarchical station specialization as the default. F3/F4 remain US-high evidence; global-high and both low-temperature cohorts require their own settlement and forecast gates. F6 must measure executable edge half-life so transient pre-action prices are not credited as information edge. No funded authority changed.
