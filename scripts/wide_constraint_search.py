#!/usr/bin/env python3
"""Run the exhaustive behavior-normalized wide grid from the decision cache."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_trader.discovery.decision_cache import DecisionCacheContract
from weather_trader.discovery.wide_analysis import WideSearchConfig, load_wide_rows, run_wide_search


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".local/state/roboweather/discovery/decision_cache.sqlite")
    parser.add_argument("--source-start-date", default="2026-07-23")
    parser.add_argument("--cutoff-exclusive", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--holdout-dates", type=int, default=5)
    parser.add_argument("--bootstrap-repetitions", type=int, default=2_000)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    config = WideSearchConfig(source_start_date=args.source_start_date, cutoff_exclusive=args.cutoff_exclusive, holdout_dates=args.holdout_dates, bootstrap_repetitions=args.bootstrap_repetitions, workers=args.workers)
    contract = DecisionCacheContract()
    with sqlite3.connect(f"file:{args.cache.expanduser()}?mode=ro", uri=True) as cache:
        cache.execute("pragma query_only=ON")
        rows, diagnostics = load_wide_rows(cache, contract_hash=contract.contract_hash, config=config)
    result = run_wide_search(rows, config=config, cache_diagnostics=diagnostics, sealed_manifest={"analysis_version": "behavior_normalized_absolute_wide_grid_v1", "decision_contract_hash": contract.contract_hash}, progress=lambda x: print(json.dumps(x, sort_keys=True), flush=True))
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = _markdown(result)
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": result["status"], "grid": result["grid"], "out": str(args.out), "funded_authorization": False}, indent=2, sort_keys=True))
    return 0


def _markdown(result: dict) -> str:
    grid = result["grid"]
    lines = ["# Absolute-Wide Causal Constraint Search", "", f"Status: **{result['status']}**", "", result.get("plain_language_answer", ""), "", "> Weather-outcome diagnostic over delayed public-tape taker counterfactuals. This grants no funded authority.", "", "## Breadth", "", f"- Syntactic rules represented: {grid.get('theoretical_syntactic_rules', 0):,}", f"- Unique discovery behaviors scored (including risk caps): {grid.get('unique_discovery_behaviors_with_risk_caps', 0):,}", f"- Rules passing strict discovery gates: {grid.get('passing_rules', 0):,}", f"- Correlated representatives opened on holdout: {grid.get('passing_correlated_families', 0):,}", f"- Strict holdout survivors: {grid.get('surviving_holdout_families', 0):,}", "", "## Representatives", "", "| Holdout | Model | Side | Strategy | Geography | Size | Discovery trades/RR | Holdout trades/RR |", "|:--:|---|---|---|---|---:|---:|---:|"]
    for item in result.get("family_representatives", [])[:100]:
        r = item["rule"]; h = item["holdout"]
        lines.append(f"| {'PASS' if item['survives_holdout'] else 'FAIL'} | {r['model_id']} | {r['selected_side']} | {r['strategy_bucket']} | {r['geography']} | ${r['target_cost_usd']:.0f} | {item['trades']}/{item['rr']} | {h['trades']}/{h['rr']} |")
    if not result.get("family_representatives"):
        lines.append("| — | — | — | — | — | — | No rule passed the strict discovery gate | — |")
    lines.extend(["", "The full sealed grammar, per-model attrition, exact rules, folds, daily economics, and bootstrap results are in `result.json`.", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
