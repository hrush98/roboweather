from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3

import numpy as np
import pytest

from weather_trader.forecasting.evaluation import EvaluationContract, FixedSupport
from weather_trader.forecasting.nbm_benchmark import (
    conservative_nbm_request,
    extract_snapshot_distributions,
    fit_convex_weight,
    load_identical_cohort,
    metric_differences,
    normal_fixed_support,
    parse_tmax_inventory,
)


UTC = timezone.utc


def test_conservative_request_observes_release_lag_and_peak_window() -> None:
    request = conservative_nbm_request(
        datetime(2026, 6, 10, 17, 59, tzinfo=UTC),
        "KATL",
        date(2026, 6, 10),
    )
    assert request.cycle_at_utc == datetime(2026, 6, 10, 12, tzinfo=UTC)
    assert request.available_at_utc == datetime(2026, 6, 10, 14, tzinfo=UTC)
    assert request.forecast_hour == 12


def test_parse_tmax_inventory_selects_mean_and_stddev_range() -> None:
    inventory = "\n".join(
        [
            "1:0:d=2026061000:TMP:2 m above ground:24 hour fcst:",
            "2:100:d=2026061000:TMAX:2 m above ground:12-24 hour max fcst:",
            "3:250:d=2026061000:TMAX:2 m above ground:12-24 hour max fcst:ens std dev",
            "4:400:d=2026061000:RH:2 m above ground:24 hour fcst:",
        ]
    )
    selected = parse_tmax_inventory(inventory)
    assert (selected.start, selected.end) == (100, 399)
    assert "ens std dev" not in selected.mean_inventory
    assert "ens std dev" in selected.std_inventory


def test_parse_tmax_inventory_fails_on_ambiguous_fields() -> None:
    with pytest.raises(ValueError, match="one NBM TMAX"):
        parse_tmax_inventory(
            "1:0:d=x:TMAX:2 m above ground:x:\n"
            "2:10:d=x:TMAX:2 m above ground:x:\n"
            "3:20:d=x:TMAX:2 m above ground:x:ens std dev\n"
            "4:30:d=x:RH:2 m above ground:x:"
        )


def test_normal_fixed_support_is_normalized_and_moves_with_mean() -> None:
    contract = EvaluationContract(
        support=FixedSupport(69, 72), bootstrap_samples=10
    )
    matrix = normal_fixed_support([70.0, 71.0], [1.0, 1.0], contract)
    assert matrix.sum(axis=1).tolist() == pytest.approx([1.0, 1.0])
    support = contract.support.values
    assert float(matrix[1] @ support) > float(matrix[0] @ support)


def test_normal_fixed_support_uses_half_degree_rounding_boundaries() -> None:
    contract = EvaluationContract(
        support=FixedSupport(69, 71), bootstrap_samples=10
    )
    matrix = normal_fixed_support([70.0], [1.0], contract)[0]
    assert matrix[0] == pytest.approx(0.3085375387)
    assert matrix[1] == pytest.approx(0.3829249225)
    assert matrix[2] == pytest.approx(0.3085375387)


def test_snapshot_ladders_use_actual_bounds_and_normalize() -> None:
    contract = EvaluationContract(
        support=FixedSupport(69, 71), bootstrap_samples=10
    )
    raw = json.dumps(
        {
            "candidate_distribution": [
                {"market_id": "a", "fair_yes": 0.2, "yes_ask": 0.3},
                {"market_id": "b", "fair_yes": 0.5, "yes_ask": 0.5},
                {"market_id": "c", "fair_yes": 0.3, "yes_ask": 0.4},
            ]
        }
    )
    bounds = {
        "a": {"lower": None, "upper": 69.0},
        "b": {"lower": 70.0, "upper": 70.0},
        "c": {"lower": 71.0, "upper": None},
    }
    model, market = extract_snapshot_distributions(raw, bounds, contract)
    assert model.tolist() == pytest.approx([0.2, 0.5, 0.3])

    assert market.tolist() == pytest.approx([0.25, 5 / 12, 1 / 3])

def test_forward_cohort_reuses_exact_local_cutoff(tmp_path) -> None:
    database = tmp_path / "research.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            create table prediction_snapshots (
                id integer primary key,
                timestamp text not null,
                station text not null,
                market_date text not null,
                decision_time_utc text not null,
                decision_time_local text not null,
                raw_json text not null,
                market_family text not null,
                model_name text not null
            );
            create table station_date_outcomes (
                station text not null,
                market_date text not null,
                final_high_tmpf real
            );
            create table markets (
                market_id text primary key,
                market_family text,
                station text,
                lower_f real,
                upper_f real
            );
            """
        )
        rows = [
            (
                1,
                "2026-07-01T17:52:01+00:00",
                "2026-07-01T17:52:00+00:00",
                "2026-07-01T13:52:00-04:00",
            ),
            (
                2,
                "2026-07-01T18:52:01+00:00",
                "2026-07-01T18:52:00+00:00",
                "2026-07-01T14:52:00-04:00",
            ),
        ]
        connection.executemany(
            """insert into prediction_snapshots
               (id,timestamp,station,market_date,decision_time_utc,
                decision_time_local,raw_json,market_family,model_name)
               values (?,?,'KATL','2026-07-01',?,?,?,'HIGH_TEMP',?)""",
            [
                (
                    row_id,
                    timestamp,
                    decision_utc,
                    decision_local,
                    json.dumps({"candidate_distribution": []}),
                    "mvp_hrrr_rich_pm_active_us12_obs_2022_2025",
                )
                for row_id, timestamp, decision_utc, decision_local in rows
            ],
        )
        connection.execute(
            "insert into station_date_outcomes values ('KATL','2026-07-01',80.0)"
        )

    cohort, _ = load_identical_cohort(
        database,
        EvaluationContract(
            validation_start="2026-01-01",
            validation_end_exclusive="2027-01-01",
            bootstrap_samples=10,
        ),
    )
    assert cohort["id"].tolist() == [1]
    assert cohort.iloc[0]["decision_time_local"] == "2026-07-01T13:52:00-04:00"



def test_convex_stacking_and_clustered_difference_favor_better_source() -> None:
    contract = EvaluationContract(
        support=FixedSupport(69, 71), bootstrap_samples=40
    )
    better = np.array([[0.8, 0.1, 0.1], [0.1, 0.8, 0.1]])
    worse = np.full((2, 3), 1 / 3)
    targets = np.array([69, 70])
    weight = fit_convex_weight(better, worse, targets, contract)
    assert weight > 0.99
    comparison = metric_differences(
        better, worse, targets, ["2026-06-01", "2026-06-02"], contract
    )
    assert comparison["candidate_minus_reference"]["log_loss"] < 0
    assert comparison["candidate_minus_reference"]["rps"] < 0
