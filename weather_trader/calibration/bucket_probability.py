from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BUCKET_CALIBRATION_PATH = Path.home() / ".local/state/roboweather/bucket_calibration_pm_us12_high_temp.json"
BUCKET_CALIBRATION_MODES = ("off", "apply")
EPSILON = 1e-6


@dataclass(frozen=True)
class BucketCalibrationFit:
    model_name: str
    station: str
    scope: str
    intercept: float
    coef: float
    n: int


@dataclass(frozen=True)
class BucketCalibrationResult:
    raw_fair_yes: float
    raw_fair_no: float
    calibrated_fair_yes: float
    calibrated_fair_no: float
    fit_scope: str | None
    fit_station: str | None
    fit_n: int | None
    applied: bool
    metadata: dict[str, Any]


class BucketProbabilityCalibrator:
    def __init__(self, payload: dict[str, Any], path: Path) -> None:
        self.path = path
        self.payload = _validate_payload(payload, path)
        self.version = int(self.payload["version"])
        self.kind = str(self.payload["kind"])
        self.feature = str(self.payload["feature"])
        self.generated_at = self.payload.get("generated_at")
        self.market_family = self.payload.get("market_family")
        self.model_family = self.payload.get("model_family")
        self._fits: dict[tuple[str, str], BucketCalibrationFit] = {}
        for row in self.payload["fits"]:
            fit = BucketCalibrationFit(
                model_name=str(row["model_name"]),
                station=str(row["station"]).upper(),
                scope=str(row["scope"]),
                intercept=float(row["intercept"]),
                coef=float(row["coef"]),
                n=int(row["n"]),
            )
            self._fits[(fit.model_name, fit.station)] = fit

    @classmethod
    def from_path(cls, path: Path | str) -> BucketProbabilityCalibrator:
        resolved = Path(path).expanduser()
        with resolved.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(payload, resolved)

    def calibrate(self, *, model_name: str, station: str, raw_fair_yes: float) -> BucketCalibrationResult:
        raw_yes = _clip_probability(raw_fair_yes)
        raw_no = 1.0 - raw_yes
        fit = self._lookup_fit(model_name=model_name, station=station)
        if fit is None:
            metadata = self._metadata(
                model_name=model_name,
                station=station,
                raw_yes=raw_yes,
                calibrated_yes=raw_yes,
                fit=None,
                reason="missing_fit",
            )
            return BucketCalibrationResult(
                raw_fair_yes=raw_yes,
                raw_fair_no=raw_no,
                calibrated_fair_yes=raw_yes,
                calibrated_fair_no=raw_no,
                fit_scope=None,
                fit_station=None,
                fit_n=None,
                applied=False,
                metadata=metadata,
            )

        calibrated_yes = _clip_probability(_sigmoid(fit.intercept + fit.coef * _logit(raw_yes)))
        metadata = self._metadata(
            model_name=model_name,
            station=station,
            raw_yes=raw_yes,
            calibrated_yes=calibrated_yes,
            fit=fit,
            reason="applied",
        )
        return BucketCalibrationResult(
            raw_fair_yes=raw_yes,
            raw_fair_no=raw_no,
            calibrated_fair_yes=calibrated_yes,
            calibrated_fair_no=1.0 - calibrated_yes,
            fit_scope=fit.scope,
            fit_station=fit.station,
            fit_n=fit.n,
            applied=True,
            metadata=metadata,
        )

    def _lookup_fit(self, *, model_name: str, station: str) -> BucketCalibrationFit | None:
        station_key = str(station).upper()
        return self._fits.get((model_name, station_key)) or self._fits.get((model_name, "*"))

    def _metadata(
        self,
        *,
        model_name: str,
        station: str,
        raw_yes: float,
        calibrated_yes: float,
        fit: BucketCalibrationFit | None,
        reason: str,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "enabled": True,
            "mode": "apply",
            "reason": reason,
            "artifact_path": str(self.path),
            "artifact_version": self.version,
            "artifact_kind": self.kind,
            "artifact_generated_at": self.generated_at,
            "artifact_feature": self.feature,
            "model_family": self.model_family,
            "market_family": self.market_family,
            "model_name": model_name,
            "station": station,
            "raw_fair_yes": raw_yes,
            "raw_fair_no": 1.0 - raw_yes,
            "calibrated_fair_yes": calibrated_yes,
            "calibrated_fair_no": 1.0 - calibrated_yes,
        }
        if fit is not None:
            metadata.update(
                {
                    "fit_scope": fit.scope,
                    "fit_station": fit.station,
                    "fit_n": fit.n,
                    "fit_intercept": fit.intercept,
                    "fit_coef": fit.coef,
                }
            )
        return metadata


def load_bucket_probability_calibrator(
    *,
    path: Path | str | None,
    mode: str,
) -> BucketProbabilityCalibrator | None:
    if mode not in BUCKET_CALIBRATION_MODES:
        raise ValueError(f"bucket calibration mode must be one of {', '.join(BUCKET_CALIBRATION_MODES)}")
    if mode == "off":
        return None
    if path is None:
        return None
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return None
    return BucketProbabilityCalibrator.from_path(resolved)


def disabled_metadata(*, path: Path | str | None, mode: str, reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": mode,
        "reason": reason,
        "artifact_path": str(Path(path).expanduser()) if path is not None else None,
    }


def _validate_payload(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid bucket calibration JSON at {path}: top-level value must be an object")
    required = {"version", "kind", "feature", "fits"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"Invalid bucket calibration JSON at {path}: missing {', '.join(missing)}")
    if payload.get("version") != 1:
        raise ValueError(f"Invalid bucket calibration JSON at {path}: expected version 1")
    if payload.get("kind") != "bucket_yes_platt_calibration":
        raise ValueError(f"Invalid bucket calibration JSON at {path}: expected bucket_yes_platt_calibration")
    if payload.get("feature") != "logit":
        raise ValueError(f"Invalid bucket calibration JSON at {path}: only logit artifacts are supported")
    fits = payload.get("fits")
    if not isinstance(fits, list) or not fits:
        raise ValueError(f"Invalid bucket calibration JSON at {path}: fits must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(fits):
        if not isinstance(row, dict):
            raise ValueError(f"Invalid bucket calibration JSON at {path}: fit {index} must be an object")
        fit_required = {"model_name", "station", "scope", "intercept", "coef", "n"}
        fit_missing = sorted(fit_required - set(row))
        if fit_missing:
            raise ValueError(f"Invalid bucket calibration JSON at {path}: fit {index} missing {', '.join(fit_missing)}")
        scope = str(row["scope"])
        station = str(row["station"]).upper()
        if scope not in {"model_station", "model_global"}:
            raise ValueError(f"Invalid bucket calibration JSON at {path}: fit {index} has invalid scope {scope}")
        if scope == "model_global" and station != "*":
            raise ValueError(f"Invalid bucket calibration JSON at {path}: model_global fit {index} must use station '*'")
        key = (str(row["model_name"]), station)
        if key in seen:
            raise ValueError(f"Invalid bucket calibration JSON at {path}: duplicate fit for {key[0]} {key[1]}")
        seen.add(key)
        try:
            float(row["intercept"])
            float(row["coef"])
            n = int(row["n"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid bucket calibration JSON at {path}: fit {index} has non-numeric parameters") from exc
        if n <= 0:
            raise ValueError(f"Invalid bucket calibration JSON at {path}: fit {index} n must be positive")
    return payload


def _clip_probability(value: float) -> float:
    if not math.isfinite(float(value)):
        return 0.5
    return min(max(float(value), EPSILON), 1.0 - EPSILON)


def _logit(value: float) -> float:
    value = _clip_probability(value)
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)
