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

The immediate implication: faster taker execution may help only when the quote is genuinely stale and reachable. Faster execution against the same bad fill distribution just loses faster. The primary next-phase build should therefore be a conservative price-maker / quoting system, not a broader grid of taker and resting variants. The model edge should set our price; the market should have to trade with us there.

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

The tradable object is a priced quote:

```text
forecast distribution
+ calibrated fair price
+ quote rule
+ cancellation rule
+ sizing rule
+ market-state filter
```

Do not evaluate `US consensus` by itself. Evaluate concrete price-making mechanisms such as:

- `US consensus / calibrated NO fair / bid fair minus 12c / GTD 120s / cancel on fair deterioration`
- `US consensus / calibrated NO fair / bid best bid plus 1c only when still at least 10c below fair / cancel on adverse book move`
- `US consensus / skip when no quote price preserves the required edge after uncertainty and toxicity haircuts`

Taker FAK can remain as a diagnostic or rare opportunistic arm, but it should not be the primary path. The phase-shift hypothesis is that RoboWeather must stop chasing visible asks and instead test whether its model can set prices that are occasionally accepted with positive filled-subset EV.

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

Yes. The current mechanism is a price-anchored taker attempt plus fallback ladder. That still starts from the market's visible ask and lets the market decide which stale quotes remain available. The next mechanism should start from our calibrated fair value and quote a conservative bid we are happy to own if filled.

| Component | Job | Non-goal |
| --- | --- | --- |
| Calibrated fair engine | Convert model distribution into a conservative buy price with uncertainty and toxicity haircuts | Do not output 0.999-style certainty as a trading price without strong calibration evidence |
| Post-only quote engine | Place bids/offers at our price, never crossing just because the ask looks cheap | Do not use passive orders as retry logic after failed FAK |
| Cancellation engine | Pull quotes when fair value, book state, weather state, or risk state changes | Do not rely only on a fixed TTL |
| Fill-quality report | Decide whether fills at our prices are profitable | Do not promote from selected replay alone |
| Taker control arm | Measure whether rare stable visible liquidity is worth taking | Do not let taker fills drive the main system unless filled-subset EV proves it |

### Do We Need A Different Model?

Not just another weather model. We need two additional models:

1. A calibrated fair-value model that treats market price as a strong prior and penalizes extreme certainty. Fair values like 0.999 against 0.19 asks are suspicious unless uncertainty and bucket-resolution semantics are fully controlled. The output must be a quoteable price, not only a trade/no-trade edge.
2. A fill/toxicity model:

```text
P(fill within TTL | candidate, book event state, tactic)
E[pnl | fill, candidate, book event state, tactic)
P(adverse book move before fill | candidate, book event state, tactic)
```

The target is not "best weather forecast"; it is "best fill-conditioned trade."

## Rebuild Plan

### Phase 0: Instrumentation And Data Integrity

No funded restart until this exists.

Implementation status, 2026-07-06: source support is in place for durable live candidate rows, stable `live_candidate_id` links, normalized CLOB feed event persistence, local receipt timestamps, and decision-time quote lifecycle feature payloads. Funded trading remains paused until a live recorder process is actually run against current candidates and the resulting timelines pass review.

- Persist the full live candidate universe before policy filtering, with stable candidate IDs linked to selected positions, order attempts, fills, user-channel events, and final outcomes.
- Add a WebSocket order-book recorder for traded and candidate token IDs: `book`, `price_change`, `best_bid_ask`, `last_trade_price`, `tick_size_change`.
- Add user-channel order/trade recording so order lifecycle is not inferred only from REST polling.
- Store event timestamps from the feed and local receipt timestamps. Current local parse timestamps are insufficient for book age.
- Store quote lifecycle features at decision time: top-of-book age, top-level add/cancel counts, spread changes, depth decay, recent trades, imbalance, and whether the selected ask was just posted or just depleted.

Exit gate: every candidate can be reconstructed as a timeline from signal creation through book events, order attempts, fills/misses, and settlement.

### Phase 1: Calibrated Price-Maker Model

Build the price sheet that the quoting engine will use. This is the real model handoff: the forecast model no longer says "take this ask"; it says "we are willing to bid up to this price after haircuts."

Required output per candidate/bucket/side:

```text
raw_model_fair
calibrated_fair
market_mid_or_reference
uncertainty_haircut
adverse_selection_haircut
min_required_edge
max_quote_price
quote_size_cap
fair_valid_until
cancel_triggers
```

Initial scope:

- US high-temperature consensus no-tiny only.
- BUY side only, preferably BUY_NO first unless the evidence says BUY_YES is cleaner.
- No global low restart in this phase.
- Conservative probability caps and minimum edge after all haircuts.

Exit gate: historical and current-window replay show that the generated quote prices would have positive theoretical EV after haircuts, and the price sheet does not depend on extreme uncalibrated fairs.

### Phase 2: Post-Only Quote Engine

Build a one-sided quoting engine, not a resting fallback.

Behavior:

- Place post-only GTC/GTD bids at or below `max_quote_price`.
- Never cross the spread as part of the primary price-maker path.
- Prefer GTD where practical so quote expiry is exchange-enforced; keep heartbeat cancel safeguards.
- Batch child quotes where supported instead of submitting a ladder sequentially.
- Cancel on any adverse fair-value change, weather update, book move, station/date risk change, or stale feed condition.
- Record each quote with the price-sheet version and all haircuts used to set the price.

Quote examples:

```text
if calibrated_fair - quote_price >= required_edge_after_haircuts:
    post_only_bid(quote_price, size, gtd_expiry)

if calibrated_fair deteriorates or book/weather state invalidates the quote:
    cancel
```

Exit gate: dry-run/shadow quotes can be reconstructed and cancelled correctly, with no funded exposure.

### Phase 3: Shadow Quote Replay

Use recorded CLOB events to replay whether our posted prices would plausibly have filled. Do not expect perfect passive-fill replay from aggregate public books; score scenarios conservatively.

For each shadow quote, label:

- postable or crossed;
- queue ahead estimate;
- pessimistic fill;
- base-case fill;
- optimistic fill;
- adverse book move before fill;
- cancel-trigger fired before fill;
- final settlement PnL if filled.

Promotion interpretation:

- If only optimistic queue assumptions are positive, the quote policy is not promotable.
- If pessimistic/base fills are rare but positive, it may support tiny live exploration.
- If fills are still the bad subset, the model edge is not accessible through this venue/tactic.

Exit gate: shadow quote replay reports fill-conditioned EV by quoted price band, spread regime, station, side, and cancellation trigger.

### Phase 4: Tiny Funded Quote Canary

Only after shadow labels are working.

- Use `$2-$5` post-only quotes and a strict daily loss cap.
- Randomize quote aggressiveness inside a preapproved safe band so the later analysis is not purely self-selected.
- Keep taker FAK off by default except as a separately tagged control arm.
- Run enough resolved fills and misses to compare `E[pnl | filled at our quote]` versus `E[pnl | missed]`.
- Promote only if actual filled R/R, filled-at-quote replay, shadow base-case replay, and current-window settlement are positive.

### Phase 5: Learned Quote Policy And Sizing

Only after the tiny quote canary passes. The learned policy should choose quote/skip/size from context, not from a hand-written list of 100 execution strategies.

Inputs:

```text
calibrated_fair
uncertainty
spread
depth
queue_age
recent cancels/trades
station/side/regime
time-to-resolution
weather update freshness
inventory/risk state
```

Outputs:

```text
skip_or_quote
quote_price
quote_size
ttl_or_gtd_expiry
cancel_rule
```

Sizing should be a function of fill-conditioned quote edge, not raw model edge:

```text
target_size =
    bankroll
    * fractional_kelly
    * calibrated_quote_edge
    * fill_quality_score
    * quote_capacity
    * regime_confidence
```

Until the fill/toxicity model is stable, use flat tiny canary sizing. Do not size up to compensate for missed fills; that scales the adverse-selection problem unless `E[pnl | filled]` is proven positive.

## Concrete Near-Term Priorities

1. Keep global low MVP stopped. Recent selected replay is negative, so execution work cannot rescue it.
2. Use US consensus no-tiny as the first price-maker research subject. It has positive selected replay but severe fill-quality degradation when we chase available liquidity.
3. Build the calibrated price-sheet generator: fair, uncertainty haircut, adverse-selection haircut, max quote price, quote size cap, and cancel triggers.
4. Run the CLOB recorder against current candidates so quote lifecycle, feed age, queue movement, and cancellation triggers are observable.
5. Build the shadow quote replay report:

```text
selected replay
posted-price shadow replay
pessimistic/base/optimistic passive fill assumptions
actual filled replay at quoted price
unfilled selected replay
actual settled PnL
fill probability by quote band
toxicity probability by quote band
cancel-trigger attribution
```

6. Build the post-only quote engine for tiny canaries only after the price sheet and shadow replay exist.
7. Do not build a broad execution-variant leaderboard before the price-maker test. The decisive question is whether our model can set bid prices that the market occasionally accepts with positive filled-subset EV.

## Kill Rules For The Next Phase

- Kill a tactic if `E[pnl | filled]` is negative, even when selected replay is positive.
- Kill a tactic if winner fill rate is materially below loser fill rate after minimum sample.
- Kill a sleeve if recent selected replay is negative before execution effects.
- Kill any quote policy whose fills occur mostly after adverse book movement.
- Kill any quote policy that only works under optimistic queue assumptions.
- Kill any quote policy that depends on extreme uncalibrated fair values.
- Kill any taker tactic that depends on quotes with high cancellation probability.

## Bottom Line

RoboWeather should not restart as a better-filtered version of the same taker bot. It should restart, if at all, as a measured price-maker that uses forecast edge to set conservative quotes.

The path is not "find the one good model" or "try enough resting TTLs." The path is:

```text
calibrated weather edge
+ event-driven book state
+ conservative quote prices
+ post-only execution with cancellation rules
+ fill/toxicity validation at our prices
+ tiny randomized live validation
```

Only after that can replay EV become tradable EV.
