# Research Policy Report: May 8-10 Multimodel Run

Date reviewed: 2026-05-10

Active database:

```text
data/paper/research_2026-05-08_multimodel.sqlite
```

This report focuses on `research_policy_positions`, not raw repeated prediction snapshots. Policy rows are closer to trade-policy evaluation because they represent first qualifying entries by station/date or by station/date/strategy scope.

## Executive Read

Policy-level results are positive so far, but not yet proven.

Across resolved policy positions:

| Resolved positions | Hit rate | Total one-share PnL | Avg entry | PnL / position |
|---:|---:|---:|---:|---:|
| 155 | 38.7% | +13.810 | 0.298 | +0.089 |

The system is making some good weather bets, especially in 15-minute high-conviction policies that buy `NO` against buckets the model views as too hot. However, the result is uneven: `KSFO` is carrying a large share of the profit, while `KMIA` is materially negative in the resolved sample.

Current position resolution:

| Total policy positions | Resolved | Unresolved |
|---:|---:|---:|
| 244 | 155 | 89 |

The unresolved rows are mostly May 10 station-days. Do not treat this as a final performance read.

## Policy Ranking

| Policy | Resolved | Hit rate | PnL | Avg entry | Avg edge | Read |
|---|---:|---:|---:|---:|---:|---|
| `max_so_far_15m_first` | 12 | 25.0% | +1.955 | 0.087 | 0.913 | Cheap exact-bucket exposure; profitable but longshot-like |
| `mvp_hc_15m_first` | 8 | 75.0% | +1.650 | 0.544 | 0.369 | Best current weather-bet candidate |
| `dynamic_best_15m_first` | 10 | 20.0% | +1.399 | 0.060 | 0.337 | Cheap bucket picker; needs more sample |
| `consensus_best_15m_first` | 9 | 22.2% | +1.396 | 0.064 | 0.397 | Similar to dynamic best-bucket |
| `mvp_best_15m_first` | 10 | 20.0% | +1.369 | 0.063 | 0.489 | Similar cheap bucket exposure |
| `consensus_hc_15m_first` | 5 | 80.0% | +1.310 | 0.538 | 0.354 | Promising but tiny sample |
| `max_so_far_first` | 12 | 16.7% | +0.871 | 0.094 | 0.906 | Positive from cheap wins |
| `max_so_far_10m_first` | 12 | 16.7% | +0.694 | 0.109 | 0.891 | Weaker than 15m version |
| `mvp_hc_first` | 10 | 50.0% | +0.690 | 0.431 | 0.464 | Mixed, helped by cheap KSFO win |
| `consensus_per_strategy_first` | 18 | 27.8% | +0.580 | 0.244 | 0.386 | Broad mixed policy |
| `dynamic_hc_15m_first` | 8 | 62.5% | +0.560 | 0.555 | 0.300 | Positive, but less efficient than MVP HC 15m |
| `dynamic_hc_first` | 9 | 55.6% | +0.468 | 0.504 | 0.375 | Positive, includes weaker 10m entries |
| `dynamic_hc_10m_first` | 9 | 55.6% | +0.458 | 0.505 | 0.374 | Roughly same as dynamic HC first |
| `consensus_hc_first` | 7 | 57.1% | +0.330 | 0.524 | 0.368 | Positive but small |
| `consensus_hc_10m_first` | 7 | 57.1% | +0.320 | 0.526 | 0.366 | Positive but small |
| `mvp_hc_10m_first` | 9 | 44.4% | -0.240 | 0.471 | 0.435 | Only negative named policy so far |

## What Is Working

### High-Conviction Weather Bets

High-conviction policies look most like genuine weather edge. They are usually buying `NO` on a range bucket when the market appears too high relative to the model.

By strategy bucket:

| Strategy | Resolved | Hit rate | PnL | Avg entry |
|---|---:|---:|---:|---:|
| `HIGH_CONVICTION` | 79 | 58.2% | +5.876 | 0.508 |
| `BEST_BUCKET` | 39 | 17.9% | +4.436 | 0.064 |
| `MAX_SO_FAR` | 36 | 19.4% | +3.520 | 0.097 |
| `TAIL` | 1 | 0.0% | -0.022 | 0.022 |

The high-conviction result is more robust-looking than best-bucket or max-so-far because it is not relying on very cheap longshot pricing.

### 15-Minute Policies

The 15-minute delay is clearly better than 10-minute in the resolved data.

| Delay | Resolved | Hit rate | PnL | Avg entry |
|---|---:|---:|---:|---:|
| `15m` | 65 | 38.5% | +10.074 | 0.229 |
| `5m` | 10 | 70.0% | +3.862 | 0.314 |
| `10m` | 80 | 35.0% | -0.126 | 0.351 |

The 5-minute bucket looks strong but has only 10 resolved positions. The 10-minute bucket is essentially flat to negative.

### BUY YES Versus BUY NO

| Side | Resolved | Hit rate | PnL | Avg entry | Read |
|---|---:|---:|---:|---:|---|
| `BUY_YES` | 78 | 19.2% | +8.852 | 0.078 | Cheap exact-bucket wins |
| `BUY_NO` | 77 | 58.4% | +4.958 | 0.520 | More conventional directional weather edge |

Both sides are positive, but for different reasons. `BUY_YES` is mostly cheap range-bucket exposure. `BUY_NO` looks more like real forecasting edge because it wins more often at mid-market prices.

## What Is Not Working

### Station Concentration

The aggregate profit is not evenly distributed.

| Station | Resolved | Hit rate | PnL | Avg entry |
|---|---:|---:|---:|---:|
| `KSFO` | 25 | 88.0% | +15.107 | 0.276 |
| `KLGA` | 21 | 61.9% | +5.538 | 0.355 |
| `KDAL` | 17 | 58.8% | +3.536 | 0.380 |
| `KATL` | 33 | 33.3% | +0.712 | 0.312 |
| `KORD` | 7 | 0.0% | -0.072 | 0.010 |
| `KLAX` | 22 | 13.6% | -2.401 | 0.246 |
| `KMIA` | 30 | 3.3% | -8.610 | 0.318 |

`KSFO` is the biggest winner. `KMIA` is the biggest problem and is not just a small loss: high-conviction entries at `KMIA` were badly wrong in this slice.

### KMIA High-Conviction

`KMIA` high-conviction policies went 1-for-19 with -8.180 PnL. Most losses were `BUY_NO` against buckets such as `90-91F` or `92-93F`, while the final high landed inside or above those buckets.

This suggests one of:

- The model is underestimating Miami same-day upside.
- The station/market parsing around hot buckets needs extra review.
- The model's high-conviction threshold is too trusting in humid/hot-station regimes.

### Very Cheap Entries

Entry price below 0.05 did not work in the resolved sample:

| Entry band | Resolved | Hit rate | PnL | Avg entry |
|---|---:|---:|---:|---:|
| `<0.05` | 44 | 0.0% | -0.595 | 0.014 |
| `0.05-0.15` | 24 | 37.5% | +6.936 | 0.084 |
| `0.15-0.35` | 11 | 27.3% | +0.869 | 0.194 |
| `0.35-0.65` | 68 | 58.8% | +4.620 | 0.520 |
| `>=0.65` | 8 | 100.0% | +1.980 | 0.753 |

The profitable cheap-bucket region is not "anything cheap"; it is more like 5-15 cents so far. Sub-5-cent entries were all losers.

## Largest Wins And Losses

Largest wins were mostly `KSFO` exact bucket hits:

| Policy | Station/date | Strategy | Delay | Side | Bucket | Final | Entry | PnL |
|---|---|---|---|---|---|---:|---:|---:|
| `mvp_hc_first` | KSFO 2026-05-08 | `HIGH_CONVICTION` | 5m | `BUY_YES` | `58-59F` | 59 | 0.080 | +0.920 |
| `consensus_best_15m_first` | KSFO 2026-05-09 | `BEST_BUCKET` | 15m | `BUY_YES` | `62-63F` | 63 | 0.087 | +0.913 |
| `mvp_best_15m_first` | KSFO 2026-05-09 | `BEST_BUCKET` | 15m | `BUY_YES` | `62-63F` | 63 | 0.087 | +0.913 |
| `dynamic_best_15m_first` | KSFO 2026-05-09 | `BEST_BUCKET` | 15m | `BUY_YES` | `62-63F` | 63 | 0.087 | +0.913 |
| `max_so_far_15m_first` | KSFO 2026-05-09 | `MAX_SO_FAR` | 15m | `BUY_YES` | `62-63F` | 63 | 0.087 | +0.913 |

Largest losses were concentrated in `KMIA` high-conviction `BUY_NO` positions:

| Policy | Station/date | Strategy | Delay | Side | Bucket | Final | Entry | PnL |
|---|---|---|---|---|---|---:|---:|---:|
| `dynamic_hc_15m_first` | KMIA 2026-05-08 | `HIGH_CONVICTION` | 15m | `BUY_NO` | `92-93F` | 92 | 0.620 | -0.620 |
| `consensus_hc_first` | KMIA 2026-05-08 | `HIGH_CONVICTION` | 10m | `BUY_NO` | `92-93F` | 92 | 0.550 | -0.550 |
| `mvp_hc_first` | KMIA 2026-05-08 | `HIGH_CONVICTION` | 10m | `BUY_NO` | `92-93F` | 92 | 0.550 | -0.550 |
| `dynamic_hc_first` | KMIA 2026-05-08 | `HIGH_CONVICTION` | 10m | `BUY_NO` | `92-93F` | 92 | 0.550 | -0.550 |
| `mvp_hc_first` | KLAX 2026-05-09 | `HIGH_CONVICTION` | 5m | `BUY_NO` | `68-69F` | 69 | 0.480 | -0.480 |

## Conclusions

The policies are probably finding real signal, but the signal is not yet broad enough to trust blindly.

What looks good:

- `mvp_hc_15m_first` is the best current candidate for a real betting policy.
- 15-minute entries are materially better than 10-minute entries so far.
- `BUY_NO` high-conviction trades look like genuine weather bets, not just cheap lottery hits.
- `KSFO`, `KLGA`, and `KDAL` are working in this sample.

What needs caution:

- `KMIA` is a major failure case.
- `KSFO` contributes more than the total net PnL, so station concentration is high.
- Very cheap entries below 5 cents are losing.
- `BEST_BUCKET` and `MAX_SO_FAR` are positive but still longshot-like and need a larger sample.

Recommended next research steps:

1. Keep collecting until at least 50-100 resolved station-days before promoting a policy.
2. Add a station guardrail report, especially for `KMIA` high-conviction `BUY_NO`.
3. Compare `10m` versus `15m` entries after May 10 resolves; current data favors `15m`.
4. Consider a policy filter that excludes entries below 5 cents unless a separate calibration check supports them.
5. Track policy performance by station and side before treating aggregate PnL as deployable.
