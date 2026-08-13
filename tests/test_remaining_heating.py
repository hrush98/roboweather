from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from weather_trader.forecasting.evaluation import FixedSupport
from weather_trader.forecasting.remaining_heating import (
    RemainingHeatingContract,
    RemainingHeatingModel,
    enforce_high_so_far_lower_bound,
    exact_cutoff_multinomial_contract,
)


def _snapshots() -> pd.DataFrame:
    rows = []
    for index in range(80):
        high = 70 + index % 3
        additional = 0 if index % 4 < 2 else 1 + index % 4
        rows.append(
            {
                "station": "KAAA" if index % 2 else "KBBB",
                "local_date": f"2024-06-{1 + index % 20:02d}",
                "hour_local": 14,
                "day_of_year": 152 + index % 20,
                "current_temp": high - 1,
                "max_temp_so_far": high,
                "temp_change_1h": -0.5 if additional == 0 else 1.0,
                "hrrr_remaining_max": high + additional,
                "final_high_tmpf": high + additional,
            }
        )
    return pd.DataFrame(rows)


def test_distribution_is_normalized_and_cannot_go_below_high_so_far() -> None:
    contract = RemainingHeatingContract(
        support=FixedSupport(65, 85), min_class_rows=2
    )
    model = RemainingHeatingModel(contract).fit(_snapshots())
    evaluation = _snapshots().iloc[:4].copy()
    matrix = model.predict_proba(evaluation)
    assert matrix.shape == (4, 21)
    assert matrix.sum(axis=1).tolist() == pytest.approx([1.0] * 4)
    for index, high in enumerate(evaluation["max_temp_so_far"].astype(int)):
        assert matrix[index, : high - contract.support.minimum].sum() == 0.0


def test_rounding_preserves_coherence_for_fractional_report_highs() -> None:
    frame = _snapshots()
    frame[["max_temp_so_far", "final_high_tmpf"]] = frame[["max_temp_so_far", "final_high_tmpf"]].astype(float)
    frame.loc[0, "max_temp_so_far"] = 70.5
    frame.loc[0, "final_high_tmpf"] = 70.5
    model = RemainingHeatingModel(
        RemainingHeatingContract(support=FixedSupport(65, 85), min_class_rows=2)
    ).fit(frame)
    predicted = model.predict_proba(frame.iloc[[0]])
    assert predicted[0, : round(70.5) - 65].sum() == 0.0


def test_training_rejects_final_high_below_observed_high() -> None:
    frame = _snapshots()
    frame[["max_temp_so_far", "final_high_tmpf"]] = frame[["max_temp_so_far", "final_high_tmpf"]].astype(float)
    frame.loc[0, "max_temp_so_far"] = 75
    frame.loc[0, "final_high_tmpf"] = 70
    with pytest.raises(ValueError, match="below integer high-so-far"):
        RemainingHeatingModel(
            RemainingHeatingContract(support=FixedSupport(65, 85))
        ).fit(frame)


def test_unseen_station_uses_pooled_features() -> None:
    model = RemainingHeatingModel(
        RemainingHeatingContract(support=FixedSupport(65, 85), min_class_rows=2)
    ).fit(_snapshots())
    row = _snapshots().iloc[[0]].copy()
    row["station"] = "KNEW"
    assert np.isfinite(model.predict_proba(row)).all()

def test_multinomial_conditional_assigns_mass_to_each_learned_delta() -> None:
    contract = replace(
        exact_cutoff_multinomial_contract(),
        support=FixedSupport(65, 90),
        min_class_rows=2,
    )
    frame = _snapshots()
    model = RemainingHeatingModel(contract).fit(frame)
    matrix = model.predict_proba(frame.iloc[:8])
    high = frame.iloc[:8]["max_temp_so_far"].astype(int).to_numpy()
    for row_index, delta in enumerate(
        frame.iloc[:8]["final_high_tmpf"].astype(int).to_numpy() - high
    ):
        target_index = high[row_index] + delta - contract.support.minimum
        assert matrix[row_index, target_index] > 0.0


def test_multinomial_contract_handles_all_peak_passed_training_rows() -> None:
    contract = replace(
        exact_cutoff_multinomial_contract(),
        support=FixedSupport(65, 90),
        min_class_rows=2,
    )
    frame = _snapshots()
    frame["final_high_tmpf"] = frame["max_temp_so_far"]
    matrix = RemainingHeatingModel(contract).fit(frame).predict_proba(frame.iloc[:3])

    assert matrix.sum(axis=1).tolist() == pytest.approx([1.0, 1.0, 1.0])

def test_high_so_far_projection_is_normalized_and_coherent() -> None:
    support = FixedSupport(69, 71)
    projected = enforce_high_so_far_lower_bound(
        np.array([[0.2, 0.5, 0.3], [1.0, 0.0, 0.0]]),
        [70, 70],
        support,
    )
    assert projected[0].tolist() == pytest.approx([0.0, 0.625, 0.375])
    assert projected[1].tolist() == pytest.approx([0.0, 1.0, 0.0])
