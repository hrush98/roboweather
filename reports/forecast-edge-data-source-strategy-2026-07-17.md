# Forecast Edge Data-Source Strategy

Date: 2026-07-17

Status: Standalone strategic analysis. Durable conclusions are integrated into `docs/current-trading-system-audit.md`, the economic hypothesis is tracked in `docs/hypotheses/2026-07-17-station-specific-forecast-edge.md`, and the proposed build contract is in `docs/implementation/forecast-edge-data-program.md`.

## Executive Verdict

RoboWeather should run a forecasting-edge program alongside the execution rebuild. The largest likely gains are not exotic human proxies such as foot traffic. They are more likely to come from three places:

1. Predicting the exact settlement measurement correctly.
2. Building a coherent, localized ensemble from genuinely different forecast sources.
3. Updating that ensemble with spatial observations and observed solar/cloud surprises faster than the models can.

WeatherNext 2 belongs in the second category, but for the late same-day trades RoboWeather currently favors, it probably is not the first priority.

One caution: RoboWeather may end up with excellent execution infrastructure, but positive execution economics remain unproven. The July audit found that the fresh configured portfolio was negative and that recent raw model fairs were overconfident. Forecast discrimination and probability calibration therefore are not optional extras; they determine whether the execution engine is quoting around a real fair value.

## What RoboWeather Already Has

The current feature set is materially richer than it was a month ago. It includes:

- current, maximum, and minimum observed temperature;
- one- and three-hour temperature trends;
- dewpoint, humidity, wet bulb, pressure trend, visibility, precipitation, and clouds;
- HRRR remaining maximum/minimum;
- HRRR future temperature trend, dewpoint, humidity, wind, gusts, cloud cover, and shortwave radiation.

Those are visible in `weather_trader/models/bucket_classifier.py`.

The remaining gap is not simply more standard weather columns. The important missing dimensions are:

- spatial context around the station;
- observed rather than forecast cloud/radiation evolution;
- ensemble uncertainty rather than one deterministic HRRR path;
- forecast-cycle revisions;
- exact settlement-source behavior;
- a coherent distribution for final temperature rather than several loosely related model probabilities.

## Priority Zero: Predict The Right Number

This is more important than WeatherNext.

The historical feature builder currently defines the final high as the maximum `tmpf` appearing in the IEM routine/special METAR feed. The research resolver does the same thing. The active US high-temperature markets in the research database, however, identify Weather Underground daily history pages as their resolution sources, while official ASOS highs have their own measurement process.

NOAA explains that ASOS samples approximately every ten seconds, produces one-minute values, and updates the official maximum from a rolling five-minute average. Consequently, the daily climate high can differ from the maximum routine METAR, five-minute report, or raw one-minute observation. See the [NWS ASOS temperature explanation](https://www.weather.gov/lot/weather_observations_faq).

This does not prove the current labels are wrong. Weather Underground may use METAR-derived values that often match IEM. It means RoboWeather has not yet demonstrated that they are right.

The true chain is:

```text
physical temperature
    -> ASOS sensor and averaging
    -> reported data product
    -> Weather Underground daily value
    -> Polymarket settlement
```

RoboWeather needs a settlement-measurement layer separate from the meteorological model.

The first forecasting project should compare, for every US station over the overlapping market history:

- venue-settled winning bucket;
- Weather Underground displayed daily high;
- official NWS/CLI high where available;
- IEM routine-METAR maximum;
- five-minute ASOS maximum;
- reconstructed rolling-five-minute maximum if the necessary data are available.

The output should be a station-level confusion table: how frequently each source differs by 1 degree Fahrenheit or more, and how often that difference changes the winning bucket. That may reveal more usable edge than a sophisticated new atmospheric model.

It would also determine what `high-so-far` should mean in live trading. The current state is the maximum reported METAR, not necessarily the maximum that the resolution source will eventually report.

## Proposed Forecast Stack

Rather than one giant classifier, build a layered distribution:

```text
multi-model prior for latent daily high
                 |
                 v
station-specific localization and bias correction
                 |
                 v
live update from station, upwind network, cloud, and radiation
                 |
                 v
mapping from latent high to resolution-source reported high
                 |
                 v
coherent probability over every integer temperature
                 |
                 v
market buckets and conservative trade fair
```

This preserves an important separation:

- `p_weather`: independent meteorological probability;
- `p_settlement`: probability after station/reporting behavior;
- `p_trade`: conservative, calibrated probability after market-aware shrinkage.

Execution should consume `p_trade`, while forecast research measures whether `p_weather` actually beats the market.

## Data-Source Priorities

| Priority | Source | What new information it adds | Likely value |
| ---: | --- | --- | --- |
| 1 | High-frequency ASOS and authoritative settlement data | Exact high-so-far and correct labels | Very high |
| 2 | WeatherNext 2 plus NBM probabilistic MaxT | Ensemble uncertainty and alternative model view | High |
| 3 | MADIS/upwind surface network | Spatial error propagation and boundary timing | High |
| 4 | GOES observed clouds and surface shortwave | Actual heating surprise versus HRRR | High, regime-dependent |
| 5 | NWS LAMP/GLMP and RRFS/REFS | Rapidly updated local guidance and regional ensemble | Medium-high |
| 6 | Coastal water temperature, snow, smoke, and soil moisture | Specific physical regimes | Medium, conditional |
| 7 | AIFS/IFS ensembles and aircraft profiles | More model diversity and vertical structure | Medium, greater effort |
| 8 | Power load, traffic, and pedestrian activity | Downstream behavioral proxies | Low |

### High-Frequency ASOS And MADIS

MADIS is more interesting than simply adding more hourly METAR fields. It includes high-frequency ASOS and thousands of quality-controlled mesonet stations, with surface files processed continuously. See [NOAA MADIS](https://madis.ncep.noaa.gov/).

The most valuable feature may be a model-relative, dynamically upwind residual:

```text
neighbor residual = observed neighbor temperature
                    - model forecast at that neighbor and time
```

Weight neighboring residuals by:

- wind direction;
- distance and expected advection time;
- elevation difference;
- whether they lie in the same air mass;
- historical usefulness for the target station.

If several stations in the air mass approaching KORD are all 1.5 degrees warmer than HRRR, that is new information. It is much stronger than knowing only that KORD itself has recently warmed.

This also captures fronts and sea-breeze boundaries:

- temperature and dewpoint gradients;
- wind-direction discontinuities;
- boundary velocity;
- coastal/inland divergence;
- regional model error propagating toward the station.

Start with engineered upwind aggregates, not a graph neural network. Consider a graph model only after the basic spatial residual demonstrates out-of-sample value.

### WeatherNext 2

WeatherNext 2 is worth testing because it supplies 64 ensemble members, four daily initializations, a historical archive from 2022, and forecasts through 15 days. Its public output is 0.25 degrees and six-hourly. See the [WeatherNext 2 dataset](https://developers.google.com/earth-engine/datasets/catalog/projects_gcp-public-data-weathernext_assets_weathernext_2_0_0).

That presents two problems for this target:

- an airport is a point with local terrain, coastline, and urban effects;
- a six-hour forecast sequence may miss the true afternoon maximum between forecast times.

Do not use the nearest-grid WeatherNext maximum directly. Retain all members and extract a small grid stencil around the station. Useful derived information includes:

- ensemble mean, median, quantiles, and spread;
- member probability of each temperature threshold;
- skew and upper-tail behavior;
- spatial temperature gradients around the station;
- cycle-to-cycle revisions;
- WeatherNext-versus-HRRR disagreement;
- WeatherNext anomaly relative to its station/day-of-year bias.

A sensible hybrid is:

```text
WeatherNext ensemble -> broad anomaly and uncertainty
HRRR hourly curve    -> intraday timing and local shape
live observations    -> current residual correction
```

WeatherNext's historical availability makes it a better first AI-ensemble experiment than ECMWF AIFS ENS, whose operational ensemble history only begins in July 2025 and whose current v2 began in May 2026. See [ECMWF AIFS data](https://www.ecmwf.int/en/forecasts/datasets/aifs-machine-learning-data).

### National Blend Of Models

NBM may be the strongest public benchmark currently missing from RoboWeather. It already blends model and post-processed guidance and publishes probabilistic daily maximum-temperature products, including percentiles and exceedance probabilities. See the [NBM product definitions](https://vlab.noaa.gov/documents/26605319/30417290/Description%2Bof%2BField-Selected%2BAlgorithms%2Bfor%2BNational%2BBlend%2Bof%2BModels%2B%28NBM%29%2B.pdf/b93e4661-369e-66ec-a60c-8fbffc674723?t=1692378911440).

NBM serves two purposes:

- a forecast input;
- a minimum viable public baseline that a custom model should beat.

If an elaborate HRRR/WeatherNext model cannot beat NBM MaxT probabilities on the same station-days and timestamps, it is not demonstrated new edge. NBM data are publicly downloadable in GRIB2 and station text products. See [NBM downloads](https://vlab.noaa.gov/web/mdl/nbm-download).

The complication is model-version drift: NBM v5 was introduced in April 2026. Version and availability timestamps must be stored explicitly.

### Observed Radiation And Cloud Evolution

The current model has HRRR forecast cloud cover and shortwave radiation. What it lacks is the corresponding observed surprise:

```text
radiation surprise = observed downward shortwave
                     - HRRR expected downward shortwave
```

Calculate this over the previous 15, 30, 60, and 120 minutes, plus cumulative radiation deficit since sunrise.

GOES products include cloud masks, cloud optical properties, derived motion winds, and surface downward-shortwave algorithms. See the [NOAA GOES product algorithms](https://www.star.nesdis.noaa.gov/goesr/documentation_ATBDs.php).

Useful derived signals include:

- whether cloud cover is thicker or thinner than HRRR predicted;
- clearing time and recent clear-sky duration;
- upwind cloud motion;
- cloud optical depth, not just METAR's categorical cloud code;
- cumulative observed solar energy;
- surface-temperature response after clearing;
- smoke or aerosol attenuation on otherwise clear days.

This should be especially valuable when a bucket depends on one more degree of heating. A model may predict the synoptic temperature correctly while missing two hours of unexpected cloud, which is enough to miss the daily high.

### LAMP/GLMP And RRFS/REFS

NWS Gridded LAMP provides hourly, approximately 2.5-km temperature analyses and short-range forecast guidance. That is close to purpose-built as an intraday nowcast baseline. See [NWS Gridded LAMP](https://vlab.noaa.gov/web/mdl/gridded-lamp).

RRFS/REFS is also worth beginning to collect prospectively. NOAA describes RRFS as its new rapidly updating regional system, with an ensemble that includes multiple members and HRRR; its 2026 transition is replacing NAM/HREF/SREF-related products while HRRR remains operational. See [NOAA RRFS](https://gsl.noaa.gov/rrfs/).

Because RRFS is new and changing, it should initially be a frozen forward challenger, not something immediately promoted or fitted aggressively.

## Better Target Formulation: Additional Heating

For a decision made at time `t`, define:

```text
H(t) = exact settlement-compatible high-so-far
Delta(t) = final reported high - H(t)
```

By construction, `Delta(t) >= 0`. Then model:

1. `P(Delta = 0)`: probability the daily peak has already occurred.
2. `P(Delta = k | Delta > 0)`: distribution of additional heating.

This formulation naturally changes with time:

- early morning: broad remaining-heating distribution;
- noon: cloud, radiation, and warming rate dominate;
- late afternoon: peak-already-occurred probability dominates;
- after a wind shift or front: the distribution may collapse onto the current high.

It should be easier to calibrate than predicting absolute daily highs across every station and season. The final output remains a coherent probability distribution over integer highs, from which every Polymarket bucket probability can be calculated.

Favor a hierarchical residual or ensemble model-output-statistics approach before deep learning:

- partial pooling across stations;
- station-specific biases;
- separate horizon/local-time effects;
- regime interactions for coastal, humid, cloudy, and frontal days;
- WeatherNext/NBM/HRRR ensemble statistics;
- live spatial and radiation residuals.

The independent sample size is weather dates, not hundreds of thousands of threshold rows. A large neural model could manufacture confidence without genuine independent information.

## Slowly Varying And Specialty Data

These are useful after the core stack:

- sea-surface, bay, and lake temperature for coastal stations;
- recent MRMS precipitation and soil wetness for evaporative cooling;
- snow cover for winter daytime highs;
- smoke/aerosol optical depth for solar attenuation;
- vegetation and seasonal surface state;
- aircraft ascent/descent profiles for boundary-layer temperature and inversion structure.

MADIS aircraft data can contain airport profiles of wind and temperature, but much real-time aircraft data is access-restricted; older data becomes public. See [MADIS aircraft observations](https://madis.ncep.noaa.gov/madis_acars.shtml).

SMAP soil moisture is physically meaningful, but the near-real-time product is coarse and satellite revisits are much slower than the intraday decision cycle. It is better as a regime/background variable than a fast signal. See [NASA SMAP near-real-time soil moisture](https://data.nasa.gov/dataset/near-real-time-smap-l2-radiometer-half-orbit-36-km-ease-grid-soil-moisture-v107).

## Power Load And Human Proxies

Power load belongs near the bottom of the priority list.

Load responds to temperature, humidity, calendar, industrial activity, rooftop solar, and human behavior. Most of its apparent temperature information will already be visible more directly in weather observations. It is spatially aggregated and often delayed or revised.

The one human-infrastructure proxy that is somewhat interesting is real-time solar generation:

- unexpected solar production can proxy actual regional irradiance;
- it might validate a GOES radiation surprise;
- it must be corrected for curtailment, installed capacity, and station distance.

If GOES shortwave and local solar sensors are available directly, generation is a noisier substitute. Pedestrian and traffic data are even less attractive because they add privacy, availability, and confounding problems while remaining downstream of weather.

## Proving Incremental Forecast Edge

Every new source should face the same controlled ablation:

```text
existing HRRR-rich baseline
+ candidate source
on identical station/date/timestamp rows
```

Evaluate:

- multiclass log loss over the full bucket ladder;
- ranked probability score or CRPS, because temperature buckets are ordered;
- threshold Brier scores;
- reliability and sharpness;
- settlement-winning-bucket accuracy;
- improvement over normalized market-implied probabilities;
- hypothetical value at contemporaneous executable prices, reported separately from execution.

Confidence intervals should be clustered by weather date and regional event. Ten stations affected by the same front are not ten independent observations.

Strict forecast vintages are required:

- model version;
- initialization time;
- publication time;
- local receipt time;
- valid time;
- raw members or fields used.

Without those fields, historical research can accidentally use data that was not available at the simulated decision time.

## Recommended Parallel Program

Keep Price Sheet V2 and market-tape work moving while starting a separate Forecast Edge track:

1. **Settlement and sensor audit.** Establish whether current labels and high-so-far match Weather Underground and venue outcomes.
2. **Probabilistic public baselines.** Backtest WeatherNext 2 and NBM on identical station/date/timestamp rows.
3. **Start forward collection.** High-frequency ASOS/MADIS, NBM, GLMP, and RRFS with source-vintage metadata.
4. **Build the additional-heating distribution.** Use the exact current high as the lower bound and predict peak occurrence plus remaining degrees.
5. **Add spatial residuals.** Dynamically upwind stations and regional model-error propagation.
6. **Add GOES radiation/cloud surprise.**
7. **Only then investigate niche sources.** Soil moisture, water temperature, smoke, aircraft profiles, or solar generation.

The strongest recommendation is:

> WeatherNext 2 is worth adding, but the highest expected-value prediction work is first fixing target fidelity and exact high-so-far, then combining WeatherNext/NBM/HRRR with upwind observations and observed radiation surprise.

That would give RoboWeather something more defensible than another weather model: a station-specific, continuously updated distribution of what the resolution source will actually report.
