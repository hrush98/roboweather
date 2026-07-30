from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from weather_trader.pricing.calibration import (
    CalibrationBaseline,
    WalkForwardCalibrationArtifact,
    WalkForwardPrediction,
)
from weather_trader.pricing.contracts import (
    V2A_PRICING_VERSION,
    MarketReferenceKind,
    PriceSheetV2A,
    V2SkipReason,
    stable_hash,
)
from weather_trader.pricing.dataset import V2ADatasetArtifact, V2ADatasetRow


PRIOR_OOF_RESIDUAL_QUANTILE = "prior_oof_market_date_overprediction_quantile"


@dataclass(frozen=True)
class V2APricingConfig:
    version: str = V2A_PRICING_VERSION
    calibration_baseline: CalibrationBaseline | None = None
    promotion_baseline_frozen: bool = False
    uncertainty_method: str = PRIOR_OOF_RESIDUAL_QUANTILE
    uncertainty_quantile: float = 0.80
    minimum_prior_oof_market_dates: int = 5
    minimum_uncertainty_reserve: float = 0.02
    minimum_profit_reserve: float = 0.05
    known_cost_reserve: float = 0.01
    tick_size: float = 0.01
    minimum_quote_price: float = 0.01
    extreme_raw_fair_threshold: float = 0.95
    minimum_gate_market_dates: int = 5
    current_window_days: int = 7
    untouched_forward_start_date: str | None = None

    def __post_init__(self) -> None:
        if self.uncertainty_method != PRIOR_OOF_RESIDUAL_QUANTILE:
            raise ValueError(f"unsupported uncertainty method: {self.uncertainty_method}")
        if not 0.0 < self.uncertainty_quantile <= 1.0:
            raise ValueError("uncertainty_quantile must be in (0, 1]")
        if self.minimum_prior_oof_market_dates < 1:
            raise ValueError("minimum_prior_oof_market_dates must be positive")
        for name in (
            "minimum_uncertainty_reserve",
            "minimum_profit_reserve",
            "known_cost_reserve",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name} must be in [0, 1)")
        if not 0.0 < self.tick_size < 1.0:
            raise ValueError("tick_size must be in (0, 1)")
        if not 0.0 < self.minimum_quote_price < 1.0:
            raise ValueError("minimum_quote_price must be in (0, 1)")
        if not 0.0 < self.extreme_raw_fair_threshold < 1.0:
            raise ValueError("extreme_raw_fair_threshold must be in (0, 1)")
        if self.minimum_gate_market_dates < 1:
            raise ValueError("minimum_gate_market_dates must be positive")
        if self.current_window_days < 1:
            raise ValueError("current_window_days must be positive")
        if self.untouched_forward_start_date is not None:
            date.fromisoformat(self.untouched_forward_start_date)

    @property
    def config_hash(self) -> str:
        payload = asdict(self)
        payload["calibration_baseline"] = (
            self.calibration_baseline.value if self.calibration_baseline is not None else None
        )
        return stable_hash(payload)


@dataclass(frozen=True)
class V2APriceSheetEvaluation:
    market_date: str
    station: str
    model_id: str
    selected_side: str
    lifecycle_horizon: str
    outcome_label: int
    evaluation_weight: float
    selected_entry_price: float | None
    v1_quote_price: float | None
    prior_oof_market_dates: int
    uncertainty_evidence_hash: str
    quality_flags: tuple[str, ...]
    sheet: PriceSheetV2A

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality_flags"] = list(self.quality_flags)
        payload["sheet"]["market_reference_kind"] = self.sheet.market_reference_kind.value
        payload["sheet"]["skip_reason"] = self.sheet.skip_reason.value if self.sheet.skip_reason else None
        return payload


@dataclass(frozen=True)
class V2APriceSheetArtifact:
    pricing_version: str
    pricing_config_hash: str
    calibration_version: str
    calibration_config_hash: str
    dataset_version: str
    signal_spec_id: str
    signal_spec_hash: str
    selected_calibration_baseline: str | None
    rows: tuple[V2APriceSheetEvaluation, ...]
    report: dict[str, Any]

    def manifest(self) -> dict[str, Any]:
        return {
            "pricing_version": self.pricing_version,
            "pricing_config_hash": self.pricing_config_hash,
            "calibration_version": self.calibration_version,
            "calibration_config_hash": self.calibration_config_hash,
            "dataset_version": self.dataset_version,
            "signal_spec_id": self.signal_spec_id,
            "signal_spec_hash": self.signal_spec_hash,
            "selected_calibration_baseline": self.selected_calibration_baseline,
            "rows": len(self.rows),
            "eligible_rows": sum(row.sheet.eligible for row in self.rows),
            "report": self.report,
        }


def build_v2a_price_sheets(
    dataset: V2ADatasetArtifact,
    calibration: WalkForwardCalibrationArtifact,
    *,
    config: V2APricingConfig | None = None,
) -> V2APriceSheetArtifact:
    config = config or V2APricingConfig()
    _validate_artifact_identity(dataset, calibration)
    dataset_rows = {row.decision_id: row for row in dataset.evaluation_rows}
    predictions = sorted(
        calibration.predictions,
        key=lambda row: (row.market_date, row.decision_id),
    )
    result: list[V2APriceSheetEvaluation] = []
    for prediction in predictions:
        source = dataset_rows.get(prediction.decision_id)
        if source is None or source.row_hash != prediction.source_row_hash:
            raise ValueError(f"calibration row cannot be reconstructed: {prediction.decision_id}")
        result.append(
            _build_evaluation(
                source,
                prediction,
                predictions,
                dataset_rows,
                calibration,
                config,
            )
        )

    report = build_v2a_report(result, config=config)
    return V2APriceSheetArtifact(
        pricing_version=config.version,
        pricing_config_hash=config.config_hash,
        calibration_version=calibration.calibration_version,
        calibration_config_hash=calibration.config_hash,
        dataset_version=dataset.dataset_version,
        signal_spec_id=dataset.signal_spec_id,
        signal_spec_hash=dataset.signal_spec_hash,
        selected_calibration_baseline=(
            config.calibration_baseline.value if config.calibration_baseline is not None else None
        ),
        rows=tuple(result),
        report=report,
    )


def build_v2a_report(
    rows: Sequence[V2APriceSheetEvaluation],
    *,
    config: V2APricingConfig,
) -> dict[str, Any]:
    broad = list(rows)
    latest = max((date.fromisoformat(row.market_date) for row in broad), default=None)
    current_cutoff = latest - timedelta(days=config.current_window_days - 1) if latest else None
    current = [
        row for row in broad if current_cutoff is not None and date.fromisoformat(row.market_date) >= current_cutoff
    ]
    forward = [
        row
        for row in broad
        if config.untouched_forward_start_date is not None
        and row.market_date >= config.untouched_forward_start_date
    ]
    windows = {
        "broad_evaluation": _window_report(broad),
        f"current_{config.current_window_days}d": _window_report(current),
        "untouched_forward": _window_report(forward),
    }
    diagnostics = {
        "model_family": _group_report(rows, lambda row: row.model_id),
        "side": _group_report(rows, lambda row: row.selected_side),
        "station": _group_report(rows, lambda row: row.station),
        "lifecycle_horizon": _group_report(rows, lambda row: row.lifecycle_horizon),
    }
    gate = _promotion_gate(broad, config)
    return {
        "selection_state": (
            "FROZEN_PROMOTION_BASELINE"
            if config.calibration_baseline is not None and config.promotion_baseline_frozen
            else (
                "RESEARCH_COMPARISON_BASELINE"
                if config.calibration_baseline is not None
                else "NO_CALIBRATOR_SELECTED_FAIL_CLOSED"
            )
        ),
        "selected_calibration_baseline": (
            config.calibration_baseline.value if config.calibration_baseline is not None else None
        ),
        "uncertainty": {
            "method": config.uncertainty_method,
            "quantile": config.uncertainty_quantile,
            "minimum_prior_oof_market_dates": config.minimum_prior_oof_market_dates,
            "minimum_reserve": config.minimum_uncertainty_reserve,
            "causal_rule": "only resolved OOF predictions with market_date strictly before the priced row",
        },
        "reserves": {
            "minimum_profit": config.minimum_profit_reserve,
            "known_cost": config.known_cost_reserve,
            "tick_size": config.tick_size,
        },
        "windows": windows,
        "diagnostics": diagnostics,
        "promotion_gate": gate,
        "comparison_notes": {
            "v1": "not comparable: the V1 consensus sleeve and pilot V2a signal specifications differ",
            "no_trade": "zero risk and zero PnL",
            "execution": "maximum-quote economics assume fills at the quote cap; no passive fill claim is made",
            "forward": (
                "not declared; untouched-forward window intentionally empty"
                if config.untouched_forward_start_date is None
                else f"market_date >= {config.untouched_forward_start_date}"
            ),
        },
    }


def write_v2a_price_sheet_artifact(artifact: V2APriceSheetArtifact, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "manifest.json", artifact.manifest())
    with (output_dir / "price_sheets.jsonl").open("w", encoding="utf-8") as handle:
        for row in artifact.rows:
            handle.write(json.dumps(row.canonical_payload(), sort_keys=True, separators=(",", ":")) + "\n")


def _build_evaluation(
    source: V2ADatasetRow,
    prediction: WalkForwardPrediction,
    all_predictions: Sequence[WalkForwardPrediction],
    dataset_rows: dict[str, V2ADatasetRow],
    calibration: WalkForwardCalibrationArtifact,
    config: V2APricingConfig,
) -> V2APriceSheetEvaluation:
    baseline = config.calibration_baseline
    calibrated = _selected_probability(prediction, baseline)
    decision_time = _parse_utc(source.decision_time_utc)
    prior = [
        row
        for row in all_predictions
        if row.market_date < prediction.market_date
        and row.decision_id in dataset_rows
        and _parse_utc(dataset_rows[row.decision_id].outcome_resolved_at_utc) < decision_time
    ]
    prior_date_residuals = _market_date_residuals(prior, baseline)
    reserve = max(
        config.minimum_uncertainty_reserve,
        _higher_quantile([max(0.0, value) for value in prior_date_residuals.values()], config.uncertainty_quantile),
    )
    conservative = _clamp(calibrated - reserve)
    skip_reason = _skip_reason(source, prediction, baseline, len(prior_date_residuals), config)
    maximum_quote = _floor_to_tick(
        conservative - config.minimum_profit_reserve - config.known_cost_reserve,
        config.tick_size,
    )
    if maximum_quote < config.minimum_quote_price:
        maximum_quote = None
        if skip_reason is None:
            skip_reason = V2SkipReason.NO_POSITIVE_QUOTE_AFTER_RESERVES
    if skip_reason is not None:
        maximum_quote = None

    evidence_payload = [
        {
            "market_date": market_date,
            "date_residual": residual,
            "prediction_hashes": sorted(
                stable_hash(
                    {
                        "decision_id": row.decision_id,
                        "source_row_hash": row.source_row_hash,
                        "probability": _selected_probability(row, baseline),
                        "outcome_label": row.outcome_label,
                        "evaluation_weight": row.evaluation_weight,
                    }
                )
                for row in prior
                if row.market_date == market_date
            ),
        }
        for market_date, residual in sorted(prior_date_residuals.items())
    ]
    calibrator_hash = _selected_calibrator_hash(prediction, baseline, calibration)
    sheet = PriceSheetV2A(
        price_sheet_version=config.version,
        signal_spec_id=source.signal_spec_id,
        signal_spec_hash=source.signal_spec_hash,
        decision_id=source.decision_id,
        decision_time_utc=source.decision_time_utc,
        quote_ready_time_utc=source.quote_ready_time_utc,
        model_ids=(source.model_id,),
        raw_token_fair=source.raw_model_fair,
        market_reference=source.market_reference,
        market_reference_kind=source.market_reference_kind,
        calibrator_version=calibrator_hash,
        calibrator_training_cutoff=prediction.training_cutoff_date_exclusive,
        calibrated_outcome_fair=calibrated,
        uncertainty_reserve=reserve,
        conservative_outcome_fair=conservative,
        minimum_profit_reserve=config.minimum_profit_reserve,
        known_cost_reserve=config.known_cost_reserve,
        maximum_quote_price=maximum_quote,
        eligible=skip_reason is None,
        skip_reason=skip_reason,
    )
    return V2APriceSheetEvaluation(
        market_date=source.market_date,
        station=source.station,
        model_id=source.model_id,
        selected_side=source.selected_side,
        lifecycle_horizon=source.lifecycle_horizon,
        outcome_label=source.outcome_label,
        evaluation_weight=prediction.evaluation_weight,
        selected_entry_price=source.selected_entry_price,
        v1_quote_price=None,
        prior_oof_market_dates=len(prior_date_residuals),
        uncertainty_evidence_hash=stable_hash(evidence_payload),
        quality_flags=tuple(sorted(set((*source.quality_flags, *prediction.quality_flags)))),
        sheet=sheet,
    )


def _skip_reason(
    source: V2ADatasetRow,
    prediction: WalkForwardPrediction,
    baseline: CalibrationBaseline | None,
    prior_market_dates: int,
    config: V2APricingConfig,
) -> V2SkipReason | None:
    if baseline is None:
        return V2SkipReason.CALIBRATOR_NOT_SELECTED
    if source.market_reference_stale:
        return V2SkipReason.STALE_MARKET_REFERENCE
    if source.market_reference_kind == MarketReferenceKind.MISSING or source.market_reference is None:
        return V2SkipReason.MISSING_MARKET_REFERENCE
    if baseline == CalibrationBaseline.MARKET_AWARE and prediction.market_probability is None:
        return V2SkipReason.MISSING_MARKET_REFERENCE
    if prior_market_dates < config.minimum_prior_oof_market_dates:
        return V2SkipReason.INSUFFICIENT_PRIOR_OOF_DATES
    return None


def _selected_probability(
    prediction: WalkForwardPrediction,
    baseline: CalibrationBaseline | None,
) -> float:
    if baseline is None or baseline == CalibrationBaseline.RAW_MODEL:
        return prediction.raw_model_probability
    if baseline == CalibrationBaseline.MARKET:
        return (
            prediction.market_probability
            if prediction.market_probability is not None
            else prediction.raw_model_probability
        )
    if baseline == CalibrationBaseline.POOLED_PLATT:
        return prediction.pooled_platt_probability
    if baseline == CalibrationBaseline.MARKET_AWARE:
        return prediction.market_aware_probability
    raise ValueError(f"unsupported calibration baseline: {baseline}")


def _selected_calibrator_hash(
    prediction: WalkForwardPrediction,
    baseline: CalibrationBaseline | None,
    calibration: WalkForwardCalibrationArtifact,
) -> str:
    if baseline == CalibrationBaseline.POOLED_PLATT:
        calibrator_hash = prediction.pooled_platt_calibrator_hash
        return f"{calibration.calibration_version}:{baseline.value}:{calibrator_hash}"
    if baseline == CalibrationBaseline.MARKET_AWARE:
        calibrator_hash = prediction.market_aware_calibrator_hash
        return f"{calibration.calibration_version}:{baseline.value}:{calibrator_hash}"
    baseline_name = baseline.value if baseline is not None else "unselected"
    baseline_hash = stable_hash(
        {
            "calibration_version": calibration.calibration_version,
            "config_hash": calibration.config_hash,
            "baseline": baseline.value if baseline is not None else None,
        }
    )
    return f"{calibration.calibration_version}:{baseline_name}:{baseline_hash}"


def _market_date_residuals(
    predictions: Sequence[WalkForwardPrediction],
    baseline: CalibrationBaseline | None,
) -> dict[str, float]:
    grouped: dict[str, list[WalkForwardPrediction]] = {}
    for row in predictions:
        if baseline in {CalibrationBaseline.MARKET, CalibrationBaseline.MARKET_AWARE} and row.market_probability is None:
            continue
        grouped.setdefault(row.market_date, []).append(row)
    result = {}
    for market_date, rows in grouped.items():
        weights = [max(0.0, row.evaluation_weight) for row in rows]
        denominator = sum(weights)
        if denominator <= 0.0:
            continue
        result[market_date] = sum(
            weight * (_selected_probability(row, baseline) - row.outcome_label)
            for row, weight in zip(rows, weights)
        ) / denominator
    return result


def _window_report(rows: Sequence[V2APriceSheetEvaluation]) -> dict[str, Any]:
    eligible = [row for row in rows if row.sheet.eligible and row.sheet.maximum_quote_price is not None]
    return {
        "rows": len(rows),
        "market_dates": len({row.market_date for row in rows}),
        "station_dates": len({(row.station, row.market_date) for row in rows}),
        "eligible_rows": len(eligible),
        "skip_reasons": dict(
            sorted(
                Counter(
                    row.sheet.skip_reason.value if row.sheet.skip_reason else "ELIGIBLE"
                    for row in rows
                ).items()
            )
        ),
        "probability_metrics": {
            "raw_model": _probability_metrics(rows, lambda row: row.sheet.raw_token_fair),
            "market": _probability_metrics(rows, lambda row: row.sheet.market_reference),
            "calibrated": _probability_metrics(rows, lambda row: row.sheet.calibrated_outcome_fair),
            "conservative": _probability_metrics(rows, lambda row: row.sheet.conservative_outcome_fair),
        },
        "average_prices": {
            "raw_model": _weighted_mean(rows, lambda row: row.sheet.raw_token_fair),
            "market": _weighted_mean(rows, lambda row: row.sheet.market_reference),
            "calibrated": _weighted_mean(rows, lambda row: row.sheet.calibrated_outcome_fair),
            "conservative": _weighted_mean(rows, lambda row: row.sheet.conservative_outcome_fair),
            "selected_entry": _weighted_mean(rows, lambda row: row.selected_entry_price),
            "v1_quote": _weighted_mean(rows, lambda row: row.v1_quote_price),
            "v2a_maximum_quote": _weighted_mean(eligible, lambda row: row.sheet.maximum_quote_price),
        },
        "theoretical_economics": {
            "v2a_maximum_quote": _price_economics(
                eligible,
                lambda row: row.sheet.maximum_quote_price,
            ),
            "selected_entry": _price_economics(rows, lambda row: row.selected_entry_price),
            "v1_quote": _price_economics(rows, lambda row: row.v1_quote_price),
            "no_trade": {
                "resolved_quotes": 0,
                "market_dates": 0,
                "risk_units": 0.0,
                "pnl_units": 0.0,
                "return_on_risk": None,
                "win_rate": None,
            },
        },
    }


def _group_report(
    rows: Sequence[V2APriceSheetEvaluation],
    key,
) -> dict[str, Any]:
    groups: dict[str, list[V2APriceSheetEvaluation]] = {}
    for row in rows:
        groups.setdefault(str(key(row)), []).append(row)
    return {name: _window_report(group) for name, group in sorted(groups.items())}


def _promotion_gate(
    rows: Sequence[V2APriceSheetEvaluation],
    config: V2APricingConfig,
) -> dict[str, Any]:
    broad = _window_report(rows)
    economics = broad["theoretical_economics"]["v2a_maximum_quote"]
    eligible = [row for row in rows if row.sheet.eligible and row.sheet.maximum_quote_price is not None]
    non_extreme = [
        row for row in eligible if row.sheet.raw_token_fair < config.extreme_raw_fair_threshold
    ]
    non_extreme_economics = _price_economics(
        non_extreme,
        lambda row: row.sheet.maximum_quote_price,
    )
    forward_rows = [
        row
        for row in rows
        if config.untouched_forward_start_date is not None
        and row.market_date >= config.untouched_forward_start_date
        and row.sheet.eligible
        and row.sheet.maximum_quote_price is not None
    ]
    forward_economics = _price_economics(
        forward_rows,
        lambda row: row.sheet.maximum_quote_price,
    )
    forward_non_extreme_economics = _price_economics(
        [
            row
            for row in forward_rows
            if row.sheet.raw_token_fair < config.extreme_raw_fair_threshold
        ],
        lambda row: row.sheet.maximum_quote_price,
    )
    reasons = []
    if config.calibration_baseline is None:
        reasons.append("NO_CALIBRATOR_SELECTED")
    elif not config.promotion_baseline_frozen:
        reasons.append("BASELINE_NOT_FROZEN_FOR_PROMOTION")
    if economics["resolved_quotes"] == 0:
        reasons.append("NO_ELIGIBLE_QUOTES")
    elif economics["return_on_risk"] is None or economics["return_on_risk"] <= 0.0:
        reasons.append("NON_POSITIVE_THEORETICAL_QUOTED_PRICE_EV")
    if economics["market_dates"] < config.minimum_gate_market_dates:
        reasons.append("INSUFFICIENT_ELIGIBLE_MARKET_DATES")
    if (
        non_extreme_economics["resolved_quotes"] == 0
        or non_extreme_economics["return_on_risk"] is None
        or non_extreme_economics["return_on_risk"] <= 0.0
    ):
        reasons.append("POSITIVE_EV_DEPENDS_ON_EXTREME_RAW_FAIRS")
    if config.untouched_forward_start_date is None:
        reasons.append("UNTOUCHED_FORWARD_WINDOW_NOT_DECLARED")
    else:
        if forward_economics["resolved_quotes"] == 0:
            reasons.append("NO_ELIGIBLE_UNTOUCHED_FORWARD_QUOTES")
        elif forward_economics["return_on_risk"] is None or forward_economics["return_on_risk"] <= 0.0:
            reasons.append("NON_POSITIVE_UNTOUCHED_FORWARD_QUOTED_PRICE_EV")
        if forward_economics["market_dates"] < config.minimum_gate_market_dates:
            reasons.append("INSUFFICIENT_UNTOUCHED_FORWARD_MARKET_DATES")
        if (
            forward_non_extreme_economics["resolved_quotes"] == 0
            or forward_non_extreme_economics["return_on_risk"] is None
            or forward_non_extreme_economics["return_on_risk"] <= 0.0
        ):
            reasons.append("UNTOUCHED_FORWARD_EV_DEPENDS_ON_EXTREME_RAW_FAIRS")
    return {
        "passed": not reasons,
        "disposition": "SHADOW_CANDIDATE" if not reasons else "RESEARCH_ONLY",
        "reasons": reasons,
        "minimum_market_dates": config.minimum_gate_market_dates,
        "extreme_raw_fair_threshold": config.extreme_raw_fair_threshold,
        "non_extreme_theoretical_quote_economics": non_extreme_economics,
        "untouched_forward_theoretical_quote_economics": forward_economics,
        "untouched_forward_non_extreme_theoretical_quote_economics": (
            forward_non_extreme_economics
        ),
    }


def _probability_metrics(rows: Sequence[V2APriceSheetEvaluation], getter) -> dict[str, Any]:
    available = [(row, getter(row)) for row in rows]
    available = [(row, float(value)) for row, value in available if value is not None and math.isfinite(float(value))]
    if not available:
        return {
            "rows": 0,
            "brier_score": None,
            "log_loss": None,
            "calibration_in_the_large": None,
            "calibration_slope": None,
            "reliability": [],
        }
    weights = np.asarray([max(0.0, row.evaluation_weight) for row, _ in available], dtype=float)
    probabilities = np.asarray([min(1.0 - 1e-6, max(1e-6, value)) for _, value in available])
    outcomes = np.asarray([row.outcome_label for row, _ in available], dtype=float)
    if weights.sum() <= 0.0:
        weights = np.ones(len(available), dtype=float)
    weights = weights / weights.sum()
    brier = float(np.sum(weights * np.square(probabilities - outcomes)))
    log_loss = float(
        -np.sum(weights * (outcomes * np.log(probabilities) + (1.0 - outcomes) * np.log(1.0 - probabilities)))
    )
    slope = None
    if len(set(outcomes.tolist())) == 2:
        logits = np.log(probabilities / (1.0 - probabilities)).reshape(-1, 1)
        model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000, random_state=0)
        try:
            model.fit(logits, outcomes.astype(int), sample_weight=weights)
            slope = float(model.coef_[0][0])
        except (ValueError, FloatingPointError):
            slope = None
    reliability = []
    for lower_index in range(10):
        lower = lower_index / 10.0
        upper = (lower_index + 1) / 10.0
        indexes = [
            index
            for index, probability in enumerate(probabilities)
            if lower <= probability < upper
            or (upper == 1.0 and lower <= probability <= upper)
        ]
        if not indexes:
            continue
        bin_weights = weights[indexes]
        bin_weights = bin_weights / bin_weights.sum()
        reliability.append(
            {
                "lower": lower,
                "upper": upper,
                "rows": len(indexes),
                "mean_probability": float(np.sum(bin_weights * probabilities[indexes])),
                "outcome_rate": float(np.sum(bin_weights * outcomes[indexes])),
            }
        )
    return {
        "rows": len(available),
        "brier_score": brier,
        "log_loss": log_loss,
        "calibration_in_the_large": float(np.sum(weights * outcomes) - np.sum(weights * probabilities)),
        "calibration_slope": slope,
        "reliability": reliability,
    }


def _price_economics(rows: Sequence[V2APriceSheetEvaluation], getter) -> dict[str, Any]:
    available = [(row, getter(row)) for row in rows]
    available = [
        (row, float(price))
        for row, price in available
        if price is not None and math.isfinite(float(price)) and float(price) > 0.0
    ]
    if not available:
        return {
            "resolved_quotes": 0,
            "market_dates": 0,
            "risk_units": 0.0,
            "pnl_units": 0.0,
            "return_on_risk": None,
            "win_rate": None,
        }
    weights = [max(0.0, row.evaluation_weight) for row, _ in available]
    risk = sum(weights)
    pnl = sum(
        weight * (row.outcome_label / price - 1.0)
        for (row, price), weight in zip(available, weights)
    )
    return {
        "resolved_quotes": len(available),
        "market_dates": len({row.market_date for row, _ in available}),
        "risk_units": risk,
        "pnl_units": pnl,
        "return_on_risk": pnl / risk if risk > 0.0 else None,
        "win_rate": (
            sum(weight * row.outcome_label for (row, _), weight in zip(available, weights)) / risk
            if risk > 0.0
            else None
        ),
    }


def _weighted_mean(rows: Sequence[V2APriceSheetEvaluation], getter) -> float | None:
    available = [(row, getter(row)) for row in rows]
    available = [(row, float(value)) for row, value in available if value is not None and math.isfinite(float(value))]
    denominator = sum(max(0.0, row.evaluation_weight) for row, _ in available)
    if denominator <= 0.0:
        return None
    return sum(max(0.0, row.evaluation_weight) * value for row, value in available) / denominator


def _higher_quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _floor_to_tick(value: float, tick_size: float) -> float:
    if not math.isfinite(value):
        return 0.0
    ticks = math.floor(max(0.0, value) / tick_size + 1e-12)
    return round(ticks * tick_size, 10)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _validate_artifact_identity(
    dataset: V2ADatasetArtifact,
    calibration: WalkForwardCalibrationArtifact,
) -> None:
    if dataset.dataset_version != calibration.dataset_version:
        raise ValueError("dataset/calibration version mismatch")
    if dataset.signal_spec_id != calibration.signal_spec_id:
        raise ValueError("dataset/calibration signal spec id mismatch")
    if dataset.signal_spec_hash != calibration.signal_spec_hash:
        raise ValueError("dataset/calibration signal spec hash mismatch")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)
