"""Immutable freeze and untouched evaluation for F5 GOES heating surprise."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np

from weather_trader.forecasting.goes_model import (
    GoesHeatingModelContract,
    GoesHeatingModels,
    fit_goes_heating_models,
)

UTC = timezone.utc
CALIBRATOR_FILENAME = "goes_heating_calibrator.joblib"
CALIBRATOR_MANIFEST_FILENAME = "goes_heating_calibrator_manifest.json"


def freeze_calibrator(
    rows: Sequence[Mapping[str, object]],
    out: Path,
    *,
    predecessor: str,
    predecessor_evaluation_fingerprint: str,
    untouched_forward_start_date: str,
    frozen_at_utc: datetime | None = None,
    contract: GoesHeatingModelContract = GoesHeatingModelContract(),
) -> tuple[GoesHeatingModels, dict[str, Any]]:
    """Fit exactly the earliest required dates and atomically freeze future use."""
    artifact_path, manifest_path = calibrator_paths(out)
    if artifact_path.exists() or manifest_path.exists():
        raise ValueError("F5 calibrator already exists; a changed rule requires a new version")
    now = (frozen_at_utc or datetime.now(UTC)).astimezone(UTC)
    start = date.fromisoformat(untouched_forward_start_date)
    if start <= now.date():
        raise ValueError("untouched forward start must be strictly future at freeze time")
    available_dates = sorted({str(row["market_date"]) for row in rows})
    if len(available_dates) < contract.minimum_calibration_dates:
        raise ValueError(
            f"GOES calibration needs {contract.minimum_calibration_dates} dates; "
            f"got {len(available_dates)}"
        )
    fit_dates = tuple(available_dates[: contract.minimum_calibration_dates])
    if start.isoformat() <= fit_dates[-1]:
        raise ValueError("untouched forward start must follow every calibration date")
    fit_set = set(fit_dates)
    fit_rows = [row for row in rows if str(row["market_date"]) in fit_set]
    models = fit_goes_heating_models(fit_rows, contract)
    manifest = {
        "schema_version": 1,
        "model_version": contract.version,
        "model_contract_fingerprint": contract.fingerprint,
        "predecessor": predecessor,
        "predecessor_evaluation_fingerprint": predecessor_evaluation_fingerprint,
        "fit_dates": list(fit_dates),
        "fit_rows": len(fit_rows),
        "calibration_rows_sha256": rows_sha256(fit_rows),
        "frozen_at_utc": now.isoformat(),
        "untouched_forward_start_date": start.isoformat(),
    }
    out.mkdir(parents=True, exist_ok=True)
    artifact_tmp = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    joblib.dump({"models": models, "manifest": manifest}, artifact_tmp)
    manifest_tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    artifact_tmp.replace(artifact_path)
    manifest_tmp.replace(manifest_path)
    return models, manifest


def load_calibrator(
    rows: Sequence[Mapping[str, object]],
    out: Path,
    *,
    predecessor: str,
    predecessor_evaluation_fingerprint: str,
    contract: GoesHeatingModelContract = GoesHeatingModelContract(),
) -> tuple[GoesHeatingModels, dict[str, Any]] | None:
    artifact_path, manifest_path = calibrator_paths(out)
    if not artifact_path.exists() and not manifest_path.exists():
        return None
    if not artifact_path.exists() or not manifest_path.exists():
        raise ValueError("F5 calibrator artifact and manifest must either both exist or both be absent")
    payload = joblib.load(artifact_path)
    manifest = json.loads(manifest_path.read_text())
    if payload.get("manifest") != manifest:
        raise ValueError("F5 calibrator manifest disagrees with the model artifact")
    if manifest.get("model_contract_fingerprint") != contract.fingerprint:
        raise ValueError("F5 calibrator model contract fingerprint changed")
    if manifest.get("predecessor") != predecessor:
        raise ValueError("F5 calibrator predecessor changed")
    if (
        manifest.get("predecessor_evaluation_fingerprint")
        != predecessor_evaluation_fingerprint
    ):
        raise ValueError("F5 calibrator predecessor evaluation fingerprint changed")
    fit_dates = set(str(item) for item in manifest["fit_dates"])
    fit_rows = [row for row in rows if str(row["market_date"]) in fit_dates]
    if len(fit_rows) != int(manifest["fit_rows"]):
        raise ValueError("F5 calibration row count changed after freeze")
    if rows_sha256(fit_rows) != manifest["calibration_rows_sha256"]:
        raise ValueError("F5 calibration rows changed after freeze")
    models = payload.get("models")
    if not isinstance(models, GoesHeatingModels):
        raise ValueError("F5 calibrator artifact has an unexpected model payload")
    if models.contract.fingerprint != contract.fingerprint:
        raise ValueError("F5 calibrator embedded model contract changed")
    return models, manifest


def evaluate_untouched(
    rows: Sequence[Mapping[str, object]],
    models: GoesHeatingModels,
    *,
    contract: GoesHeatingModelContract = GoesHeatingModelContract(),
    bootstrap_samples: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dates = sorted({str(row["market_date"]) for row in rows})
    if len(dates) < contract.minimum_untouched_dates:
        raise ValueError(
            f"GOES untouched evaluation needs {contract.minimum_untouched_dates} dates; "
            f"got {len(dates)}"
        )
    baseline, challenger = models.predict(rows)
    scored: list[dict[str, Any]] = []
    for row, base_probability, challenger_probability in zip(
        rows, baseline, challenger, strict=True
    ):
        item = dict(row)
        item["goes_baseline_selected_token_probability"] = float(base_probability)
        item["goes_challenger_selected_token_probability"] = float(challenger_probability)
        ask = finite(item.get("source_same_side_ask"))
        item["challenger_edge_to_displayed_ask"] = (
            float(challenger_probability) - ask if ask is not None else None
        )
        scored.append(item)
    samples = bootstrap_samples or contract.cluster_bootstrap_samples
    comparisons = {
        "challenger_minus_no_surprise_baseline": paired_comparison(
            scored,
            "goes_challenger_selected_token_probability",
            "goes_baseline_selected_token_probability",
            contract,
            samples,
        ),
        "challenger_minus_predecessor": paired_comparison(
            scored,
            "goes_challenger_selected_token_probability",
            "predecessor_selected_token_probability",
            contract,
            samples,
        ),
        "challenger_minus_market": paired_comparison(
            scored,
            "goes_challenger_selected_token_probability",
            "market_selected_token_probability",
            contract,
            samples,
        ),
        "challenger_minus_displayed_ask": paired_comparison(
            scored,
            "goes_challenger_selected_token_probability",
            "source_same_side_ask",
            contract,
            samples,
        ),
    }
    evaluation = {
        "rows": len(scored),
        "weather_dates": len(dates),
        "first_date": dates[0],
        "last_date": dates[-1],
        "selected_token_metrics": {
            field: binary_metrics(scored, field)
            for field in (
                "goes_challenger_selected_token_probability",
                "goes_baseline_selected_token_probability",
                "predecessor_selected_token_probability",
                "market_selected_token_probability",
                "source_same_side_ask",
            )
        },
        "comparisons": comparisons,
        "selected_token_calibration": calibration_diagnostics(scored, contract),
        "surprise_threshold_diagnostics": {
            str(threshold): {
                "positive": diagnostic_slice([
                    row
                    for row in scored
                    if float(row["radiation_surprise"]) >= threshold
                ]),
                "negative": diagnostic_slice([
                    row
                    for row in scored
                    if float(row["radiation_surprise"]) <= -threshold
                ]),
                "absolute": diagnostic_slice([
                    row
                    for row in scored
                    if abs(float(row["radiation_surprise"])) >= threshold
                ]),
            }
            for threshold in contract.surprise_thresholds
        },
        "station_diagnostics": grouped_diagnostics(scored, "station"),
        "regime_diagnostics": grouped_diagnostics(scored, "cloud_regime"),
        "abstention_curve": abstention_curve(scored, contract),
        "displayed_ask_limitation": (
            "source_same_side_ask is the contemporaneous displayed ask captured with the "
            "prediction snapshot; it is not a tape-valid useful-size execution claim"
        ),
    }
    return evaluation, scored


def calibrator_paths(out: Path) -> tuple[Path, Path]:
    return out / CALIBRATOR_FILENAME, out / CALIBRATOR_MANIFEST_FILENAME


def rows_sha256(rows: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("market_date")),
            str(row.get("station")),
            int(row.get("source_prediction_snapshot_id", 0)),
        ),
    )
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def binary_metrics(
    rows: Sequence[Mapping[str, object]], probability_field: str
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        probability = finite(row.get(probability_field))
        if probability is None:
            continue
        probability = min(max(probability, 1e-6), 1.0 - 1e-6)
        grouped.setdefault(str(row["market_date"]), []).append(
            (probability, int(row["outcome_label"]))
        )
    if not grouped:
        return {
            "rows": 0,
            "weather_dates": 0,
            "brier": None,
            "log_loss": None,
            "mean_probability": None,
            "outcome_rate": None,
        }
    per_date = []
    for values in grouped.values():
        per_date.append({
            "brier": float(np.mean([(probability - outcome) ** 2 for probability, outcome in values])),
            "log_loss": float(np.mean([log_loss(probability, outcome) for probability, outcome in values])),
            "probability": float(np.mean([probability for probability, _outcome in values])),
            "outcome": float(np.mean([outcome for _probability, outcome in values])),
        })
    return {
        "rows": sum(len(values) for values in grouped.values()),
        "weather_dates": len(grouped),
        "brier": float(np.mean([row["brier"] for row in per_date])),
        "log_loss": float(np.mean([row["log_loss"] for row in per_date])),
        "mean_probability": float(np.mean([row["probability"] for row in per_date])),
        "outcome_rate": float(np.mean([row["outcome"] for row in per_date])),
    }


def paired_comparison(
    rows: Sequence[Mapping[str, object]],
    candidate_field: str,
    reference_field: str,
    contract: GoesHeatingModelContract,
    bootstrap_samples: int,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        candidate = finite(row.get(candidate_field))
        reference = finite(row.get(reference_field))
        if candidate is None or reference is None:
            continue
        outcome = int(row["outcome_label"])
        candidate = min(max(candidate, 1e-6), 1.0 - 1e-6)
        reference = min(max(reference, 1e-6), 1.0 - 1e-6)
        grouped.setdefault(str(row["market_date"]), []).append((
            log_loss(candidate, outcome) - log_loss(reference, outcome),
            (candidate - outcome) ** 2 - (reference - outcome) ** 2,
        ))
    if not grouped:
        return {
            "rows": 0,
            "weather_dates": 0,
            "log_loss_delta": None,
            "brier_delta": None,
            "weather_date_clustered_95pct_ci": {"log_loss": None, "brier": None},
        }
    per_date = np.asarray([
        [float(np.mean([item[0] for item in values])), float(np.mean([item[1] for item in values]))]
        for _date, values in sorted(grouped.items())
    ])
    rng = np.random.default_rng(contract.cluster_bootstrap_seed)
    indices = rng.integers(0, len(per_date), size=(bootstrap_samples, len(per_date)))
    draws = per_date[indices].mean(axis=1)
    return {
        "rows": sum(len(values) for values in grouped.values()),
        "weather_dates": len(grouped),
        "log_loss_delta": float(per_date[:, 0].mean()),
        "brier_delta": float(per_date[:, 1].mean()),
        "weather_date_clustered_95pct_ci": {
            "log_loss": [float(value) for value in np.quantile(draws[:, 0], [0.025, 0.975])],
            "brier": [float(value) for value in np.quantile(draws[:, 1], [0.025, 0.975])],
        },
    }


def calibration_diagnostics(
    rows: Sequence[Mapping[str, object]], contract: GoesHeatingModelContract
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        date_key = str(row["market_date"])
        counts[date_key] = counts.get(date_key, 0) + 1
    probabilities = np.asarray([
        float(row["goes_challenger_selected_token_probability"]) for row in rows
    ])
    outcomes = np.asarray([int(row["outcome_label"]) for row in rows], dtype=float)
    weights = np.asarray([1.0 / counts[str(row["market_date"])] for row in rows])
    weights /= weights.sum()
    bias = float(np.sum(weights * (probabilities - outcomes)))
    bins = []
    ece = 0.0
    edges = contract.calibration_bin_edges
    for index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        mask = (probabilities >= lower) & (
            probabilities <= upper if index == len(edges) - 2 else probabilities < upper
        )
        if not mask.any():
            continue
        bin_weight = float(weights[mask].sum())
        mean_probability = float(np.average(probabilities[mask], weights=weights[mask]))
        outcome_rate = float(np.average(outcomes[mask], weights=weights[mask]))
        ece += bin_weight * abs(mean_probability - outcome_rate)
        bins.append({
            "lower": lower,
            "upper": upper,
            "rows": int(mask.sum()),
            "mean_probability": mean_probability,
            "outcome_rate": outcome_rate,
            "date_equal_weight": bin_weight,
        })
    return {
        "absolute_bias": abs(bias),
        "signed_bias": bias,
        "expected_calibration_error": float(ece),
        "maximum_absolute_bias": contract.maximum_absolute_calibration_bias,
        "maximum_expected_calibration_error": contract.maximum_expected_calibration_error,
        "passes": (
            abs(bias) <= contract.maximum_absolute_calibration_bias
            and ece <= contract.maximum_expected_calibration_error
        ),
        "bins": bins,
    }


def grouped_diagnostics(
    rows: Sequence[Mapping[str, object]], key: str
) -> dict[str, Any]:
    values = sorted({str(row[key]) for row in rows})
    return {
        value: diagnostic_slice([row for row in rows if str(row[key]) == value])
        for value in values
    }


def diagnostic_slice(rows: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    challenger = binary_metrics(rows, "goes_challenger_selected_token_probability")
    baseline = binary_metrics(rows, "goes_baseline_selected_token_probability")
    market = binary_metrics(rows, "market_selected_token_probability")
    displayed_ask = binary_metrics(rows, "source_same_side_ask")
    return {
        "rows": len(rows),
        "weather_dates": len({str(row["market_date"]) for row in rows}),
        "challenger": challenger,
        "no_surprise_baseline": baseline,
        "market": market,
        "displayed_ask": displayed_ask,
        "challenger_minus_no_surprise_log_loss": difference(
            challenger.get("log_loss"), baseline.get("log_loss")
        ),
        "challenger_minus_market_log_loss": difference(
            challenger.get("log_loss"), market.get("log_loss")
        ),
        "challenger_minus_displayed_ask_log_loss": difference(
            challenger.get("log_loss"), displayed_ask.get("log_loss")
        ),
    }


def difference(candidate: object, reference: object) -> float | None:
    if candidate is None or reference is None:
        return None
    return float(candidate) - float(reference)


def abstention_curve(
    rows: Sequence[Mapping[str, object]], contract: GoesHeatingModelContract
) -> list[dict[str, Any]]:
    output = []
    for threshold in contract.abstention_edge_thresholds:
        eligible = []
        for row in rows:
            ask = finite(row.get("source_same_side_ask"))
            probability = finite(row.get("goes_challenger_selected_token_probability"))
            if ask is None or probability is None or probability - ask < threshold:
                continue
            eligible.append({
                "market_date": str(row["market_date"]),
                "edge": probability - ask,
                "unit_pnl_at_displayed_ask": int(row["outcome_label"]) - ask,
            })
        grouped: dict[str, list[Mapping[str, object]]] = {}
        for row in eligible:
            grouped.setdefault(str(row["market_date"]), []).append(row)
        output.append({
            "minimum_predicted_edge": threshold,
            "rows": len(eligible),
            "weather_dates": len(grouped),
            "mean_predicted_edge": (
                float(np.mean([float(row["edge"]) for row in eligible])) if eligible else None
            ),
            "date_equal_mean_unit_pnl_at_displayed_ask": (
                float(np.mean([
                    np.mean([float(row["unit_pnl_at_displayed_ask"]) for row in values])
                    for values in grouped.values()
                ]))
                if grouped
                else None
            ),
        })
    return output


def log_loss(probability: float, outcome: int) -> float:
    return -(outcome * math.log(probability) + (1 - outcome) * math.log(1.0 - probability))


def finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
