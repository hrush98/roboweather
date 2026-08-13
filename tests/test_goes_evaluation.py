from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.forecast_goes_heating_report import acceptance_checks, render_markdown
from weather_trader.forecasting.goes_evaluation import (
    calibrator_paths,
    evaluate_untouched,
    freeze_calibrator,
    load_calibrator,
)

UTC = timezone.utc


def make_rows(start: date, dates: int = 20) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    snapshot_id = 1
    for date_offset in range(dates):
        market_date = (start + timedelta(days=date_offset)).isoformat()
        for row_index, surprise in enumerate((-4.5, -2.0, 2.0, 4.5)):
            outcome = int(surprise > 0)
            rows.append({
                "source_prediction_snapshot_id": snapshot_id,
                "station": ("KATL", "KLAX")[row_index % 2],
                "market_date": market_date,
                "selected_market_id": f"m-{snapshot_id}",
                "selected_side": "BUY_YES",
                "outcome_label": outcome,
                "f3_selected_token_probability": 0.50,
                "market_selected_token_probability": 0.50,
                "source_same_side_ask": 0.48,
                "cloud_regime": ("CLEAR", "MIXED", "CLOUDY", "MIXED")[row_index],
                "radiation_surprise": surprise,
                "artifact_ids": [f"a-{snapshot_id}"],
            })
            snapshot_id += 1
    return rows


def test_freeze_is_immutable_future_activated_and_replay_validated(tmp_path) -> None:
    rows = make_rows(date(2026, 9, 1), dates=22)
    frozen_at = datetime(2026, 9, 23, 12, tzinfo=UTC)
    models, manifest = freeze_calibrator(
        rows,
        tmp_path,
        predecessor="f3-v1",
        f3_evaluation_fingerprint="f3-fingerprint",
        untouched_forward_start_date="2026-09-24",
        frozen_at_utc=frozen_at,
    )
    artifact, manifest_path = calibrator_paths(tmp_path)
    assert artifact.exists()
    assert manifest_path.exists()
    assert manifest["fit_dates"] == [
        (date(2026, 9, 1) + timedelta(days=offset)).isoformat()
        for offset in range(20)
    ]
    assert manifest["untouched_forward_start_date"] == "2026-09-24"
    loaded = load_calibrator(
        rows,
        tmp_path,
        predecessor="f3-v1",
        f3_evaluation_fingerprint="f3-fingerprint",
    )
    assert loaded is not None
    assert loaded[0].fit_dates == models.fit_dates

    changed = [dict(row) for row in rows]
    changed[0]["outcome_label"] = 1
    with pytest.raises(ValueError, match="rows changed"):
        load_calibrator(
            changed,
            tmp_path,
            predecessor="f3-v1",
            f3_evaluation_fingerprint="f3-fingerprint",
        )
    with pytest.raises(ValueError, match="already exists"):
        freeze_calibrator(
            rows,
            tmp_path,
            predecessor="f3-v1",
            f3_evaluation_fingerprint="f3-fingerprint",
            untouched_forward_start_date="2026-09-23",
            frozen_at_utc=frozen_at,
        )


def test_freeze_refuses_nonfuture_or_undersized_evidence(tmp_path) -> None:
    with pytest.raises(ValueError, match="strictly future"):
        freeze_calibrator(
            make_rows(date(2026, 9, 1)),
            tmp_path,
            predecessor="f3-v1",
            f3_evaluation_fingerprint="f3-fingerprint",
            untouched_forward_start_date="2026-09-21",
            frozen_at_utc=datetime(2026, 9, 21, 12, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="needs 20 dates"):
        freeze_calibrator(
            make_rows(date(2026, 9, 1), dates=19),
            tmp_path,
            predecessor="f3-v1",
            f3_evaluation_fingerprint="f3-fingerprint",
            untouched_forward_start_date="2026-09-22",
            frozen_at_utc=datetime(2026, 9, 21, 12, tzinfo=UTC),
        )


def test_untouched_report_covers_incremental_groups_calibration_and_abstention(tmp_path) -> None:
    calibration = make_rows(date(2026, 9, 1))
    models, _manifest = freeze_calibrator(
        calibration,
        tmp_path,
        predecessor="f3-v1",
        f3_evaluation_fingerprint="f3-fingerprint",
        untouched_forward_start_date="2026-10-01",
        frozen_at_utc=datetime(2026, 9, 21, 12, tzinfo=UTC),
    )
    untouched = make_rows(date(2026, 10, 1))
    evaluation, scored = evaluate_untouched(
        untouched,
        models,
        bootstrap_samples=300,
    )
    assert evaluation["rows"] == 80
    assert evaluation["weather_dates"] == 20
    for comparison in (
        "challenger_minus_no_surprise_baseline",
        "challenger_minus_f3",
        "challenger_minus_market",
    ):
        assert evaluation["comparisons"][comparison]["log_loss_delta"] < 0
        assert (
            evaluation["comparisons"][comparison][
                "weather_date_clustered_95pct_ci"
            ]["log_loss"][1]
            < 0
        )
    assert evaluation["selected_token_calibration"]["passes"]
    assert set(evaluation["station_diagnostics"]) == {"KATL", "KLAX"}
    assert set(evaluation["regime_diagnostics"]) == {"CLEAR", "CLOUDY", "MIXED"}
    assert set(evaluation["surprise_threshold_diagnostics"]) == {"0.1", "0.2"}
    abstention_rows = [item["rows"] for item in evaluation["abstention_curve"]]
    assert abstention_rows == sorted(abstention_rows, reverse=True)
    assert all("challenger_edge_to_displayed_ask" in row for row in scored)

    checks = acceptance_checks(
        [f"fit-{index}" for index in range(20)],
        {"frozen_at_utc": "2026-09-21T12:00:00+00:00"},
        [f"test-{index}" for index in range(20)],
        evaluation,
    )
    assert all(checks.values())


def test_markdown_renders_every_required_diagnostic_section(tmp_path) -> None:
    calibration = make_rows(date(2026, 9, 1))
    models, _manifest = freeze_calibrator(
        calibration,
        tmp_path,
        predecessor="f3-v1",
        f3_evaluation_fingerprint="f3-fingerprint",
        untouched_forward_start_date="2026-10-01",
        frozen_at_utc=datetime(2026, 9, 21, 12, tzinfo=UTC),
    )
    evaluation, _scored = evaluate_untouched(
        make_rows(date(2026, 10, 1)), models, bootstrap_samples=100
    )
    text = render_markdown({
        "status": "COMPLETED_WITH_INCREMENTAL_INFORMATION",
        "verdict": "ACCEPT_F5_INCREMENTAL_INFORMATION",
        "source_coverage": {"artifacts": 100, "bytes": 1234},
        "cohort": {
            "eligible_rows": 160,
            "eligible_dates": 40,
            "calibrator_frozen_at_utc": "2026-09-21T12:00:00+00:00",
            "untouched_forward_start_date": "2026-10-01",
            "untouched_dates": [f"d-{index}" for index in range(20)],
        },
        "contract": {
            "minimum_calibration_dates": 20,
            "minimum_untouched_dates": 20,
        },
        "acceptance_checks": {"example": True},
        "evaluation": evaluation,
        "limitations": ["no execution claim"],
    })
    for heading in (
        "Exact Selected-Token Scores",
        "Date-Clustered Comparisons",
        "Selected-Token Calibration",
        "Station Diagnostics",
        "Cloud-Regime Diagnostics",
        "Predeclared Surprise Thresholds",
        "Abstention At Displayed Ask",
    ):
        assert heading in text


def test_untouched_evaluation_refuses_fewer_than_twenty_dates(tmp_path) -> None:
    models, _manifest = freeze_calibrator(
        make_rows(date(2026, 9, 1)),
        tmp_path,
        predecessor="f3-v1",
        f3_evaluation_fingerprint="f3-fingerprint",
        untouched_forward_start_date="2026-10-01",
        frozen_at_utc=datetime(2026, 9, 21, 12, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="needs 20 dates"):
        evaluate_untouched(
            make_rows(date(2026, 10, 1), dates=19),
            models,
            bootstrap_samples=100,
        )
