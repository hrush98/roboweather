# 2026-07-06 Adverse Selection And Execution Rebuild

## Status

Funded live trading should remain paused. The next phase is not another replay-filter tweak; it is an execution-intelligence rebuild that treats fill/no-fill as part of the signal.

Related phase record: `docs/hypotheses/2026-07-06-execution-first-phase.md`.

## Executive Read

RoboWeather's repeated live losses are consistent with conditional execution failure:

```text
replay EV = E[pnl | selected]
live EV   = P(fill | selected, book state, order tactic) * E[pnl | filled, selected, book state, order tactic]
```

The system has repeatedly optimized the first term and only loosely measured the second. In thin weather books, posted liquidity is not a commitment to be filled. If the quote is stale in our favor, the maker can cancel or move before we arrive. If the quote is stale against us, it remains available and we fill. That is adverse selection.

The immediate implication: faster taker execution may help only when the quote is genuinely stale and reachable. Faster execution against the same bad fill distribution just loses faster. The system needs to decide, per candidate, whether to take, make, wait, or skip.

## Local Evidence

Reports rerun on 2026-07-06:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/whole_chain_truth_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date 2026-06-20

/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/whole_chain_truth_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

### Since 2026-06-20

- US consensus no-tiny: live-selected replay was +$1,644.02 / 3.288 R/R across 10 rows, but filled-at-entry was only +$25.77 / 0.806 R/R across 2 rows. Unfilled selected replay was 4.031 R/R.
- Global low MVP add-on: live-selected replay was -$1,393.52 / -0.453 R/R across 63 rows, and filled-at-actual was -$122.57 / -0.232 R/R across 21 rows. This sleeve is signal-negative before execution.
- US consensus lost capacity was mostly FAK misses, resting TTL expiry, and other rejects, not model selection alone: $19.50 FAK miss, $89.97 TTL expired, $109.57 other rejects.

### All Loaded Live History

- US consensus no-tiny: live-selected replay +$2,521.07 / 1.580 R/R, filled-at-entry +$154.22 / 0.323 R/R, unfilled selected 2.435 R/R.
- Global low canary: live-selected +0.048 R/R, filled-at-entry -0.217 R/R, actual -0.249 R/R.
- Global low MVP add-on: live-selected -0.256 R/R, filled-at-actual -0.013 R/R, actual -0.198 R/R, with historical settlement mismatch drag.
- Legacy dynamic and NGBoost were both negative live-selected and negative filled-at-entry; they should remain retired.

### Microstructure Fields Checked

For all-history US consensus, filled rows had higher average recorded `selected_sweep_fillable_50_usd` than unfilled rows, so raw snapshot depth alone does not explain which rows filled. For the recent June 20+ subset, the 2 filled rows had higher entry price, lower edge, and wider spread than the 8 unfilled rows:

| Group | Rows | Avg entry | Avg edge | Avg spread | Avg ask depth | Avg ask+1c depth | Avg sweep50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Filled | 2 | 0.425 | 0.495 | 0.050 | 3.46 | 10.50 | 30.41 |
| Unfilled | 8 | 0.297 | 0.655 | 0.034 | 9.94 | 12.94 | 22.79 |

This is too small for a rule, but it is exactly the kind of feature pattern a fill-toxicity model should learn.

Initial FAK attempts usually occur about 1-2 seconds after the live position record. That is not enough to declare latency harmless: current `BookSnapshot.timestamp` is local parse time from REST, so `selected_book_age_seconds=0.0` mostly proves the code just fetched the book, not that the exchange quote is stable or young.

## External Microstructure Lessons

1. Limit orders involve an execution-cost versus execution-risk tradeoff. Recent fill-probability work models order books as queues and emphasizes that a limit order may not fill before the opposite quote moves. That is the exact problem here: missed fills are not random missingness. [Lokin and Yu, 2024/2026](https://arxiv.org/abs/2403.02572).

2. Order-book state contains information about short-term price movement. Queue sizes, cancellations, and market-order arrivals affect the probability of a price move conditional on current book state. A single REST snapshot is an inadequate state representation for fill/no-fill. [Cont and de Larrard, 2011](https://arxiv.org/abs/1104.4596).

3. Toxic-flow literature treats each fill as potentially loss-leading for the liquidity provider and recommends predicting toxicity from LOB state and recent activity, not just trader identity. RoboWeather should invert that framing: when our order fills, ask whether that fill predicts future loss for us. [Cartea, Duran-Martin, and Sanchez-Betancourt, 2023](https://arxiv.org/abs/2312.05827).

4. Market-making models do not quote blindly; they optimize price placement against fill intensity, adverse selection, and inventory/risk. If RoboWeather posts passive orders, it becomes a liquidity provider and needs maker-style controls, not just a longer TTL. [Gueant, 2016](https://arxiv.org/abs/1605.01862).

5. Polymarket's current API supports the mechanisms needed for a better system: near real-time market/user WebSocket channels, FAK/FOK/GTC/GTD order types, post-only GTC/GTD, batch orders up to 15, and heartbeat-based open-order cleanup. [WebSocket docs](https://docs.polymarket.com/market-data/websocket/overview), [order docs](https://docs.polymarket.com/trading/orders/create).

6. Polymarket-specific empirical work points in the same direction: quote lifecycle is off-chain, public archives cannot reconstruct address-level placements/cancellations, and order-book direction inference can be unreliable without authoritative fills. RoboWeather must record its own quote lifecycle and user-channel order events. [Dubach, 2026](https://arxiv.org/abs/2604.24366), [Nechepurenko, 2026](https://arxiv.org/abs/2605.11640).

## Why Band-Aid Fixes Failed

The prior changes were mostly local repairs:

- tighter FAK price caps;
- narrow resting ladders;
- replay portfolio ordering;
- station/side calibration gates;
- settlement-source fixes;
- risk caps and sizing changes.

Those were necessary, but they still assumed selected replay rows are representative of fillable live rows. July 6 evidence says they are not. The market decides which of our selected candidates become real positions. That decision is endogenous and informative.

## First-Principles Trading System Design

A weather prediction-market trade has four separate edges:

1. Forecast edge: our probability is better than the market's probability.
2. Timing edge: our probability is updated before the book updates.
3. Execution edge: we can access enough size before the quote disappears or becomes toxic.
4. Portfolio edge: the position adds independent PnL after existing caps and correlations.

The current system has mostly measured 1 and 4. The next system must measure 2 and 3.

### The Correct Unit

The tradable object is:

```text
signal policy + execution tactic + sizing rule + market-state filter
```

Do not evaluate `US consensus` by itself. Evaluate concrete arms such as:

- `US consensus / taker FAK entry+1c / max $10 / only if ask depth age and queue stability pass`
- `US consensus / post-only bid improves best bid by 1c / 180s GTD / cancel on adverse book move`
- `US consensus / no trade when fill-toxicity score is high`

## Answering The Key Questions

### Do We Need To Be Faster?

Yes, but not as the primary fix.

Move from REST-polling to WebSocket-maintained books because weather quotes can cancel between snapshot and submit. Also reduce avoidable submit latency with precomputed order payloads, batch GTC/GTD placement, and fewer sequential child submissions.

But do not simply chase more quotes. If the fill event is negatively correlated with outcome, speed without fill-quality gating increases exposure to toxic fills. Speed should be used to:

- know that a quote is still present;
- measure cancellations and queue movement;
- enter maker queues earlier when the signal is durable;
- cancel passive orders when the book or weather state invalidates them.

### Do We Need Better Mechanisms?

Yes. The current mechanism is a price-anchored taker attempt plus fallback ladder. The next mechanism should be a router:

| Tactic | Use When | Avoid When |
| --- | --- | --- |
| FAK/FOK taker | Quote is deep, stable, spread is tight, edge remains positive after a fill-toxicity haircut | Quote just appeared, spread is wide, top ask is tiny, model edge is mostly extreme calibration artifact |
| Post-only maker | Signal is durable for minutes, best bid can be improved without crossing, fill-toxicity score is acceptable | Outcome information is changing fast or fills historically predict losses |
| GTD ladder | Multiple price levels are justified by calibrated fair and TTL is event-aware | It is only being used to rescue missed taker fills without a queue model |
| Skip | Fill-conditioned EV is negative or unknown | Never treat replay EV alone as approval |

### Do We Need A Different Model?

Not just another weather model. We need two additional models:

1. A calibrated fair-value model that treats market price as a strong prior and penalizes extreme certainty. Fair values like 0.999 against 0.19 asks are suspicious unless uncertainty and bucket-resolution semantics are fully controlled.
2. A fill/toxicity model:

```text
P(fill within TTL | candidate, book event state, tactic)
E[pnl | fill, candidate, book event state, tactic)
P(adverse book move before fill | candidate, book event state, tactic)
```

The target is not "best weather forecast"; it is "best fill-conditioned trade."

## Rebuild Plan

### Phase 0: Instrumentation Before Trading

No funded restart until this exists.

- Persist the full live candidate universe before policy filtering, with stable candidate IDs linked to selected positions, order attempts, fills, user-channel events, and final outcomes.
- Add a WebSocket order-book recorder for traded and candidate token IDs: `book`, `price_change`, `best_bid_ask`, `last_trade_price`, `tick_size_change`.
- Add user-channel order/trade recording so order lifecycle is not inferred only from REST polling.
- Store event timestamps from the feed and local receipt timestamps. Current local parse timestamps are insufficient for book age.
- Store quote lifecycle features at decision time: top-of-book age, top-level add/cancel counts, spread changes, depth decay, recent trades, imbalance, and whether the selected ask was just posted or just depleted.

Exit gate: every candidate can be reconstructed as a timeline from signal creation through book events, order attempts, fills/misses, and settlement.

### Phase 1: Shadow Queue And Toxicity Replay

Build a shadow simulator from recorded WebSocket books:

- simulate whether a FAK at `entry+1c` would have filled at the next event;
- simulate post-only queue placement and whether the quote would have filled before an adverse book move;
- label each selected candidate as filled, missed, toxic fill, benign fill, adverse no-fill, or stale quote;
- compare selected, fillable, filled, unfilled, and skipped rows with the same settlement labels.

Exit gate: a report shows fill-conditioned EV by tactic, not just selected replay EV.

### Phase 2: Execution Router

Implement a small policy router:

```text
if fill_conditioned_ev(taker) > threshold and quote_stability passes:
    submit FAK/FOK
elif fill_conditioned_ev(maker) > threshold and signal_half_life passes:
    submit post-only GTD ladder
else:
    skip
```

Requirements:

- Use WebSocket-maintained books, not only REST snapshots.
- Batch resting ladder children where supported instead of submitting each child sequentially.
- Prefer GTD auto-expiry for passive orders where viable; keep heartbeat/cancel safeguards.
- Cancel passive orders on adverse book moves, weather feature refresh, model fair deterioration, or station/date risk changes.
- Keep FAK and maker arms separate in reporting. Do not pool them.

### Phase 3: Randomized Tiny Live Experiments

Only after shadow labels are working.

- Use tiny, explicit funded arms such as `$2-$5` per order and a strict daily loss cap.
- Randomize among equivalent eligible candidates across tactics to avoid choosing the arm after seeing book behavior.
- Run enough resolved rows to compare `E[pnl | filled]` versus `E[pnl | missed]` per tactic.
- Promote only if actual filled R/R, filled-at-entry replay, and fill-conditioned replay are positive in the current window.

### Phase 4: Sizing

Sizing should be a function of fill-conditioned edge, not raw edge:

```text
target_size =
    bankroll
    * fractional_kelly
    * calibrated_edge
    * fill_quality_score
    * liquidity_capacity
    * regime_confidence
```

Until the fill/toxicity model is stable, use flat tiny canary sizing. Do not size up to compensate for missed fills; that scales the adverse-selection problem unless `E[pnl | filled]` is proven positive.

## Concrete Near-Term Priorities

1. Keep global low MVP stopped. Recent selected replay is negative, so execution work cannot rescue it.
2. Use US consensus no-tiny as the first execution research subject. It has positive selected replay but severe fill-quality degradation.
3. Replace `selected_book_age_seconds` with real exchange/feed age and quote-lifecycle metrics.
4. Build the candidate-event/fill-attribution dataset before another live restart.
5. Add a fill-conditioned promotion report:

```text
selected replay
fillable shadow replay by tactic
actual filled replay at entry
actual filled replay at fill price
unfilled selected replay
actual settled PnL
fill probability
toxicity probability
capacity by tactic
```

6. Add the first router only after the report exists; otherwise another execution tactic becomes another unmeasured band-aid.

## Kill Rules For The Next Phase

- Kill a tactic if `E[pnl | filled]` is negative, even when selected replay is positive.
- Kill a tactic if winner fill rate is materially below loser fill rate after minimum sample.
- Kill a sleeve if recent selected replay is negative before execution effects.
- Kill any passive tactic whose fills occur mostly after adverse book movement.
- Kill any taker tactic that depends on quotes with high cancellation probability.

## Bottom Line

RoboWeather should not restart as a better-filtered version of the same taker bot. It should restart as a measured execution system.

The path is not "find the one good model." The path is:

```text
calibrated weather edge
+ event-driven book state
+ fill/toxicity prediction
+ tactic-specific execution
+ tiny randomized live validation
```

Only after that can replay EV become tradable EV.
