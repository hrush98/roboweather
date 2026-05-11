# Station Expansion Research

Date reviewed: 2026-05-10

## Summary

The current research loop has been evaluating real Polymarket weather markets for stations that were not in the current training dataset. That explains why `KMIA` can fail badly while `KSFO` can still appear strong: both are out-of-training-distribution stations for the existing models, so the current results are not a clean station-specific model test.

The correct next move is to expand the same-day training dataset to the full US station set already in `weather_trader/stations/station_map.csv`, retrain, then rerun policy evaluation by station.

## Current Training Coverage

Current main dataset:

```text
data/raw/dataset_2022-01-01_2025-12-31_initial5.csv
```

Stations in that dataset:

| Station | Rows by year | Days/year coverage |
|---|---:|---:|
| `KATL` | 19,710 / 19,710 / 19,764 / 19,656 | 365 / 365 / 366 / 364 |
| `KBOS` | 19,710 / 19,710 / 19,764 / 19,656 | 365 / 365 / 366 / 364 |
| `KDCA` | 19,710 / 19,710 / 19,764 / 19,656 | 365 / 365 / 366 / 364 |
| `KLGA` | 19,710 / 19,710 / 19,764 / 19,656 | 365 / 365 / 366 / 364 |
| `KORD` | 19,710 / 19,710 / 19,764 / 19,656 | 365 / 365 / 366 / 364 |

Current model feature bundles include `station`:

| Model | Type | Feature includes station? | Train rows |
|---|---|---:|---:|
| `dynamic_bucket_obs_2022_2025.joblib` | dynamic bucket | yes | 295,893 candidate rows |
| `mvp_obs_corrected.joblib` | threshold classifier | yes | 8,370 rows |
| `high_regression_obs_2022_2025.joblib` | high regression residual | yes | 32,877 rows |

But station encoding only helps when the station was present in training. For unseen stations, the sklearn ordinal encoder maps the station category to the unknown value, so the model sees weather features plus an unknown-station bucket rather than a learned `KMIA`, `KSFO`, `KDAL`, `KLAX`, or `KBKF` effect.

## Traded Stations Missing From Training

Active research DB:

```text
data/paper/research_2026-05-08_multimodel.sqlite
```

Stations observed in research-loop markets:

| Station | City | Markets in DB | In current training? | IEM ASOS data sample available? |
|---|---|---:|---:|---:|
| `KATL` | Atlanta | 32 | yes | yes |
| `KBKF` | Denver/Buckley | 11 | no | yes |
| `KDAL` | Dallas Love Field | 15 | no | yes |
| `KLAX` | Los Angeles | 22 | no | yes |
| `KLGA` | New York City | 30 | yes | yes |
| `KMIA` | Miami | 28 | no | yes |
| `KORD` | Chicago | 17 | yes | yes |
| `KSFO` | San Francisco | 23 | no | yes |

Missing trained station tags:

```text
KBKF, KDAL, KLAX, KMIA, KSFO
```

All five had successful two-day IEM ASOS sample pulls for May 1-2, 2025:

| Station | Rows | Non-null tmpf rows |
|---|---:|---:|
| `KBKF` | 66 | 66 |
| `KDAL` | 648 | 74 |
| `KLAX` | 632 | 58 |
| `KMIA` | 622 | 48 |
| `KSFO` | 606 | 58 |

`KBKF` has lower reporting frequency but still enough hourly-ish observations to build same-day features.

## US Expansion Candidates

The station map already contains a reasonable US expansion set:

```text
KLGA, KBOS, KDCA, KORD, KATL, KDFW, KDAL, KDEN, KBKF, KLAX, KSFO, KMIA
```

For the near-term strategy, use `--all-stations` and retrain on this 12-station universe. That covers:

- Current research-loop traded stations.
- Alternate Dallas and Denver stations that may appear in PM markets.
- More climate regimes than the initial eastern/central five.

The code already supports this via:

```bash
python -m weather_trader.cli build-dataset --start 2022-01-01 --end 2025-12-31 --all-stations
```

## Polymarket Market Reality Check

Local DB confirms real PM ladders for:

```text
Atlanta/KATL, Denver/KBKF, Dallas/KDAL, Los Angeles/KLAX,
NYC/KLGA, Miami/KMIA, Chicago/KORD, San Francisco/KSFO
```

Web/API spot checks also confirm PM pages/events for:

- San Francisco, resolving to Wunderground `KSFO`.
- Miami, resolving to Miami Intl Airport.
- Dallas, resolving to Wunderground `KDAL`.
- Denver, resolving to Buckley Space Force Base.
- NYC, resolving to LaGuardia Airport.

Current PM search also showed global daily highest-temperature events for London, Hong Kong, Shanghai, Wellington, and Seoul. These are real markets, but they are a different modeling problem.

## Global Market Feasibility

Global markets exist, and the current IEM ASOS client can fetch sample observations for several non-US ICAO stations:

| Market city | Station/source seen | IEM sample data? | Notes |
|---|---|---:|---|
| London | `EGLC` | yes | PM uses Celsius and Wunderground London City Airport |
| Shanghai | `ZSPD` | yes | PM uses Celsius and Wunderground |
| Wellington | `NZWN` | yes | PM uses Celsius and Wunderground |
| Seoul | `RKSI` | yes | PM uses Celsius and Wunderground/Incheon |
| Hong Kong | Hong Kong Observatory | unknown via station parser | PM source is HKO, not Wunderground station path |

Do not add global markets to the existing live strategy yet. Required changes:

- Parser must support Celsius markets, not only Fahrenheit.
- Station parsing must support non-`Kxxx` ICAO IDs and non-Wunderground sources.
- Station metadata needs global stations, time zones, and coordinates.
- Features should normalize unit internally, probably Fahrenheit or Celsius consistently.
- HRRR cannot support global markets because it is a CONUS model. Global markets need a different forecast source, likely GFS/ECMWF-style point forecasts.

## HRRR Cache Impact

The current HRRR cache is not extensible without more station-specific work.

Cache keys include station:

```text
<station>|<as_of_utc>|lag=<n>|maxfh=<n>|stride=<n>|v2
<station>|<cycle_utc>|fh=<n>|point_v1
```

Current readable `hrrr_features` cache coverage is only the initial five:

| Station | Cached snapshots |
|---|---:|
| `KATL` | 652 |
| `KBOS` | 652 |
| `KDCA` | 651 |
| `KLGA` | 653 |
| `KORD` | 652 |

So if the dataset expands to the 12-station set, the existing long HRRR run does not cover:

```text
KDFW, KDAL, KDEN, KBKF, KLAX, KSFO, KMIA
```

The existing cache should still be reusable for the original five stations, but expanded stations require additional point-row downloads and materialized snapshot rows.

Important cache warning: `data/cache/hrrr_features.sqlite` currently has a WAL file and read attempts showed lock/corruption symptoms, especially around `hrrr_point_rows`. Do not delete or reset it casually. Before continuing a multi-day HRRR job, checkpoint/backup the cache files and verify integrity from a clean stopped state.

## Recommendations

1. Build a 12-station same-day dataset with `--all-stations`.
2. Retrain dynamic bucket, MVP threshold, and regression models on the 12-station dataset.
3. Re-run validation with station metrics and policy backtests split by station.
4. Extend HRRR cache for the 12-station dataset only after backing up the current cache.
5. Keep global PM markets as a separate project track; they need Celsius parsing and non-HRRR forecast features.
6. Do not interpret current `KMIA`/`KSFO` results as trained station performance until models are retrained with those stations present.
