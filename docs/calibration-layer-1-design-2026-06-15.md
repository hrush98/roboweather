# Layer 1 Calibration: Design & Implementation Plan

**Date:** 2026-06-15
**Status:** Design approved, implementation pending
**References:** `docs/deep-dive-pnl-root-cause-2026-06-15.md`, `scripts/calibration_table.py`

---

## 1. Background

### 1.1 The Problem

Live trading PnL has been negative (-$358 across 146 settled positions) despite positive research replay EV. The June 15 execution fixes (entry-anchored FAK/retry/ladder) addressed overpayment on fills but did not fix the primary problem: **severe model miscalibration**.

The models output uncalibrated probabilities used directly as "fair value":

```
edge = model_probability - ask_price
```

The calibration error is structural:
- Model claims 79-93% win probability across most buckets
- Actual win rates are 0-83%, with the worst errors concentrated in the 0.35-0.55 entry band
- At some station/band combinations (KATL BUY_NO 0.35-0.45), the model overestimates win probability by 68 percentage points
- Higher claimed edge correlates with LOWER actual win rate — the model's confidence is inverted

### 1.2 What The Execution Fixes Did And Didn't Do

The June 15 changes (FAK capped at entry+1c, retry at entry+1c, resting ladder at entry±2c) prevent the worst overpayment scenarios. They are correct and should be kept.

They did not fix: a model that selects negative-EV trades with high confidence. Tightening execution on bad trade selection loses money more precisely.

### 1.3 Key Finding: Slippage Is Secondary

Even if every position filled exactly at `entry_price` (zero slippage):
- Consensus no-tiny: +$205.78 (vs actual +$192.68) — slippage cost $13.10
- Old dynamic core: -$88.93 (vs actual -$135.18) — still negative
- NGBoost BUY_YES: -$94.12 (vs actual -$115.55) — still negative
- Global low canary: -$166.14 (vs actual -$199.53) — still negative
- Global low MVP: -$75.79 (vs actual -$100.00) — still negative

Slippage adds 6-14% to losses but does not flip any strategy from negative to positive.

---

## 2. Calibration Approach

### 2.0 Scope: Defensive Gate, Not A Scaling Engine

Layer 1 calibration is a **defensive live-trading gate**. Its job is to prevent
known bad station/side/entry-band/model-family combinations from receiving
normal live risk. It is not a proof that a sleeve is profitable, not a reason to
increase caps, and not a replacement for model-level probability calibration.

The gate answers one narrow question at live-entry time:

```text
Has this model-family / station / side / entry-band historically been bad enough
that we should block it or force canary size?
```

It deliberately does **not** answer:

- whether a replay slice should be promoted;
- whether global low settlement semantics are correct;
- whether filled live rows match raw replay rows;
- whether a policy should be scaled;
- whether raw model fair probabilities are calibrated.

Those questions belong to the whole-chain truth report and portfolio promotion
report. Negative live-settlement evidence overrides positive weather-outcome
calibration. In particular, global-low buckets that score well against
`station_date_outcomes` must still remain canary-sized or blocked if Polymarket
settlement disagrees with the weather-outcome interpretation.

### 2.1 Three Options Considered

| Option | Where | What It Does | Complexity |
|---|---|---|---|
| **A** | Model-level | Recalibrate raw model probability before consensus | High (touches fair value engine, needs per-model cal) |
| **B** | Policy-level | Block candidate trades based on historical station/side/band R/R | Low (JSON gate, no model changes) |
| **C** | Consensus-level | Scale consensus edge by calibration ratio | Medium (needs enough consensus pairs) |

### 2.2 Decision: Option B

Option B chosen because:
1. Simplest to implement (~60 lines in live engine + script output flag)
2. Directly answers the question: "does this type of trade make money?"
3. Captures execution artifacts that pure model calibration misses
4. Works with single-model calibration data (2,954 rows vs 165 consensus pairs)
5. Can coexist with Option A if added later

### 2.3 Model-Family Scoping

Each live policy trades a specific model family. The calibration gate checks the family relevant to the policy:

| Live Policy | Model Family | Calibration Table |
|---|---|---|
| Consensus no-tiny (US high-temp) | obs | `calibration_obs.json` |
| Global low canary / MVP / tiny tail | global_low | `calibration_global_low.json` |
| HRRR inland overlay (future) | hrrr_v2 | `calibration_hrrr_v2.json` |

A station/band that's BLOCK for obs may be TRADE for hrrr_v2 (e.g., KATL BUY_NO 0.35-0.45: obs R/R -0.091, hrrr_v2 R/R +0.455). The per-family scoping prevents cross-contamination.

### 2.4 Calibration Data Source

The research DB's `prediction_snapshots` joined with `station_date_outcomes` provides ground-truth calibration data:

```
Family        Snapshots    Market Dates    Notes
obs           2,954        36              Full per-station, per-band coverage
global_low      303        19              Station-level, thinner bands
hrrr_v2       1,066        11              Growing, usable for inland stations
```

Single-model snapshots from the primary model in each family are used (dynamic_tuned for obs, mvp for global_low, dynamic_tuned_hrrr_v2 for hrrr_v2). The live system trades consensus policies, but single-model data provides 10-15x more calibration volume than consensus pairs (which only exist when both models agree on direction — ~30% of station/dates).

---

## 3. Calibration Script

### 3.1 Location

`scripts/calibration_table.py`

### 3.2 Usage

```bash
# Single family
python scripts/calibration_table.py \
    --db ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite \
    --family obs --single-model \
    --out ~/.local/state/roboweather/calibration_obs.json

# All families at once
python scripts/calibration_table.py \
    --db ... --family all --single-model \
    --out ~/.local/state/roboweather/calibration.json
```

### 3.3 Output Format

```json
{
  "version": 1,
  "generated_at": "2026-06-15T12:00:00Z",
  "families": {
    "obs": {
      "label": "US High-Temp Obs-Only",
      "market_dates": 36,
      "buckets": {
        "KATL": {
          "BUY_NO": {
            "0.35-0.45": {"decision": "WATCH", "n": 64, "avg_rr": -0.091, "win_pct": 37.5},
            "0.45-0.55": {"decision": "BLOCK", "n": 85, "avg_rr": -0.467, "win_pct": 27.1}
          }
        }
      }
    },
    "global_low": {
      "label": "Global Low-Temp Celsius",
      "market_dates": 19,
      "buckets": { ... }
    }
  }
}
```

### 3.4 Decision Thresholds

| Decision | R/R Threshold | Min N | Live Behavior |
|---|---|---|---|
| TRADE | >= 0.15 | >= 15 | Normal sizing |
| CANARY | > 0 | >= 5 | Downsize to canary_notional_usd (e.g., $5) |
| WATCH | > -0.10 | >= 5 | Pass through (insufficient evidence to block) |
| BLOCK | <= -0.10 | >= 5 | Reject with reason CALIBRATION_BLOCK |
| INSUFFICIENT_DATA | (any) | < 5 | Pass through |

Conservative asymmetry: it's better to miss a +EV trade (false BLOCK) than to take a -EV trade (false TRADE). CANARY provides an escape hatch for buckets that need more data.

### 3.5 Regeneration Cadence

Weekly, after new market dates resolve (Sunday/Monday):

```bash
python scripts/calibration_table.py --db ... --family all --single-model \
    --out ~/.local/state/roboweather/calibration.json
```

The live engine loads the file on restart.

---

## 4. Live Engine Integration

### 4.1 Files Changed

- `weather_trader/live/execution.py` — three touch points
- `scripts/calibration_table.py` — add `--out` and `--family all` flags

### 4.2 Config Addition

```python
@dataclass(frozen=True)
class LiveExecutionConfig:
    # ... existing fields ...
    calibration_path: Path | None = None
    calibration_canary_notional_usd: float = 5.0
    calibration_unknown_behavior: str = "allow"  # allow only for v1

CALIBRATION_FAMILY_MAP = {
    "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first": "obs",
    "global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first": "global_low",
    "global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first": "global_low",
    "global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first": "global_low",
    "pm_us12_dynamic_tuned_hc_late_entry_05_10_buy_no_by_bucket_side_delay_first": "obs",
}
```

Use `Path | None` to match the rest of live config. The config must be inert by
default: if `calibration_path` is unset, live behavior is unchanged.

### 4.3 Engine Changes

**Init:** Load calibration JSON if `calibration_path` is provided. Invalid JSON,
missing `version`, or missing `families` should raise at startup rather than
silently running without the gate. If the path is unset, use an empty
calibration object and record `calibration_enabled=false` in cycle debug.

**Do not mutate frozen dataclasses.** `LiveCandidate` and `LiveStrategyPlan` are
frozen. The calibration hook must return a result and, for CANARY, a copied
candidate/plan. Do not assign to `candidate.plan`.

Recommended shape:

```python
@dataclass(frozen=True)
class CalibrationDecision:
    candidate: LiveCandidate
    reject_reason: str | None
    metadata: dict[str, Any]

def _apply_calibration(self, candidate: LiveCandidate) -> CalibrationDecision:
    metadata = self._calibration_metadata(candidate)
    decision = metadata.get("decision")

    if decision == "BLOCK":
        return CalibrationDecision(candidate, "CALIBRATION_BLOCK", metadata)

    if decision == "CANARY":
        capped_notional = min(
            candidate.plan.target_notional_usd,
            self.config.calibration_canary_notional_usd,
        )
        plan = replace(candidate.plan, target_notional_usd=capped_notional)
        metadata["calibration_target_notional_before"] = candidate.plan.target_notional_usd
        metadata["calibration_target_notional_after"] = capped_notional
        return CalibrationDecision(replace(candidate, plan=plan), None, metadata)

    return CalibrationDecision(candidate, None, metadata)
```

**Run order in `run_once`:**

```text
for candidate in candidates:
    market/book checks -> preliminary reject_reason
    calibration -> may set CALIBRATION_BLOCK or return canary-capped candidate
    sizing -> uses canary-capped target if applicable
    live position insert -> raw_json includes calibration metadata
    rejected position/order attempt -> final_reason CALIBRATION_BLOCK when blocked
    submit -> unchanged for allowed candidates
```

Calibration should run after book/market sanity checks and before sizing. That
keeps missing-book/stale-book failures distinct, while ensuring risk caps and
depth sizing operate on the canary-capped target.

### 4.4 Decision Semantics And Metadata

Every candidate whose strategy maps to a calibration family must get calibration
metadata in `LivePolicyPosition.raw_json`, even when allowed. Minimum fields:

```json
{
  "calibration": {
    "enabled": true,
    "family": "obs",
    "station": "KATL",
    "side": "BUY_NO",
    "entry_band": "0.35-0.45",
    "decision": "BLOCK",
    "reason": "bucket_match",
    "n": 64,
    "market_dates": 18,
    "avg_rr": -0.091,
    "win_pct": 37.5,
    "generated_at": "2026-06-15T12:00:00Z"
  }
}
```

Decision behavior:

| Decision | Live behavior | Required metadata reason |
|---|---|---|
| `BLOCK` | Reject before sizing/submission with `CALIBRATION_BLOCK` | `bucket_match` |
| `CANARY` | Copy candidate with target capped to `calibration_canary_notional_usd` | `bucket_match` |
| `WATCH` | Allow normal sizing, but log bucket stats | `bucket_match` |
| `TRADE` | Allow normal sizing, but log bucket stats | `bucket_match` |
| `INSUFFICIENT_DATA` | Allow for v1, log as unproven | `bucket_match` |
| Missing family mapping | Allow, log `family_unmapped` in cycle debug | `family_unmapped` |
| Missing station/side/band bucket | Allow for v1, log `bucket_missing` | `bucket_missing` |
| Calibration file disabled | Allow, log `disabled` | `disabled` |

Unknowns are allowed in v1 only to avoid accidental full-system shutdown. They
must be counted in cycle debug and visible in position raw JSON when a family is
mapped. If missing-bucket counts are high, the weekly operator review should
regenerate or expand the table before trusting the gate.

**Logging:** Rejected candidates get `CALIBRATION_BLOCK` as the reject reason in
`live_order_attempts.final_reason`, and the order attempt raw payload should
include the same calibration metadata.

### 4.5 What Gets Blocked Initially (Obs Family)

19 buckets across 9 stations, covering the worst-performing combinations:

| Station | Side | Band | R/R | Primary Offender |
|---|---|---|---|---|
| KATL | BUY_NO | 0.35-0.45 | -0.091 | Old dynamic core, consensus no-tiny |
| KATL | BUY_NO | 0.45-0.55 | -0.467 | Old dynamic core |
| KLAX | BUY_NO | 0.25-0.35 | -0.323 | Edge case |
| KLAX | BUY_NO | 0.45-0.55 | -0.211 | Consensus no-tiny edge |
| KLGA | BUY_NO | 0.25-0.35 | -0.349 | Old dynamic core |
| KLGA | BUY_NO | 0.35-0.45 | -0.457 | Old dynamic core (major loser) |
| KLGA | BUY_NO | >=0.55 | -0.124 | Consensus canary |
| KMIA | BUY_NO | 0.25-0.35 | -0.625 | Old dynamic core |
| KMIA | BUY_NO | 0.35-0.45 | -0.544 | Old dynamic core (major loser) |
| KMIA | BUY_NO | 0.45-0.55 | -0.313 | Consensus no-tiny |
| KORD | BUY_NO | 0.45-0.55 | -0.431 | Old dynamic core |
| KSEA | BUY_NO | 0.25-0.35 | -1.000 | Zero wins |
| KSEA | BUY_NO | 0.35-0.45 | -0.560 | Old dynamic core |
| KSEA | BUY_NO | 0.45-0.55 | -0.483 | Consensus no-tiny |
| KDAL | BUY_NO | 0.35-0.45 | -0.773 | Edge case |
| KDAL | BUY_YES | <0.15 | -1.000 | Zero wins |
| KHOU | BUY_NO | 0.35-0.45 | -0.503 | Old dynamic core |
| KSFO | BUY_YES | <0.15 | -0.490 | Old dynamic core |
| KSFO | BUY_NO | 0.35-0.45 | -0.643 | Edge case |

The consensus no-tiny core currently trades through many of these (KATL 0.35-0.45, KLAX 0.45-0.55, KLGA >=0.55, KMIA 0.45-0.55, KSEA 0.45-0.55). These would be blocked immediately.

### 4.6 Global Low-Temp

The global_low family has **zero BLOCK buckets** after calibration. The table serves as a safety net:

| Station | Band | Decision | R/R |
|---|---|---|---|
| VHHH | 0.45-0.55 | TRADE | +0.962 |
| RJTT | 0.45-0.55 | TRADE | +0.867 |
| RKSI | >=0.55 | TRADE | +0.480 |
| VHHH | >=0.55 | TRADE | +0.271 |
| LFPB | >=0.55 | TRADE | +0.268 |
| ZSPD | >=0.55 | CANARY | +0.135 |
| RJTT | >=0.55 | WATCH | -0.063 |

The one WATCH bucket (RJTT >=0.55) passes through at normal sizing — it's mildly negative (-0.063 R/R on n=28) but not strong enough to block.

---

## 5. Calibration vs Research/Live Split

### 5.1 Research Loop Provides Calibration Data

The research loop collects `prediction_snapshots` paired with `station_date_outcomes`. This provides:
- Ground-truth weather outcomes (what actually happened)
- Complete decision universe (every model prediction, not just filled trades)
- Growing dataset (36+ market dates for obs, 19+ for global_low)

### 5.2 Live Loop Provides Execution Validation

The live `live_policy_positions` and `live_order_attempts` tables provide:
- Actual fill prices vs scored entry prices
- Rejection reasons
- Polymarket settlement vs weather-outcome comparison

### 5.3 Both Feed Calibration Regeneration

Weekly regeneration combines:
1. Research DB for statistical power (model calibration)
2. Live DB for reconciliation (settlement mismatches, execution drift)

The calibration is scored against weather outcomes (research DB source of truth), with live settlement mismatches tracked separately as a known risk factor for global low-temp.

---

## 6. Future Upgrades

### 6.1 Option A (Model-Level Calibration)

Once Option B is stable, add probability calibration curves per model per station. This would fix edge computation at the source rather than filtering at the output. The calibration script can output Platt scaling parameters alongside the BLOCK/TRADE table.

### 6.2 HRRR Family Activation

The hrrr_v2 family calibration shows positive R/R at inland stations where obs is negative (KATL 0.35-0.45: +0.455 vs -0.091). When the HRRR inland overlay goes live, its own calibration table gates it independently.

### 6.3 Dynamic Thresholds

Replace fixed R/R thresholds with confidence-interval-based decisions (only BLOCK if the upper bound of the 95% CI is below zero). This handles small-sample buckets more rigorously.

### 6.4 Live Candidate Persistence

The June 15 journal entry identifies that live doesn't persist the full candidate universe. Adding live prediction snapshots would allow calibration against the exact live decision set, closing the replay-vs-live gap.

---

## 7. Implementation Checklist

Hand this section to the implementing agent. The change is not complete until all
items are true.

### 7.1 Script Work

- Add `--out` JSON output to `scripts/calibration_table.py`.
- Add `--family all` to emit one combined file with `families`.
- Include `version`, `generated_at`, input DB path, family mode, thresholds,
  `single_model`, `per_station`, and per-family row/date counts.
- Preserve the existing human-readable table output when `--out` is omitted.
- Add tests for `entry_band`, decision thresholds, JSON shape, single-family
  output, and all-family output.

### 7.2 Live Engine Work

- Add inert config fields: `calibration_path`,
  `calibration_canary_notional_usd`, and optional unknown behavior.
- Load and validate JSON once during `LiveExecutionEngine.__init__`.
- Apply calibration after `_candidate_reject_reason` and before
  `_size_candidate`.
- Return a copied canary candidate/plan; never mutate frozen dataclasses.
- Record calibration metadata in every mapped candidate position.
- Record `CALIBRATION_BLOCK` in rejected positions and order attempts.
- Add cycle-debug counters for `BLOCK`, `CANARY`, `WATCH`, `TRADE`,
  `INSUFFICIENT_DATA`, `bucket_missing`, `family_unmapped`, and `disabled`.

### 7.3 Required Tests

- No calibration path leaves live behavior unchanged.
- Bad calibration JSON fails engine startup.
- `BLOCK` candidate is inserted as rejected with `CALIBRATION_BLOCK`.
- `CANARY` candidate sizes from the capped target and preserves normal risk caps.
- `WATCH` and `TRADE` pass through with metadata.
- Missing bucket passes through with `bucket_missing` metadata.
- Unmapped strategy passes through and increments `family_unmapped`.
- Calibration metadata appears in `LivePolicyPosition.raw_json` and rejected
  `LiveOrderAttempt.raw_payload`.

### 7.4 Non-Goals For This Change

- Do not change fair-value computation.
- Do not change model artifacts.
- Do not scale any policy because a bucket is `TRADE`.
- Do not treat global-low weather-outcome calibration as settlement proof.
- Do not remove the portfolio promotion or whole-chain truth report gates.
