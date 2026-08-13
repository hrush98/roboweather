import numpy as np
import pandas as pd
import pytest

from weather_trader.forecasting.evaluation import (
    EvaluationContract,
    FixedSupport,
    evaluate_probability_matrix,
    normalize_observed_market_ladder,
    pairwise_prediction_diagnostics,
    prune_behavioral_duplicates,
    select_horizon_snapshots,
)


def test_snapshot_selection_is_independent_of_synthetic_threshold_rows() -> None:
    rows = []
    for date, outcome in [("2025-01-01", 70), ("2025-01-02", 71)]:
        for hour in [9, 14, 15]:
            for threshold in [outcome - 1, outcome, outcome + 1]:
                rows.append(
                    {
                        "station": "KAAA",
                        "timezone": "America/New_York",
                        "local_date": date,
                        "snapshot_time_local": f"{date} {hour:02d}:00:00-05:00",
                        "hour_local": hour,
                        "final_high_tmpf": outcome,
                        "threshold": threshold,
                    }
                )
    selected = select_horizon_snapshots(pd.DataFrame(rows), EvaluationContract(bootstrap_samples=10))
    assert selected[["local_date", "hour_local", "target_value"]].values.tolist() == [
        [pd.Timestamp("2025-01-01").date(), 14, 70],
        [pd.Timestamp("2025-01-02").date(), 14, 71],
    ]
def test_snapshot_selection_rejects_observations_after_exact_cutoff() -> None:
    rows = [
        {
            "station": "KAAA",
            "timezone": "America/New_York",
            "local_date": "2025-07-01",
            "snapshot_time_local": f"2025-07-01 {clock}:00-04:00",
            "hour_local": hour,
            "final_high_tmpf": 80,
        }
        for clock, hour in [("13:52", 13), ("14:52", 14)]
    ]
    selected = select_horizon_snapshots(
        pd.DataFrame(rows), EvaluationContract(bootstrap_samples=10)
    )
    assert len(selected) == 1
    assert selected.iloc[0]["snapshot_time_local"] == pd.Timestamp(
        "2025-07-01 17:52:00+00:00"
    )


def test_full_distribution_metrics_equal_weight_weather_dates() -> None:
    contract = EvaluationContract(support=FixedSupport(69, 71), bootstrap_samples=20)
    probabilities = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    result = evaluate_probability_matrix(probabilities, [70, 70, 71], ["2025-01-01", "2025-01-01", "2025-01-02"], contract)
    assert result["weather_dates"] == 2
    assert result["metrics"]["log_loss"] == pytest.approx(0.0)
    assert result["metrics"]["top_bucket_accuracy"] == pytest.approx(1.0)


def test_pairwise_pruning_uses_behavior_and_declared_order_not_outcomes() -> None:
    contract = EvaluationContract(support=FixedSupport(69, 71), bootstrap_samples=10)
    base = np.array([[0.1, 0.8, 0.1], [0.2, 0.7, 0.1]])
    distinct = np.array([[0.8, 0.1, 0.1], [0.1, 0.1, 0.8]])
    pairwise = pairwise_prediction_diagnostics(
        {"obs_control": base, "renamed_copy": base.copy(), "distributional_control": distinct},
        contract.support,
        {"obs_control": "same", "renamed_copy": "same", "distributional_control": "other"},
    )
    decisions = prune_behavioral_duplicates(["obs_control", "renamed_copy", "distributional_control"], pairwise, contract)
    assert decisions == [
        {"model_id": "obs_control", "representative": "obs_control", "decision": "RETAIN"},
        {"model_id": "renamed_copy", "representative": "obs_control", "decision": "COLLAPSE_DUPLICATE"},
        {"model_id": "distributional_control", "representative": "distributional_control", "decision": "RETAIN"},
    ]


def test_market_ladder_must_be_complete_and_is_normalized() -> None:
    support = FixedSupport(69, 71)
    ladder = pd.DataFrame(
        [
            {"bucket_lower": np.nan, "bucket_upper": 70, "price": 0.2},
            {"bucket_lower": 70, "bucket_upper": 71, "price": 0.5},
            {"bucket_lower": 71, "bucket_upper": np.nan, "price": 0.4},
        ]
    )
    assert normalize_observed_market_ladder(ladder, support).tolist() == pytest.approx([2 / 11, 5 / 11, 4 / 11])
    with pytest.raises(ValueError, match="partition"):
        normalize_observed_market_ladder(ladder.iloc[1:], support)


def test_contract_fingerprint_changes_with_horizon() -> None:
    assert EvaluationContract(horizon_hour_local=13).fingerprint != EvaluationContract(horizon_hour_local=14).fingerprint
