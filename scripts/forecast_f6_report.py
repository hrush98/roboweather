#!/usr/bin/env python3
"""Build the corrected US-high F6 pricing and executable edge-decay report."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import timedelta
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from scripts.forecast_remaining_heating_report import _load_forward_cohort
from weather_trader.discovery.decision_cache import (
    DecisionCacheContract,
    quote_ready_timestamp,
)
from weather_trader.forecasting.evaluation import EvaluationContract, FixedSupport
from weather_trader.forecasting.remaining_heating import enforce_high_so_far_lower_bound
from weather_trader.tape.replay import PostReadyCheckpointBookProvider, sweep_asks

DEFAULT_RESEARCH_DB = Path("/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite")
DEFAULT_TAPE_CATALOG = Path("/home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite")
DEFAULT_F3_DIR = ROOT / "reports/forecast-edge/f3-current"
DEFAULT_OUT = ROOT / "reports/forecast-edge/f6-current"
CHECKPOINTS_SECONDS = (0, 30, 120, 300, 900)
TARGET_COSTS_USD = (25.0, 50.0, 100.0)
UNCERTAINTY_RESERVE = 0.02
MINIMUM_PROFIT_RESERVE = 0.05
KNOWN_COST_RESERVE = 0.01
TOTAL_RESERVE = UNCERTAINTY_RESERVE + MINIMUM_PROFIT_RESERVE + KNOWN_COST_RESERVE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--tape-catalog", type=Path, default=DEFAULT_TAPE_CATALOG)
    parser.add_argument("--f3-dir", type=Path, default=DEFAULT_F3_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result, _ = run_report(args.research_db, args.tape_catalog, args.f3_dir, args.out)
    print(json.dumps({
        "status": result["status"],
        "verdict": result["verdict"],
        "selected_rows": result["cohort"]["selected_rows"],
        "tape_mapped_rows": result["cohort"]["tape_mapped_rows"],
        "acceptance_checks": result["acceptance_checks"],
    }, indent=2, sort_keys=True))


def run_report(
    research_db: Path,
    tape_catalog: Path,
    f3_dir: Path,
    out: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f3_result_path = f3_dir / "result.json"
    f3_model_path = f3_dir / "remaining_heating_weather_ensemble.joblib"
    f3_result = json.loads(f3_result_path.read_text(encoding="utf-8"))
    bundle = joblib.load(f3_model_path)
    contract_payload = dict(bundle["evaluation_contract"])
    contract_payload["support"] = FixedSupport(**contract_payload["support"])
    contract = EvaluationContract(**contract_payload)
    expected_fingerprint = f3_result["contract"]["evaluation"]["fingerprint"]
    forward_contract = replace(contract, validation_start="2026-01-01", validation_end_exclusive="2100-01-01")
    if bundle.get("evaluation_fingerprint") != expected_fingerprint or contract.fingerprint != expected_fingerprint:
        raise ValueError("F3 model evaluation fingerprint does not match F6 contract")
    forward, baseline, _market, exclusions = _load_forward_cohort(research_db, forward_contract)
    remaining = bundle["remaining_heating_model"].predict_proba(forward)
    coherent_baseline = enforce_high_so_far_lower_bound(
        baseline, forward["max_temp_so_far"], contract.support
    )
    weather = (
        float(bundle["remaining_heating_weight"]) * remaining
        + float(bundle["hrrr_baseline_weight"]) * coherent_baseline
    )
    bounds = _load_market_bounds(research_db)
    selected = build_selected_rows(
        forward, weather, bounds, contract.support.values,
        activation_date=str(bundle["activation_date"]),
    )
    post_activation = [
        row for row in selected if row["market_date"] >= str(bundle["activation_date"])
    ]
    selected_market_gate = compare_selected_to_market(post_activation)
    quoted = quoted_price_diagnostic(post_activation)

    tape_rows: list[dict[str, Any]] = []
    tape_mapping: dict[str, str] = {}
    if tape_catalog.exists():
        with _read_only_connection(tape_catalog) as tape:
            tape_mapping = _load_tape_tokens(
                tape, {str(row["selected_market_id"]) for row in post_activation}
            )
            provider = PostReadyCheckpointBookProvider(
                tape, tape_mapping.values(), maximum_execution_delay_seconds=30.0
            )
            tape_rows = replay_edge_decay(
                post_activation, tape_mapping, provider,
                timing_contract=DecisionCacheContract(),
            )
    curve = summarize_edge_decay(tape_rows)
    half_life = summarize_half_life(tape_rows)
    f3_checks = dict(f3_result.get("acceptance_checks") or {})
    f3_passed = (
        f3_result.get("verdict") == "ACCEPT_F3_FOR_PRICE_SHEET_V2_RESEARCH"
        and all(f3_checks.values())
    )
    selected_gate_passed = (
        selected_market_gate["market_dates"] >= 20
        and selected_market_gate["candidate_minus_market"]["brier"] < 0
        and selected_market_gate["candidate_minus_market"]["log_loss"] < 0
    )
    tape_t0 = next((row for row in curve if row["offset_seconds"] == 0), None)
    tape_gate_passed = bool(
        tape_t0
        and tape_t0["market_dates"] >= 20
        and tape_t0["fillable_25_rows"] == tape_t0["valid_rows"]
        and tape_t0["median_net_edge_25"] is not None
        and tape_t0["median_net_edge_25"] > 0
    )
    checks = {
        "corrected_f3_prerequisite_passed": f3_passed,
        "selected_market_relative_gate_passed": selected_gate_passed,
        "preactivation_calibrator_was_frozen": False,
        "quoted_price_gate_passed": False,
        "tape_t0_useful_size_gate_passed": tape_gate_passed,
        "edge_decay_curve_emitted": bool(curve),
        "funded_authority_unchanged": True,
    }
    result = {
        "status": "COMPLETE",
        "verdict": (
            "ACCEPT_F6_FOR_RESEARCH"
            if all(checks.values())
            else "REJECT_F6_CORRECTED_F3_PREREQUISITE_AND_PRICING_GATES"
        ),
        "contract": {
            "cohort": "US_HIGH",
            "lifecycle_horizon": "d0_late_exact_14_local",
            "forecast_version": bundle["forecast_version"],
            "evaluation_fingerprint": bundle["evaluation_fingerprint"],
            "activation_date": bundle["activation_date"],
            "selection_rule": (
                "preserve the accepted HRRR-rich baseline snapshot's selected "
                "market, bucket, and side at the exact-cutoff row; never reselect "
                "from F3 probabilities"
            ),
            "quote_ready": {
                "availability_bucket_seconds": 60,
                "latency_ms": 250,
                "maximum_checkpoint_delay_seconds": 30,
                "pre_signal_seconds": 60,
            },
            "checkpoints_seconds": list(CHECKPOINTS_SECONDS),
            "target_costs_usd": list(TARGET_COSTS_USD),
            "reserves": {
                "uncertainty": UNCERTAINTY_RESERVE,
                "minimum_profit": MINIMUM_PROFIT_RESERVE,
                "known_cost": KNOWN_COST_RESERVE,
                "side_aligned_net_edge_formula": (
                    "raw_f3_token_fair - uncertainty - minimum_profit - "
                    "known_cost - executable_vwap"
                ),
            },
        },
        "inputs": {
            "research_db": str(research_db),
            "tape_catalog": str(tape_catalog),
            "f3_result": str(f3_result_path),
            "f3_model": str(f3_model_path),
        },
        "cohort": {
            "forward_rows": len(forward),
            "forward_exclusions": exclusions,
            "selected_rows": len(post_activation),
            "selected_market_dates": len({row["market_date"] for row in post_activation}),
            "tape_mapped_rows": sum(
                str(row["selected_market_id"]) in tape_mapping for row in post_activation
            ),
        },
        "f3_prerequisite": {
            "verdict": f3_result.get("verdict"),
            "failed_checks": sorted(name for name, passed in f3_checks.items() if not passed),
            "timing_diagnostic": f3_result.get("timing_diagnostic"),
        },
        "selected_market_relative": selected_market_gate,
        "quoted_price_diagnostic": quoted,
        "price_sheet_v2": {
            "status": "NOT_ELIGIBLE",
            "reason": (
                "The corrected F3 prerequisite failed, and no calibrator was "
                "frozen before the 2026-07-20 activation boundary. Retrospective "
                "calibrator selection cannot promote these same rows."
            ),
        },
        "edge_decay_curve": curve,
        "edge_half_life": half_life,
        "acceptance_checks": checks,
        "limitations": [
            "The edge-decay curve is diagnostic because the corrected F3 and Price Sheet V2 gates failed.",
            "Public full-book checkpoints support delayed taker counterfactuals, not passive or actual-fill claims.",
            "Rows without a continuously valid pre-t0-through-checkpoint interval or full requested size are right-censored, never assigned zero edge.",
            "Weather-outcome labels are not venue-authoritative settlement evidence.",
            "No result changes funded trading authority.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pd.DataFrame(tape_rows).to_csv(out / "edge_decay_rows.csv", index=False)
    (out / "report.md").write_text(render_markdown(result), encoding="utf-8")
    return result, tape_rows


def build_selected_rows(
    forward: pd.DataFrame,
    weather: np.ndarray,
    bounds: Mapping[str, tuple[float | None, float | None]],
    support: np.ndarray,
    *,
    activation_date: str,
) -> list[dict[str, Any]]:
    rows = []
    for position, (_, source) in enumerate(forward.iterrows()):
        side = str(source.get("selected_side") or "SKIP")
        market_id = str(source.get("selected_market_id") or "")
        if side not in {"BUY_YES", "BUY_NO"} or not market_id:
            continue
        bound = bounds.get(market_id)
        if bound is None:
            continue
        yes_fair = bucket_probability(weather[position], support, *bound)
        fair = yes_fair if side == "BUY_YES" else 1.0 - yes_fair
        candidate = next((
            item for item in source.get("candidate_distribution") or []
            if str(item.get("market_id")) == market_id
        ), {})
        ask_key = "yes_ask" if side == "BUY_YES" else "no_ask"
        ask = _finite(candidate.get(ask_key))
        outcome_yes = _bucket_won(int(source["target_value"]), *bound)
        outcome = int(outcome_yes if side == "BUY_YES" else not outcome_yes)
        rows.append({
            "source_prediction_snapshot_id": int(source["source_prediction_snapshot_id"]),
            "source_snapshot_timestamp_utc": str(source["source_snapshot_timestamp_utc"]),
            "decision_time_local": str(source["decision_time_local"]),
            "station": str(source["station"]),
            "market_date": str(source["local_date"]),
            "selected_market_id": market_id,
            "selected_bucket": str(source.get("selected_bucket") or ""),
            "selected_side": side,
            "raw_f3_token_fair": fair,
            "source_same_side_ask": ask,
            "outcome_label": outcome,
            "activation_eligible": str(source["local_date"]) >= activation_date,
        })
    return rows


def bucket_probability(
    probabilities: Sequence[float],
    support: Sequence[int],
    lower: float | None,
    upper: float | None,
) -> float:
    values = np.asarray(support, dtype=float)
    matrix = np.asarray(probabilities, dtype=float)
    mask = np.ones(len(values), dtype=bool)
    if lower is not None:
        mask &= values >= float(lower)
    if upper is not None:
        mask &= values <= float(upper)
    return float(matrix[mask].sum())


def compare_selected_to_market(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row.get("source_same_side_ask") is not None]
    candidate = binary_metrics(available, "raw_f3_token_fair")
    market = binary_metrics(available, "source_same_side_ask")
    return {
        "rows": len(available),
        "market_dates": len({str(row["market_date"]) for row in available}),
        "candidate": candidate,
        "market": market,
        "candidate_minus_market": {
            "brier": _difference(candidate.get("brier"), market.get("brier")),
            "log_loss": _difference(candidate.get("log_loss"), market.get("log_loss")),
        },
    }


def binary_metrics(
    rows: Sequence[Mapping[str, Any]], probability_field: str
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        probability = _finite(row.get(probability_field))
        if probability is None:
            continue
        grouped.setdefault(str(row["market_date"]), []).append(
            (min(max(probability, 1e-6), 1.0 - 1e-6), int(row["outcome_label"]))
        )
    if not grouped:
        return {"rows": 0, "market_dates": 0, "brier": None, "log_loss": None}
    per_date = []
    for values in grouped.values():
        brier = np.mean([(probability - outcome) ** 2 for probability, outcome in values])
        log_loss = np.mean([
            -(outcome * math.log(probability) + (1 - outcome) * math.log(1.0 - probability))
            for probability, outcome in values
        ])
        per_date.append((brier, log_loss))
    return {
        "rows": sum(len(values) for values in grouped.values()),
        "market_dates": len(grouped),
        "brier": float(np.mean([value[0] for value in per_date])),
        "log_loss": float(np.mean([value[1] for value in per_date])),
    }


def quoted_price_diagnostic(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = []
    for row in rows:
        quote = math.floor(max(0.0, float(row["raw_f3_token_fair"]) - TOTAL_RESERVE) * 100) / 100
        if quote <= 0:
            continue
        risk = quote
        pnl = (1.0 - quote) if int(row["outcome_label"]) else -quote
        eligible.append((str(row["market_date"]), risk, pnl, quote))
    if not eligible:
        return {
            "status": "DIAGNOSTIC_ONLY_NO_FROZEN_CALIBRATOR",
            "eligible_rows": 0,
            "market_dates": 0,
            "risk": 0.0,
            "pnl": 0.0,
            "return_on_risk": None,
        }
    risk = sum(row[1] for row in eligible)
    pnl = sum(row[2] for row in eligible)
    return {
        "status": "DIAGNOSTIC_ONLY_NO_FROZEN_CALIBRATOR",
        "eligible_rows": len(eligible),
        "market_dates": len({row[0] for row in eligible}),
        "average_maximum_quote": float(np.mean([row[3] for row in eligible])),
        "risk": risk,
        "pnl": pnl,
        "return_on_risk": pnl / risk if risk else None,
    }


def replay_edge_decay(
    rows: Sequence[Mapping[str, Any]],
    token_mapping: Mapping[str, str],
    provider: PostReadyCheckpointBookProvider,
    *,
    timing_contract: DecisionCacheContract,
) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        market_id = str(row["selected_market_id"])
        token_id = token_mapping.get(market_id)
        quote_ready = quote_ready_timestamp(str(row["source_snapshot_timestamp_utc"]), timing_contract)
        for offset in CHECKPOINTS_SECONDS:
            item = {
                "source_prediction_snapshot_id": row["source_prediction_snapshot_id"],
                "station": row["station"],
                "market_date": row["market_date"],
                "selected_market_id": market_id,
                "token_id": token_id,
                "selected_side": row["selected_side"],
                "raw_f3_token_fair": row["raw_f3_token_fair"],
                "quote_ready_timestamp_utc": quote_ready.isoformat(),
                "offset_seconds": offset,
                "target_timestamp_utc": (quote_ready + timedelta(seconds=offset)).isoformat(),
            }
            if token_id is None:
                item.update(status="RIGHT_CENSORED", reason="token_not_in_tape_catalog")
                output.append(item)
                continue
            book, reason = provider.book_at(
                token_id,
                quote_ready + timedelta(seconds=offset),
                pre_signal_seconds=timing_contract.pre_signal_seconds + offset,
            )
            if book is None:
                item.update(status="RIGHT_CENSORED", reason=reason)
                output.append(item)
                continue
            item.update(
                status="VALID",
                reason=None,
                execution_timestamp_utc=book["execution_timestamp_utc"],
                execution_delay_ms_after_target=book["execution_delay_ms_after_ready"],
                coverage_interval_id=book["coverage_interval_id"],
                reconstruction_hash=book["reconstruction_hash"],
            )
            for target_cost in TARGET_COSTS_USD:
                cost, _shares, vwap = sweep_asks(book["asks"], price_cap=1.0, target_cost=target_cost)
                suffix = int(target_cost)
                item[f"fillable_{suffix}_usd"] = cost
                item[f"vwap_{suffix}"] = vwap
            full_vwap = item["vwap_25"] if item["fillable_25_usd"] >= 25.0 - 1e-6 else None
            item["net_edge_25"] = (
                float(row["raw_f3_token_fair"]) - TOTAL_RESERVE - full_vwap
                if full_vwap is not None else None
            )
            output.append(item)
    return output


def summarize_edge_decay(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for offset in CHECKPOINTS_SECONDS:
        cohort = [row for row in rows if int(row["offset_seconds"]) == offset]
        valid = [row for row in cohort if row.get("status") == "VALID"]
        fillable = [
            row for row in valid
            if _finite(row.get("fillable_25_usd")) is not None
            and float(row["fillable_25_usd"]) >= 25.0 - 1e-6
            and _finite(row.get("net_edge_25")) is not None
        ]
        edges = [float(row["net_edge_25"]) for row in fillable]
        result.append({
            "offset_seconds": offset,
            "selected_rows": len(cohort),
            "market_dates": len({str(row["market_date"]) for row in cohort}),
            "valid_rows": len(valid),
            "fillable_25_rows": len(fillable),
            "right_censored_rows": len(cohort) - len(fillable),
            "median_net_edge_25": float(np.median(edges)) if edges else None,
            "mean_net_edge_25": float(np.mean(edges)) if edges else None,
            "positive_net_edge_25_rate": sum(edge > 0 for edge in edges) / len(edges) if edges else None,
            "censor_reasons": dict(sorted(Counter(
                str(row.get("reason") or "UNFILLABLE_25")
                for row in cohort if row not in fillable
            ).items())),
        })
    return result


def summarize_half_life(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_decision: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_decision.setdefault(int(row["source_prediction_snapshot_id"]), []).append(row)
    summaries = []
    for decision_id, cohort in sorted(by_decision.items()):
        ordered = sorted(cohort, key=lambda row: int(row["offset_seconds"]))
        initial = next((
            _finite(row.get("net_edge_25"))
            for row in ordered if int(row["offset_seconds"]) == 0
        ), None)
        half = None
        nonpositive = None
        if initial is not None and initial <= 0:
            nonpositive = 0
        if initial is not None and initial > 0:
            for row in ordered:
                edge = _finite(row.get("net_edge_25"))
                if edge is None:
                    continue
                offset = int(row["offset_seconds"])
                if half is None and edge <= initial / 2.0:
                    half = offset
                if nonpositive is None and edge <= 0:
                    nonpositive = offset
        summaries.append({
            "source_prediction_snapshot_id": decision_id,
            "initial_net_edge_25": initial,
            "half_life_seconds": half,
            "time_to_nonpositive_seconds": nonpositive,
            "right_censored": initial is None or any(row.get("status") != "VALID" for row in ordered),
        })
    observed_half = [row["half_life_seconds"] for row in summaries if row["half_life_seconds"] is not None]
    return {
        "decisions": len(summaries),
        "initial_edge_observed": sum(row["initial_net_edge_25"] is not None for row in summaries),
        "half_life_observed": len(observed_half),
        "median_half_life_seconds": float(np.median(observed_half)) if observed_half else None,
        "rows": summaries,
    }


def render_markdown(result: Mapping[str, Any]) -> str:
    checks = result["acceptance_checks"]
    lines = [
        "# F6 US-High Price Sheet And Edge Half-Life",
        "",
        f"Status: {result['status']}",
        "",
        f"Verdict: **{result['verdict']}**",
        "",
        "## Gate Result",
        "",
        (
            "The corrected exact-cutoff F3 artifact fails the market-relative "
            "prerequisite, and no Price Sheet V2 calibrator was frozen before "
            "activation. Tape results below are diagnostic only."
        ),
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: {name}")
    lines.extend([
        "",
        "## Executable Edge Decay",
        "",
        "| offset | valid | fillable $25 | median net edge | positive rate | censored |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in result["edge_decay_curve"]:
        lines.append(
            "| {offset}s | {valid} | {fillable} | {median} | {positive} | {censored} |".format(
                offset=row["offset_seconds"],
                valid=row["valid_rows"],
                fillable=row["fillable_25_rows"],
                median=_format(row["median_net_edge_25"]),
                positive=_format(row["positive_net_edge_25_rate"]),
                censored=row["right_censored_rows"],
            )
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    return "\n".join(lines) + "\n"


def _load_market_bounds(database: Path) -> dict[str, tuple[float | None, float | None]]:
    with _read_only_connection(database) as connection:
        rows = connection.execute(
            "select market_id,lower_f,upper_f from markets where market_family='HIGH_TEMP' and station like 'K%'"
        ).fetchall()
    return {
        str(row["market_id"]): (_finite(row["lower_f"]), _finite(row["upper_f"]))
        for row in rows
    }


def _load_tape_tokens(tape: sqlite3.Connection, market_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted(set(market_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = tape.execute(
        f"select market_id,token_id,outcome from tape_tokens where market_id in ({placeholders}) and outcome='YES'",
        ids,
    ).fetchall()
    return {str(row["market_id"]): str(row["token_id"]) for row in rows}


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _bucket_won(value: int, lower: float | None, upper: float | None) -> bool:
    return (lower is None or value >= lower) and (upper is None or value <= upper)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _difference(left: Any, right: Any) -> float | None:
    a, b = _finite(left), _finite(right)
    return a - b if a is not None and b is not None else None


def _format(value: Any) -> str:
    parsed = _finite(value)
    return f"{parsed:.4f}" if parsed is not None else "—"


if __name__ == "__main__":
    main()
