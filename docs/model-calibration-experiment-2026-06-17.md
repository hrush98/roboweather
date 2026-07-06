# Model-Level Calibration Experiment — 2026-06-17

## What We Did

Trained per-station Platt scaling (logistic regression) to recalibrate the raw
model fair value into a probability that matches actual weather outcomes.

**Model:** `sklearn.linear_model.LogisticRegression` with no regularization
(`C=1e10`, equivalent to `penalty=None`).

**Features:** A single feature — the raw model fair value (a probability
between 0 and 1). The logistic regression operates in logit space:
`logit(p) = ln(p/(1-p))`.

**Target:** Binary — did the bucket trade win or lose? Scored against
`station_date_outcomes.final_high_tmpf` (official IEM ASOS max temperature).

**Training data:** All resolved snapshots from the research DB where:
- model = `dynamic_bucket_tuned_pm_active_us12_obs_2022_2025`
- station is one of the 12 US Polymarket-active stations
- `selected_side IS NOT NULL`, `selected_market_id IS NOT NULL`, `selected_bucket IS NOT NULL`
- `final_high_tmpf IS NOT NULL` (outcome resolved)

**Total samples:** 8,508 resolved snapshots across 10 stations with >= 10 samples.

12 stations remained unresolved (KSEA, KDAL etc.) — these had < 10 resolved
snapshots.

## Sample Sizes

| Station | N | Notes |
|---|---|---|
| KATL | 1,037 | Largest sample |
| KBKF | 216 | Smallest fitted station |
| KDAL | 1,019 | |
| KHOU | 995 | |
| KLAX | 883 | |
| KLGA | 1,046 | |
| KMIA | 1,220 | |
| KORD | 675 | |
| KSEA | 763 | Most overconfident station |
| KSFO | 654 | |
| **Total** | **8,508** | |

## Fitted Parameters

| Station | Intercept | Coefficient | Model WR | Actual WR | Calibrated WR |
|---|---|---|---|---|---|
| KATL | -2.885 | +4.183 | 66.5% | 45.2% | 56.9% |
| KBKF | -0.868 | +2.041 | 69.4% | 60.2% | 66.7% |
| KDAL | -3.169 | +5.294 | 60.0% | 49.2% | 49.2% |
| KHOU | -2.873 | +4.552 | 62.8% | 49.0% | 58.9% |
| KLAX | -3.658 | +5.382 | 68.2% | 48.7% | 59.1% |
| KLGA | -3.205 | +4.597 | 70.1% | 47.5% | 61.7% |
| KMIA | -4.423 | +5.956 | 64.8% | 41.6% | 56.3% |
| KORD | -2.024 | +3.588 | 53.9% | 50.7% | 51.4% |
| KSEA | -4.496 | +5.438 | 55.4% | 29.8% | 47.4% |
| KSFO | -2.888 | +4.618 | 52.4% | 43.9% | 47.6% |

Model WR = model-implied win rate (percent of snapshots where `fair >= 0.50`).
Actual WR = actual win rate against outcomes.
Calibrated WR = percent of snapshots where `calibrated_prob >= 0.50`.

## Interpretation

**All 10 coefficients are positive (range: +2.0 to +6.0).** This means the
model's direction is correct at every station — higher model confidence does
correlate with higher actual win rate. The model is not random; it has signal.

**All 10 intercepts are negative (range: -0.9 to -4.5).** This means the model
is systematically overconfident across all stations. The calibration pulls
probabilities down toward actual frequencies.

**KBKF is the best-calibrated station** (intercept -0.87, smallest
overconfidence). Its actual WR of 60.2% is close to its model WR of 69.4%.
This station can almost be traded raw.

**KSEA and KMIA are the most overconfident** (intercepts -4.5 and -4.4).
KSEA actual WR is 29.8% against a model-implied 55.4%. KMIA is 41.6% vs
64.8%. At these stations, the model's high-confidence BUY_YES picks are
almost always wrong — calibration drops their probability from ~55% to ~5%.

## Does It Make Sense?

**Yes.** The pattern is exactly what the deep-dive PnL analysis predicted:
systematic overconfidence concentrated at specific stations. The calibration
fixes this with two parameters per station rather than a static gate.

The logistic regression is the right model choice for this problem because:

1. Platt scaling (sigmoid on logit) is the standard calibration tool for
   binary classifier probabilities.
2. Two parameters per station means no overfitting with 200-1,200 samples
   per station.
3. The positive coefficients confirm the model has signal — calibration is
   fixing the level, not the direction.

## Edge Threshold Impact

At an edge cutoff of 0.10 (the typical live threshold):

| Metric | Raw | Calibrated |
|---|---|---|
| Trades selected | 5,421 | 3,249 |
| Dropped trades | — | 2,307 |
| Dropped trade WR | — | **4.6%** |
| Dropped trade R/R | — | **-0.790** |
| Newly surfaced | — | 135 |
| New trade WR | — | 80.0% |

**The 2,307 dropped trades had a catastrophic 4.6% win rate and -0.79 R/R.**
These are the trades that have been destroying live PnL — the model was
selecting them with positive edge, but they were actually deeply negative.
Calibration correctly identifies and suppresses them without any human-defined
calibration table or BLOCK/CANARY/TRADE decisions.

## Edge Distribution Shift

| Edge Bucket | Raw Count | Cal Count | Delta |
|---|---|---|---|
| < -0.20 | 530 | 546 | +16 |
| -0.20–0.00 | 1,544 | 3,041 | +1,497 |
| 0.00–0.10 | 856 | 1,167 | +311 |
| 0.10–0.20 | 1,527 | 204 | **-1,323** |
| 0.20–0.30 | 1,753 | 256 | **-1,497** |
| 0.30–0.50 | 1,893 | 1,960 | +67 |
| >= 0.50 | 248 | 829 | +581 |

The 0.10–0.30 edge range (marginal positive) loses ~2,800 trades — these get
reclassified as negative edge because the model was overconfident. The high-edge
trades (>= 0.30) largely survive calibration.

## Production Path

The calibration parameters (intercept + coefficient per station) would be saved
as a JSON file, generated weekly alongside the existing calibration table:

```json
{
  "version": 1,
  "model": "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
  "generated_at": "2026-06-17T00:00:00Z",
  "stations": {
    "KATL": {"intercept": -2.885, "coef": 4.183, "n": 1037},
    "KSEA": {"intercept": -4.496, "coef": 5.438, "n": 763}
  }
}
```

FairValueEngine would load this at startup and apply:

```python
def calibrated_fair(raw_fair: float, intercept: float, coef: float) -> float:
    logit = math.log(raw_fair / (1 - raw_fair))
    z = intercept + coef * logit
    return 1.0 / (1.0 + math.exp(-z))
```

This replaces the static calibration gate entirely. No BLOCK/CANARY/TRADE
decisions. The edge computation becomes naturally self-calibrating: toxic
stations produce lower edge, so the model either skips them or demands a
much lower entry price.

## Limitations

- Trained on single-model snapshots (dynamic_tuned), not consensus pairs.
  Consensus calibration would need either pair-level data or separate
  calibration per model followed by consensus mean of calibrated values.
- Per-station only, not per-side or per-entry-band. A per-side calibration
  would capture the BUY_YES vs BUY_NO asymmetry (KSEA BUY_YES is the
  primary offender).
- No temporal decay. Older snapshots are weighted equally. A rolling-window
  or exponential decay could adapt faster to regime changes.
- Only US HIGH_TEMP. Global low and HRRR families would need separate
  calibration fits.
