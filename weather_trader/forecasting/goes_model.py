"""Frozen market-relative calibration model for F5 GOES heating surprise."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression


@dataclass(frozen=True)
class GoesHeatingModelContract:
    version: str = "goes_dsr_market_relative_logit_v1"
    minimum_calibration_dates: int = 20
    minimum_untouched_dates: int = 20
    regularization_c: float = 0.25
    probability_clip: float = 1e-4
    base_features: tuple[str, ...] = (
        "logit_f3",
        "logit_market",
        "regime_mixed",
        "regime_cloudy",
    )
    surprise_features: tuple[str, ...] = (
        "radiation_surprise",
        "surprise_x_mixed",
        "surprise_x_cloudy",
    )
    surprise_thresholds: tuple[float, ...] = (0.10, 0.20)
    abstention_edge_thresholds: tuple[float, ...] = (0.00, 0.05, 0.10, 0.15)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class GoesHeatingModels:
    contract: GoesHeatingModelContract
    baseline: LogisticRegression
    challenger: LogisticRegression
    fit_dates: tuple[str, ...]

    def predict(self, rows: Sequence[Mapping[str, object]]) -> tuple[np.ndarray, np.ndarray]:
        base = self.baseline.predict_proba(design_matrix(rows, self.contract, False))[:, 1]
        challenger = self.challenger.predict_proba(
            design_matrix(rows, self.contract, True)
        )[:, 1]
        return base, challenger


def fit_goes_heating_models(
    rows: Sequence[Mapping[str, object]],
    contract: GoesHeatingModelContract = GoesHeatingModelContract(),
) -> GoesHeatingModels:
    dates = tuple(sorted({str(row["market_date"]) for row in rows}))
    if len(dates) < contract.minimum_calibration_dates:
        raise ValueError(
            f"GOES calibration needs {contract.minimum_calibration_dates} dates; got {len(dates)}"
        )
    labels = np.asarray([int(row["outcome_label"]) for row in rows], dtype=int)
    if len(set(labels.tolist())) < 2:
        raise ValueError("GOES calibration requires both outcome classes")
    weights = equal_date_weights(rows)
    baseline = _new_model(contract).fit(
        design_matrix(rows, contract, False), labels, sample_weight=weights
    )
    challenger = _new_model(contract).fit(
        design_matrix(rows, contract, True), labels, sample_weight=weights
    )
    return GoesHeatingModels(contract, baseline, challenger, dates)


def design_matrix(
    rows: Sequence[Mapping[str, object]],
    contract: GoesHeatingModelContract,
    include_surprise: bool,
) -> np.ndarray:
    output = []
    for row in rows:
        f3 = clipped_probability(row["f3_selected_token_probability"], contract)
        market = clipped_probability(row["market_selected_token_probability"], contract)
        regime = str(row["cloud_regime"])
        mixed = float(regime == "MIXED")
        cloudy = float(regime == "CLOUDY")
        values = [logit(f3), logit(market), mixed, cloudy]
        if include_surprise:
            surprise = float(row["radiation_surprise"])
            values.extend([surprise, surprise * mixed, surprise * cloudy])
        output.append(values)
    expected = len(contract.base_features) + (
        len(contract.surprise_features) if include_surprise else 0
    )
    matrix = np.asarray(output, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != expected or not np.isfinite(matrix).all():
        raise ValueError("GOES model features are incomplete or non-finite")
    return matrix


def equal_date_weights(rows: Sequence[Mapping[str, object]]) -> np.ndarray:
    counts: dict[str, int] = {}
    for row in rows:
        date = str(row["market_date"])
        counts[date] = counts.get(date, 0) + 1
    return np.asarray([1.0 / counts[str(row["market_date"])] for row in rows])


def clipped_probability(
    value: object, contract: GoesHeatingModelContract
) -> float:
    parsed = float(value)
    return min(max(parsed, contract.probability_clip), 1.0 - contract.probability_clip)


def logit(probability: float) -> float:
    return float(np.log(probability / (1.0 - probability)))


def _new_model(contract: GoesHeatingModelContract) -> LogisticRegression:
    return LogisticRegression(
        C=contract.regularization_c,
        solver="lbfgs",
        max_iter=2000,
        random_state=0,
    )
