# RoboWeather

Research and live-execution system for weather prediction markets: causal
data, conservative pricing, and fail-closed evals that treat a working stack
as substrate, not as proof of edge.

The domain is same-day temperature contracts on Polymarket. Public weather
models, live observations, and a liquid market all exist, so a naive backtest
is easy and usually wrong. The hard problem is not "can I train a temperature
model." It is that weather labels, venue settlement, displayed book, and
actual fills are different scores, and mixing them invents edge.

## Status

Funded trading is **paused**. Collectors, models, tape replay, and a CLOB
execution engine exist and are tested. No exact candidate has passed the full
conservative gate (venue-authoritative settlement, valid markouts, fill-conditioned
economics at useful size). Negative results are kept: models that beat weather
baselines but lose to the market are rejected, not promoted.

This is not an LLM agent, not a general weather model, and not a claim of
tradable edge.

## What is in the repo

```text
observations + causal forecast vintages
  -> station/bucket probabilities at an exact decision time
  -> conservative quoteable price
  -> join to causal L2 tape (or reject the window)
  -> paper / dry-run / live CLOB adapter
  -> settlement-aware scoring
```

| Layer | Role |
| --- | --- |
| Forecast / observations | IEM ASOS, HRRR, NBM, GOES, and related sources with explicit availability clocks. Replay may not use a file before it was actually observable. |
| Models and calibration | Bucket classifiers and walk-forward calibration. Raw model probability is not a quote. |
| Research loop | Continuous prediction snapshots scored against official highs. Policy tables are derived views, not the live order queue. |
| Market tape | Policy-independent L2 recorder. Decision joins fail closed on coverage gaps; missing tape is never bridged. |
| Discovery | One deterministic command over a cached executable-decision identity, chronological folds, and an untouched holdout. |
| Live engine | Separate process and SQLite ledger from research. Kill switch, caps, idempotent orders. Default is dry-run. |

Runtime databases, tape partitions, model artifacts, and generated reports
are intentionally not committed.

## Documentation

| Question | Document |
| --- | --- |
| Current financial and systems verdict | [docs/current-trading-system-audit.md](docs/current-trading-system-audit.md) |
| What is funded or paused | [docs/live-trading-journal.md](docs/live-trading-journal.md) |
| Architecture and research data model | [docs/project-overview.md](docs/project-overview.md) |
| Phase sequence and exit gates | [docs/execution-rebuild-roadmap.md](docs/execution-rebuild-roadmap.md) |
| What changed | [docs/changelog.md](docs/changelog.md) |

`docs/implementation/` holds active contracts (tape replay, discovery, pricing,
forecast-edge). `docs/hypotheses/` holds economic ideas separately from those
contracts. Generated tables live under `reports/` and are not canonical
conclusions.

## Setup

Python 3.11, conda-forge. The repo expects the `roboweather` environment:

```bash
conda env create -f environment.yml
conda activate roboweather
pip install -e '.[dev]'
pytest -q
```

`h5py` is required for GOES tests. Live CLOB submit extras:

```bash
pip install -e '.[dev,live]'
```

A few focused commands, if you want to poke the original data path:

```bash
python -m weather_trader.cli pull-obs --station KATL --start 2025-05-01 --end 2025-05-03
python -m weather_trader.cli hrrr-probe --station KATL --as-of 2026-05-05T16:00:00+00:00
```

Continuous research collection, market-tape recording, and live execution are
separate user systemd units under `deploy/systemd/`. They write private runtime
state (SQLite, raw tape, logs). Do not point them at this checkout's git tree.
Operator workflow for those services lives in `AGENTS.md`.

## License

Private research code unless a license file is added. No strategy in this
repository is authorized for funded trading.
