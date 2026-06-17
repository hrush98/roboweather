from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from weather_trader.calibration.bucket_probability import BucketProbabilityCalibrator


def test_model_station_fit_preferred_over_model_global(tmp_path: Path) -> None:
    path = _artifact(
        tmp_path,
        [
            _fit("model-a", "*", "model_global", intercept=-1.0, coef=0.5, n=1000),
            _fit("model-a", "KATL", "model_station", intercept=0.0, coef=1.0, n=200),
        ],
    )

    result = BucketProbabilityCalibrator.from_path(path).calibrate(model_name="model-a", station="KATL", raw_fair_yes=0.80)

    assert result.applied is True
    assert result.fit_scope == "model_station"
    assert result.fit_n == 200
    assert result.calibrated_fair_yes == pytest.approx(0.80)
    assert result.calibrated_fair_yes + result.calibrated_fair_no == pytest.approx(1.0)


def test_model_global_fallback_for_missing_station_fit(tmp_path: Path) -> None:
    path = _artifact(
        tmp_path,
        [
            _fit("model-a", "*", "model_global", intercept=0.0, coef=1.0, n=1000),
        ],
    )

    result = BucketProbabilityCalibrator.from_path(path).calibrate(model_name="model-a", station="KBOS", raw_fair_yes=0.35)

    assert result.applied is True
    assert result.fit_scope == "model_global"
    assert result.fit_station == "*"
    assert result.calibrated_fair_yes == pytest.approx(0.35)


def test_missing_model_fit_returns_raw_probability(tmp_path: Path) -> None:
    path = _artifact(
        tmp_path,
        [
            _fit("model-a", "*", "model_global", intercept=0.0, coef=1.0, n=1000),
        ],
    )

    result = BucketProbabilityCalibrator.from_path(path).calibrate(model_name="model-b", station="KATL", raw_fair_yes=0.42)

    assert result.applied is False
    assert result.fit_scope is None
    assert result.calibrated_fair_yes == pytest.approx(0.42)
    assert result.calibrated_fair_no == pytest.approx(0.58)
    assert result.metadata["reason"] == "missing_fit"


def test_logit_platt_formula_is_applied(tmp_path: Path) -> None:
    path = _artifact(
        tmp_path,
        [
            _fit("model-a", "*", "model_global", intercept=-0.25, coef=0.5, n=1000),
        ],
    )

    result = BucketProbabilityCalibrator.from_path(path).calibrate(model_name="model-a", station="KATL", raw_fair_yes=0.80)

    expected = 1.0 / (1.0 + math.exp(-(-0.25 + 0.5 * math.log(0.80 / 0.20))))
    assert result.calibrated_fair_yes == pytest.approx(expected)


def test_bad_artifact_schema_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 1, "kind": "bucket_yes_platt_calibration", "feature": "logit"}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing fits"):
        BucketProbabilityCalibrator.from_path(path)


def _artifact(tmp_path: Path, fits: list[dict[str, object]]) -> Path:
    path = tmp_path / "artifact.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "bucket_yes_platt_calibration",
                "feature": "logit",
                "fits": fits,
            }
        ),
        encoding="utf-8",
    )
    return path


def _fit(
    model_name: str,
    station: str,
    scope: str,
    *,
    intercept: float,
    coef: float,
    n: int,
) -> dict[str, object]:
    return {
        "model_name": model_name,
        "station": station,
        "scope": scope,
        "intercept": intercept,
        "coef": coef,
        "n": n,
    }
