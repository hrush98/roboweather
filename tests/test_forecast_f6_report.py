from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.forecast_f6_report import (
    binary_metrics,
    bucket_probability,
    build_selected_rows,
    summarize_edge_decay,
    summarize_half_life,
)


def test_bucket_probability_respects_inclusive_and_open_bounds() -> None:
    support = np.array([69, 70, 71, 72])
    probabilities = np.array([0.1, 0.2, 0.3, 0.4])
    assert bucket_probability(probabilities, support, None, 70) == pytest.approx(0.3)
    assert bucket_probability(probabilities, support, 71, None) == pytest.approx(0.7)
    assert bucket_probability(probabilities, support, 70, 71) == pytest.approx(0.5)


def test_selected_rows_preserve_source_selection_without_reselecting() -> None:
    forward = pd.DataFrame([
        {
            "source_prediction_snapshot_id": 7,
            "source_snapshot_timestamp_utc": "2026-07-20T18:00:01+00:00",
            "decision_time_local": "2026-07-20T13:59:00-04:00",
            "station": "KATL",
            "local_date": "2026-07-20",
            "selected_market_id": "m1",
            "selected_bucket": "70-71F",
            "selected_side": "BUY_YES",
            "target_value": 71,
            "candidate_distribution": [{"market_id": "m1", "yes_ask": 0.4}],
        },
        {
            "source_prediction_snapshot_id": 8,
            "source_snapshot_timestamp_utc": "2026-07-20T18:00:02+00:00",
            "decision_time_local": "2026-07-20T13:59:00-04:00",
            "station": "KBOS",
            "local_date": "2026-07-20",
            "selected_market_id": None,
            "selected_bucket": None,
            "selected_side": "SKIP",
            "target_value": 72,
            "candidate_distribution": [],
        },
    ])
    weather = np.array([[0.1, 0.6, 0.3], [0.2, 0.3, 0.5]])
    rows = build_selected_rows(
        forward,
        weather,
        {"m1": (70.0, 71.0)},
        np.array([69, 70, 71]),
        activation_date="2026-07-20",
    )
    assert len(rows) == 1
    assert rows[0]["selected_market_id"] == "m1"
    assert rows[0]["raw_f3_token_fair"] == pytest.approx(0.9)
    assert rows[0]["outcome_label"] == 1


def test_binary_metrics_equal_weight_market_dates() -> None:
    rows = [
        {"market_date": "2026-01-01", "outcome_label": 1, "p": 0.9},
        {"market_date": "2026-01-01", "outcome_label": 1, "p": 0.9},
        {"market_date": "2026-01-02", "outcome_label": 0, "p": 0.1},
    ]
    result = binary_metrics(rows, "p")
    assert result["market_dates"] == 2
    assert result["brier"] == pytest.approx(0.01)


def test_edge_decay_summaries_keep_censoring_explicit() -> None:
    rows = [
        {
            "source_prediction_snapshot_id": 1,
            "market_date": "2026-01-01",
            "offset_seconds": 0,
            "status": "VALID",
            "fillable_25_usd": 25.0,
            "net_edge_25": 0.20,
            "reason": None,
        },
        {
            "source_prediction_snapshot_id": 1,
            "market_date": "2026-01-01",
            "offset_seconds": 30,
            "status": "VALID",
            "fillable_25_usd": 25.0,
            "net_edge_25": 0.09,
            "reason": None,
        },
        *[
            {
                "source_prediction_snapshot_id": 1,
                "market_date": "2026-01-01",
                "offset_seconds": offset,
                "status": "RIGHT_CENSORED",
                "reason": "coverage_gap",
            }
            for offset in (120, 300, 900)
        ],
    ]
    curve = summarize_edge_decay(rows)
    assert curve[0]["fillable_25_rows"] == 1
    assert curve[2]["right_censored_rows"] == 1
    half_life = summarize_half_life(rows)
    assert half_life["rows"][0]["half_life_seconds"] == 30
    assert half_life["rows"][0]["right_censored"]


def test_nonpositive_initial_edge_has_no_positive_edge_half_life() -> None:
    rows = [
        {
            "source_prediction_snapshot_id": 2,
            "market_date": "2026-01-02",
            "offset_seconds": offset,
            "status": "VALID",
            "fillable_25_usd": 25.0,
            "net_edge_25": edge,
            "reason": None,
        }
        for offset, edge in [(0, -0.02), (30, -0.01), (120, 0.01)]
    ]
    result = summarize_half_life(rows)
    assert result["half_life_observed"] == 0
    assert result["rows"][0]["half_life_seconds"] is None
    assert result["rows"][0]["time_to_nonpositive_seconds"] == 0
