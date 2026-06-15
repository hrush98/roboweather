# RoboWeather PnL Deep Dive — Root Cause Analysis

**Date:** 2026-06-15
**Context:** Investigation into why live PnL has been trending negative despite positive research replay EV, execution upgrades, and improved promotion/policy selection tooling.
**DB:** live_trading.sqlite (8.4 GB, 146 settled, 89 rejected, 8 partial); research_2026-05-08_multimodel.sqlite

---

## Executive Summary

The June 15 execution fixes (entry-anchored FAK/retry/ladder) were correct and necessary but address a secondary problem. The primary driver of negative PnL is severe model miscalibration — the models' edge estimates are not just slightly off, they are structurally inverted at higher confidence levels. Slippage adds cost but would not turn a +EV strategy negative by itself. The system will not recover on execution fixes alone.

---

## 1. Live PnL Reality (All-Time Settled)

| Strategy | N | Total PnL | Avg PnL | Win Rate | Avg Edge | Model-Implied Win% |
|---|---|---|---|---|---|---|
| Consensus no-tiny (current core) | 13 | +$192.68 | +$14.82 | 53.8% | 0.64 | ~96% |
| Old dynamic core (edge_025) | 70 | -$135.18 | -$1.93 | 52.9% | 0.38 | ~83% |
| NGBoost BUY_YES | 31 | -$115.55 | -$3.73 | 19.4% | 0.42 | ~66% |
| Global low canary (old) | 19 | -$199.53 | -$10.50 | 57.9% | 0.30 | ~88% |
| Global low MVP add-on | 12 | -$100.00 | -$8.33 | 41.7% | 0.47 | ~83% |

**Overall settled: -$358.58 across 146 positions.** Only consensus no-tiny is profitable.

For June 1-15 specifically: intended notional $5,571, filled $2,651 (47.6% fill rate), realized PnL -$502, vs uncalibrated model-implied EV of +$6,222.

## 1a. Daily PnL June 2026

| Date | Settled | Daily PnL |
|---|---|---|
| Jun 15 | 6 | $0.00 |
| Jun 14 | 12 | +$30.38 |
| Jun 13 | 15 | -$301.42 |
| Jun 12 | 12 | -$17.88 |
| Jun 11 | 9 | +$91.51 |
| Jun 10 | 15 | -$43.86 |
| Jun 09 | 5 | -$52.74 |
| Jun 08 | 5 | -$30.00 |
| Jun 07 | 17 | -$16.70 |
| Jun 06 | 15 | -$32.56 |
| Jun 05 | 14 | -$87.91 |
| Jun 04 | 15 | +$272.86 |
| Jun 03 | 11 | -$170.00 |
| Jun 02 | 10 | -$152.00 |
| Jun 01 | 6 | +$8.00 |

Two massive blowout days (Jun 13: -$301, Jun 2: -$152) account for most of the losses. Jun 4 (+$273) was the only strong positive day, driven by KSFO consensus no-tiny wins.

---

## 2. Execution Slippage: Real but Secondary

PnL comparison if every position filled exactly at entry_price (no slippage):

| Strategy | Actual PnL | Entry-Price PnL | Slippage Cost |
|---|---|---|---|
| Consensus no-tiny | +$192.68 | +$205.78 | -$13.10 |
| Old dynamic core | -$135.18 | -$88.93 | -$46.25 |
| NGBoost BUY_YES | -$115.55 | -$94.12 | -$21.43 |
| Global low canary | -$199.53 | -$166.14 | -$33.39 |
| Global low MVP | -$100.00 | -$75.79 | -$24.21 |

Even at perfect entry-price execution, 4 of 5 strategies are negative. Slippage adds 6-14% to losses but does not flip any strategy from positive to negative. The execution fixes were right to tighten tolerances, but they cannot save losing strategies.

Average position-level slippage (fill_price - entry_price):
- Consensus no-tiny: +2.28c on 32.3c entry (7.1%)
- Old dynamic core: +1.78c on 45.3c entry (3.9%)
- NGBoost BUY_YES: +1.63c on 23.9c entry (6.8%)
- Global low canary: +2.05c on 57.6c entry (3.6%)
- Global low MVP: +3.48c on 36.3c entry (9.6%)

---

## 3. The Real Problem: Model Edge Is Structurally Uncalibrated

Settled PnL broken down by model edge bucket:

### Old dynamic core (edge_025 filter)

| Edge Bucket | N | Total PnL | Avg PnL | Win Rate |
|---|---|---|---|---|
| 0.29 | 30 | +$115.21 | +$3.84 | 66.7% |
| 0.43 | 28 | -$276.45 | -$9.87 | 39.3% |
| 0.47 | 7 | -$13.17 | -$1.88 | 42.9% |

The model's edge is inverted: higher claimed edge → lower actual win rate. The model is most confident exactly where it's most wrong.

### Consensus no-tiny

| Edge Bucket | N | Total PnL | Avg PnL | Win Rate |
|---|---|---|---|---|
| 0.45 | 4 | -$5.13 | -$1.28 | 50.0% |
| 0.59 | 3 | +$21.04 | +$7.01 | 66.7% |
| 0.81 | 2 | +$113.13 | +$56.56 | 100.0% |
| 0.92 | 3 | +$93.64 | +$31.21 | 33.3% |

Even the winning consensus strategy shows inversion at the extreme: edge 0.92 has 33.3% win rate.

### NGBoost BUY_YES

| Edge Bucket | N | Total PnL | Win Rate |
|---|---|---|---|
| 0.32 | 16 | -$31.86 | 31.3% |
| 0.42 | 4 | -$40.00 | 0.0% |
| 0.46 | 3 | -$30.00 | 0.0% |
| 0.56 | 2 | -$20.00 | 0.0% |
| 0.71 | 5 | +$10.81 | 20.0% |
| 0.76 | 1 | -$4.50 | 0.0% |

Catastrophic: the model claims high edge on BUY_YES but almost never wins.

### Root mechanism

The fair value engine uses raw model probability directly as "fair value" with no calibration layer:

```
edge = model_probability - ask_price
```

The model-performance-log confirms top-bucket accuracy of only 34.9% for the obs-family dynamic bucket model. Grouped log loss of 1.42-1.47 means the model is barely better than random for bucket selection. The calibration that exists (sigmoid/isotonic in CalibratedClassifierCV) is global — there is no per-station, per-side, or per-entry-band calibration.

---

## 4. The Replay vs Live Gap

Portfolio promotion report for June 1-15:

| Sleeve | Replay Fills | Replay Risk | Replay PnL | Replay R/R | Live R/R | Live PnL |
|---|---|---|---|---|---|---|
| US consensus no-tiny | 8 | $178 | +$515 | 2.89x | 0.70x | +$220 |
| Global low canary | 41 | $2,300 | +$1,168 | 0.51x | -0.25x | -$200 |
| Global low MVP add-on | 43 | $832 | +$1,874 | 2.25x | -0.23x | -$100 |

The consensus no-tiny replay-to-live gap (2.89x → 0.70x) has three components:

1. **Position selection difference**: replay found 8 fills, live executed 16 (13 settled). Live is trading more positions than replay would select, possibly because live builds snapshots in real-time with different book/weather state than the research collector, or because live candidate deduplication differs from replay first-by-scope logic.

2. **Fill price**: 2.28c average above entry erodes about 7% of R/R. Not the main driver but measurable.

3. **Fill selection bias**: 47.6% fill rate means the 52.4% that doesn't fill may be the harder-to-fill but better-priced positions. Adverse selection in what gets filled vs what stays reserved.

---

## 5. Secondary Issues Found

### 5a. Global low-temp settlement mismatches

5 positions where weather outcomes say BUY_NO won but Polymarket settled BUY_YES:

| Station | Date | Bucket | Entry | Edge | Final Low | Weather Win | Poly Win | PnL |
|---|---|---|---|---|---|---|---|---|
| VHHH | 2026-06-14 | 26-26F | 0.53 | 0.339 | 29F | YES | NO | -$100 |
| RJTT | 2026-06-13 | 19-19F | 0.49 | 0.289 | 23F | YES | NO | -$50 |
| VHHH | 2026-06-12 | 26-26F | 0.50 | 0.288 | 27F | YES | NO | -$25 |
| RJTT | 2026-06-11 | 18-18F | 0.40 | 0.391 | 20F | YES | NO | -$2.50 |
| VHHH | 2026-06-11 | 25-25F | 0.34 | 0.438 | 26F | YES | NO | -$1.00 |

All 5 are single-degree buckets on international stations. A VHHH (Hong Kong) low of 29F is physically impossible — likely the Polymarket buckets are in Celsius while station_date_outcomes stores Fahrenheit, or vice versa. These 5 mismatches cost $178.50 total.

### 5b. Fill rate bottleneck

- 58 resting TTL expirations: $1,316 intended → $0 filled
- 32 exact bucket/side cap rejections
- 22 insufficient depth rejections
- 15 no-match FAK orders (book moved between snapshot and submission)
- 11 allowance/balance rejections
- 7 order_version_mismatch rejections
- Fill rate: 47.6% overall

### 5c. Old strategies still settling

70 positions from deactivated dynamic core and 31 from deactivated NGBoost are still working through settlement. These will continue to drag PnL until fully resolved.

### 5d. Station toxicity

The systematic edge improvement analysis (June 3) identified KLGA (26% win rate on 154 positions) and KMIA (15.8% on 95) as toxic for BUY_NO. The live data confirms this — both stations appear frequently in losing positions across multiple strategies.

---

## 6. Assessment of June 15 Execution Fixes

The changes are sound and necessary:
- FAK capped at `entry + 1c` instead of chasing `selected_sweep_price_cap` (which could be 5-10c above entry)
- Retry uses same entry-anchored cap, skips when ask exceeds it
- Resting ladder at `entry+1c/entry/entry-1c/entry-2c` with 30/40/20/10 weights and post-only on entry-and-below rungs
- 420-second TTL, narrow price band — designed for fill improvement, not passive market-making

These prevent the worst-case overpayment scenario. They are a genuine improvement and should be kept.

**What they don't fix:** A model that selects -EV trades with high confidence. Tightening execution on bad trade selection just loses money more precisely.

---

## 7. Recommendations

### Immediate (this week)

1. **Add a calibration layer before edge computation.** Track rolling per-station, per-entry-band realized win rates from settled positions and scale model edge by the calibration ratio. At minimum, flag trades where historical calibration ratio < 0.5.

2. **Tighten the consensus no-tiny edge filter.** The data shows edge 0.29 works (66.7% WR) while edge 0.43 loses (39.3%). Raise minimum edge or add a per-station historical filter.

3. **Station blacklist for BUY_NO.** Block KLGA, KMIA, KATL until per-station calibration exists.

### Short-term (1-2 weeks)

4. **Build station/side/entry-band calibration table** from research DB resolved outcomes. Scale model edge before trade selection.

5. **Verify global low-temp bucket units.** Check whether Polymarket international low-temp buckets are Celsius while the system assumes Fahrenheit.

6. **Reduce global low sizing to canary-only** ($5-10 per position) until calibration and settlement reconciliation are complete.

### Medium-term

7. **Activate HRRR-rich models for execution.** They dominate snapshot replay (R/R 2-8x, Sharpe 1.5-28x) but are flagged EXECUTION_WEAK. Build depth-aware execution for HRRR signals.

8. **Regime-aware position sizing.** Check for correlated same-day positions before adding more exposure.

9. **Persist live prediction snapshots.** The journal's June 15 entry identifies that live doesn't persist the full candidate universe, making live-vs-replay audits incomplete. Without this, calibration feedback loops will be biased toward filled-only data.

---

## 8. Verdict

The execution fixes close one leak (overpaying on FAK). But the tank has a much larger hole: the models are picking negative-EV trades. The consensus no-tiny core is marginally profitable (+$193 on 13 settled) and its 2.89x replay R/R suggests genuine edge exists. But the other sleeves are destroying that edge.

The system needs calibration before it can scale. The good news is that calibration is a simpler engineering problem than model improvement — settled trade data provides direct ground truth. A rolling per-station win-rate table keyed by station/side/entry_band would immediately filter out the worst trades and likely flip the global low strategies from negative to breakeven or slightly positive.
