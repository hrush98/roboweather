#!/usr/bin/env python3
"""Demonstration: model-level probability calibration (Platt scaling) per station.

Shows before/after edge comparison using logistic regression to recalibrate
raw model fair values against actual outcomes, per station.

This is a demo only. It reads from the research DB, fits per-station calibration,
and prints a comparison of original vs calibrated edge, plus the trades that
would flip from selected to rejected (or vice versa) at typical edge thresholds.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

# ── Helpers (same bucket parsing as calibration_table.py) ──────────────────

def parse_bucket(bucket_str: str) -> tuple[float, float] | None:
    if not bucket_str:
        return None
    text = str(bucket_str).replace("F", "").replace("C", "").strip()
    if ">=" in text:
        m = re.search(r">=(\d+\.?\d*)", text)
        if m:
            return (float(m.group(1)), 200.0)
    if "-" in text:
        parts = text.split("-")
        if len(parts) == 2:
            return (float(parts[0]), float(parts[1]))
    return None


def score_snapshot(
    entry: float, side: str, fair: float, bucket_str: str, final_temp: float
) -> tuple[bool, float, float | None]:
    """Return (won, rr, model_probability) for one snapshot."""
    bounds = parse_bucket(bucket_str)
    if bounds is None:
        return (False, 0.0, None)
    low, high = bounds
    in_bucket = low <= final_temp <= high
    won = (side == "BUY_NO" and not in_bucket) or (side == "BUY_YES" and in_bucket)
    rr = (1 - entry) / entry if won else -1.0
    return (won, rr, fair)


# ── Core ──────────────────────────────────────────────────────────────────

@dataclass
class CalibrationResult:
    station: str
    side: str
    n_samples: int
    intercept: float
    coef: float
    raw_accuracy: float
    calibrated_accuracy: float


def build_calibration(db_path: str, model_name: str, outcome_column: str, min_samples: int = 10) -> dict[str, CalibrationResult]:
    """Fit per-station logistic regression on raw fair value -> actual outcome."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        f"""
        SELECT ps.station, ps.selected_side, ps.selected_bucket,
               sdo.{outcome_column} as final_temp,
               CASE WHEN ps.selected_side = 'BUY_YES'
                    THEN ps.selected_yes_ask
                    ELSE ps.selected_no_ask
               END as entry,
               CASE WHEN ps.selected_side = 'BUY_YES'
                    THEN ps.selected_fair_yes
                    ELSE ps.selected_fair_no
               END as fair,
               ps.selected_edge
        FROM prediction_snapshots ps
        JOIN station_date_outcomes sdo
          ON ps.station = sdo.station AND ps.market_date = sdo.market_date
        WHERE ps.model_name = ?
          AND ps.selected_side IS NOT NULL
          AND ps.selected_market_id IS NOT NULL
          AND ps.selected_bucket IS NOT NULL
          AND sdo.{outcome_column} IS NOT NULL
        """,
        (model_name,),
    ).fetchall()

    db.close()

    # Group by station
    by_station: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        entry = float(r["entry"]) if r["entry"] is not None else None
        fair = float(r["fair"]) if r["fair"] is not None else None
        if entry is None or fair is None or entry <= 0 or fair <= 0 or fair >= 1:
            continue
        by_station[str(r["station"])].append({
            "station": str(r["station"]),
            "side": str(r["selected_side"]),
            "entry": entry,
            "fair": fair,
            "edge": float(r["selected_edge"] or 0),
            "bucket": str(r["selected_bucket"]),
            "temp": float(r["final_temp"]),
        })

    results: dict[str, CalibrationResult] = {}
    for station, samples in sorted(by_station.items()):
        if len(samples) < min_samples:
            continue

        X = np.array([[s["fair"]] for s in samples])
        y = np.array([
            1.0 if score_snapshot(s["entry"], s["side"], s["fair"], s["bucket"], s["temp"])[0]
            else 0.0
            for s in samples
        ])

        # Skip if all same class
        if len(set(y)) < 2:
            continue

        model = LogisticRegression(C=1e10, solver="lbfgs")  # C=large ~ no regularization
        model.fit(X, y)

        raw_preds = (np.array([s["fair"] for s in samples]) >= 0.5).astype(float)
        calibrated_probs = model.predict_proba(X)[:, 1]
        cal_preds = (calibrated_probs >= 0.5).astype(float)

        results[station] = CalibrationResult(
            station=station,
            side="both",
            n_samples=len(samples),
            intercept=float(model.intercept_[0]),
            coef=float(model.coef_[0][0]),
            raw_accuracy=float(np.mean(raw_preds == y)),
            calibrated_accuracy=float(np.mean(cal_preds == y)),
        )

    return results


def calibrate_fair(raw_fair: float, intercept: float, coef: float) -> float:
    """Apply Platt scaling: P(win) = sigmoid(intercept + coef * logit(raw_fair))."""
    # logit(p) = ln(p/(1-p))
    if raw_fair <= 0.001:
        raw_fair = 0.001
    if raw_fair >= 0.999:
        raw_fair = 0.999
    logit = np.log(raw_fair / (1 - raw_fair))
    z = intercept + coef * logit
    return float(1.0 / (1.0 + np.exp(-z)))


def demo_edge_comparison(
    db_path: str,
    model_name: str,
    outcome_column: str = "final_high_tmpf",
    min_samples: int = 10,
    edge_threshold: float = 0.10,
    top_n: int = 20,
):
    """Load data, fit calibration, print before/after edge comparison."""
    print("=" * 110)
    print(" MODEL-LEVEL PROBABILITY CALIBRATION DEMO")
    print("=" * 110)
    print(f"  Model:         {model_name}")
    print(f"  Outcome col:   {outcome_column}")
    print(f"  DB:            {db_path}")
    print(f"  Min samples:   {min_samples}")
    print(f"  Edge cutoff:   {edge_threshold:+.2f}")
    print()

    # Fit calibration
    cal = build_calibration(db_path, model_name, outcome_column, min_samples=min_samples)

    print(f"  Fitted per-station calibrations: {len(cal)} stations")
    print()

    # Load all resolved data for comparison
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        f"""
        SELECT ps.station, ps.market_date, ps.selected_side, ps.selected_bucket,
               sdo.{outcome_column} as final_temp,
               CASE WHEN ps.selected_side = 'BUY_YES'
                    THEN ps.selected_yes_ask
                    ELSE ps.selected_no_ask
               END as entry,
               CASE WHEN ps.selected_side = 'BUY_YES'
                    THEN ps.selected_fair_yes
                    ELSE ps.selected_fair_no
               END as fair,
               ps.selected_edge
        FROM prediction_snapshots ps
        JOIN station_date_outcomes sdo
          ON ps.station = sdo.station AND ps.market_date = sdo.market_date
        WHERE ps.model_name = ?
          AND ps.selected_side IS NOT NULL
          AND ps.selected_market_id IS NOT NULL
          AND ps.selected_bucket IS NOT NULL
          AND sdo.{outcome_column} IS NOT NULL
        ORDER BY ps.station, ps.market_date, ps.selected_side
        """,
        (model_name,),
    ).fetchall()

    db.close()

    # ── Build comparison data ─────────────────────────────────────────────
    comparisons: list[dict] = []
    for r in rows:
        station = str(r["station"])
        entry = float(r["entry"]) if r["entry"] is not None else None
        fair = float(r["fair"]) if r["fair"] is not None else None
        if entry is None or fair is None or entry <= 0 or fair <= 0 or fair >= 1:
            continue
        if station not in cal:
            continue

        side = str(r["selected_side"])
        edge_raw = fair - entry
        cal_prob = calibrate_fair(fair, cal[station].intercept, cal[station].coef)
        edge_cal = cal_prob - entry

        won, _, _ = score_snapshot(
            entry, side, fair, str(r["selected_bucket"]), float(r["final_temp"])
        )

        comparisons.append({
            "station": station,
            "side": side,
            "date": str(r["market_date"]),
            "bucket": str(r["selected_bucket"]),
            "entry": entry,
            "fair": fair,
            "cal_prob": cal_prob,
            "edge_raw": edge_raw,
            "edge_cal": edge_cal,
            "won": won,
            "edge_delta": edge_cal - edge_raw,
        })

    # ── Per-station summary ────────────────────────────────────────────────
    print("─" * 110)
    print(f" {'Station':<8s} {'N':>5s} {'Intercept':>10s} {'Coef':>8s}  {'ModelWR':>8s} {'ActualWR':>8s} {'CalWR':>8s}  {'Raw Edge':>10s} {'Cal Edge':>10s}")
    print("─" * 110)

    by_station_data = defaultdict(list)
    for c in comparisons:
        by_station_data[c["station"]].append(c)

    for station in sorted(by_station_data):
        items = by_station_data[station]
        n = len(items)
        raw_wr = sum(1 for c in items if c["fair"] >= 0.5) / n * 100
        actual_wr = sum(1 for c in items if c["won"]) / n * 100
        cal_wr = sum(1 for c in items if c["cal_prob"] >= 0.5) / n * 100
        avg_raw_edge = np.mean([c["edge_raw"] for c in items])
        avg_cal_edge = np.mean([c["edge_cal"] for c in items])
        cdata = cal[station]
        print(
            f" {station:<8s} {n:>5d} {cdata.intercept:>+10.3f} {cdata.coef:>+8.3f}  "
            f"{raw_wr:>7.1f}% {actual_wr:>7.1f}% {cal_wr:>7.1f}%  "
            f"{avg_raw_edge:>+10.4f} {avg_cal_edge:>+10.4f}"
        )

    # ── Edge threshold flip analysis ───────────────────────────────────────
    print()
    print("─" * 110)
    print(f" EDGE THRESHOLD FLIP ANALYSIS (threshold = {edge_threshold:+.2f})")
    print("─" * 110)

    # Original: select if edge_raw >= edge_threshold
    # Calibrated: select if edge_cal >= edge_threshold
    raw_selected = sum(1 for c in comparisons if c["edge_raw"] >= edge_threshold)
    cal_selected = sum(1 for c in comparisons if c["edge_cal"] >= edge_threshold)
    new_entries = sum(1 for c in comparisons if c["edge_raw"] < edge_threshold and c["edge_cal"] >= edge_threshold)
    dropped_entries = sum(1 for c in comparisons if c["edge_raw"] >= edge_threshold and c["edge_cal"] < edge_threshold)
    same_selected = sum(1 for c in comparisons if c["edge_raw"] >= edge_threshold and c["edge_cal"] >= edge_threshold)

    print(f"  Total comparisons:              {len(comparisons):>5d}")
    print(f"  Selected (raw edge):            {raw_selected:>5d}")
    print(f"  Selected (calibrated edge):     {cal_selected:>5d}")
    print(f"  Same in both:                   {same_selected:>5d}")
    print(f"  NEWLY selected (calibration):   {new_entries:>5d}")
    print(f"  DROPPED (calibration):          {dropped_entries:>5d}")

    # Performance of dropped trades
    dropped = [c for c in comparisons if c["edge_raw"] >= edge_threshold and c["edge_cal"] < edge_threshold]
    if dropped:
        dropped_wr = sum(1 for c in dropped if c["won"]) / len(dropped) * 100
        dropped_rr = np.mean([(1-c["entry"])/c["entry"] if c["won"] else -1.0 for c in dropped])
        print()
        print(f"  Dropped trades: {len(dropped)} total, {dropped_wr:.1f}% WR, avg R/R {dropped_rr:+.3f}")
        print(f"  (These would have been traded before calibration, now blocked)")

    # Performance of newly selected trades
    new = [c for c in comparisons if c["edge_raw"] < edge_threshold and c["edge_cal"] >= edge_threshold]
    if new:
        new_wr = sum(1 for c in new if c["won"]) / len(new) * 100
        new_rr = np.mean([(1-c["entry"])/c["entry"] if c["won"] else -1.0 for c in new])
        print()
        print(f"  Newly selected trades: {len(new)} total, {new_wr:.1f}% WR, avg R/R {new_rr:+.3f}")
        print(f"  (These were missed before calibration, now picked up)")

    # ── Before/after edge histogram ────────────────────────────────────────
    print()
    print("─" * 110)
    print(" EDGE DISTRIBUTION: BEFORE vs AFTER CALIBRATION")
    print("─" * 110)

    bins = [(-0.50, -0.20), (-0.20, 0.0), (0.0, 0.10), (0.10, 0.20),
            (0.20, 0.30), (0.30, 0.50), (0.50, float("inf"))]
    labels = ["< -0.20", "-0.20-0.00", "0.00-0.10", "0.10-0.20",
              "0.20-0.30", "0.30-0.50", ">= 0.50"]

    raw_counts = [0] * len(bins)
    cal_counts = [0] * len(bins)
    for c in comparisons:
        for i, (lo, hi) in enumerate(bins):
            if lo <= c["edge_raw"] < hi or (hi == float("inf") and c["edge_raw"] >= lo):
                raw_counts[i] += 1
            if lo <= c["edge_cal"] < hi or (hi == float("inf") and c["edge_cal"] >= lo):
                cal_counts[i] += 1

    print(f"  {'Bucket':<14s} {'Raw':>6s} {'Cal':>6s} {'Delta':>6s}")
    for i, label in enumerate(labels):
        delta = cal_counts[i] - raw_counts[i]
        print(f"  {label:<14s} {raw_counts[i]:>6d} {cal_counts[i]:>6d} {delta:>+6d}")

    # ── Show specific examples (deduplicated) ──────────────────────────────
    print()
    print("─" * 110)
    print(f" TOP {top_n} MOST IMPACTED TRADES (unique station/date/side/bucket)")
    print("─" * 110)
    print(f"  {'Station':<8s} {'Date':<12s} {'Side':<7s} {'Bucket':<12s} {'Entry':>8s} {'Fair':>8s} {'CalProb':>8s} {'RawEdge':>10s} {'CalEdge':>10s} {'Won':>5s} {'Impact':>8s}")
    print("─" * 110)

    # Deduplicate: keep first per (station, date, side, bucket)
    seen = set()
    unique = []
    for c in comparisons:
        key = (c["station"], c["date"], c["side"], c["bucket"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    # Show trades where calibration made the biggest difference
    impacted = sorted(unique, key=lambda c: abs(c["edge_delta"]), reverse=True)
    for c in impacted[:top_n]:
        direction = "↓DROP" if c["edge_raw"] >= edge_threshold and c["edge_cal"] < edge_threshold else \
                    "↑NEW " if c["edge_raw"] < edge_threshold and c["edge_cal"] >= edge_threshold else \
                    "     "
        impact = f"{c['edge_delta']:+.4f}"
        print(
            f"  {c['station']:<8s} {c['date']:<12s} {c['side']:<7s} {c['bucket']:<12s} "
            f"{c['entry']:>8.3f} {c['fair']:>8.3f} {c['cal_prob']:>8.3f} "
            f"{c['edge_raw']:>+10.4f} {c['edge_cal']:>+10.4f} "
            f"{'WIN' if c['won'] else 'LOSS':>5s} {impact:>8s} {direction}"
        )

    print()
    dropped_wr: float = 0.0
    dropped_rr: float = 0.0
    new_wr: float = 0.0
    new_rr: float = 0.0
    if dropped_entries > 0:
        print(f"  SUMMARY: Calibration would drop {dropped_entries} previously-selected trades")
        if dropped:
            dropped_wr = sum(1 for c in dropped if c["won"]) / len(dropped) * 100
            dropped_rr = float(np.mean([(1-c["entry"])/c["entry"] if c["won"] else -1.0 for c in dropped]))
            print(f"           Dropped trades historically had {dropped_wr:.1f}% WR, {dropped_rr:+.3f} R/R")
    if new_entries > 0:
        print(f"           Calibration would surface {new_entries} previously-missed trades")
        if new:
            new_wr = sum(1 for c in new if c["won"]) / len(new) * 100
            new_rr = float(np.mean([(1-c["entry"])/c["entry"] if c["won"] else -1.0 for c in new]))
            print(f"           New trades historically had {new_wr:.1f}% WR, {new_rr:+.3f} R/R")

    print()
    print("─" * 110)
    print(" INTERPRETATION")
    print("─" * 110)
    print("  Raw edge:     model_fair - entry_price  (current system)")
    print("  Cal edge:     platt_scaled(fair) - entry_price  (proposed)")
    print()
    print("  The calibration learns per-station how the model's raw probability")
    print("  maps to actual outcomes. A positive coef means the model's confidence")
    print("  direction is correct (higher fair -> higher win rate). A near-zero or")
    print("  negative coef means the model's confidence is inverted or useless at")
    print("  that station.")
    print()
    print("  This is a demo. In production, this would run inside FairValueEngine")
    print("  so edge = calibrated_prob - ask_price for every candidate.")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Demo model-level probability calibration")
    parser.add_argument(
        "--db",
        default="/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite",
        help="Research DB path",
    )
    parser.add_argument(
        "--model",
        default="dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
        help="Model name to calibrate",
    )
    parser.add_argument("--min-samples", type=int, default=10, help="Min samples per station")
    parser.add_argument("--edge-threshold", type=float, default=0.10, help="Edge cutoff for selection")
    parser.add_argument("--top-n", type=int, default=20, help="Number of example trades to show")
    parser.add_argument("--outcome-column", default="final_high_tmpf", help="Outcome column (final_high_tmpf or final_low_tmpf)")
    args = parser.parse_args()

    demo_edge_comparison(
        db_path=args.db,
        model_name=args.model,
        outcome_column=args.outcome_column,
        min_samples=args.min_samples,
        edge_threshold=args.edge_threshold,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    main()
