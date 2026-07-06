# Calibration System Audit — 2026-06-17

## VERDICT ON CALIBRATION

**The calibration system is not garbage. It's the right idea deployed at the wrong aggression level, aimed at the wrong primary problem.**

The concept of station/side/entry-band gating is sound — the June 15 deep-dive proved the models are severely miscalibrated (edge inverted: higher confidence = lower win rate). But the current implementation has three concrete problems I can point to from live data.

---

## WHAT THE WHOLE-CHAIN REPORT REVEALS (June 15-16)

Actual output from `scripts/whole_chain_truth_report.py --start-date 2026-06-15`:

| Sleeve | Live-Selected Replay | Filled @ Actual | Actual PnL | Why Gap? |
|---|---|---|---|---|
| **US consensus no-tiny** | +$573.31 / 1.433 R/R (4 candidates) | -$100.00 / -1.000 R/R (1 fill) | -$100.00 | Fill selection bias + calibration blocked 2 |
| **Global low canary** | +$228.45 / 0.388 R/R (6) | +$6.22 / 0.050 R/R (5) | -$18.77 | Settlement mismatches: -$14.05 |
| **Global low MVP add-on** | +$395.96 / 1.980 R/R (4) | +$189.62 / 1.416 R/R (4) | -$28.82 | Settlement mismatches: -$83.87 |

Calibration decisions for consensus no-tiny: 2 BLOCK, 2 MISSING (out of 4 candidates).

### The three failure modes, ranked by dollars destroyed:

#### #1 — SETTLEMENT MISMATCHES (largest, ~$98 lost)

Polymarket settles differently than weather-outcome scoring. Global low MVP: filled-at-actual should have made +$189.62 (1.416 R/R). Instead lost $28.82 because 3 positions where weather says BUY_NO won, Polymarket settled BUY_YES. $83.87 evaporated.

This is NOT a calibration issue — the picks were great, the fills were good (0.045c avg slippage, 1.416 R/R at actual fill prices). The settlement source is wrong for international stations.

The June 16 journal claims this was fixed for RJTT/RKSI/VHHH/ZSPD by using Polymarket Gamma's settled bucket instead of weather-outcome scoring. But those sleeves were retired from live trading the same day. The fix exists; it just isn't being used live because the whole global low stack was deactivated.

#### #2 — ADVERSE FILL SELECTION (second, ~$200+ opportunity cost)

US consensus no-tiny: 4 candidates reserved, live-selected replay +$573.31 on all 4. Only 1 filled. The one that filled lost $100. The 3 that didn't fill had +$673.31 combined replay value at live-selected. The filled-vs-unfilled replay comparison: filled = -1.000 R/R, unfilled = +2.244 R/R.

The market is selectively filling the bad trade and keeping the good ones unavailable. This is the "47.6% fill rate" problem from the journal — the 52.4% that doesn't fill may be the better-priced positions. This is a book-depth/liquidity issue, not calibration.

#### #3 — CALIBRATION BLOCKING GOOD CANDIDATES (third, but real)

2 of the 4 consensus no-tiny candidates were BLOCK'd by calibration. Since all 4 replayed at +$573.31 combined, the blocked candidates were statistically among the profitable ones. The obs family calibration table has only 3 TRADE buckets and 19 BLOCK buckets across all stations — the TRADE-only gate strangles the core strategy.

---

## IS THE CALIBRATION APPROACH ITSELF WRONG?

**The approach is right, the operating mode is wrong.**

The design doc (`docs/calibration-layer-1-design-2026-06-15.md`) explicitly says: "Layer 1 calibration is a **defensive live-trading gate**." It answers: "Has this station/side/band historically been bad enough to block?" This is a valid question given the data (KLGA 26% WR, KMIA 15.8%, KSEA 0% WR in some bands).

But the implementation leap from June 15 (allow/canary/block sizing) to June 16 (hard TRADE-only gate) went too far. The original design had a measured response: BLOCK → reject, CANARY → downsize to $5, WATCH/TRADE → normal. The June 16 gate mode says: TRADE → normal, everything else → BLOCK. That's a completely different calibration philosophy and it's too aggressive for the current calibration data quality.

### Specific problems with the gate-mode approach:

**1. Sample size and source mismatch.** The obs family calibration uses single-model snapshots (dynamic_tuned only), not consensus pairs. The design doc admits: "single-model data provides 10-15x more calibration volume than consensus pairs." But single-model calibration doesn't match what the live system trades (bucket consensus of dynamic_tuned + catboost). The calibration and the live system are looking at different signals.

**2. Missing buckets = blocked.** 2 of 4 consensus no-tiny candidates hit `bucket_missing` in the calibration table. Missing gets treated as INSUFFICIENT_DATA, which in gate mode means BLOCK. So unknown station/side/bands get blocked by default.

**3. No edge discrimination within bands.** The calibration buckets by entry band (0.35-0.45, 0.45-0.55, etc.) and applies one decision per band. But the deep-dive PnL report showed the model's own edge is actually the best discriminator — edge 0.29 has 66.7% WR, edge 0.43 has 39.3% WR. A single BLOCK on all 0.35-0.45 entries throws out the baby with the bathwater.

---

## ON ACADEMIC PRECEDENT

The key concept here is "probability calibration in betting markets" — it's well-studied. The canonical finding (Dawid 1982, "The Well-Calibrated Bayesian") is that subjective probabilities should match empirical frequencies: when a forecaster says "80% chance," it should happen 80% of the time. Your models claim 79-93% but deliver 0-83% — that's classic miscalibration.

The standard fixes are:

1. **Platt scaling** (Platt 1999) — fit a sigmoid to map model scores to calibrated probabilities. This is exactly what the calibration_table.py data enables. You already compute `overconfidence_pp` (model_wr minus actual win_pct) for every station/side/band.
2. **Isotonic regression** — non-parametric calibration. Your models already use `CalibratedClassifierCV` (mentioned in the deep-dive) but it's global, not per-station.
3. **Beta calibration** (Kull et al. 2017) — specifically designed for binary classifiers, better than Platt for your use case since bucket outcomes are binary (in-bucket or not).

The academic consensus is clear: **recalibrating the probability at the model output level is more robust than filtering trades at the policy level.** Your calibration gate is filtering after the fact; Platt scaling would fix the edge computation so the bad trades never get selected in the first place.

---

## WHERE TO GO: DEVELOPMENT PATH

### Immediate (today/tomorrow) — fix the gate to not strangle the core

**Option A: Revert to allow/canary/block sizing mode.**
The June 15 original design was better calibrated to the data quality. Keep BLOCK for the confirmed-toxic buckets (19 buckets with R/R < -0.10, from the design doc), downsize CANARY buckets to $5-10, pass WATCH/TRADE through. This preserves the defensive function without strangling the core.

Implementation: ~20-line change in `LiveExecutionEngine._apply_calibration()` in `weather_trader/live/execution.py`. Restore the CANARY downsizing path and allow WATCH/INSUFFICIENT_DATA through instead of blocking them.

**Option B: Add an edge-aware override.**
Keep the gate but add: "Even if calibration says BLOCK, allow if model edge >= 0.X AND consensus agreement is high." The data shows high-edge trades are the profitable ones even at "bad" stations.

**Recommendation: Option A first** (lowest risk, revert to existing code path), then **add Option B** once you have a week of canary-sized data from the previously-blocked buckets.

### Short-term (this week) — fix the actual profit killers

**Fix #1: Reconcile global low settlement before re-enabling.**
The June 16 fix (using Polymarket Gamma for international stations) is already coded in the resolver. The global low sleeves were retired because "adverse live fill selection and unresolved calibration issues." But the whole-chain report shows the fill selection for global low MVP was actually GOOD — 4/4 candidates filled, replay at actual fill was +$189.62. The only problem was settlement.

Action: Compare global low MVP replay vs Polymarket-settled PnL for resolved rows AFTER June 16 to verify the fix worked. If it did, re-enable global low BUY_NO at $25-50 as a canary.

**Fix #2: Build a fill-selection-bias alert.**
The consensus no-tiny data is screaming: filled candidates are worse than unfilled. Add a check in the live loop or a post-hoc script: "if filled-subset replay R/R < 50% of unfilled-subset replay R/R, flag for review." This is your early warning that the book is adverse-selecting against you.

**Fix #3: Regenerate calibration with consensus-pair data.**
Run `calibration_table.py` without `--single-model` to use consensus pairs (smaller sample but matches what live trades). Or build the calibration from `research_policy_positions` for the exact consensus policies, which includes execution metadata like depth and fillability.

### Medium-term (next 2 weeks) — structural improvements

**1. Model-level calibration (Option A from the design doc).**
This is the real fix. Instead of filtering at the output, fix edge computation at the source:
```
edge = calibrated_model_probability - ask_price
calibrated_model_probability = platt_scale(model_probability, station, side)
```

The calibration script already computes the data needed: `model_wr` (model-implied win%) vs actual `win_pct` per station/side/band. The `overconfidence_pp` field literally tells you how many percentage points the model is overconfident. You can train Platt scaling parameters from this data with a simple logistic regression on model_probability → actual_outcome, per station.

This is a ~50-line change to the FairValueEngine, reusing the calibration table data you already generate. It doesn't require new model training — just a post-processing layer on the existing probability output.

**2. Persist live prediction snapshots.**
The journal (June 15) identifies this: live doesn't persist the full candidate universe, making live-vs-replay audits incomplete. Without this, you can't tell if calibration is blocking good trades or bad trades — you can only see what survived. The whole-chain report for consensus no-tiny shows 4 reserved, but you don't know what the scanner rejected pre-reservation.

**3. Depth-aware sizing multipliers.**
The consensus no-tiny core should be the most reliable sleeve, but only 1 of 4 candidates filled. The 3 unfilled had +$673 replay value. If the book is too thin to fill at the entry price, consider: (a) posting passive orders deeper in the book rather than FAK, (b) using the resting ladder as the primary execution path for thin books, or (c) accepting that fill rate will be low and sizing up the target to compensate (more risk per fill, fewer fills).

### What NOT to do

- Do NOT deactivate calibration. The 19 BLOCK buckets (KSEA BUY_NO 0.25-0.35 at -1.000 R/R, KMIA BUY_NO 0.25-0.35 at -0.625) are genuinely toxic. Removing the gate entirely would re-expose the system to known losers.
- Do NOT add more model families to live execution. The current 4-policy stack (consensus no-tiny, moonshot, 2x HRRR inland) already has unresolved fill-selection and settlement issues. Fix the existing sleeves first.
- Do NOT scale sizing. Consensus no-tiny at $100 is fine, but any size-up before fixing fill-selection bias just scales the adverse selection problem.

---

## BOTTOM LINE

Your system is closer to profitable than it feels. The execution engine is solid. The consensus no-tiny core replayed at +$573.31 on just 4 candidates in two days (1.433 R/R). The global low MVP replayed at +$189.62 on 4/4 filled candidates (1.416 R/R at actual fill prices). The gap from replay to reality is dominated by settlement mismatches and adverse fill selection — both fixable — not by fundamental lack of edge.

The calibration gate, in its current TRADE-only mode, is strangling the one sleeve that's actually working (consensus no-tiny). Soften it back to allow/canary/block sizing, verify the global low settlement fix, and add model-level calibration as the next structural improvement. The path to consistent profitability runs through probability calibration at the fair value engine, not through harder gates at the execution layer.
