# Roadmap To $1000/Day EV

This document is the strategic roadmap for scaling RoboWeather from a small live edge harvester into a portfolio engine that can plausibly reach `$1000/day` expected value.

It is not the live operating journal. Use `docs/live-trading-journal.md` for the current live policy stack, sizing, risk caps, and execution lessons. Use this document for the research and engineering progression required before materially higher sizing is justified.

## Capacity Math

Daily EV is constrained by filled risk and realized return after slippage:

```text
daily EV = daily filled risk * realized ROI after slippage
```

Approximate filled risk required:

| Realized ROI | Filled risk required for $1000/day EV |
| ---: | ---: |
| 10% | $10000/day |
| 20% | $5000/day |
| 50% | $2000/day |
| 100% | $1000/day |

The current live stack cannot be assumed to support this capacity. At a `$750/day` new-risk cap, even a strong `50%` realized ROI only supports about `$375/day` before missed fills, adverse selection, and execution loss. A more sober `10-25%` realized ROI supports roughly `$75-$190/day`.

The path to `$1000/day` therefore requires all three:

- more reliable calibrated edge;
- more independent opportunities;
- more fillable size per opportunity.

The most important current lesson is that these cannot be evaluated from raw
snapshot replay alone. RoboWeather has positive-EV pockets, but the live
portfolio can still lose money when those pockets are widened, filled at worse
prices, settled differently by Polymarket than weather-outcome replay, or
duplicated behind earlier sleeves. Before any material sizing increase, the
system needs a single report that follows each sleeve through the whole chain:

```text
raw snapshot replay
-> live-selected candidate replay
-> actual filled replay at entry/fill price
-> actual Polymarket settled PnL
```

## Progression Plan

### 1. Build The Whole-Chain Truth Report

The next gate is a truth report that reconciles research replay to actual live
trading. It should answer why a slice made or lost money, not just whether a
standalone backtest looked good.

Required report, by live sleeve and candidate sleeve:

- raw `prediction_snapshots` replay at scored entry;
- replay of the exact candidates selected/reserved by the live loop;
- filled-subset replay using actual average fill prices;
- actual settled live PnL from Polymarket resolution;
- unfilled/reserved candidate results versus filled candidate results;
- slippage versus scored entry by strategy, station, side, and entry band;
- settlement-source mismatches between weather-outcome scoring and Polymarket;
- capacity lost to caps, insufficient depth, no-match FAKs, and resting TTL expiry.

This report is now the first gate before calibration-driven sizing, new sleeve
promotion, or cap increases. A replay slice is only scale evidence when the same
slice survives live selection, fills, and settlement.

Initial sleeves to include:

- US high-temp consensus no-tiny;
- global low canary;
- global low MVP add-on;
- global low tiny tail;
- METAR+HRRR inland late disagreement candidate;
- HRRR-rich inland late disagreement candidate;
- global high research candidates.

### 2. Maintain The Portfolio Promotion Report

Standalone policy leaderboards are no longer sufficient. Every candidate must be evaluated after the current live stack consumes plan order, station/date caps, station/date/side caps, exact bucket/side caps, entry bands, and recorded depth/VWAP.

Required report:

- replay directly from `prediction_snapshots`, not stale `research_policy_positions`;
- support `HIGH_TEMP` and `LOW_TEMP`, US and global;
- apply the current live strategy order and caps;
- classify each candidate as duplicate size-up, overlapping variant, low-sample niche, or genuinely additive;
- report incremental filled risk, incremental PnL, incremental R/R, and capacity blocked by caps/depth.

This remains the portfolio construction gate. It is the workflow that caught the
weak NGBoost and 15m-overlay promotions. It is not sufficient by itself for live
sizing until the whole-chain truth report also confirms live selection,
execution, and settlement.

### 3. Run Controlled Execution Experiments

Replay can identify candidate edge, but it cannot prove that the edge can be
captured in thin live order books. Shadow mode can confirm that the live scanner
would have selected the same rows, but it does not answer the harder questions:
whether those rows fill, whether filled rows are adversely selected, or whether
small fills translate to useful capacity.

Treat every scalable sleeve as a combined object:

```text
tradable sleeve = signal policy + execution policy + sizing policy
```

Execution promotion requirements:

- test execution tactics separately, such as FAK-only at `entry + 1c`, passive resting, and split FAK-plus-resting paths;
- use meaningful but capped live size, because `$5` fills do not prove `$100` tradability;
- start in the cleanest domain first, currently US high-temperature consensus or tightly scoped inland late HRRR/METAR+HRRR candidates, not unresolved global-low semantics;
- keep one pick per station/date and strict daily loss caps during tests;
- compare filled candidates against missed candidates to detect adverse fill selection;
- require actual fill prices to remain close to scored entry, not just below a broad sweep cap;
- evaluate Polymarket-settled PnL, not only weather-outcome replay.

This is the bridge between research edge and scale. A policy that wins in replay
but loses when filled is not ready for size; the system should either improve
the execution contract or keep the policy research-only.

### 4. Add Calibration And Regime Sizing

The current replay history shows repeated overconfidence by station, side, entry band, and model family. Scaling without calibration will mostly scale the worst mistakes.

Build:

- a Layer 1 station/side/entry-band gate keyed by model family;
- station-specific calibration layers;
- side-specific calibration for `BUY_YES` and `BUY_NO`;
- regime buckets such as inland/coastal, late-day, cloudy, humid, frontal, and low-liquidity;
- rolling 30/60/90-day calibration reports;
- sizing multipliers based on calibrated edge, not raw model edge.

Sizing should eventually depend on:

```text
target size = bankroll * fractional_kelly * calibrated_edge * liquidity_score * regime_confidence
```

Hard caps still apply. The output should be conservative until live fill and
settlement data confirms replay quality. Negative live-settlement evidence
overrides weather-outcome replay. Treat `BLOCK` buckets as no-trade,
`CANARY` buckets as tiny size, and `WATCH` buckets as normal only when the
whole-chain report is positive.

### 5. Build HRRR/Regime Specialist Overlays

HRRR should not be blanket-promoted. It should be a second-opinion overlay that trades only where HRRR adds orthogonal information to the observation-only core.

Candidate shape:

```text
Trade only when:
1. The obs-core policy does not already own the same bucket/side, or is much weaker.
2. HRRR-rich dynamic model sees strong edge.
3. The station/regime historically favors HRRR.
4. The market is late enough that HRRR remaining-high/remaining-low information matters.
5. The trade passes normal caps and fillability checks.
```

Initial replay result from the local research DB:

- DB: `/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
- Scope: US `HIGH_TEMP`
- Resolved overlap window: `2026-06-04` through `2026-06-10`
- Obs-core proxy: `obs_bucket_consensus`
- HRRR sources: `dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025` and `dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025`
- Filters: local `12:00-15:00`, `HIGH_CONVICTION`, entry `<= 0.50`, HRRR edge `>= 0.25`, HRRR fair at least `0.15` stronger than obs-core for the same station/date/bucket/side/delay or obs-core absent

Replay summary:

| Slice | N | Win rate | R/R | PnL | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| HRRR dynamic-tuned standalone | 27 | 51.9% | 0.332 | +3.49 | Positive, but not automatically additive. |
| HRRR dynamic-tuned additive | 23 | 43.5% | 0.015 | +0.15 | Basically flat overall. |
| HRRR dynamic-tuned additive, inland only | 10 | 60.0% | 0.429 | +1.80 | Promising canary candidate. |
| HRRR dynamic-tuned additive, coastal only | 13 | 30.8% | -0.292 | -1.65 | Avoid. |
| METAR+HRRR dynamic additive, inland only | 11 | 63.6% | 0.496 | +2.32 | Promising canary candidate. |
| HRRR CatBoost additive | 158 | 7.0% | -0.473 | -9.88 | Do not use in this overlay shape. |

Initial candidate name:

```text
hrrr_dynamic_tuned_inland_late_disagreement_entry_00_50_by_bucket_side_delay_first
```

Initial policy shape:

- models: dynamic-tuned HRRR-rich and possibly METAR+HRRR dynamic-tuned;
- stations: `KATL`, `KDAL`, `KORD`; consider `KBKF` only after more sample;
- exclude coastal stations for now;
- market family: `HIGH_TEMP`;
- entry: `<= 0.50`;
- window: local `12:00-15:00`;
- side: score `BUY_YES` and `BUY_NO` separately before promotion;
- sizing: research or tiny canary only until at least `30+` resolved inland additive trades.

Example replay rows:

| Date | Station | Bucket | Side | Entry | Result |
| --- | --- | --- | --- | ---: | --- |
| 2026-06-06 | KORD | 84-85F | BUY_YES | 0.28 | Won, +0.72 |
| 2026-06-07 | KATL | 82-83F | BUY_NO | 0.26 | Won, +0.74 |
| 2026-06-08 | KDAL | 92-93F | BUY_NO | 0.50 | Won, +0.50 |
| 2026-06-10 | KORD | 90-91F | BUY_NO | 0.33 | Lost, -0.33 |

This is evidence for a canary research overlay, not full promotion.

Fresher replay through `2026-06-15` keeps the direction but tightens the
interpretation: METAR+HRRR inland remains the stronger canary candidate, while
plain HRRR inland weakened in the recent cap-aware window. Do not promote broad
HRRR or coastal HRRR from raw ranking tables; keep the candidate inland,
late-day, entry `<= 0.50`, and disagreement/additive only until at least `30+`
resolved additive trades remain positive.

### 5. Expand Breadth Across Market Families

US high-temperature alone is unlikely to support `$1000/day` EV. The system needs more independent, fillable sleeves:

- US high-temp core consensus;
- US low-temp where replay, liquidity, and settlement are clean;
- global low-temp BUY_NO core, but only in the exact slices that survive live settlement and fill audits;
- low-temp convex tail sleeve;
- HRRR inland late-day specialist;
- global high-temp only if sample grows and the whole-chain report confirms live viability;
- future weather families such as precipitation, snowfall, wind, hurricane, and air-quality/event markets if market availability and data quality support them.

Current interpretation:

- global low MVP `entry <= 0.50` is the strongest global replay slice, but
  global-low live settlement mismatches mean it must stay canary-sized until
  Polymarket-vs-weather semantics are reconciled;
- global low `0.50-0.75` is not scale evidence and should be reduced, blocked,
  or separately proven;
- global high is positive but thin and remains research-only;
- US high-temp core should not be scaled from recent replay until calibration and
  live-selected/fill attribution improve.

Each new sleeve must pass the portfolio promotion report and the whole-chain
truth report. Do not promote because a standalone leaderboard looks good.

### 6. Turn Execution Into Alpha, Not Leakage

Execution quality directly changes realized edge. The live DB already shows that invalid tick-size GTC rejects, insufficient depth, no-match FAKs, and TTL-expired resting orders can materially change capacity.

Build execution attribution:

- expected EV at decision time;
- FAK-fill EV after actual sweep depth;
- passive ladder EV after accepted or rejected child orders;
- final realized EV after fills and settlement;
- rejected reason grouped by strategy, market family, and station;
- slippage by entry band and book age;
- fill-rate by intended notional bucket.

Execution upgrades should include:

- pre-submit tick-size validation for every signed order path;
- maker-first passive ladders when edge is large and urgency is low;
- adaptive TTL by book age, spread, liquidity, and weather information decay;
- cancel/replace rules when books move or observations refresh;
- live mark/settlement reconciliation between bot ledger and exchange view.

## Milestones

### $100/day EV

Requirements:

- current live stack is settlement-positive after recent execution fixes;
- whole-chain truth report exists for US high and global low;
- portfolio promotion report exists for US high, global low, and candidate overlays;
- all active policies have post-fix live fill attribution;
- active global low slices have no unresolved settlement semantics mismatch;
- daily filled risk can reach roughly `$500-$1000` with clean realized ROI.

### $250/day EV

Requirements:

- calibrated station/regime sizing is active;
- global low-temp BUY_NO has enough whole-chain post-fix evidence to size beyond canary;
- low-temp tail sleeve has strict daily loss caps;
- METAR+HRRR or HRRR inland specialist remains positive after `30+` resolved additive trades.

### $500/day EV

Requirements:

- at least three independent sleeves are live and additive after caps;
- daily filled risk can reach roughly `$2000-$5000`;
- execution attribution shows realized entry quality close to replay assumptions;
- drawdown limits and kill switch are tested.

### $1000/day EV

Requirements:

- daily filled risk can reach roughly `$5000-$10000` at realistic ROI;
- capacity is diversified across market families and station/regime buckets;
- no single station, market family, model family, or weather regime dominates expected PnL;
- live settlement data confirms replay-derived EV after slippage;
- bankroll, loss limits, and operator workflow can tolerate the larger variance.

## Operating Rule

Do not scale a policy because it is profitable in isolation. Scale only when it is
profitable, fillable, additive behind the current live stack under current caps,
and reconciled from raw replay through live selection, actual fills, and
Polymarket settlement.
