#!/usr/bin/env python3
"""Materialize the forward-causal F5 GOES heating-surprise evidence cohort."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from scripts.forecast_remaining_heating_report import _load_forward_cohort
from weather_trader.forecasting.evaluation import EvaluationContract, FixedSupport
from weather_trader.forecasting.goes_heating import (
    GOES_DSR_SOURCE_ID,
    causal_station_window,
    normalized_radiation_surprise,
)
from weather_trader.forecasting.goes_model import GoesHeatingModelContract
from weather_trader.forecasting.remaining_heating import enforce_high_so_far_lower_bound
from weather_trader.stations.metadata import get_station

UTC = timezone.utc
DEFAULT_RESEARCH_DB = Path("/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite")
DEFAULT_CATALOG = Path("/home/maxrush/.local/state/roboweather/forecast_sources/catalog.sqlite")
DEFAULT_F3 = ROOT / "reports/forecast-edge/f3-current/remaining_heating_weather_ensemble.joblib"
DEFAULT_OUT = ROOT / "reports/forecast-edge/f5-current"
COLLECTION_ACTIVATION_DATE = "2026-08-14"
MODEL_CONTRACT = GoesHeatingModelContract()
MINIMUM_CALIBRATION_DATES = MODEL_CONTRACT.minimum_calibration_dates
MINIMUM_UNTOUCHED_DATES = MODEL_CONTRACT.minimum_untouched_dates
SURPRISE_THRESHOLDS = MODEL_CONTRACT.surprise_thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--f3-model", type=Path, default=DEFAULT_F3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result, rows = run_report(args.research_db, args.catalog, args.f3_model, args.out)
    print(json.dumps({
        "status": result["status"],
        "verdict": result["verdict"],
        "artifacts": result["source_coverage"]["artifacts"],
        "eligible_rows": result["cohort"]["eligible_rows"],
        "eligible_dates": result["cohort"]["eligible_dates"],
    }, indent=2))
    return 0


def run_report(
    research_db: Path,
    catalog_path: Path,
    f3_model_path: Path,
    out: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bundle = joblib.load(f3_model_path)
    payload = dict(bundle["evaluation_contract"])
    payload["support"] = FixedSupport(**payload["support"])
    contract = replace(
        EvaluationContract(**payload),
        validation_start="2026-01-01",
        validation_end_exclusive="2100-01-01",
    )
    forward, baseline, market, exclusions = _load_forward_cohort(research_db, contract)
    remaining = bundle["remaining_heating_model"].predict_proba(forward)
    coherent = enforce_high_so_far_lower_bound(
        baseline, forward["max_temp_so_far"], contract.support
    )
    f3 = (
        float(bundle["remaining_heating_weight"]) * remaining
        + float(bundle["hrrr_baseline_weight"]) * coherent
    )
    artifacts = load_artifacts(catalog_path)
    bounds = load_bounds(research_db)
    rows = materialize_rows(
        forward,
        f3,
        market,
        artifacts,
        bounds,
        contract.support.values,
        activation_date=COLLECTION_ACTIVATION_DATE,
    )
    dates = sorted({row["market_date"] for row in rows})
    fit_dates = dates
    untouched_dates: list[str] = []
    source_bytes = sum(int(row["byte_count"]) for row in artifacts)
    result = {
        "status": (
            "ACCUMULATING_CALIBRATION"
            if len(fit_dates) < MINIMUM_CALIBRATION_DATES
            else "READY_TO_FREEZE_CALIBRATOR"
            if not untouched_dates
            else "ACCUMULATING_UNTOUCHED_FORWARD"
        ),
        "verdict": "NOT_EVALUATED_FORWARD_EVIDENCE_INCOMPLETE",
        "contract": {
            "cohort": "US_HIGH",
            "predecessor": bundle["forecast_version"],
            "f3_evaluation_fingerprint": bundle["evaluation_fingerprint"],
            "horizon": "d0_exact_14_local",
            "model_contract": {**asdict(MODEL_CONTRACT), "fingerprint": MODEL_CONTRACT.fingerprint},
            "collection_activation_date": COLLECTION_ACTIVATION_DATE,
            "trailing_observed_minutes": 60,
            "minimum_scans": 3,
            "minimum_calibration_dates": MINIMUM_CALIBRATION_DATES,
            "minimum_untouched_dates": MINIMUM_UNTOUCHED_DATES,
            "surprise_definition": (
                "trailing GOES DSR transmission proxy minus HRRR next-3h "
                "shortwave transmission proxy, each normalized by frozen NOAA solar geometry"
            ),
            "surprise_thresholds": list(SURPRISE_THRESHOLDS),
            "cloud_regimes": {
                "CLEAR": "hrrr_cloud_cover_current <= 25",
                "MIXED": "25 < hrrr_cloud_cover_current < 75",
                "CLOUDY": "hrrr_cloud_cover_current >= 75",
            },
            "acceptance_scope": (
                "exact selected-token calibration and incremental log loss versus both "
                "frozen F3 and contemporaneous normalized market distribution; station/regime/threshold "
                "and abstention diagnostics are mandatory"
            ),
            "earlier_horizons": (
                "require separate frozen forecast versions and evidence clocks"
            ),
        },
        "source_coverage": {
            "source_id": GOES_DSR_SOURCE_ID,
            "artifacts": len(artifacts),
            "bytes": source_bytes,
            "first_causal_at": min(
                (str(row["causal_available_at_utc"]) for row in artifacts), default=None
            ),
            "latest_causal_at": max(
                (str(row["causal_available_at_utc"]) for row in artifacts), default=None
            ),
        },
        "cohort": {
            "forward_rows": len(forward),
            "forward_exclusions": exclusions,
            "eligible_rows": len(rows),
            "eligible_dates": len(dates),
            "calibration_dates": fit_dates,
            "calibrator_frozen_at_utc": None,
            "untouched_dates": untouched_dates,
            "station_rows": count_values(rows, "station"),
            "cloud_regime_rows": count_values(rows, "cloud_regime"),
            "threshold_rows": {
                str(threshold): sum(
                    abs(float(row["radiation_surprise"])) >= threshold for row in rows
                )
                for threshold in SURPRISE_THRESHOLDS
            },
        },
        "acceptance_checks": {
            "calibration_has_at_least_20_weather_dates": len(fit_dates) >= MINIMUM_CALIBRATION_DATES,
            "calibrator_frozen_before_untouched_evaluation": False,
            "untouched_has_at_least_20_weather_dates": len(untouched_dates) >= MINIMUM_UNTOUCHED_DATES,
            "improves_exact_token_log_loss_vs_f3": False,
            "improves_exact_token_log_loss_vs_market": False,
            "selected_token_calibration_passes": False,
            "predeclared_regime_threshold_and_abstention_reported": False,
            "funded_authority_unchanged": True,
        },
        "limitations": [
            "Historical S3 and embedded GOES clocks are provenance only and do not create retrospective causal rows.",
            "The current artifact is an accumulating evidence cohort, not a fitted challenger or edge verdict.",
            "No date becomes untouched evaluation evidence until a calibrator and future activation boundary are persisted.",
            "A passing information result would still require a separate executable-ask edge-decay integration gate.",
            "No result changes funded trading authority.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    pd.DataFrame(rows).to_json(out / "rows.jsonl", orient="records", lines=True)
    (out / "report.md").write_text(render_markdown(result))
    return result, rows


def materialize_rows(
    forward: pd.DataFrame,
    f3: np.ndarray,
    market: np.ndarray,
    artifacts: Sequence[Mapping[str, object]],
    bounds: Mapping[str, tuple[float | None, float | None]],
    support: Sequence[int],
    *,
    activation_date: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    support_values = np.asarray(support, dtype=float)
    for position, (_, row) in enumerate(forward.iterrows()):
        market_date = str(row["local_date"])
        if market_date < activation_date:
            continue
        station = get_station(str(row["station"]))
        decision = datetime.fromisoformat(
            str(row["decision_time_utc"]).replace("Z", "+00:00")
        )
        window = causal_station_window(
            artifacts,
            station,
            decision_time_utc=decision,
            trailing_minutes=60,
            minimum_scans=3,
        )
        expected_shortwave = finite(row.get("hrrr_shortwave_next_3h_mean"))
        if window is None or expected_shortwave is None:
            continue
        try:
            surprise = normalized_radiation_surprise(
                window,
                station,
                hrrr_shortwave_next_3h_mean=expected_shortwave,
            )
        except ValueError:
            continue
        selected_market_id = str(row.get("selected_market_id") or "")
        selected_side = str(row.get("selected_side") or "SKIP")
        bound = bounds.get(selected_market_id)
        if not bound or selected_side not in {"BUY_YES", "BUY_NO"}:
            continue
        f3_yes = bucket_probability(f3[position], support_values, *bound)
        market_yes = bucket_probability(market[position], support_values, *bound)
        candidate = next((
            item for item in row.get("candidate_distribution") or []
            if str(item.get("market_id")) == selected_market_id
        ), {})
        ask_key = "yes_ask" if selected_side == "BUY_YES" else "no_ask"
        source_same_side_ask = finite(candidate.get(ask_key))
        selected_f3 = f3_yes if selected_side == "BUY_YES" else 1.0 - f3_yes
        selected_market = market_yes if selected_side == "BUY_YES" else 1.0 - market_yes
        outcome_yes = bucket_won(int(row["target_value"]), *bound)
        cloud = finite(row.get("hrrr_cloud_cover_current"))
        output.append({
            "source_prediction_snapshot_id": int(row["source_prediction_snapshot_id"]),
            "station": station.station,
            "market_date": market_date,
            "decision_time_utc": decision.astimezone(UTC).isoformat(),
            "selected_market_id": selected_market_id,
            "selected_side": selected_side,
            "outcome_label": int(outcome_yes if selected_side == "BUY_YES" else not outcome_yes),
            "f3_selected_token_probability": selected_f3,
            "market_selected_token_probability": selected_market,
            "source_same_side_ask": source_same_side_ask,
            "hrrr_shortwave_next_3h_mean": expected_shortwave,
            "hrrr_cloud_cover_current": cloud,
            "cloud_regime": cloud_regime(cloud),
            **window,
            **surprise,
        })
    return output


def cloud_regime(cloud_cover: float | None) -> str:
    if cloud_cover is None:
        return "MISSING"
    if cloud_cover <= 25.0:
        return "CLEAR"
    if cloud_cover >= 75.0:
        return "CLOUDY"
    return "MIXED"


def load_artifacts(catalog: Path) -> list[dict[str, Any]]:
    if not catalog.exists():
        return []
    connection = sqlite3.connect(catalog.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "select * from source_artifacts where source_id=? order by valid_end_at_utc,artifact_id",
            (GOES_DSR_SOURCE_ID,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def load_bounds(database: Path) -> dict[str, tuple[float | None, float | None]]:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "select market_id,lower_f,upper_f from markets where market_family='HIGH_TEMP' and station like 'K%'"
        ).fetchall()
    finally:
        connection.close()
    return {
        str(row["market_id"]): (finite(row["lower_f"]), finite(row["upper_f"]))
        for row in rows
    }


def bucket_probability(
    probabilities: Sequence[float],
    support: np.ndarray,
    lower: float | None,
    upper: float | None,
) -> float:
    mask = np.ones(len(support), dtype=bool)
    if lower is not None:
        mask &= support >= lower
    if upper is not None:
        mask &= support <= upper
    return float(np.asarray(probabilities, dtype=float)[mask].sum())


def bucket_won(value: int, lower: float | None, upper: float | None) -> bool:
    return (lower is None or value >= lower) and (upper is None or value <= upper)


def finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def count_values(rows: Sequence[Mapping[str, object]], key: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        output[value] = output.get(value, 0) + 1
    return dict(sorted(output.items()))


def render_markdown(result: Mapping[str, Any]) -> str:
    cohort = result["cohort"]
    source = result["source_coverage"]
    lines = [
        "# F5 GOES Heating-Surprise Forward Evidence",
        "",
        f"Status: **{result['status']}**",
        "",
        f"Verdict: **{result['verdict']}**",
        "",
        "## Current Coverage",
        "",
        f"- GOES artifacts: {source['artifacts']}",
        f"- Retained bytes: {source['bytes']}",
        f"- Eligible exact-decision rows: {cohort['eligible_rows']}",
        f"- Eligible weather dates: {cohort['eligible_dates']}",
        f"- Calibration dates required: {result['contract']['minimum_calibration_dates']}",
        f"- Untouched dates required after freeze: {result['contract']['minimum_untouched_dates']}",
        "",
        "## Gate State",
        "",
    ]
    for name, passed in result["acceptance_checks"].items():
        lines.append(f"- {'PASS' if passed else 'PENDING'}: {name}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
