# 2026-07-06 Execution-First Phase

## Status

Live trading paused. Research and shadow execution only until fill-conditioned gates pass.

## Hypothesis

The current system is not failing primarily because of one bad forecast model or one bad price-slippage bug. It is failing because replay measures selected signal EV, while live PnL is determined by conditional execution: which orders actually fill, which stale quotes disappear, and whether the filled subset is worse than the missed subset.

## Expected Mechanism

In thin Polymarket weather books, market makers and other liquidity providers can cancel stale offers when the bot has real edge and leave offers available when the bot is stale, wrong, or overconfident. A replay that treats recorded ask depth as deterministically executable will overstate live EV whenever fill probability is negatively correlated with eventual outcome.

The next phase should therefore optimize the combined `signal policy + execution policy + sizing policy`, not standalone forecast replay.

## Scope

- Market family: all live weather markets
- Stations/regimes: all live and candidate stations
- Side: both YES and NO
- Entry band: all live entry bands
- Local window: all live windows
- Model/source: all live and candidate model families
- Policy/sleeve name: all funded sleeves

## Evidence Required

- Replay gate: raw-snapshot replay remains useful only as hypothesis generation.
- Recent-window requirement: live or shadow evidence must include a recent resolved window; broad all-history replay is insufficient.
- Minimum resolved sample: no normal live sizing without a filled-subset sample large enough to compare to unfilled selected candidates.
- Fillability/depth requirement: promotion must include actual or shadow fill outcomes, not just recorded ask-sweep depth.
- Live canary requirement: funded canaries must have explicit daily loss caps, tiny size, and predeclared kill conditions.

## Current Evidence

Analysis run on 2026-07-06 using:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/whole_chain_truth_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date 2026-06-20
```

Since 2026-06-20:

- All live-selected rows: 82 resolved selected rows, $3,878.50 intended risk, +0.455 selected replay R/R.
- Actual filled rows: 24 filled rows, $578.22 cost, -0.138 actual R/R.
- Filled-at-entry replay: -0.150 R/R.
- Unfilled selected replay: +0.624 R/R.
- US consensus selected replay: +3.288 R/R, but filled-at-entry replay was +0.806 R/R and unfilled selected replay was +3.445 R/R.
- Global low MVP add-on selected replay: -0.453 R/R and actual R/R -0.232, so that sleeve degraded at the signal layer as well as execution layer.

All loaded live history:

- All selected live rows: 395 resolved selected rows, $13,677.00 intended risk, +0.210 selected replay R/R.
- Actual filled rows: $4,742.79 cost, -0.148 actual R/R.
- Filled-at-entry replay: -0.061 R/R.
- Unfilled selected replay: +0.324 R/R.
- US consensus winner fill rate was 17.8% versus loser fill rate 52.4%, a direct adverse-selection signature.
- Global low consensus winner fill rate was 21.6% versus loser fill rate 43.0%.

This means the gap is not mostly purchase price. Since 2026-06-20, average actual fill price was slightly better than recorded decision entry overall, but the filled subset still underperformed.

## Risks And Failure Modes

- Raw snapshot depth is treated as executable even though live orders face cancellations, FAK misses, TTL expirations, balance rejects, and partial fills.
- Existing replay allocates deterministic notional from recorded sweep fields and does not model fill probability or adverse selection.
- Polling cadence, book age tolerance, weather feature latency, and retry/resting TTL can make live execution materially different from replay.
- Candidate promotion can still overfit by selecting high replay R/R rows that never fill.
- Settlement mismatches can still contaminate global/international results if replay labels diverge from Polymarket settlement.
- Adding faster execution without fill-conditioned evidence may simply fill losing quotes faster.

## Kill Conditions

- Stop or keep paused any funded sleeve whose filled-at-entry R/R is below zero in the recent resolved window.
- Stop or keep paused any funded sleeve where filled-subset replay materially underperforms unfilled selected replay without a credible execution explanation.
- Stop or keep paused any sleeve whose recent raw replay turns negative, even before live execution effects.
- Do not size up a sleeve unless actual filled R/R and filled-at-entry replay are both positive and settlement-aligned.

## Gates Added Or Required

- Required: full live candidate persistence before policy filtering, with stable IDs linking candidates, selected positions, order attempts, and final outcomes.
- Required: fill-conditioned promotion report comparing selected, filled, unfilled, fill prices, and actual settlement.
- Required: adverse-selection alert when filled-subset replay R/R is materially below unfilled selected replay R/R.
- Required: replay downgrade factor or fill-probability model before treating recorded snapshot depth as live capacity.
- Required: balance/allowance throttle so insufficient balance does not continue generating exchange rejects.
- Required: funded live trading remains paused until the above gates exist and pass on current candidates.

## Review Trigger

Review after the execution-first gates are implemented and a shadow/live-candidate dataset has at least 20 resolved filled-or-fillable decisions for a candidate sleeve.

## Decision Log

- 2026-07-06: Created after whole-chain analysis showed persistent replay-to-live divergence, adverse fill selection, and recent global low MVP signal degradation. Funded live trading should remain paused while the system moves to an execution-first phase.
