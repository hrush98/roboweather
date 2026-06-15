#!/usr/bin/env python3
"""Build a Layer 1 calibration table from the research DB.

Reads prediction_snapshots, builds consensus pairs or uses single-model snapshots,
scores against station_date_outcomes, and outputs a calibration table with
TRADE / CANARY / BLOCK decisions per (station, side, entry_band).

Usage:
    # Consensus mode (pairs two models)
    python scripts/calibration_table.py \
        --db ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite \
        --family obs

    # Single-model mode (uses all snapshots from one model)
    python scripts/calibration_table.py --db ... --family obs --single-model

    # Per-station only (no entry band split, for smaller families)
    python scripts/calibration_table.py --db ... --family global_low --per-station

    # Combined: single-model + per-station
    python scripts/calibration_table.py --db ... --family obs --single-model --per-station
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from typing import Any

# ── Model family definitions ──────────────────────────────────────────────

MODEL_FAMILIES: dict[str, dict[str, Any]] = {
    "obs": {
        "label": "US High-Temp Obs-Only",
        "market_family": "HIGH_TEMP",
        "models": [
            "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
            "catboost_bucket_pm_active_us12_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025",
            "catboost_bucket_pm_active_us12_obs_2022_2025",
        ),
        "outcome_column": "final_high_tmpf",
    },
    "hrrr_v2": {
        "label": "US High-Temp HRRR v2",
        "market_family": "HIGH_TEMP",
        "models": [
            "dynamic_bucket_tuned_hrrr_v2_obs_2022_2025",
            "catboost_bucket_hrrr_v2_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_tuned_hrrr_v2_obs_2022_2025",
            "catboost_bucket_hrrr_v2_obs_2022_2025",
        ),
        "outcome_column": "final_high_tmpf",
    },
    "hrrr_rich": {
        "label": "US High-Temp HRRR-Rich",
        "market_family": "HIGH_TEMP",
        "models": [
            "dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025",
            "catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025",
            "catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025",
        ),
        "outcome_column": "final_high_tmpf",
    },
    "metar_hrrr_rich": {
        "label": "US High-Temp METAR+HRRR-Rich",
        "market_family": "HIGH_TEMP",
        "models": [
            "dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025",
            "catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025",
            "catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025",
        ),
        "outcome_column": "final_high_tmpf",
    },
    "global_low": {
        "label": "Global Low-Temp Celsius",
        "market_family": "LOW_TEMP",
        "models": [
            "dynamic_bucket_international_celsius_low_obs_2022_2025",
            "mvp_international_celsius_low_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_international_celsius_low_obs_2022_2025",
            "mvp_international_celsius_low_obs_2022_2025",
        ),
        "outcome_column": "final_low_tmpf",
    },
    "global_high": {
        "label": "Global High-Temp Celsius",
        "market_family": "HIGH_TEMP",
        "models": [
            "dynamic_bucket_international_celsius_high_obs_2022_2025",
            "mvp_international_celsius_high_obs_2022_2025",
        ],
        "consensus_pair": (
            "dynamic_bucket_international_celsius_high_obs_2022_2025",
            "mvp_international_celsius_high_obs_2022_2025",
        ),
        "outcome_column": "final_high_tmpf",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────


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


def entry_band(entry_price: float) -> str:
    if entry_price < 0.15:
        return "<0.15"
    if entry_price < 0.25:
        return "0.15-0.25"
    if entry_price < 0.35:
        return "0.25-0.35"
    if entry_price < 0.45:
        return "0.35-0.45"
    if entry_price < 0.55:
        return "0.45-0.55"
    return ">=0.55"


def calibrate(
    n: int,
    avg_rr: float,
    trade_rr: float,
    min_n_trade: int,
    min_n_canary: int,
) -> str:
    if n < min_n_canary:
        return "INSUFFICIENT_DATA"
    if avg_rr >= trade_rr and n >= min_n_trade:
        return "TRADE"
    if avg_rr > 0:
        return "CANARY"
    if avg_rr > -0.10:
        return "WATCH"
    return "BLOCK"


# ── Scoring ───────────────────────────────────────────────────────────────


def score_snapshot(
    entry: float,
    side: str,
    fair: float,
    bucket_str: str,
    final_temp: float,
) -> tuple[bool, float]:
    """Return (won, rr) for one snapshot."""
    bounds = parse_bucket(bucket_str)
    if bounds is None:
        return (False, 0.0)
    low, high = bounds
    in_bucket = low <= final_temp <= high
    won = (side == "BUY_NO" and not in_bucket) or (side == "BUY_YES" and in_bucket)
    rr = (1 - entry) / entry if won else -1.0
    return (won, rr)


# ── Core ──────────────────────────────────────────────────────────────────


def build_calibration(
    db_path: str,
    family: str,
    *,
    single_model: bool = False,
    relaxed_consensus: bool = False,
    per_station: bool = False,
    trade_rr: float = 0.15,
    min_n_trade: int = 15,
    min_n_canary: int = 5,
    verbose: bool = False,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    config = MODEL_FAMILIES[family]
    outcome_col = config["outcome_column"]
    model_a, model_b = config["consensus_pair"]

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    if single_model:
        # Use all snapshots from the primary model (model_a)
        rows = db.execute(
            f"""
            SELECT ps.*, sdo.{outcome_col} as final_temp
            FROM prediction_snapshots ps
            JOIN station_date_outcomes sdo
              ON ps.station = sdo.station AND ps.market_date = sdo.market_date
            WHERE ps.model_name = ?
              AND ps.strategy_bucket = 'HIGH_CONVICTION'
              AND ps.selected_side IS NOT NULL
              AND ps.selected_market_id IS NOT NULL
              AND ps.selected_bucket IS NOT NULL
              AND sdo.{outcome_col} IS NOT NULL
            ORDER BY ps.station, ps.market_date, ps.obs_delay_bucket,
                     ps.selected_side, ps.selected_market_id, ps.selected_bucket
            """,
            (model_a,),
        ).fetchall()

        if verbose:
            print(
                f"Loaded {len(rows)} single-model snapshots for {model_a}",
                file=sys.stderr,
            )

        pairs: list[dict[str, Any]] = []
        for r in rows:
            side = r["selected_side"]
            entry = (
                float(r["selected_yes_ask"])
                if side == "BUY_YES"
                else float(r["selected_no_ask"])
            )
            if entry is None or entry <= 0:
                continue
            edge = float(r["selected_edge"] or 0)
            if side == "BUY_NO":
                fair = float(r["selected_fair_no"] or (entry + edge))
            else:
                fair = float(r["selected_fair_yes"] or (entry + edge))

            pairs.append(
                {
                    "station": r["station"],
                    "market_date": r["market_date"],
                    "side": side,
                    "bucket": r["selected_bucket"],
                    "entry": entry,
                    "edge": edge,
                    "fair": fair,
                    "temp": float(r["final_temp"]),
                }
            )
    else:
        # Consensus mode: pair the two models
        rows = db.execute(
            f"""
            SELECT ps.*, sdo.{outcome_col} as final_temp
            FROM prediction_snapshots ps
            JOIN station_date_outcomes sdo
              ON ps.station = sdo.station AND ps.market_date = sdo.market_date
            WHERE ps.model_name IN (?, ?)
              AND ps.strategy_bucket = 'HIGH_CONVICTION'
              AND ps.selected_side IS NOT NULL
              AND ps.selected_market_id IS NOT NULL
              AND ps.selected_bucket IS NOT NULL
              AND sdo.{outcome_col} IS NOT NULL
            ORDER BY ps.station, ps.market_date, ps.selected_side, ps.model_name,
                     ps.obs_delay_bucket, ps.selected_market_id, ps.selected_bucket
            """,
            (model_a, model_b),
        ).fetchall()

        if verbose:
            print(
                f"Loaded {len(rows)} individual snapshots for consensus pairing",
                file=sys.stderr,
            )

        pairs = []

        if relaxed_consensus:
            # Pair on (station, market_date, side) only.
            # Uses the first-arriving model's bucket/entry, consensus edge from both.
            relaxed_key = lambda r: (r["station"], r["market_date"], r["selected_side"])
            rows_sorted = sorted(rows, key=relaxed_key)

            for key, group in groupby(rows_sorted, key=relaxed_key):
                models = list(group)
                a = next((m for m in models if m["model_name"] == model_a), None)
                b = next((m for m in models if m["model_name"] == model_b), None)
                if a is None or b is None:
                    continue

                edge_a = a["selected_edge"]
                edge_b = b["selected_edge"]
                if edge_a is None or edge_b is None:
                    continue

                side = a["selected_side"]
                entry = (
                    float(a["selected_yes_ask"])
                    if side == "BUY_YES"
                    else float(a["selected_no_ask"])
                )
                if entry is None or entry <= 0:
                    continue
                entry = float(entry)

                consensus_edge = (float(edge_a) + float(edge_b)) / 2.0

                if side == "BUY_NO":
                    fair_a = float(a["selected_fair_no"] or (entry + float(edge_a)))
                    fair_b = float(b["selected_fair_no"] or (entry + float(edge_b)))
                else:
                    fair_a = float(a["selected_fair_yes"] or (entry + float(edge_a)))
                    fair_b = float(b["selected_fair_yes"] or (entry + float(edge_b)))
                consensus_fair = (fair_a + fair_b) / 2.0

                pairs.append(
                    {
                        "station": key[0],
                        "market_date": key[1],
                        "side": side,
                        "bucket": a["selected_bucket"],
                        "entry": entry,
                        "edge": consensus_edge,
                        "fair": consensus_fair,
                        "temp": float(a["final_temp"]),
                    }
                )
        else:
            # Strict consensus: pair on exact bucket/side/market/delay
            consensus_key = lambda r: (
                r["station"],
                r["market_date"],
                r["obs_delay_bucket"],
                r["strategy_bucket"],
                r["selected_side"],
                r["selected_market_id"],
                r["selected_bucket"],
            )
            rows_sorted = sorted(rows, key=consensus_key)

            for key, group in groupby(rows_sorted, key=consensus_key):
                models = list(group)
                if len(models) < 2:
                    continue
                a = next((m for m in models if m["model_name"] == model_a), None)
                b = next((m for m in models if m["model_name"] == model_b), None)
                if a is None or b is None:
                    continue

                edge_a = a["selected_edge"]
                edge_b = b["selected_edge"]
                if edge_a is None or edge_b is None:
                    continue

                side = a["selected_side"]
                entry = (
                    float(a["selected_yes_ask"])
                    if side == "BUY_YES"
                    else float(a["selected_no_ask"])
                )
                if entry is None or entry <= 0:
                    continue
                entry = float(entry)

                consensus_edge = (float(edge_a) + float(edge_b)) / 2.0

                if side == "BUY_NO":
                    fair_a = float(a["selected_fair_no"] or (entry + float(edge_a)))
                    fair_b = float(b["selected_fair_no"] or (entry + float(edge_b)))
                else:
                    fair_a = float(a["selected_fair_yes"] or (entry + float(edge_a)))
                    fair_b = float(b["selected_fair_yes"] or (entry + float(edge_b)))
                consensus_fair = (fair_a + fair_b) / 2.0

                pairs.append(
                    {
                        "station": key[0],
                        "market_date": key[1],
                        "side": side,
                        "bucket": a["selected_bucket"],
                        "entry": entry,
                        "edge": consensus_edge,
                        "fair": consensus_fair,
                        "temp": float(a["final_temp"]),
                    }
                )

    if verbose:
        mode = "single-model" if single_model else "consensus"
        print(f"Formed {len(pairs)} scored {mode} rows", file=sys.stderr)

    db.close()

    # ── Aggregate into calibration buckets ────────────────────────────────
    buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "wins": 0,
            "losses": 0,
            "total_rr": 0.0,
            "n": 0,
            "total_model_prob": 0.0,
            "total_entry": 0.0,
            "stations": set(),
            "dates": set(),
        }
    )

    for p in pairs:
        won, rr = score_snapshot(
            p["entry"], p["side"], p["fair"], p["bucket"], p["temp"]
        )
        if rr == 0.0 and not won:
            continue

        band = entry_band(p["entry"])
        if per_station:
            key = (str(p["station"]), str(p["side"]), "ALL")
        else:
            key = (str(p["station"]), str(p["side"]), band)

        b = buckets[key]
        b["wins"] += 1 if won else 0
        b["losses"] += 0 if won else 1
        b["total_rr"] += rr
        b["n"] += 1
        b["total_model_prob"] += p["fair"]
        b["total_entry"] += p["entry"]
        b["stations"].add(p["station"])
        b["dates"].add(str(p["market_date"]))

    # ── Produce results ───────────────────────────────────────────────────
    results: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (station, side, band), data in buckets.items():
        n = data["n"]
        if n == 0:
            continue
        win_pct = data["wins"] / n * 100
        avg_rr = data["total_rr"] / n
        model_wr = (data["total_model_prob"] / n) * 100
        overconfidence = model_wr - win_pct
        avg_entry = data["total_entry"] / n
        decision = calibrate(n, avg_rr, trade_rr, min_n_trade, min_n_canary)

        results[(station, side, band)] = {
            "n": n,
            "win_pct": round(win_pct, 1),
            "avg_rr": round(avg_rr, 3),
            "model_wr": round(model_wr, 1),
            "overconfidence_pp": round(overconfidence, 1),
            "avg_entry": round(avg_entry, 3),
            "decision": decision,
            "market_dates": len(data["dates"]),
        }

    return results


# ── Display ───────────────────────────────────────────────────────────────


def print_calibration(
    results: dict[tuple[str, str, str], dict[str, Any]],
    family_label: str,
    mode_label: str,
    min_n_show: int = 3,
) -> None:
    priority = {
        "TRADE": 0,
        "CANARY": 1,
        "WATCH": 2,
        "BLOCK": 3,
        "INSUFFICIENT_DATA": 4,
    }
    filtered = [
        (key, data)
        for key, data in results.items()
        if data["n"] >= min_n_show
    ]
    filtered.sort(
        key=lambda x: (priority.get(x[1]["decision"], 99), -x[1]["avg_rr"])
    )

    if not filtered:
        print(
            f"\n{family_label} [{mode_label}]: no buckets with n >= {min_n_show}"
        )
        return

    print(f"\n{'='*95}")
    print(f" {family_label}  [{mode_label}]")
    print(f"{'='*95}")
    print(
        f"{'Station':<8} {'Side':<8} {'Band':<10} {'N':>5} {'Win%':>7} "
        f"{'AvgRR':>8} {'ModelWR':>8} {'Overconf':>9} {'Decision':>12}"
    )
    print("-" * 95)

    for (station, side, band), data in filtered:
        print(
            f"{station:<8} {side:<8} {band:<10} {data['n']:>5} "
            f"{data['win_pct']:>6.1f}% {data['avg_rr']:>8.3f} "
            f"{data['model_wr']:>6.1f}% {data['overconfidence_pp']:>+8.1f}pp "
            f"{data['decision']:>12}"
        )

    decisions = defaultdict(int)
    for _, data in filtered:
        decisions[data["decision"]] += 1
    print("-" * 95)
    print(
        f"  Summary: {decisions.get('TRADE', 0)} TRADE, "
        f"{decisions.get('CANARY', 0)} CANARY, "
        f"{decisions.get('WATCH', 0)} WATCH, "
        f"{decisions.get('BLOCK', 0)} BLOCK "
        f"(n >= {min_n_show})"
    )


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a Layer 1 calibration table from the research DB"
    )
    parser.add_argument("--db", required=True, help="Path to research SQLite DB")
    parser.add_argument(
        "--family",
        choices=list(MODEL_FAMILIES),
        default="obs",
        help="Model family to calibrate",
    )
    parser.add_argument(
        "--single-model",
        action="store_true",
        help="Use single-model snapshots instead of consensus pairs (more data, less filtered)",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Relaxed consensus: pair on (station, date, side) only, not exact bucket",
    )
    parser.add_argument(
        "--per-station",
        action="store_true",
        help="Aggregate by station/side only, no entry band split",
    )
    parser.add_argument(
        "--min-n-show",
        type=int,
        default=3,
        help="Minimum n to display a bucket",
    )
    parser.add_argument(
        "--min-n-trade",
        type=int,
        default=15,
        help="Minimum n for TRADE classification",
    )
    parser.add_argument(
        "--min-n-canary",
        type=int,
        default=5,
        help="Minimum n for CANARY classification",
    )
    parser.add_argument(
        "--trade-rr",
        type=float,
        default=0.15,
        help="Minimum avg R/R for TRADE classification",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print data-loading diagnostics to stderr",
    )
    args = parser.parse_args()

    config = MODEL_FAMILIES[args.family]
    mode_parts = []
    if args.single_model:
        mode_parts.append("single-model")
    else:
        mode_parts.append("consensus")
    if args.relaxed:
        mode_parts.append("relaxed")
    if args.per_station:
        mode_parts.append("per-station")
    mode_label = ", ".join(mode_parts)

    results = build_calibration(
        db_path=args.db,
        family=args.family,
        single_model=args.single_model,
        relaxed_consensus=args.relaxed,
        per_station=args.per_station,
        trade_rr=args.trade_rr,
        min_n_trade=args.min_n_trade,
        min_n_canary=args.min_n_canary,
        verbose=args.verbose,
    )

    print_calibration(
        results, config["label"], mode_label, min_n_show=args.min_n_show
    )


if __name__ == "__main__":
    main()
