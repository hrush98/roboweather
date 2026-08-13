from __future__ import annotations

import pytest

from weather_trader.forecasting.goes_horizon import (
    chronological_fit_holdout_dates,
    goes_model_contract,
    horizon_contract,
    predecessor_evaluation_contract,
    predecessor_model_contract,
)


def test_exact_12_and_14_have_independent_frozen_identities() -> None:
    early = horizon_contract(12)
    late = horizon_contract(14)
    assert early.horizon_id == "d0_exact_12_local"
    assert late.horizon_id == "d0_exact_14_local"
    assert early.predecessor_version != late.predecessor_version
    assert early.goes_model_version != late.goes_model_version
    assert early.report_directory != late.report_directory
    assert (
        early.collection_activation_date
        == late.collection_activation_date
        == "2026-08-14"
    )
    assert predecessor_evaluation_contract(12).horizon_hour_local == 12
    assert predecessor_model_contract(12).version == early.predecessor_version
    assert goes_model_contract(12).horizon_id == early.horizon_id
    assert goes_model_contract(12).fingerprint != goes_model_contract(14).fingerprint


def test_only_predeclared_horizons_are_accepted() -> None:
    with pytest.raises(ValueError, match="frozen horizons are 12, 14"):
        horizon_contract(13)


def test_chronological_split_is_stable_and_disjoint() -> None:
    values = ["2025-01-03", "2025-01-01", "2025-01-02", "2025-01-04"]
    fit, holdout = chronological_fit_holdout_dates(values)
    assert fit == ("2025-01-01", "2025-01-02")
    assert holdout == ("2025-01-03", "2025-01-04")
    assert set(fit).isdisjoint(holdout)


def test_builder_refuses_to_replace_existing_exact_14_builder(tmp_path) -> None:
    from scripts.build_goes_horizon_predecessor import build_predecessor

    with pytest.raises(ValueError, match="exact-14 predecessor"):
        build_predecessor(
            tmp_path / "missing.csv",
            tmp_path,
            tmp_path / "out",
            horizon_hour_local=14,
        )


def test_predecessor_publish_is_immutable_and_idempotent(tmp_path) -> None:
    from scripts.build_goes_horizon_predecessor import publish_immutable

    artifact = {"version": "v1", "weight": 0.25}
    result = {"status": "FROZEN", "weight": 0.25}
    publish_immutable(tmp_path, artifact, result, "report\n")
    paths = sorted(tmp_path.iterdir())
    before = {path.name: path.read_bytes() for path in paths}
    publish_immutable(tmp_path, artifact, result, "report\n")
    assert {path.name: path.read_bytes() for path in paths} == before
    with pytest.raises(ValueError, match="create a new version"):
        publish_immutable(
            tmp_path,
            {"version": "v1", "weight": 0.50},
            result,
            "report\n",
        )
