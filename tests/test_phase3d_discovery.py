from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weather_trader.discovery.contracts import (
    BroadDiscoveryRow,
    CandidateRule,
    DiscoveryRunSpec,
    write_immutable_json,
)
from weather_trader.discovery.engine import (
    discover,
    evaluate_frozen_manifest,
    freeze_winner_manifest,
    select_frozen_rows,
)
from weather_trader.discovery.materializer import _research_label, materialize_broad_discovery_view


def run_spec(**overrides) -> DiscoveryRunSpec:
    values = {
        "source_start_date": "2026-01-01",
        "discovery_cutoff_exclusive": "2026-01-10",
        "earliest_activation_timestamp": "2026-01-10T00:00:00+00:00",
        "research_watermark": 100,
        "tape_session_ids": ("session-1",),
        "tape_partition_ids": ("partition-1",),
        "build_hash": "build-1",
        "outcome_watermark": "2026-01-11T00:00:00+00:00",
        "venue_settlement_watermark": "2026-01-11T00:00:00+00:00",
        "model_ids": ("model-a",),
        "model_market_families": (("model-a", "HIGH_TEMP"),),
        "fold_count": 3,
        "minimum_effective_dates": 3,
        "minimum_executable_station_dates": 3,
    }
    values.update(overrides)
    return DiscoveryRunSpec(**values)


def broad_row(index: int, *, won: int = 1, venue: int | None = None) -> BroadDiscoveryRow:
    day = index + 1
    built = BroadDiscoveryRow(
        row_id=f"row-{index}",
        row_hash="",
        discovery_run_id="run-1",
        build_hash="build-1",
        source_prediction_snapshot_ids=(index,),
        source_snapshot_payload_hash=f"snapshot-{index}",
        snapshot_timestamp_utc=f"2026-01-{day:02d}T20:00:00+00:00",
        decision_time_utc=f"2026-01-{day:02d}T19:59:59+00:00",
        quote_ready_timestamp_utc=f"2026-01-{day:02d}T20:00:00.250000+00:00",
        latest_observation_time_utc=f"2026-01-{day:02d}T19:50:00+00:00",
        observation_age_minutes=10.0,
        station="KATL",
        market_date=f"2026-01-{day:02d}",
        market_family="HIGH_TEMP",
        model_id="model-a",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        local_decision_hhmm="13:00",
        lifecycle_horizon="D0_LATE",
        selected_market_id=f"market-{index}",
        selected_bucket="<=90F",
        selected_side="BUY_YES",
        token_id=f"token-{index}",
        raw_model_fair=0.8,
        snapshot_entry_price=0.2,
        high_conviction=True,
        tape_eligible=True,
        tape_ineligibility_reason=None,
        tape_session_id="session-1",
        coverage_interval_id=index,
        reconstruction_hash=f"book-{index}",
        best_bid=0.19,
        best_ask=0.20,
        spread=0.01,
        depth_at_best_ask=200.0,
        ask_levels=((0.20, 200.0),),
        taker_cost_usd=25.0,
        taker_shares=125.0,
        taker_vwap=0.20,
        fill_fraction=1.0,
        research_outcome_label=won,
        research_outcome_source="IEM_ASOS",
        venue_outcome_label=venue,
        venue_resolution_source="POLYMARKET" if venue is not None else None,
        settlement_disagreement=won != venue if venue is not None else None,
        markouts_valid=True,
        markout_midpoints=(("30s", 0.21), ("2m", 0.22)),
        actual_fill_status="UNAVAILABLE_PUBLIC_TAPE_COUNTERFACTUAL",
        discovery_eligible=True,
        discovery_ineligibility_reasons=(),
    )
    return built.with_hash()


def test_discovery_run_id_and_immutable_write_are_deterministic(tmp_path: Path) -> None:
    first = run_spec()
    second = run_spec()
    assert first.run_id == second.run_id

    target = tmp_path / "run.json"
    write_immutable_json(target, {"run_id": first.run_id})
    write_immutable_json(target, {"run_id": first.run_id})
    with pytest.raises(FileExistsError):
        write_immutable_json(target, {"run_id": "changed"})


class FakeBookProvider:
    def book_at(self, token_id, ready, *, pre_signal_seconds):
        assert token_id == "token-yes"
        assert ready == datetime(2026, 1, 2, 20, 0, 0, 250000, tzinfo=timezone.utc)
        assert pre_signal_seconds == 60
        return {
            "bids": {0.19: 10.0},
            "asks": {0.20: 200.0},
            "session_id": "session-1",
            "coverage_interval_id": 7,
            "reconstruction_hash": "book-hash",
        }, None


def test_broad_materializer_is_policy_neutral_causal_and_settlement_explicit() -> None:
    research = sqlite3.connect(":memory:")
    research.row_factory = sqlite3.Row
    research.executescript(
        """
        create table prediction_snapshots (
            id integer primary key, timestamp text, station text, market_date text,
            decision_time_utc text, decision_time_local text, latest_obs_time_utc text,
            obs_age_minutes real,
            obs_delay_bucket text, strategy_bucket text, selected_market_id text,
            selected_bucket text, selected_side text, selected_fair_yes real,
            selected_fair_no real, selected_yes_ask real, selected_no_ask real,
            high_conviction integer, model_name text, market_family text, raw_json text
        );
        create table station_date_outcomes (
            station text, market_date text, final_high_tmpf real, source text, resolved_at text
        );
        create table resolutions (
            market_id text, winning_side text, source text, resolved_at text
        );
        insert into prediction_snapshots values (
            1,'2026-01-02T20:00:00+00:00','KATL','2026-01-02',
            '2026-01-02T19:59:59+00:00','2026-01-02T13:00:00-07:00',
            '2026-01-02T19:50:00+00:00',10.0,'10m','HIGH_CONVICTION','market-1',
            '<=90F','BUY_YES',0.80,0.20,0.20,0.80,1,'previously-unknown-model','HIGH_TEMP','{}'
        );
        insert into station_date_outcomes values (
            'KATL','2026-01-02',85.0,'IEM_ASOS','2026-01-03T12:00:00+00:00'
        );
        insert into resolutions values (
            'market-1','YES','POLYMARKET','2026-01-03T13:00:00+00:00'
        );
        """
    )
    tape = sqlite3.connect(":memory:")
    tape.row_factory = sqlite3.Row
    tape.executescript(
        """
        create table tape_tokens (market_id text, outcome text, token_id text);
        insert into tape_tokens values ('market-1','YES','token-yes');
        """
    )

    rows, diagnostics = materialize_broad_discovery_view(
        research, tape, run_spec(), book_provider=FakeBookProvider()
    )

    assert len(rows) == 1
    assert rows[0].model_id == "previously-unknown-model"
    assert rows[0].discovery_eligible
    assert rows[0].research_outcome_label == rows[0].venue_outcome_label == 1
    assert rows[0].settlement_disagreement is False
    assert rows[0].reconstruction_hash == "book-hash"
    assert diagnostics["counts"]["ELIGIBLE"] == 1


def test_discovery_collapses_variants_and_freezes_only_one_winner() -> None:
    rows = [broad_row(index) for index in range(1, 10)]
    run = run_spec()

    report = discover(rows, run)
    manifest = freeze_winner_manifest(
        report,
        run,
        source_hash="rows-hash",
        code_hash="code-hash",
        activation_timestamp="2026-01-10T00:00:00+00:00",
        untouched_holdout_start="2026-01-10T00:00:00+00:00",
    )

    assert report["winner_candidate_id"] is not None
    assert report["correlated_family_count"] < report["candidate_count"]
    assert manifest is not None
    assert manifest.manifest_hash == manifest.frozen().manifest_hash
    assert manifest.discovery_run_id == run.run_id


def test_forward_evaluation_cannot_pass_without_venue_settlement() -> None:
    discovery_rows = [broad_row(index) for index in range(1, 10)]
    run = run_spec()
    report = discover(discovery_rows, run)
    manifest = freeze_winner_manifest(
        report,
        run,
        source_hash="rows-hash",
        code_hash="code-hash",
        activation_timestamp="2026-01-10T00:00:00+00:00",
        untouched_holdout_start="2026-01-10T00:00:00+00:00",
    )
    assert manifest is not None
    forward_rows = [
        replace(
            broad_row(index),
            snapshot_timestamp_utc=f"2026-01-{index + 10:02d}T20:00:00+00:00",
            market_date=f"2026-01-{index + 10:02d}",
        ).with_hash()
        for index in range(1, 5)
    ]

    pending = evaluate_frozen_manifest(forward_rows, manifest)
    assert pending["disposition"] == "CONTINUE_COLLECTING"
    assert "INSUFFICIENT_VENUE_RESOLVED_MARKET_DATES" in pending["reasons"]

    aligned = [
        replace(
            row,
            venue_outcome_label=1,
            venue_resolution_source="POLYMARKET",
            settlement_disagreement=False,
        ).with_hash()
        for row in forward_rows
    ]
    passed = evaluate_frozen_manifest(aligned, manifest)
    assert passed["disposition"] == "PASS_TO_PHASE4_REQUEST"
    assert passed["pnl"] > 0

    missing_markouts = [replace(row, markouts_valid=False, markout_midpoints=()).with_hash() for row in aligned]
    blocked = evaluate_frozen_manifest(missing_markouts, manifest)
    assert blocked["disposition"] == "CONTINUE_COLLECTING"
    assert "MARKOUTS_UNAVAILABLE_OR_INVALID" in blocked["reasons"]


def test_manifest_rejects_activation_before_cutoff() -> None:
    with pytest.raises(ValueError, match="activation"):
        run_spec(earliest_activation_timestamp="2026-01-09T23:59:59+00:00")


def test_frozen_selection_applies_daily_portfolio_cap() -> None:
    run = run_spec()
    report = discover([broad_row(index) for index in range(1, 10)], run)
    manifest = freeze_winner_manifest(
        report,
        run,
        source_hash="rows-hash",
        code_hash="code-hash",
        activation_timestamp="2026-01-10T00:00:00+00:00",
        untouched_holdout_start="2026-01-10T00:00:00+00:00",
    )
    assert manifest is not None
    manifest = replace(manifest, daily_risk_cap_usd=50.0, manifest_hash="").frozen()
    rows = [
        replace(
            broad_row(index),
            station=f"K{index:03d}",
            market_date="2026-01-11",
            snapshot_timestamp_utc=f"2026-01-11T20:00:0{index}+00:00",
        ).with_hash()
        for index in range(1, 5)
    ]

    selected = select_frozen_rows(rows, manifest)
    assert len(selected) == 2


def test_research_label_uses_daily_low_for_low_temperature_markets() -> None:
    row = {
        "market_family": "LOW_TEMP",
        "final_high_tmpf": 80.0,
        "final_low_tmpf": 25.0,
        "selected_bucket": "<=30F",
        "selected_side": "BUY_YES",
    }
    assert _research_label(row) == 1
