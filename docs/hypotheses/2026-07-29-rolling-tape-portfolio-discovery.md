# 2026-07-29 Rolling Tape Portfolio Discovery

## Status

Preliminary positive execution holdout; forward shadow only. Activated for analysis on 2026-07-23 after a 2026-07-22 discovery cutoff. Funded trading remains paused.

## Hypothesis

A portfolio selected from broad, policy-independent prediction snapshots before tape activation can retain positive weather-outcome PnL when its later signals are replayed at realistic quote-ready times against continuously valid shared market tape.

The key claim is not that one predefined strategy was collected prospectively. The snapshot universe and token tape are reusable, so policy families may be extracted later as long as discovery ends before the execution holdout and the frozen portfolio is not retuned on holdout results.

## Frozen Initial Portfolio

Priority order:

1. `pm_high_regression_10m_late`
2. `pm_mvp_late`
3. `pm_dynamic_tuned_10m_late`

Shared constraints:

- US high-temperature markets;
- high-conviction candidates;
- station-local 12:00-15:00 decisions;
- entry range `$0.05-$0.50`;
- first eligible row per station/date;
- one combined position per station/date after priority-order deduplication.

High regression and dynamic tuned require the `10m` observation-delay bucket. Execution is frozen at 250 ms decision latency, 60 seconds of continuous valid pre-signal coverage, a `$0.50` maximum ask, and a `$25` immediate ask-sweep target with partial fills allowed.

## Discovery And Holdout Split

- Discovery evidence: raw `prediction_snapshots` no later than 2026-07-22, evaluated through the rolling snapshot opportunity sweep.
- Holdout: decisions for market dates beginning 2026-07-23.
- Initial resolved evaluation: July 23-28, six market dates.
- Truth: `station_date_outcomes` weather result, not authoritative Polymarket settlement.

This split is a retrospective post-cutoff holdout, not a claim that the exact policy was prospectively registered on July 23. The activation date above prevents later July evidence from redefining the initial portfolio.

## Initial Evidence

| stage | count |
| --- | ---: |
| Raw high-regression selections | 6 |
| Raw MVP selections | 14 |
| Raw dynamic-tuned selections | 13 |
| Priority-deduplicated signals | 19 |
| Executed capped tape sweeps | 12 |
| Rejected: no continuous valid interval | 3 |
| Rejected: no ask at/below cap | 4 |

| sleeve | executions | wins | cost | PnL | R/R |
| --- | ---: | ---: | ---: | ---: | ---: |
| High regression 10m late | 5 | 3 | `$79.52` | `+$19.09` | `+0.240` |
| MVP late | 5 | 2 | `$75.99` | `+$47.85` | `+0.630` |
| Dynamic tuned 10m late | 2 | 1 | `$50.00` | `+$26.27` | `+0.525` |
| Combined | 12 | 6 | `$205.51` | `+$93.22` | `+0.454` |

Average executed VWAP was `$0.423`. All missing executions failed closed rather than falling back to snapshot ask data.

## Falsification And Promotion Gates

Kill or materially revise the hypothesis if any of these occurs:

- additional independent resolved dates make post-cutoff selected or executed R/R negative;
- venue-authoritative settlement reverses the weather-outcome conclusion;
- the positive result depends on one date, station, or sleeve after concentration analysis;
- quote-ready reconstruction frequently fails because valid coverage cannot be sustained;
- filled-subset markouts or a stable real-taker canary show materially toxic selection;
- the result disappears under the exact current portfolio caps, costs, and tested size.

Do not request funded promotion until:

- the portfolio remains positive on a meaningfully larger set of independent dates/regimes;
- Polymarket settlement is linked;
- Price Sheet V2 supplies a calibrated economic maximum price;
- fill-conditioned markouts and current portfolio-cap replay pass;
- controlled real orders validate replay fidelity and intended-size capacity.

## Reproduction

Run pre-cutoff discovery first:

```bash
/home/maxrush/miniconda3/envs/roboweather/bin/python \
  scripts/snapshot_opportunity_sweep.py \
  --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite \
  --end-date 2026-07-22 --market-family HIGH_TEMP \
  --us-high-temp-only --rolling-summary --min-policy-n 20 --top-n 8
```

Then run the frozen holdout:

```bash
/home/maxrush/miniconda3/envs/roboweather/bin/python \
  scripts/tape_strategy_holdout_report.py \
  --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite \
  --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite \
  --discovery-cutoff 2026-07-22 --holdout-start 2026-07-23 \
  --end-date 2026-07-28
```

## Decision Log

- 2026-07-29: Froze the initial three-family priority portfolio and reproduced the July 23-28 tape holdout. Classified the positive result as forward-shadow hypothesis evidence only; no funded or live-policy change was made.
