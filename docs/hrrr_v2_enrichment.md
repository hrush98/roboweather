# HRRR v2 Enrichment

The v2 HRRR workflow separates expensive archive extraction from cheap training
dataset materialization.

## Why v2 Exists

The original cache fetched and stored rows by `station + cycle + forecast_hour`.
That made station expansion expensive because every new station repeated byte-range
downloads for the same HRRR files. The old cache file also showed corruption
symptoms around `hrrr_point_rows`, so v2 writes to a separate cache.

## Cache Build

Build the local point-forecast cache first:

```bash
python -m weather_trader.cli hrrr-v2-cache \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --cache data/cache/hrrr_v2.sqlite \
  --mode build-cache \
  --stations all \
  --workers 4
```

The extraction unit is `cycle_utc + forecast_hour`. For each HRRR file, v2
downloads each configured variable once, decodes it once, samples every selected
station, and writes normalized point rows.

`--stations all` extracts every station in `weather_trader/stations/station_map.csv`
for each HRRR file, even if the input dataset only contains the initial stations.
Use `--stations dataset` to restrict extraction to stations present in the input
CSV, or pass a comma-separated station list.

## Export Enriched Dataset

After the cache is built, materialize features locally:

```bash
python -m weather_trader.cli hrrr-v2-cache \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --cache data/cache/hrrr_v2.sqlite \
  --mode export \
  --output data/processed/dataset_2022_2025_hrrr_v2.csv
```

You can combine both phases with `--mode build-and-export`.

## Status

```bash
python -m weather_trader.cli hrrr-v2-cache \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --cache data/cache/hrrr_v2.sqlite \
  --mode status
```

The status output reports task counts and point rows by station.

