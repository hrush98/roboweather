from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from weather_trader.pricing.contracts import (
    HRRR_RICH_DYNAMIC_TUNED_MODEL,
    MarketReferenceKind,
    PILOT_SIGNAL_SPECS,
)
from weather_trader.pricing.dataset import materialize_v2a_dataset, write_v2a_dataset_artifact


SPEC = PILOT_SIGNAL_SPECS[0]


def test_signal_spec_hash_and_decision_id_are_stable() -> None:
    source = {
        "id": 17,
        "station": "KATL",
        "market_date": "2026-07-14",
        "selected_market_id": "market-1",
        "selected_bucket": "74-75F",
        "selected_side": "BUY_NO",
        "obs_delay_bucket": "10m",
    }

    assert SPEC.spec_hash == replace(SPEC).spec_hash
    assert SPEC.decision_id(source) == SPEC.decision_id({**source, "selected_edge": 999.0})
    assert SPEC.decision_id(source) != SPEC.decision_id({**source, "obs_delay_bucket": "15m"})
    assert SPEC.v1_rollback_version == "phase1_price_maker_v1"


def test_materializer_builds_distinct_fit_and_frozen_evaluation_rows() -> None:
    connection = _connection()
    _outcome(connection, "KATL", "2026-07-13", 80.0)
    _outcome(connection, "KBOS", "2026-07-13", 74.0)
    _outcome(connection, "KATL", "2026-07-14", 80.0)
    _outcome(connection, "KBOS", "2026-07-14", 74.0)
    _market(connection, "market-atl")
    _market(connection, "market-bos")
    _snapshot(connection, 1, station="KATL", market_id="market-atl", market_date="2026-07-13", obs_delay="10m")
    _snapshot(connection, 2, station="KATL", market_id="market-atl", market_date="2026-07-13", obs_delay="10m", timestamp="2026-07-13T17:02:00+00:00")
    _snapshot(connection, 3, station="KATL", market_id="market-atl", market_date="2026-07-13", obs_delay="15m", timestamp="2026-07-13T17:03:00+00:00")
    _snapshot(connection, 4, station="KBOS", market_id="market-bos", market_date="2026-07-13", obs_delay="10m")
    _snapshot(connection, 5, station="KATL", market_id="market-atl", market_date="2026-07-13", side="BUY_YES", strategy="BEST_BUCKET")
    _snapshot(connection, 11, station="KATL", market_id="market-atl", obs_delay="10m")
    _snapshot(connection, 12, station="KATL", market_id="market-atl", obs_delay="10m", timestamp="2026-07-14T17:02:00+00:00")
    _snapshot(connection, 13, station="KATL", market_id="market-atl", obs_delay="15m", timestamp="2026-07-14T17:03:00+00:00")
    _snapshot(connection, 14, station="KBOS", market_id="market-bos", obs_delay="10m")
    _snapshot(connection, 15, station="KATL", market_id="market-atl", side="BUY_YES", strategy="BEST_BUCKET")
    connection.commit()

    artifact = materialize_v2a_dataset(connection, SPEC, evaluation_start_date="2026-07-14")

    assert [row.source_prediction_snapshot_ids for row in artifact.evaluation_rows] == [(11,), (14,), (13,)]
    assert len(artifact.fit_rows) == 5
    assert {row.outcome_label for row in artifact.evaluation_rows if row.station == "KATL"} == {1}
    assert [row.outcome_label for row in artifact.evaluation_rows if row.station == "KBOS"] == [0]
    assert all(row.market_reference_kind == MarketReferenceKind.MIDPOINT for row in artifact.evaluation_rows)
    assert all("OUTCOME_SOURCE_NOT_VENUE_ALIGNED" in row.quality_flags for row in artifact.evaluation_rows)
    assert sum(row.market_date_cluster_weight for row in artifact.fit_rows) == pytest.approx(1.0)
    assert sum(row.station_date_cluster_weight for row in artifact.fit_rows if row.station == "KATL") == pytest.approx(1.0)
    assert sum(row.station_date_cluster_weight for row in artifact.fit_rows if row.station == "KBOS") == pytest.approx(1.0)
    assert len({row.row_hash for row in artifact.fit_rows}) == len(artifact.fit_rows)


def test_materializer_enforces_fit_cutoff_and_timestamp_causality() -> None:
    connection = _connection()
    _outcome(connection, "KATL", "2026-07-14", 80.0)
    _outcome(connection, "KATL", "2026-07-16", 80.0)
    _market(connection, "market-1")
    _snapshot(connection, 1, station="KATL", market_id="market-1", market_date="2026-07-14")
    _snapshot(connection, 2, station="KATL", market_id="market-1", market_date="2026-07-16")
    _snapshot(
        connection,
        3,
        station="KATL",
        market_id="market-1",
        market_date="2026-07-14",
        latest_obs_time="2026-07-14T17:01:00+00:00",
        timestamp="2026-07-14T17:00:30+00:00",
    )
    _snapshot(connection, 4, station="KDAL", market_id="market-1", market_date="2026-07-14")
    connection.commit()

    artifact = materialize_v2a_dataset(
        connection,
        SPEC,
        fit_cutoff_date_exclusive="2026-07-16",
        evaluation_start_date="2026-07-16",
    )

    assert [row.market_date for row in artifact.fit_rows] == ["2026-07-14"]
    assert [row.market_date for row in artifact.evaluation_rows] == ["2026-07-16"]
    assert artifact.evaluation_rows[0].is_forward is True
    assert artifact.diagnostics["counts"]["excluded:INVALID_TIMESTAMP_ORDER"] == 1
    assert artifact.diagnostics["counts"]["excluded:MISSING_OUTCOME_LABEL"] == 1

    with pytest.raises(ValueError, match="fit cutoff must not follow"):
        materialize_v2a_dataset(
            connection,
            SPEC,
            fit_cutoff_date_exclusive="2026-07-17",
            evaluation_start_date="2026-07-16",
        )


def test_stale_and_crossed_market_references_fail_closed_without_dropping_labels() -> None:
    connection = _connection()
    _outcome(connection, "KATL", "2026-07-14", 80.0)
    _market(connection, "market-1")
    _snapshot(connection, 1, station="KATL", market_id="market-1", book_age=61.0)
    _snapshot(
        connection,
        2,
        station="KATL",
        market_id="market-1",
        obs_delay="15m",
        best_bid=0.30,
        best_ask=0.20,
    )
    connection.commit()

    artifact = materialize_v2a_dataset(connection, SPEC, evaluation_start_date="2026-07-14")
    stale, crossed = artifact.evaluation_rows

    assert stale.market_reference is None
    assert stale.market_reference_stale is True
    assert "STALE_MARKET_REFERENCE" in stale.quality_flags
    assert crossed.market_reference is None
    assert crossed.market_reference_kind == MarketReferenceKind.MISSING
    assert "CROSSED_MARKET_REFERENCE" in crossed.quality_flags
    assert stale.outcome_label == crossed.outcome_label == 1


def test_artifact_writer_persists_reconstructable_manifest_and_jsonl(tmp_path: Path) -> None:
    connection = _connection()
    _outcome(connection, "KATL", "2026-07-13", 80.0)
    _outcome(connection, "KATL", "2026-07-14", 80.0)
    _market(connection, "market-1")
    _snapshot(connection, 1, station="KATL", market_id="market-1", market_date="2026-07-13")
    _snapshot(connection, 2, station="KATL", market_id="market-1")
    connection.commit()
    artifact = materialize_v2a_dataset(connection, SPEC, evaluation_start_date="2026-07-14")

    write_v2a_dataset_artifact(artifact, tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    eval_row = json.loads((tmp_path / "frozen_policy_evaluation.jsonl").read_text(encoding="utf-8"))
    assert manifest["signal_spec_hash"] == SPEC.spec_hash
    assert manifest["fit_rows"] == manifest["evaluation_rows"] == 1
    assert eval_row["row_hash"] == artifact.evaluation_rows[0].row_hash
    assert eval_row["source_prediction_snapshot_ids"] == [2]


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        create table prediction_snapshots (
            id integer primary key,
            timestamp text not null,
            station text not null,
            market_date text not null,
            decision_time_utc text not null,
            decision_time_local text not null,
            latest_obs_time_utc text not null,
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
            selected_book_timestamp text,
            selected_book_age_seconds real
        );
        create table station_date_outcomes (
            station text not null,
            market_date text not null,
            timestamp text not null,
            final_high_tmpf real not null,
            source text not null,
            resolved_at text not null,
            primary key(station, market_date)
        );
        create table markets (
            market_id text primary key,
            resolution_source text
        );
        """
    )
    return connection


def _outcome(connection: sqlite3.Connection, station: str, market_date: str, final_high: float) -> None:
    resolved_date = "2026-07-17" if market_date >= "2026-07-16" else "2026-07-15"
    connection.execute(
        "insert into station_date_outcomes values (?, ?, ?, ?, ?, ?)",
        (station, market_date, f"{resolved_date}T12:00:00+00:00", final_high, "IEM_ASOS", f"{resolved_date}T12:00:00+00:00"),
    )


def _market(connection: sqlite3.Connection, market_id: str) -> None:
    connection.execute("insert or ignore into markets values (?, ?)", (market_id, "Weather Underground"))


def _snapshot(
    connection: sqlite3.Connection,
    id_: int,
    *,
    station: str,
    market_id: str,
    market_date: str = "2026-07-14",
    obs_delay: str = "10m",
    side: str = "BUY_NO",
    strategy: str = "HIGH_CONVICTION",
    timestamp: str | None = None,
    latest_obs_time: str | None = None,
    best_bid: float = 0.18,
    best_ask: float = 0.22,
    book_age: float = 2.0,
) -> None:
    day = market_date
    timestamp = timestamp or f"{day}T17:01:00+00:00"
    latest_obs_time = latest_obs_time or f"{day}T16:50:00+00:00"
    decision_utc = f"{day}T17:00:00+00:00"
    local_offset = "-04:00" if station in {"KATL", "KBOS"} else "+00:00"
    decision_local = f"{day}T13:00:00{local_offset}"
    yes_fair, no_fair = 0.25, 0.75
    yes_ask, no_ask = 0.78, 0.20
    connection.execute(
        """
        insert into prediction_snapshots values (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            id_,
            timestamp,
            station,
            market_date,
            decision_utc,
            decision_local,
            latest_obs_time,
            obs_delay,
            strategy,
            market_id,
            "74-75F",
            side,
            0.50,
            yes_fair,
            no_fair,
            yes_ask,
            no_ask,
            HRRR_RICH_DYNAMIC_TUNED_MODEL,
            "HIGH_TEMP",
            best_bid,
            best_ask,
            f"{day}T17:00:58+00:00",
            book_age,
        ),
    )
