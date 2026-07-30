from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from weather_trader.pricing.calibration import (
    CalibrationBaseline,
    WalkForwardCalibrationConfig,
    walk_forward_calibration,
)
from weather_trader.pricing.contracts import (
    V2A_DATASET_VERSION,
    DatasetRole,
    MarketReferenceKind,
    OutcomeLabelSource,
    V2SkipReason,
)
from weather_trader.pricing.dataset import V2ADatasetArtifact, V2ADatasetRow
from weather_trader.pricing.price_sheet_v2 import (
    V2APricingConfig,
    build_v2a_price_sheets,
    write_v2a_price_sheet_artifact,
)


def test_default_price_sheet_fails_closed_without_selected_calibrator() -> None:
    dataset = _artifact()
    calibration = _calibration(dataset)

    result = build_v2a_price_sheets(dataset, calibration)

    assert result.selected_calibration_baseline is None
    assert all(not row.sheet.eligible for row in result.rows)
    assert {row.sheet.skip_reason for row in result.rows} == {
        V2SkipReason.CALIBRATOR_NOT_SELECTED
    }
    assert result.report["promotion_gate"]["disposition"] == "RESEARCH_ONLY"
    assert "NO_CALIBRATOR_SELECTED" in result.report["promotion_gate"]["reasons"]


def test_uncertainty_uses_only_prior_oof_market_dates_and_floors_quote_to_tick() -> None:
    dataset = _artifact()
    calibration = _calibration(dataset)
    config = V2APricingConfig(
        calibration_baseline=CalibrationBaseline.RAW_MODEL,
        uncertainty_quantile=0.80,
        minimum_prior_oof_market_dates=1,
        minimum_uncertainty_reserve=0.02,
        minimum_profit_reserve=0.05,
        known_cost_reserve=0.01,
        tick_size=0.01,
    )

    result = build_v2a_price_sheets(dataset, calibration, config=config)

    first, second, third = result.rows
    assert first.sheet.skip_reason == V2SkipReason.INSUFFICIENT_PRIOR_OOF_DATES
    assert first.prior_oof_market_dates == 0
    assert second.prior_oof_market_dates == 1
    assert second.sheet.uncertainty_reserve == pytest.approx(0.20)
    assert second.sheet.conservative_outcome_fair == pytest.approx(0.60)
    assert second.sheet.maximum_quote_price == pytest.approx(0.54)
    assert second.sheet.minimum_profit_reserve == pytest.approx(0.05)
    assert second.sheet.known_cost_reserve == pytest.approx(0.01)
    assert third.prior_oof_market_dates == 2


def test_future_outcome_mutation_cannot_change_earlier_price_sheet() -> None:
    dataset = _artifact()
    config = V2APricingConfig(
        calibration_baseline=CalibrationBaseline.RAW_MODEL,
        minimum_prior_oof_market_dates=1,
    )
    original = build_v2a_price_sheets(dataset, _calibration(dataset), config=config)
    changed_dataset = replace(
        dataset,
        evaluation_rows=(
            dataset.evaluation_rows[0],
            dataset.evaluation_rows[1],
            replace(
                dataset.evaluation_rows[2],
                outcome_label=1 - dataset.evaluation_rows[2].outcome_label,
            ),
        ),
    )
    changed = build_v2a_price_sheets(
        changed_dataset,
        _calibration(changed_dataset),
        config=config,
    )

    assert original.rows[0] == changed.rows[0]
    assert original.rows[1] == changed.rows[1]


def test_uncertainty_excludes_prior_date_outcome_resolved_after_decision() -> None:
    dataset = _artifact()
    delayed = replace(
        dataset.evaluation_rows[1],
        outcome_resolved_at_utc="2026-07-03T18:00:00+00:00",
    )
    dataset = replace(
        dataset,
        evaluation_rows=(
            dataset.evaluation_rows[0],
            delayed,
            dataset.evaluation_rows[2],
        ),
    )
    result = build_v2a_price_sheets(
        dataset,
        _calibration(dataset),
        config=V2APricingConfig(
            calibration_baseline=CalibrationBaseline.RAW_MODEL,
            minimum_prior_oof_market_dates=2,
        ),
    )

    assert result.rows[2].prior_oof_market_dates == 1
    assert result.rows[2].sheet.skip_reason == V2SkipReason.INSUFFICIENT_PRIOR_OOF_DATES


def test_market_aware_sheet_rejects_missing_market_reference() -> None:
    dataset = _artifact()
    missing = replace(
        dataset.evaluation_rows[2],
        market_reference=None,
        market_reference_kind=MarketReferenceKind.MISSING,
    )
    dataset = replace(
        dataset,
        evaluation_rows=(*dataset.evaluation_rows[:2], missing),
    )
    result = build_v2a_price_sheets(
        dataset,
        _calibration(dataset),
        config=V2APricingConfig(
            calibration_baseline=CalibrationBaseline.MARKET_AWARE,
            minimum_prior_oof_market_dates=1,
        ),
    )

    assert result.rows[2].sheet.skip_reason == V2SkipReason.MISSING_MARKET_REFERENCE
    assert result.rows[2].sheet.maximum_quote_price is None


def test_report_separates_probability_quality_economics_and_forward_gate() -> None:
    dataset = _artifact()
    result = build_v2a_price_sheets(
        dataset,
        _calibration(dataset),
        config=V2APricingConfig(
            calibration_baseline=CalibrationBaseline.RAW_MODEL,
            minimum_prior_oof_market_dates=1,
            untouched_forward_start_date="2026-07-03",
        ),
    )

    broad = result.report["windows"]["broad_evaluation"]
    assert broad["probability_metrics"]["raw_model"]["rows"] == 3
    assert broad["probability_metrics"]["market"]["brier_score"] is not None
    assert broad["probability_metrics"]["conservative"]["reliability"]
    assert broad["theoretical_economics"]["v2a_maximum_quote"]["resolved_quotes"] == 2
    assert broad["theoretical_economics"]["selected_entry"]["resolved_quotes"] == 3
    assert result.report["windows"]["untouched_forward"]["rows"] == 1
    assert result.report["comparison_notes"]["v1"].startswith("not comparable")
    assert "INSUFFICIENT_UNTOUCHED_FORWARD_MARKET_DATES" in result.report["promotion_gate"]["reasons"]


def test_price_sheet_artifact_writer_is_reconstructable(tmp_path: Path) -> None:
    dataset = _artifact()
    result = build_v2a_price_sheets(
        dataset,
        _calibration(dataset),
        config=V2APricingConfig(
            calibration_baseline=CalibrationBaseline.POOLED_PLATT,
            minimum_prior_oof_market_dates=1,
        ),
    )

    write_v2a_price_sheet_artifact(result, tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "price_sheets.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    assert manifest["rows"] == 3
    assert len(rows) == 3
    assert first["sheet"]["skip_reason"] == "INSUFFICIENT_PRIOR_OOF_DATES"
    assert first["uncertainty_evidence_hash"]


def _calibration(dataset: V2ADatasetArtifact):
    return walk_forward_calibration(
        dataset,
        config=WalkForwardCalibrationConfig(min_training_market_dates=2),
    )


def _artifact() -> V2ADatasetArtifact:
    return V2ADatasetArtifact(
        dataset_version=V2A_DATASET_VERSION,
        signal_spec_id="test_signal_v1",
        signal_spec_hash="signal-hash",
        fit_cutoff_date_exclusive="2026-07-01",
        evaluation_start_date="2026-07-01",
        evaluation_end_date="2026-07-03",
        fit_rows=(
            _row(10, "2026-06-29", 0, 0.20, 0.25, DatasetRole.CALIBRATION_FIT),
            _row(11, "2026-06-30", 1, 0.80, 0.75, DatasetRole.CALIBRATION_FIT),
        ),
        evaluation_rows=(
            _row(1, "2026-07-01", 0, 0.20, 0.25, DatasetRole.FROZEN_POLICY_EVALUATION),
            _row(2, "2026-07-02", 1, 0.80, 0.70, DatasetRole.FROZEN_POLICY_EVALUATION),
            _row(3, "2026-07-03", 1, 0.70, 0.65, DatasetRole.FROZEN_POLICY_EVALUATION),
        ),
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
        row_hash=f"row-hash-{id_}-{outcome}",
    )
