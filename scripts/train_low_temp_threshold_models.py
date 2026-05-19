#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from weather_trader.config import ensure_directories
from weather_trader.features.low_dataset_builder import build_default_low_dataset
from weather_trader.forecasts.hrrr_v2 import materialize_hrrr_v2_features
from weather_trader.models.low_temp_classifier import (
    build_low_bucket_reports,
    build_low_reliability_report,
    build_low_station_report,
    build_low_validation_predictions,
    save_low_artifacts,
    train_low_temp_classifier,
)
from weather_trader.stations.metadata import get_station


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and train daily low-temperature threshold models.")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--stations", default="initial5", help="'initial5', 'all', or comma-separated station IDs.")
    parser.add_argument("--validation-year", type=int, default=2025)
    parser.add_argument("--raw-output", default="data/raw/dataset_low_2022-01-01_2025-12-31_initial5.csv")
    parser.add_argument("--hrrr-cache", default="/home/maxrush/General/roboweather/data/cache/hrrr_v2.sqlite")
    parser.add_argument("--hrrr-output", default="data/processed/dataset_low_2022_2025_initial5_hrrr_v2.csv")
    parser.add_argument("--obs-model-output", default="data/models/low_mvp_obs_2022_2025.joblib")
    parser.add_argument("--hrrr-model-output", default="data/models/low_mvp_hrrr_v2_obs_2022_2025.joblib")
    parser.add_argument("--obs-report-dir", default="data/reports/low_mvp_obs_2022_2025")
    parser.add_argument("--hrrr-report-dir", default="data/reports/low_mvp_hrrr_v2_obs_2022_2025")
    parser.add_argument("--skip-build", action="store_true", help="Use --raw-output if it already exists.")
    parser.add_argument("--skip-hrrr", action="store_true", help="Use --hrrr-output if it already exists.")
    args = parser.parse_args()

    ensure_directories()
    raw_path = Path(args.raw_output)
    hrrr_path = Path(args.hrrr_output)

    if args.skip_build:
        low_dataset = pd.read_csv(raw_path)
    else:
        station_ids, initial_only = _parse_stations(args.stations)
        low_dataset = build_default_low_dataset(
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            initial_only=initial_only,
            station_ids=station_ids,
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        low_dataset.to_csv(raw_path, index=False)

    if args.skip_hrrr:
        hrrr_dataset = pd.read_csv(hrrr_path)
    else:
        hrrr_dataset = materialize_hrrr_v2_features(low_dataset, Path(args.hrrr_cache))
        if "hrrr_remaining_min" in hrrr_dataset:
            hrrr_dataset["hrrr_remaining_min_minus_threshold"] = hrrr_dataset["hrrr_remaining_min"] - hrrr_dataset["threshold"]
        hrrr_path.parent.mkdir(parents=True, exist_ok=True)
        hrrr_dataset.to_csv(hrrr_path, index=False)

    obs_artifacts = train_low_temp_classifier(low_dataset, validation_year=args.validation_year)
    save_low_artifacts(obs_artifacts, Path(args.obs_model_output))
    _write_reports(low_dataset, obs_artifacts, Path(args.obs_report_dir), args.validation_year)

    hrrr_train = hrrr_dataset.loc[hrrr_dataset["hrrr_remaining_min"].notna()].copy()
    hrrr_artifacts = train_low_temp_classifier(hrrr_train, validation_year=args.validation_year)
    save_low_artifacts(hrrr_artifacts, Path(args.hrrr_model_output))
    _write_reports(hrrr_train, hrrr_artifacts, Path(args.hrrr_report_dir), args.validation_year)

    print(
        json.dumps(
            {
                "raw_dataset": str(raw_path),
                "hrrr_dataset": str(hrrr_path),
                "obs_model": str(args.obs_model_output),
                "hrrr_model": str(args.hrrr_model_output),
                "obs": {
                    "train_rows": obs_artifacts.train_rows,
                    "validation_rows": obs_artifacts.validation_rows,
                    "feature_columns": obs_artifacts.feature_columns,
                    "metrics": obs_artifacts.metrics,
                },
                "hrrr": {
                    "train_rows": hrrr_artifacts.train_rows,
                    "validation_rows": hrrr_artifacts.validation_rows,
                    "feature_columns": hrrr_artifacts.feature_columns,
                    "metrics": hrrr_artifacts.metrics,
                },
            },
            indent=2,
        )
    )


def _parse_stations(value: str) -> tuple[list[str] | None, bool]:
    cleaned = value.strip()
    if cleaned == "initial5":
        return None, True
    if cleaned == "all":
        return None, False
    station_ids = [station.strip().upper() for station in cleaned.split(",") if station.strip()]
    for station_id in station_ids:
        get_station(station_id)
    return station_ids, False


def _write_reports(dataset: pd.DataFrame, artifacts, report_dir: Path, validation_year: int) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    predictions = build_low_validation_predictions(
        dataset=dataset,
        model=artifacts.model,
        feature_columns=artifacts.feature_columns,
        validation_year=validation_year,
    )
    predictions.to_csv(report_dir / "validation_predictions.csv", index=False)
    for name, frame in build_low_bucket_reports(predictions).items():
        frame.to_csv(report_dir / f"bucket_{name}.csv", index=False)
    build_low_reliability_report(predictions).to_csv(report_dir / "reliability.csv", index=False)
    build_low_station_report(predictions).to_csv(report_dir / "station_metrics.csv", index=False)


if __name__ == "__main__":
    main()
