# Full-Market-Lifecycle Trading Hypothesis

Date opened: 2026-07-17

Status: Approved for research and implementation; not approved for production pricing or funded trading

Owner: RoboWeather research and execution program

Related records:

- full report: `reports/full-market-lifecycle-trading-strategy-2026-07-17.md`;
- implementation contract: `docs/implementation/full-market-lifecycle-trading.md`;
- forecast-data program: `docs/implementation/forecast-edge-data-program.md`;
- shared tape: `docs/implementation/phase-3-market-tape-replay.md`;
- pricing contract: `docs/implementation/price-sheet-v2.md`.

## Hypothesis

A station-specific, causally versioned forecast distribution combined with market data from first listing through settlement can identify at least one day-before or early-day quote tactic that adds positive fill-conditioned, settlement-aligned net PnL after the existing late strategy, execution costs, inventory limits, and portfolio capacity are applied.

## Expected Mechanism

Earlier in the market lifecycle:

- displayed and traded capacity may be greater;
- market prices may incorporate forecast revisions gradually;
- passive quotes have more time to fill;
- cross-model disagreement can create compensated risk.

The proposed edge is not mere access to WeatherNext, NBM, HRRR/RRFS, ASOS, MADIS, or GOES. It is the causal integration of source vintages, station localization, venue-aligned truth, horizon-specific calibration, forecast-revision handling, and conservative execution.

## Required Separation

The following are distinct hypotheses and must not be pooled into one favorable aggregate:

1. D-1 opening passive quoting;
2. D-1 post-forecast-update quoting;
3. D0 pre-dawn or early-morning quoting;
4. D0 intraday updating;
5. the existing late control;
6. exit or hold-to-settlement rules for filled inventory.

Each promoted unit is the exact combination of horizon, forecast/pricing version, side-selection rule, quote/update/cancel/exit tactic, inventory cap, and size.

## Current Evidence

- Existing research snapshots show materially more representative displayed `$50` ask depth in station-local morning hours than late afternoon. Since 2026-06-01, mean displayed `$50` fillable notional was approximately `$46.94` at 07, `$39.61` at 12, and `$27.84` at 17.
- Existing early bucket work found probabilistic skill in a dynamic-bucket model, so the early horizon is not obviously devoid of signal.
- The repository contains a next-day classifier scaffold, but its features are primarily current-day observations and recent/climatological highs. It is not a proper D-1 multi-source forecast system.
- The current snapshots mostly cover local hours 07 through 18. There is no adequate evidence yet about actual D-1 traded volume, passive fills, or round-trip exit capacity.

The depth figures are static book diagnostics, not volume or fill evidence. They justify collection and replay, not promotion.

## Evidence Required

### Forecast

- causally timestamped D-1 and D0 forecast source vintages;
- coherent resolution-source-reported-high distributions;
- identical-row comparisons against climatology, public baselines, and current models;
- horizon-specific calibration, log loss, ranked probability score, and threshold reliability;
- ablation of each source family with weather-date clustered uncertainty.

### Market And Execution

- valid tape beginning at first listing and spanning quote termination;
- actual lifecycle traded volume, spreads, displayed depth, and price evolution;
- conservative/base/optimistic fill bounds and comparable missed-order samples;
- markouts around source releases and quote activation;
- exit-versus-hold results with costs and settlement truth;
- useful-size evidence reported separately at `$50` and `$100`.

### Portfolio

- incremental results after the current live-plan order and risk caps;
- reserved versus consumed capacity across horizons;
- station/date, side, bucket, and inventory-duration concentration;
- recent and all-history walk-forward results;
- forward shadow performance for an immutable activated specification.

## Falsification And Kill Conditions

Reject an exact early-horizon tactic if any of the following persists after a predeclared evaluation:

- forecast skill does not beat simple causal baselines on identical held-out rows;
- calibration fails materially in the quoted probability range;
- apparent PnL disappears under conservative dissemination lags;
- actual lifecycle liquidity does not support useful size;
- passive fills have toxic markouts around forecast revisions;
- entry is available but economical exit or hold-to-settlement PnL is not;
- incremental portfolio PnL is non-positive after later sleeves consume or reserve capacity;
- results depend on a small number of station/dates, one regime, or post-hoc horizon choices;
- the tactic exceeds its predeclared inventory duration or loss bound.

Rejecting one tactic does not reject full-lifecycle collection. It rejects only that exact horizon and execution specification.

## Promotion Gates

1. Venue-aligned truth and causal source timestamps pass audit.
2. The horizon-specific forecast passes walk-forward probability and calibration gates.
3. Complete valid tape supports causal entry, repricing, termination, and markout replay.
4. Conservative fill-conditioned settlement PnL is positive with uncertainty bounds.
5. Portfolio replay shows genuine incrementality after shared caps.
6. A frozen forward shadow arm passes its declared window.
7. Minimum-risk funded orders reconcile private order truth with replay.
8. Useful-size evidence authorizes only the tested tactic and size.

## Decision History

- 2026-07-17: Approved full-market-lifecycle collection and research as a cross-cutting program.
- 2026-07-17: Kept the late Price Sheet V2a pilot as the immediate critical path and control arm.
- 2026-07-17: Required horizon-specific strategies and inventory/exit rules; explicitly rejected treating D-1 as the late model run earlier.
- 2026-07-17: Funded trading remains paused.
