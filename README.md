# roboweather

Simple MVP for same-day U.S. daily-high temperature markets.

Reference docs:

- [Changelog](docs/changelog.md)
- [Modeling and trading decisions](docs/modeling-and-trading-decisions.md)

Scope:

- 5 U.S. stations
- IEM ASOS/METAR observations
- HRRR forecast guidance
- Synthetic threshold training data
- Calibrated classifier
- Polymarket market reader
- Live paper-trading scanner

This project is intentionally narrow. It targets:

`P(final daily high >= threshold | live obs + time + forecast guidance)`

Update [docs/changelog.md](docs/changelog.md) for meaningful data, modeling, or trading changes, and use [docs/modeling-and-trading-decisions.md](docs/modeling-and-trading-decisions.md) as the running technical reference for future discussions.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m weather_trader.cli pull-obs --station KATL --start 2025-05-01 --end 2025-05-03
```

## Implemented commands

Download observations:

```bash
python -m weather_trader.cli pull-obs --station KATL --start 2025-05-01 --end 2025-05-03
```

Build one-station synthetic features:

```bash
python -m weather_trader.cli build-features --station KATL --start 2025-05-01 --end 2025-05-03
```

Build a 5-station dataset:

```bash
python -m weather_trader.cli build-dataset --start 2024-12-01 --end 2025-05-01
```

Train the calibrated classifier:

```bash
python -m weather_trader.cli train-model \
  --dataset data/raw/dataset_2024-12-01_2025-05-01_initial5.csv \
  --output data/models/mvp.joblib \
  --validation-year 2025 \
  --report-dir data/reports/mvp
```

Run training-data diagnostics before trusting a model run:

```bash
python -m weather_trader.cli validate-model-data \
  --dataset data/raw/dataset_2022-01-01_2025-12-31_initial5.csv \
  --kind same-day \
  --validation-year 2025 \
  --report-dir data/reports/data_diagnostics_same_day
```

Train only rows that have historical HRRR features:

```bash
python -m weather_trader.cli train-model \
  --dataset data/processed/dataset_2024-12-01_2025-05-01_initial5_hrrr_sample.csv \
  --output data/models/mvp_hrrr_sample.joblib \
  --validation-year 2025 \
  --report-dir data/reports/mvp_hrrr_sample \
  --require-hrrr
```

Enrich a dataset with historical HRRR archive features:

```bash
python -m weather_trader.cli enrich-hrrr \
  --dataset data/raw/dataset_2024-12-01_2025-05-01_initial5.csv \
  --output data/processed/dataset_2024-12-01_2025-05-01_initial5_hrrr.csv \
  --forecast-stride-hours 3
```

Probe HRRR point guidance:

```bash
python -m weather_trader.cli hrrr-probe --station KATL --as-of 2026-05-05T16:00:00+00:00
```

Run the live scanner:

```bash
python -m weather_trader.cli scan-live --model data/models/mvp.joblib
```

Run the paper-trading engine with station/date-level pick selection:

```bash
./scripts/run_paper.sh
```

The wrapper defaults to one cycle, writes a log under `data/logs/`, and uses
`data/models/mvp_obs_corrected.joblib`. Loop mode defaults to a 360-second
cadence and blocks entries when the latest observation is more than 30 minutes
old. Set environment variables when needed:

```bash
SUBMIT=1 ./scripts/run_paper.sh cycle
SUBMIT=1 MAX_CYCLES=6 ./scripts/run_paper.sh loop
SUBMIT=1 INTERVAL_SECONDS=360 MAX_OBS_AGE_MINUTES=30 ./scripts/run_paper.sh loop
./scripts/run_paper.sh tui
```

Run the headless research collector:

```bash
./scripts/run_research.sh
```

This records prediction snapshots for fresh observation-delay buckets during
the 10:00-15:00 local window, auto-resolves prior station/dates after the next
local morning, and scores snapshots against IEM ASOS final highs. It does not
submit paper trades. Monitor the same database from another terminal or SSH
session:

```bash
./scripts/run_research.sh tui
```

## Current status

Working:

- IEM ASOS historical observation pulls
- Local-day reconstruction and synthetic same-day threshold examples
- Multi-station dataset build
- Chronological train/validation split
- Calibrated `HistGradientBoostingClassifier`
- HRRR point extraction from NOAA NOMADS GRIB subsets
- Polymarket public market scan and simple weather-market parsing
- Polymarket Gamma pagination and CLOB YES-token orderbook fallback
- Historical HRRR archive enrichment from NOAA public S3 byte ranges
- Validation prediction exports and probability/temp bucket loss reports

Verified locally:

- `pytest -q` passes
- HRRR probe returns live NOAA values
- Corrected observation-only model trains on 8,370 train rows and validates on 32,400 rows
- HRRR-enriched sample model trains on 180 train rows and validates on 180 rows
- Live scanner runs and exits cleanly when no active temperature markets are found

Known limitations:

- Full historical HRRR backfill is still slow because each archive message has to be range-fetched and decoded from GRIB. The current checked run is a balanced diagnostic sample, not a full multi-year archive.
- Active temperature markets were not present in the public Polymarket feed during verification on 2026-05-05, so signal generation against real live weather contracts could not be exercised.
- Market parsing is intentionally conservative and will skip ambiguous questions.
