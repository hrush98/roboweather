from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import log_loss

from weather_trader.forecasting.goes_model import (
    GoesHeatingModelContract,
    design_matrix,
    equal_date_weights,
    fit_goes_heating_models,
)


def make_rows(dates: int = 24) -> list[dict[str, object]]:
    rows = []
    for date_index in range(dates):
        for row_index in range(4):
            surprise = -0.35 + 0.7 * row_index / 3.0
            outcome = int(surprise + (0.05 if date_index % 2 else -0.05) > 0)
            rows.append({
                "market_date": f"2026-09-{date_index + 1:02d}",
                "outcome_label": outcome,
                "f3_selected_token_probability": 0.5,
                "market_selected_token_probability": 0.5,
                "cloud_regime": ("CLEAR", "MIXED", "CLOUDY")[row_index % 3],
                "radiation_surprise": surprise,
            })
    return rows


def test_contract_fingerprint_and_feature_order_are_stable() -> None:
    contract = GoesHeatingModelContract()
    assert contract.fingerprint == GoesHeatingModelContract().fingerprint
    rows = make_rows(1)
    assert design_matrix(rows, contract, False).shape == (4, 4)
    assert design_matrix(rows, contract, True).shape == (4, 7)


def test_equal_date_weights_prevent_row_count_dominance() -> None:
    rows = make_rows(2)
    rows.append(dict(rows[0]))
    weights = equal_date_weights(rows)
    assert weights[[str(row["market_date"]) == "2026-09-01" for row in rows]].sum() == pytest.approx(1.0)
    assert weights[[str(row["market_date"]) == "2026-09-02" for row in rows]].sum() == pytest.approx(1.0)


def test_frozen_surprise_challenger_learns_incremental_signal() -> None:
    rows = make_rows()
    models = fit_goes_heating_models(rows)
    base, challenger = models.predict(rows)
    labels = np.asarray([row["outcome_label"] for row in rows])
    weights = equal_date_weights(rows)
    assert log_loss(labels, challenger, sample_weight=weights) < log_loss(
        labels, base, sample_weight=weights
    )
    assert models.fit_dates[0] == "2026-09-01"


def test_calibrator_refuses_too_few_dates() -> None:
    with pytest.raises(ValueError, match="needs 20 dates"):
        fit_goes_heating_models(make_rows(19))
