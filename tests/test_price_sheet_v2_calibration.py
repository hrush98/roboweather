from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from weather_trader.pricing.calibration import (
    CalibrationBaseline,
    WalkForwardCalibrationConfig,
    load_v2a_dataset_artifact,
    walk_forward_calibration,
    write_calibration_artifact,
)
from weather_trader.pricing.contracts import (
    V2A_DATASET_VERSION,
    DatasetRole,
    MarketReferenceKind,
    OutcomeLabelSource,
)
from weather_trader.pricing.dataset import (
    V2ADatasetArtifact,
    V2ADatasetRow,
    write_v2a_dataset_artifact,
)


def test_walk_forward_folds_expand_without_future_date_leakage() -> None:
    artifact = _artifact()
    config = WalkForwardCalibrationConfig(min_training_market_dates=2)

    result = walk_forward_calibration(artifact, config=config)

    assert [row.market_date for row in result.predictions] == [
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]
    pooled = [row for row in result.calibrators if row.baseline == CalibrationBaseline.POOLED_PLATT]
    assert [row.training_cutoff_date_exclusive for row in pooled] == [
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]
    assert [row.training_rows for row in pooled] == [2, 3, 4]
    assert [row.training_market_dates for row in pooled] == [2, 3, 4]
    assert all(row.fallback is None for row in pooled)

    changed_future = replace(
        artifact,
        evaluation_rows=(
            artifact.evaluation_rows[0],
            artifact.evaluation_rows[1],
            replace(artifact.evaluation_rows[2], outcome_label=1 - artifact.evaluation_rows[2].outcome_label),
        ),
    )
    changed = walk_forward_calibration(changed_future, config=config)
    assert result.predictions[0] == changed.predictions[0]
    assert result.predictions[1] == changed.predictions[1]
    assert result.predictions[2].pooled_platt_probability == pytest.approx(
        changed.predictions[2].pooled_platt_probability
    )


def test_market_aware_baseline_falls_back_explicitly_when_reference_is_missing() -> None:
    artifact = _artifact()
    missing = replace(
        artifact.evaluation_rows[0],
        market_reference=None,
        market_reference_kind=MarketReferenceKind.MISSING,
    )
    artifact = replace(
        artifact,
        evaluation_rows=(missing, *artifact.evaluation_rows[1:]),
    )

    result = walk_forward_calibration(
        artifact,
        config=WalkForwardCalibrationConfig(min_training_market_dates=2),
    )

    first = result.predictions[0]
    assert first.market_probability is None
    assert first.market_aware_probability == pytest.approx(first.pooled_platt_probability)
    assert "MARKET_AWARE_EVALUATION_REFERENCE_MISSING" in first.quality_flags
    assert result.report["metrics"]["market"]["rows"] == 2
    assert result.report["metrics"]["raw_model"]["rows"] == 3
    assert result.report["market_missing_rows"] == 1


def test_sparse_fold_uses_versioned_raw_and_pooled_fallbacks() -> None:
    result = walk_forward_calibration(
        _artifact(),
        config=WalkForwardCalibrationConfig(min_training_market_dates=10),
    )

    first_prediction = result.predictions[0]
    first_folds = [row for row in result.calibrators if row.evaluation_date == "2026-07-03"]
    assert {row.fallback for row in first_folds} == {"INSUFFICIENT_MARKET_DATES"}
    assert first_prediction.pooled_platt_probability == pytest.approx(first_prediction.raw_model_probability)
    assert first_prediction.market_aware_probability == pytest.approx(first_prediction.pooled_platt_probability)
    assert all(row.calibrator_hash for row in first_folds)


def test_dataset_loader_and_calibration_writer_round_trip(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    calibration_dir = tmp_path / "calibration"
    source = _artifact()
    write_v2a_dataset_artifact(source, dataset_dir)

    loaded = load_v2a_dataset_artifact(dataset_dir)
    result = walk_forward_calibration(
        loaded,
        config=WalkForwardCalibrationConfig(min_training_market_dates=2),
    )
    write_calibration_artifact(result, calibration_dir)

    manifest = json.loads((calibration_dir / "manifest.json").read_text(encoding="utf-8"))
    prediction_lines = (calibration_dir / "walk_forward_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    calibrator_lines = (calibration_dir / "calibrators.jsonl").read_text(encoding="utf-8").splitlines()
    assert manifest["signal_spec_hash"] == source.signal_spec_hash
    assert manifest["prediction_rows"] == 3
    assert len(prediction_lines) == 3
    assert len(calibrator_lines) == 6
    assert manifest["report"]["metrics"]["market_aware"]["brier_score"] is not None


def _artifact() -> V2ADatasetArtifact:
    fit_rows = (
        _row(1, "2026-07-01", 0, 0.20, 0.25, DatasetRole.CALIBRATION_FIT),
        _row(2, "2026-07-02", 1, 0.80, 0.75, DatasetRole.CALIBRATION_FIT),
    )
    evaluation_rows = (
        _row(3, "2026-07-03", 0, 0.30, 0.35, DatasetRole.FROZEN_POLICY_EVALUATION),
        _row(4, "2026-07-04", 1, 0.70, 0.65, DatasetRole.FROZEN_POLICY_EVALUATION),
        _row(5, "2026-07-05", 1, 0.60, 0.55, DatasetRole.FROZEN_POLICY_EVALUATION),
    )
    return V2ADatasetArtifact(
        dataset_version=V2A_DATASET_VERSION,
        signal_spec_id="test_signal_v1",
        signal_spec_hash="signal-hash",
        fit_cutoff_date_exclusive="2026-07-03",
        evaluation_start_date="2026-07-03",
        evaluation_end_date="2026-07-05",
        fit_rows=fit_rows,
        evaluation_rows=evaluation_rows,
        diagnostics={},
    )


def _row(
    id_: int,
    market_date: str,
    outcome: int,
    raw_fair: float,
    market_reference: float,
    role: DatasetRole,
) -> V2ADatasetRow:
    timestamp = f"{market_date}T17:01:00+00:00"
    return V2ADatasetRow(
        dataset_version=V2A_DATASET_VERSION,
        dataset_role=role,
        signal_spec_id="test_signal_v1",
        signal_spec_hash="signal-hash",
        decision_id=f"decision-{id_}",
        source_prediction_snapshot_ids=(id_,),
        source_snapshot_timestamp_utc=timestamp,
        decision_time_utc=f"{market_date}T17:00:00+00:00",
        decision_time_local=f"{market_date}T13:00:00-04:00",
        quote_ready_time_utc=timestamp,
        latest_observation_time_utc=f"{market_date}T16:50:00+00:00",
        station="KATL",
        market_date=market_date,
        market_family="HIGH_TEMP",
        lifecycle_horizon="d0_late",
        model_id="test-model",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        selected_market_id=f"market-{id_}",
        selected_bucket="74-75F",
        selected_side="BUY_NO",
        raw_model_fair=raw_fair,
        selected_entry_price=0.20,
        selected_edge=raw_fair - 0.20,
        market_reference=market_reference,
        market_reference_kind=MarketReferenceKind.MIDPOINT,
        market_reference_timestamp_utc=f"{market_date}T17:00:58+00:00",
        market_reference_age_seconds=2.0,
        market_reference_stale=False,
        outcome_label=outcome,
        outcome_label_source=OutcomeLabelSource.IEM_ASOS_RESEARCH_HIGH,
        final_high_tmpf=80.0,
        outcome_source="IEM_ASOS",
        outcome_resolved_at_utc=f"{market_date}T23:00:00+00:00",
        venue_resolution_source=None,
        is_forward=True,
        station_date_cluster_weight=1.0,
        market_date_cluster_weight=1.0,
        quality_flags=("OUTCOME_SOURCE_NOT_VENUE_ALIGNED",),
        row_hash=f"row-hash-{id_}",
    )
