from __future__ import annotations

import sqlite3
import subprocess

import pytest
import sys
from pathlib import Path

from scripts import snapshot_opportunity_sweep as sweep


def test_selection_modes() -> None:
    rows = [
        _row(1, edge=0.10, paper_pnl=-0.20, timestamp="2026-05-07T10:00:00+00:00"),
        _row(2, edge=0.14, paper_pnl=0.40, timestamp="2026-05-07T11:00:00+00:00"),
        _row(3, edge=0.15, paper_pnl=-0.30, timestamp="2026-05-07T12:00:00+00:00", selected_bucket="75-76F"),
        _row(4, edge=0.23, paper_pnl=0.30, timestamp="2026-05-07T13:00:00+00:00"),
    ]

    modes = sweep.apply_modes(rows)

    assert [row["id"] for row in modes["all_snapshots"].rows] == [1, 2, 3, 4]
    assert [row["id"] for row in modes["first_opportunity"].rows] == [1, 3]
    assert [row["id"] for row in modes["station_date_first"].rows] == [1]
    assert [row["id"] for row in modes["edge_improve_50"].rows] == [1, 3, 4]
    assert [row["id"] for row in modes["best_edge"].rows] == [4, 3]
    assert [row["id"] for row in modes["hindsight_best"].rows] == [2, 3]
    assert modes["hindsight_best"].hindsight_only is True


def test_best_edge_does_not_use_realized_pnl() -> None:
    rows = [
        _row(1, edge=0.10, paper_pnl=0.90),
        _row(2, edge=0.20, paper_pnl=-0.40, timestamp="2026-05-07T11:00:00+00:00"),
    ]

    modes = sweep.apply_modes(rows)

    assert [row["id"] for row in modes["best_edge"].rows] == [2]
    assert [row["id"] for row in modes["hindsight_best"].rows] == [1]


def test_consensus_requires_all_models_and_uses_newest_liquidity() -> None:
    group_name = "obs_dynamic_tuned_mvp"
    model_a, model_b = sweep.CONSENSUS_GROUPS[group_name]
    matched_a = _row(
        1,
        model_name=model_a,
        edge=0.10,
        selected_fair_yes=0.62,
        selected_sweep_fillable_50_usd=20.0,
        timestamp="2026-05-07T10:00:00+00:00",
    )
    matched_b = _row(
        2,
        model_name=model_b,
        edge=0.20,
        selected_fair_yes=0.72,
        selected_sweep_fillable_50_usd=80.0,
        timestamp="2026-05-07T11:00:00+00:00",
    )
    unmatched = _row(3, model_name=model_a, selected_market_id="other", timestamp="2026-05-07T12:00:00+00:00")

    consensus = sweep.build_consensus_rows([matched_a, matched_b, unmatched])

    assert len([row for row in consensus if row["source"] == f"consensus:{group_name}"]) == 1
    row = [row for row in consensus if row["source"] == f"consensus:{group_name}"][0]
    assert row["selected_edge"] == pytest.approx(0.15)
    assert row["selected_fair"] == pytest.approx(0.67)
    assert row["selected_sweep_fillable_50_usd"] == 80.0
    assert row["source_prediction_snapshot_ids"] == [1, 2]


def test_policy_search_reconstructs_pm_us12_consensus_hc_late_entry_policy() -> None:
    dynamic = _row(
        1,
        model_name=sweep.PM_ACTIVE_DYNAMIC_MODEL,
        source=sweep.PM_ACTIVE_DYNAMIC_MODEL,
        edge=0.12,
        selected_fair_yes=0.72,
        entry_price=0.60,
        timestamp="2026-05-07T17:00:00+00:00",
        decision_time_local="2026-05-07T13:00:00-04:00",
    )
    mvp = _row(
        2,
        model_name=sweep.PM_ACTIVE_MVP_MODEL,
        source=sweep.PM_ACTIVE_MVP_MODEL,
        edge=0.18,
        selected_fair_yes=0.78,
        entry_price=0.60,
        timestamp="2026-05-07T17:01:00+00:00",
        decision_time_local="2026-05-07T13:01:00-04:00",
    )

    policy_rows, policy_summaries = sweep.build_policy_search_rows([dynamic, mvp])

    matching_rows = [
        row for row in policy_rows if row["policy_name"] == "pm_us12_consensus_hc_late_entry_50_75_first"
    ]
    assert len(matching_rows) == 1
    assert matching_rows[0]["source_prediction_snapshot_ids"] == [1, 2]
    assert matching_rows[0]["selected_edge"] == pytest.approx(0.15)
    assert matching_rows[0]["selected_fair"] == pytest.approx(0.75)

    matching_summaries = [
        row for row in policy_summaries if row["policy_name"] == "pm_us12_consensus_hc_late_entry_50_75_first"
    ]
    assert len(matching_summaries) == 1
    assert matching_summaries[0]["resolved"] == 1
    assert matching_summaries[0]["rr"] == pytest.approx((1.0 - 0.60) / 0.60)


def test_cli_smoke_emits_all_modes(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite"
    db = sqlite3.connect(db_path)
    _create_schema(db)
    _insert_snapshot(db, 1, edge=0.10, paper_pnl=0.60)
    _insert_snapshot(db, 2, edge=0.20, paper_pnl=-0.30, timestamp="2026-05-07T11:00:00+00:00")
    db.commit()
    db.close()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/snapshot_opportunity_sweep.py",
            "--db",
            str(db_path),
            "--min-n",
            "1",
            "--top-n",
            "3",
            "--no-include-consensus",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        capture_output=True,
    )

    output = result.stdout
    assert "# Snapshot Opportunity Sweep" in output
    for mode in sweep.MODE_ORDER:
        assert f"## {mode}" in output
    assert "## Promotion Candidates" in output
    assert "## Best Recorded Execution Policies" in output
    assert "## Policy Search Candidates" in output
    assert "### Highest Sharpe" in output
    assert "### Highest Win Rate" in output
    assert "### Highest R/R" in output


def _row(
    id_: int,
    *,
    source: str = "model_a",
    model_name: str | None = None,
    strategy_bucket: str = "HIGH_CONVICTION",
    station: str = "KATL",
    market_date: str = "2026-05-07",
    market_family: str = "HIGH_TEMP",
    selected_bucket: str = "74-75F",
    selected_side: str = "BUY_YES",
    obs_delay_bucket: str = "15m",
    selected_market_id: str = "m1",
    edge: float | None = 0.10,
    selected_fair_yes: float | None = 0.60,
    entry_price: float | None = 0.40,
    paper_pnl: float | None = 0.60,
    correct: int | None = 1,
    timestamp: str = "2026-05-07T10:00:00+00:00",
    decision_time_local: str = "2026-05-07T06:00:00-04:00",
    selected_sweep_fillable_50_usd: float | None = 100.0,
) -> dict:
    model = model_name or source
    return {
        "id": id_,
        "timestamp": timestamp,
        "source": source,
        "model_name": model,
        "strategy_bucket": strategy_bucket,
        "station": station,
        "market_date": market_date,
        "market_family": market_family,
        "selected_bucket": selected_bucket,
        "selected_side": selected_side,
        "obs_delay_bucket": obs_delay_bucket,
        "selected_market_id": selected_market_id,
        "selected_edge": edge,
        "selected_fair_yes": selected_fair_yes,
        "selected_fair_no": None if selected_fair_yes is None else 1.0 - selected_fair_yes,
        "selected_fair": selected_fair_yes,
        "selected_yes_ask": entry_price,
        "selected_no_ask": None,
        "entry_price": entry_price,
        "paper_pnl": paper_pnl,
        "correct": correct,
        "decision_time_local": decision_time_local,
        "selected_sweep_fillable_50_usd": selected_sweep_fillable_50_usd,
        "selected_book_age_seconds": 12.0,
    }


def _create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        create table prediction_snapshots (
            id integer primary key,
            timestamp text not null,
            station text not null,
            market_date text not null,
            decision_time_utc text not null,
            decision_time_local text not null,
            latest_obs_time_utc text not null,
            latest_obs_time_local text not null,
            obs_age_minutes real not null,
            obs_delay_bucket text not null,
            strategy_bucket text not null,
            selected_market_id text,
            selected_bucket text,
            selected_side text not null,
            selected_edge real,
            selected_fair_yes real,
            selected_fair_no real,
            selected_yes_ask real,
            selected_no_ask real,
            model_name text not null,
            market_family text not null,
            selected_best_bid real,
            selected_best_ask real,
            selected_spread real,
            selected_depth_at_ask real,
            selected_depth_ask_plus_0_01 real,
            selected_depth_ask_plus_0_03 real,
            selected_depth_ask_plus_0_05 real,
            selected_book_timestamp text,
            selected_book_age_seconds real,
            selected_sweep_price_cap real,
            selected_sweep_depth_to_cap real,
            selected_sweep_fillable_25_usd real,
            selected_sweep_fillable_50_usd real,
            selected_sweep_fillable_100_usd real,
            selected_sweep_vwap_25 real,
            selected_sweep_vwap_50 real,
            selected_sweep_vwap_100 real
        );
        create table prediction_results (
            prediction_snapshot_id integer primary key,
            correct integer,
            entry_price real,
            paper_pnl real,
            edge real,
            resolved_at text
        );
        """
    )


def _insert_snapshot(
    db: sqlite3.Connection,
    id_: int,
    *,
    timestamp: str = "2026-05-07T10:00:00+00:00",
    edge: float = 0.10,
    paper_pnl: float = 0.60,
) -> None:
    db.execute(
        """
        insert into prediction_snapshots (
            id, timestamp, station, market_date, decision_time_utc, decision_time_local,
            latest_obs_time_utc, latest_obs_time_local, obs_age_minutes, obs_delay_bucket,
            strategy_bucket, selected_market_id, selected_bucket, selected_side, selected_edge,
            selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask, model_name,
            market_family, selected_book_age_seconds, selected_sweep_fillable_50_usd
        ) values (?, ?, 'KATL', '2026-05-07', ?, ?, ?, ?, 0, '15m',
            'HIGH_CONVICTION', 'm1', '74-75F', 'BUY_YES', ?,
            0.70, 0.30, 0.40, 0.60, 'model_a', 'HIGH_TEMP', 10.0, 100.0)
        """,
        (id_, timestamp, timestamp, timestamp, timestamp, timestamp, edge),
    )
    db.execute(
        """
        insert into prediction_results (
            prediction_snapshot_id, correct, entry_price, paper_pnl, edge, resolved_at
        ) values (?, ?, 0.40, ?, ?, ?)
        """,
        (id_, 1 if paper_pnl > 0 else 0, paper_pnl, edge, timestamp),
    )
