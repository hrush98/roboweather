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

## Jane Street-Style Desk Intuition

The correct mental model is not "the model found a good bucket, now get filled." It is "we are showing or taking a price, and the fill itself is information." In a thin order book, the counterparty's decision to trade with us is part of the signal. If better-informed or faster liquidity providers only leave quotes up when our fair is stale, then a fill is not a neutral event; it is evidence that the conditional distribution changed against us.

That means every tactic needs the same accounting a serious trading desk would demand:

- Mark every fill against immediate and delayed book/fair markouts: `10s`, `30s`, `2m`, `10m`, next weather update, close, and settlement.
- Treat missed fills as data, not failure noise. A missed winner and a filled loser are both observations about the execution policy.
- Separate forecast alpha from access alpha. A good weather forecast is not monetizable unless the venue lets us trade it at a price and size that survive fill conditioning.
- Avoid one-sided backtests. If the rule only looks good when unfilled quotes are assumed filled, the rule is not trading evidence.
- Randomize small quote aggressiveness within a safe band so later analysis can estimate response curves instead of only analyzing self-selected fills.
- Quote only prices we are happy to own after haircuts. Do not chase to prove the model right.

The desk question for every sleeve is therefore:

```text
At this price, size, and book state, does getting filled make us happier or more worried?
```

If the answer is "more worried," the system should skip, quote lower, or cancel faster. If the answer cannot be measured at useful size, funded trading should stay paused for that tactic.

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

Yes. The current mechanism is a price-anchored taker attempt plus fallback ladder. That still starts from the market's visible ask and lets the market decide which stale quotes remain available. The next mechanism should start from our quoteable fair value, capped and haircut until true calibration is proven, and quote a conservative bid we are happy to own if filled.

| Component | Job | Non-goal |
| --- | --- | --- |
| Quoteable fair engine | Convert model distribution into a conservative buy price with uncertainty and toxicity haircuts | Do not output 0.999-style certainty as a trading price without strong calibration evidence |
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

### ML Setup Implication

This phase does not require replacing the core weather-model training stack. Continue using the existing regression, tree-based, HRRR/METAR-enriched, bucket, CatBoost, and consensus model families as the forecast layer.

What changes is the product of the ML system. The forecast layer should feed a pricing layer, not directly authorize ask-taking:

```text
existing forecast models
-> calibrated outcome distribution
-> market-aware shrinkage
-> uncertainty estimate
-> quoteable fair price
-> adverse-selection haircut
-> max bid / size / cancel rules
```

The practical additions are post-model:

- Calibrate model probabilities by station, side, market family, time window, and model family.
- Treat Polymarket price as a strong prior or reference point, not merely an after-the-fact comparison.
- Penalize extreme model confidence unless recent calibration supports it.
- Estimate uncertainty from model disagreement, station sample size, weather-data freshness, time-to-resolution, spread, depth, and recent calibration error.
- Convert fair value into a conservative quote price with explicit haircuts and required margin.

Evaluation should split forecast quality from tradability:

| Layer | Main question | Metrics |
| --- | --- | --- |
| Forecast model | Did we predict the weather distribution well? | Brier, log loss, reliability curves, station/side calibration |
| Pricing layer | Did the quoteable fair value preserve edge after haircuts? | quoted-price EV, calibration by fair band, edge monotonicity |
| Quote policy | Did fills at our prices make money? | filled-subset R/R, filled vs missed replay, toxicity after fill, settlement PnL |

The existing models can remain the backbone. Promotion should change: a model family is not live-ready because it has good selected replay; it is live-ready only when its calibrated quote prices lead to profitable filled-subset quote PnL.

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

### Phase 1: Capped/Haircut Price Sheet V1

Build the price sheet that the quoting engine will use. This is the real model handoff: the forecast model no longer says "take this ask"; it says "we are willing to bid up to this price after haircuts."

Important naming discipline: the current source field is called `calibrated_fair`, but Phase 1 v1 is only a capped and haircut fair value. It is not yet a full market-aware calibration layer. Treat the column as `capped_quote_fair` for trading interpretation until walk-forward reliability, market-prior shrinkage, and fair-band calibration are implemented and measured.

Implementation status, 2026-07-06: source support is in place for a scoped Phase 1 price sheet on the US high-temperature consensus no-tiny BUY_NO sleeve. Live candidate builds now persist `live_price_sheets` with raw/capped fair, market reference, uncertainty/adverse-selection haircuts, minimum edge, max quote price, size cap, validity, and cancel triggers. The read-only replay command is:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase1_price_sheet_report.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Initial active-DB sanity check on 2026-07-06: 15 resolved all-history sheets were +0.059 R/R, and 7 resolved last-30-day sheets were +0.044 R/R. This clears only the narrow "sheet generation is positive after haircuts" sanity check. It is not funded-trading approval because passive fill probability, queue position, cancellation behavior, and filled-subset quote PnL remain Phase 3/4 evidence.

Required output per candidate/bucket/side:

```text
raw_model_fair
capped_or_calibrated_fair
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

Exit gate: historical and current-window replay show that the generated quote prices would have positive theoretical EV after haircuts, and the price sheet does not depend on extreme uncalibrated fairs. This is a sheet-generation gate only. It does not approve funded execution without Phase 3/4 fill-conditioned evidence.

### Phase 2: Post-Only Quote Engine

Build a one-sided quoting engine, not a resting fallback.

Implementation status, 2026-07-06: shadow support is in place for the scoped Phase 1 sheet. Live candidate builds now persist `live_quote_intents` as post-only GTD shadow quotes linked to the price sheet, live candidate, and eventual live position. The engine clamps quote price to `min(max_quote_price, best_ask - 0.01)`, marks unpostable rows as skipped, and reconciles open shadow quotes to `SHADOW_EXPIRED` or `SHADOW_CANCELLED` when the fair validity window, feed/book state, or post-only crossing rule invalidates the quote. No funded CLOB quote placement was added.

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

### Steady-State Shadow Collection Milestone

This is the next build target for data collection. It is not funded trading and it should not require registering a large set of live strategies.

Implementation status, 2026-07-06: source support now targets the full collection milestone, but any given run must still pass the strict health report before it counts as steady-state data. The scoped Phase 1 sheet fans out a bounded 24-spec shadow grid per candidate using `$50` baseline and `$100` target-capacity intended notional, with stable `quote_spec_id` values, direct `live_quote_intents` columns for spec/rule metadata, `would_post` flags, top/depth context, lifecycle states, and markout hook metadata for `10s`, `30s`, `2m`, `10m`, next weather update, close, and settlement. `scripts/collect_candidate_clob_events.py` subscribes the market CLOB feed to current candidate token IDs and stores normalized events. `scripts/label_shadow_quote_outcomes.py` persists conservative/base/optimistic fill labels, queue estimates, adverse-move flags, cancel triggers, and markouts into `live_shadow_quote_outcomes`. `scripts/shadow_collection_report.py` now fails/flags missing token coverage, missing CLOB feed coverage, only tiny-size coverage, and missing shadow outcome labels. This remains dry-run/shadow infrastructure only; no funded CLOB quote placement or broad live strategy registration was added.

Keep these concepts separate:

```text
live strategy registry = funded execution sleeves, kept small and paused
shadow quote specs    = broad research parameter arms, persisted and replayed
```

The collection architecture should be:

```text
dry-run/live scan
-> full candidate universe
-> one or more price sheets
-> many useful-size virtual quote specs per candidate
-> shadow quote intents
-> shared CLOB/book event stream by token
-> lifecycle states and markout hooks
-> later shadow fill/toxicity/settlement labels at $50-$100 intended size
```

Minimum components to enter steady-state shadow collection:

1. Phase 0 recorder is operational, not merely implemented:
   - dry-run cycles persist the full candidate universe before policy filtering;
   - every row has a stable `live_candidate_id`;
   - the recorder subscribes to the union of candidate token IDs;
   - feed events persist exchange/feed timestamps and local receipt timestamps;
   - a health check shows candidate rows have book-event coverage.
2. A bounded shadow quote spec grid exists:
   - specs are not live strategies;
   - each spec has a stable `quote_spec_id` or hash;
   - each spec records `fair_source`, haircut/edge rule, `quote_rule`, `ttl`, `cancel_rule`, size, side, and post-only/crossing behavior;
   - the useful-size axis is `$50` baseline and `$100` target-capacity stress, because that is the size range the system must trade to matter;
   - do not use `$5` or `$10` quote arms as phase evidence. If the market cannot support `$50` shadow capacity under conservative labels, the right answer is skip/not executable, not "small size worked";
   - start with roughly 20-60 specs, broad enough to learn response curves but small enough to audit.
3. The shadow quote intent emitter fans out specs over candidates:
   - for each eligible candidate/spec pair, persist `candidate_id`, `quote_spec_id`, `quote_price`, size, `would_post`, `skip_reason`, creation timestamp, expiry timestamp, cancel rule, and feature payload;
   - persist intended notional and enough top/depth context to label whether `$50` and `$100` were plausibly fillable under conservative, base, and optimistic assumptions;
   - no funded order path is reachable from these rows.
4. Lifecycle reconciliation runs continuously:
   - mark intents as `SHADOW_POSTABLE`, `SHADOW_SKIPPED`, `SHADOW_EXPIRED`, `SHADOW_CANCELLED_BY_RULE`, `SHADOW_STALE_FEED`, or equivalent;
   - cancellation reasons must be reconstructable from book/fair/weather/risk state.
5. Minimal markout hooks are present:
   - quote timestamps and token/event coverage are sufficient to compute `10s`, `30s`, `2m`, `10m`, next weather update, close, and settlement markouts later;
   - the first implementation can compute these in batch rather than synchronously.
6. The run-readiness report proves useful-size coverage:
   - candidate rows, quote intents, book snapshots, and CLOB feed events are present for current candidates;
   - the report separates `$50` and `$100` intended notional coverage from smaller incidental depth;
   - missing feed coverage, missing token coverage, or only tiny-size quote coverage fails the milestone.

The first done gate is useful-size operational reconstruction, not profitability:

```text
For a random current candidate, an operator can inspect:
candidate features
all emitted $50/$100 shadow quote specs
initial book state
book events after quote time
shadow expiry/cancel state
markout windows available or pending
whether $50 and $100 intended notional have enough event/depth data to label later
```

When this is true for current candidates across a full dry-run session, the system is in the new mental model and can collect the dataset needed for Phase 3. Strategy registration, funded validation, learned sizing, and promotion logic should remain out of scope until after this collection milestone is stable.

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
- markout after fill or hypothetical fill at `10s`, `30s`, `2m`, `10m`, next weather update, close, and settlement;
- quote/fill toxicity: whether the book moved away from our side after the fill, whether top-of-book depth vanished before the fill, and whether the fill occurred after fair-value deterioration;
- final settlement PnL if filled.

Promotion interpretation:

- If only optimistic queue assumptions are positive, the quote policy is not promotable.
- If pessimistic/base fills are rare but positive at `$50-$100`, it may support tightly capped funded validation at the same size. If they are only positive below useful size, the tactic is not promotable.
- If fills are still the bad subset, the model edge is not accessible through this venue/tactic.
- If immediate markouts are adverse but settlement is positive, keep size capped until the reason is understood; the policy may be taking toxic intraday fills and surviving only because the weather forecast is strong enough.
- If markouts are favorable but settlement is negative, investigate bucket semantics, late weather updates, and settlement-source alignment before changing execution.

Exit gate: shadow quote replay reports fill-conditioned EV and markouts by quoted price band, spread regime, station, side, queue estimate, and cancellation trigger.

### Phase 4: Funded Useful-Size Quote Validation

Only after shadow labels are working.

Do not run `$5` or `$10` quote canaries for this phase. They are not representative of the execution problem we need to solve and should not be treated as plumbing, capacity, or promotion evidence. If a tactic cannot pass shadow and funded checks at roughly the size we need to trade, it is not useful for this system.

Capacity evidence must be size-specific:

```text
$50 fills are the minimum useful-size validation
$100 fills are the target-capacity validation
```

Funded validation ladder:

1. `$50` quote validation: first funded adverse-selection/capacity read with strict daily loss caps.
2. `$100` quote validation: target-capacity evidence only after `$50` fills and misses are clean.

Rules:

- Randomize quote aggressiveness inside a preapproved safe band so the later analysis is not purely self-selected.
- Keep taker FAK off by default except as a separately tagged control arm.
- Run enough resolved fills and misses at each size to compare `E[pnl | filled at our quote]` versus `E[pnl | missed]`.
- Promote only at the size that passed. Do not extrapolate sub-`$50` behavior to `$50` or `$100`.
- Promote only if actual filled R/R, filled-at-quote replay, shadow base-case replay, and current-window settlement are positive at the target size.

### Hard No-Promote Gates

Do not restart normal funded trading or size up a sleeve until all of these are true for the exact `signal policy + quote policy + size` being promoted:

- The current resolved window is positive; all-history performance alone is not enough.
- Theoretical selected replay is positive, but the filled-at-quote subset is also positive.
- Filled rows do not materially underperform missed rows after controlling for quote price band and station/date risk caps.
- Winner fill rate is not materially below loser fill rate after the minimum sample.
- Base-case shadow queue assumptions are positive; optimistic-only profitability is research-only.
- Post-fill markouts are not persistently adverse at `30s`, `2m`, and next weather update.
- Settlement labels match Polymarket outcomes for the market family being traded.
- The tested size has direct evidence. `$50` fills authorize at most `$50`; `$100` sizing requires `$100` evidence.

Minimum evidence before normal sizing:

```text
>= 20 resolved quote outcomes at the target tactic/size
>= 10 actual funded fills at that target tactic/size
comparable resolved missed/expired quote sample
no open data-integrity gaps in quote, order, fill, and settlement linkage
```

Below that threshold, the system stays paused for that tactic/size. Do not substitute tiny funded exposure for useful-size validation.

### Phase 5: Learned Quote Policy And Sizing

Only after useful-size quote validation passes. The learned policy should choose quote/skip/size from context, not from a hand-written list of 100 execution strategies.

Inputs:

```text
calibrated_fair
uncertainty
spread
depth
queue_age
recent cancels/trades
post_fill_markouts
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

Until the fill/toxicity model is stable, use staged quote sizing with strict loss caps. Capacity and promotion evidence must come from the same approximate size intended for live use, with `$50` as the minimum useful validation and `$100` as target-capacity validation. Do not size up to compensate for missed fills; that scales the adverse-selection problem unless `E[pnl | filled]` is proven positive at the target size.

## Concrete Near-Term Priorities

1. Keep global low MVP stopped. Recent selected replay is negative, so execution work cannot rescue it.
2. Use US consensus no-tiny as the first price-maker research subject. It has positive selected replay but severe fill-quality degradation when we chase available liquidity.
3. Build the calibrated price-sheet generator: fair, uncertainty haircut, adverse-selection haircut, max quote price, quote size cap, and cancel triggers.
4. Build the bounded shadow quote spec grid and intent fanout. Do not register these as live strategies.
5. Run the CLOB recorder against current candidates so quote lifecycle, feed age, queue movement, and cancellation triggers are observable.
6. Reach the steady-state shadow collection milestone: a random current candidate can be reconstructed from candidate features through all shadow specs, book events, lifecycle state, and pending/available markouts.
7. Build the shadow quote replay report:

```text
selected replay
posted-price shadow replay
pessimistic/base/optimistic passive fill assumptions
actual filled replay at quoted price
unfilled selected replay
post-fill markouts at 10s/30s/2m/10m/next weather update
actual settled PnL
fill probability by quote band
toxicity probability by quote band
cancel-trigger attribution
```

8. Build funded post-only placement only after the price sheet, CLOB recorder, useful-size shadow specs, and shadow replay exist. The current quote-intent support is shadow/dry-run infrastructure, not live CLOB placement. Do not add `$5` or `$10` funded quote canaries for this phase.
9. Require `$50` useful-size validation and then `$100` target-capacity validation before considering normal funded restart.
10. Do not build a broad execution-variant leaderboard before the price-maker test. The decisive question is whether our model can set bid prices that the market occasionally accepts with positive filled-subset EV at useful size.

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
+ useful-size live validation at $50-$100
```

Only after that can replay EV become tradable EV.
