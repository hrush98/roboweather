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

## Progression Plan

### 1. Build The Portfolio Promotion Report

Standalone policy leaderboards are no longer sufficient. Every candidate must be evaluated after the current live stack consumes plan order, station/date caps, station/date/side caps, exact bucket/side caps, entry bands, and recorded depth/VWAP.

Required report:

- replay directly from `prediction_snapshots`, not stale `research_policy_positions`;
- support `HIGH_TEMP` and `LOW_TEMP`, US and global;
- apply the current live strategy order and caps;
- classify each candidate as duplicate size-up, overlapping variant, low-sample niche, or genuinely additive;
- report incremental filled risk, incremental PnL, incremental R/R, and capacity blocked by caps/depth.

This is the first gate before new policy promotion or sizing changes. It is the workflow that caught the weak NGBoost and 15m-overlay promotions.

### 2. Add Calibration And Regime Sizing

The current replay history shows repeated overconfidence by station, side, entry band, and model family. Scaling without calibration will mostly scale the worst mistakes.

Build:

- station-specific calibration layers;
- side-specific calibration for `BUY_YES` and `BUY_NO`;
- regime buckets such as inland/coastal, late-day, cloudy, humid, frontal, and low-liquidity;
- rolling 30/60/90-day calibration reports;
- sizing multipliers based on calibrated edge, not raw model edge.

Sizing should eventually depend on:

```text
target size = bankroll * fractional_kelly * calibrated_edge * liquidity_score * regime_confidence
```

Hard caps still apply. The output should be conservative until live fill and settlement data confirms replay quality.

### 3. Build HRRR/Regime Specialist Overlays

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

### 4. Expand Breadth Across Market Families

US high-temperature alone is unlikely to support `$1000/day` EV. The system needs more independent, fillable sleeves:

- US high-temp core consensus;
- US low-temp where replay and liquidity are clean;
- global low-temp BUY_NO core;
- low-temp convex tail sleeve;
- HRRR inland late-day specialist;
- global high-temp only if raw-snapshot replay improves;
- future weather families such as precipitation, snowfall, wind, hurricane, and air-quality/event markets if market availability and data quality support them.

Each new sleeve must pass the portfolio promotion report. Do not promote because a standalone leaderboard looks good.

### 5. Turn Execution Into Alpha, Not Leakage

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
- portfolio promotion report exists for US high and global low;
- all active policies have post-fix live fill attribution;
- daily filled risk can reach roughly `$500-$1000` with clean realized ROI.

### $250/day EV

Requirements:

- calibrated station/regime sizing is active;
- global low-temp BUY_NO has enough resolved post-fix evidence to size beyond canary;
- low-temp tail sleeve has strict daily loss caps;
- HRRR inland specialist remains positive after `30+` resolved additive trades.

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

Do not scale a policy because it is profitable in isolation. Scale only when it is profitable, fillable, and additive behind the current live stack under current caps.
