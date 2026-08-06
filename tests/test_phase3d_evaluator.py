from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from weather_trader.discovery.contracts import BroadDiscoveryRow, CandidateRule, DiscoveryRunSpec
from weather_trader.discovery.evaluator import ContinuousCohortEvaluator
from weather_trader.discovery.registry import DiscoveryRegistry


ACTIVATION = "2026-01-10T00:00:00+00:00"


def _run() -> DiscoveryRunSpec:
    return DiscoveryRunSpec(
        source_start_date="2026-01-01",
        discovery_cutoff_exclusive="2026-01-10",
        earliest_activation_timestamp=ACTIVATION,
        research_watermark=100,
        tape_session_ids=("session-1",),
        tape_partition_ids=("partition-1",),
        build_hash="build-1",
        outcome_watermark="2026-01-09T12:00:00+00:00",
        venue_settlement_watermark="2026-01-09T13:00:00+00:00",
        model_ids=("model-a",),
        model_market_families=(("model-a", "HIGH_TEMP"),),
        minimum_effective_dates=3,
        minimum_executable_station_dates=3,
    )


def _rule(*, entry_max: float = 0.5) -> CandidateRule:
    return CandidateRule(
        model_id="model-a",
        market_family="HIGH_TEMP",
        selected_side="BUY_YES",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        local_start="12:00",
        local_end="15:00",
        entry_price_min=0.05,
        entry_price_max=entry_max,
        require_high_conviction=True,
    )


def _register(registry: DiscoveryRegistry, *, second: bool = False) -> list[str]:
    run = _run()
    run_id = registry.register_discovery_run(run, created_at_utc=ACTIVATION)
    family_id = registry.register_family(
        definition={"family_id": _rule().correlated_family_id, "model_id": "model-a"},
        economic_rationale="test",
        grammar_provenance=run.grammar_version,
        correlation_group=_rule().correlated_family_id,
        created_at_utc=ACTIVATION,
    )
    rules = [_rule()] + ([_rule(entry_max=0.4)] if second else [])
    candidates = []
    for index, rule in enumerate(rules):
        candidate = registry.register_candidate_version(
            family_id=family_id,
            source_run_id=run_id,
            rule=rule,
            activation_timestamp_utc=ACTIVATION,
            pricing_version="raw-v1",
            execution_version="stable-taker-v1",
            risk_version="fixed-caps-v1",
            sizing_and_risk={
                "target_cost_usd": 25.0,
                "station_date_cap_usd": 25.0,
                "daily_risk_cap_usd": 300.0,
                "latency_ms": 250,
                "pre_signal_seconds": 60,
            },
            created_at_utc=ACTIVATION,
        )
        registry.append_lifecycle_event(
            candidate_version_id=candidate,
            event_type="NOMINATED",
            from_role="GENERATED",
            to_role="NOMINATED",
            occurred_at_utc=f"2026-01-10T00:0{index + 1}:00+00:00",
            reason="test nomination",
            source_run_id=run_id,
        )
        candidates.append(candidate)
    return candidates


def _row(day: int, *, venue: int | None = 1, markouts: bool = True) -> BroadDiscoveryRow:
    value = BroadDiscoveryRow(
        row_id=f"row-{day}",
        row_hash="",
        discovery_run_id="evaluation-view",
        build_hash="build-2",
        source_prediction_snapshot_ids=(day,),
        source_snapshot_payload_hash=f"snapshot-{day}",
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
        selected_market_id=f"market-{day}",
        selected_bucket="<=90F",
        selected_side="BUY_YES",
        token_id=f"token-{day}",
        raw_model_fair=0.8,
        snapshot_entry_price=0.2,
        high_conviction=True,
        tape_eligible=True,
        tape_ineligibility_reason=None,
        tape_session_id="session-1",
        coverage_interval_id=day,
        reconstruction_hash=f"book-{day}",
        best_bid=0.19,
        best_ask=0.20,
        spread=0.01,
        depth_at_best_ask=200.0,
        ask_levels=((0.20, 200.0),),
        taker_cost_usd=25.0,
        taker_shares=125.0,
        taker_vwap=0.20,
        fill_fraction=1.0,
        research_outcome_label=1,
        research_outcome_source="IEM_ASOS",
        venue_outcome_label=venue,
        venue_resolution_source="POLYMARKET" if venue is not None else None,
        settlement_disagreement=False if venue is not None else None,
        markouts_valid=markouts,
        markout_midpoints=(("30s", 0.21), ("2m", 0.22)) if markouts else (),
        actual_fill_status="UNAVAILABLE_PUBLIC_TAPE_COUNTERFACTUAL",
        discovery_eligible=True,
        discovery_ineligibility_reasons=(),
    )
    return value.with_hash()


def _watermarks() -> dict[str, object]:
    return {
        "research_prediction_snapshot_id": 140,
        "outcome_resolved_at": "2026-01-14T12:00:00+00:00",
        "venue_settlement_resolved_at": "2026-01-14T13:00:00+00:00",
        "tape_membership_hash": "tape-1",
        "row_set_hash": "rows-1",
        "end_date_exclusive": "2026-01-15",
        "as_of_timestamp_utc": "2026-01-15T00:00:00+00:00",
        "build_hash": "build-2",
    }


def _scorecard(registry: DiscoveryRegistry, kind: str) -> dict:
    row = registry.connection.execute(
        "select statistics_json from candidate_scorecards where evidence_kind=? order by rowid limit 1",
        (kind,),
    ).fetchone()
    return json.loads(row[0])


def test_c4_scorecards_are_activation_bounded_idempotent_and_evidence_explicit(
    tmp_path: Path,
) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        _register(registry)
        evaluator = ContinuousCohortEvaluator(registry)
        rows = [_row(9), _row(11), _row(12), _row(13)]
        first = evaluator.evaluate(
            rows=rows,
            as_of_watermarks=_watermarks(),
            materialization_diagnostics={"row_set_hash": "rows-1", "counts": {"ELIGIBLE": 4}},
            created_at_utc="2026-01-15T00:00:00+00:00",
        )
        counts = registry.table_counts()
        repeated = evaluator.evaluate(
            rows=rows,
            as_of_watermarks=_watermarks(),
            materialization_diagnostics={"row_set_hash": "rows-1", "counts": {"ELIGIBLE": 4}},
            created_at_utc="2026-01-15T01:00:00+00:00",
        )

        assert first["status"] == repeated["status"] == "COMPLETED"
        assert registry.table_counts() == counts
        assert counts["evaluation_cohorts"] == 1
        assert counts["candidate_scorecards"] == 2
        forward = _scorecard(registry, "FORWARD_SHADOW")
        assert forward["counts"]["pre_activation_excluded"] == 1
        assert forward["counts"]["raw_post_activation_rows"] == 3
        assert forward["counts"]["venue_resolved_executions"] == 3
        assert forward["review_state"] == "READY_FOR_C5_REVIEW"
        assert forward["actual_order_evidence"]["status"] == "SEPARATE_STREAM_NOT_INFERRED_FROM_PUBLIC_TAPE"
        assert forward["funded_authorization"] is False


def test_c4_fails_closed_on_research_only_truth_invalid_tape_and_markouts(
    tmp_path: Path,
) -> None:
    invalid = replace(
        _row(12),
        tape_eligible=False,
        tape_ineligibility_reason="coverage_gap",
        discovery_eligible=False,
        discovery_ineligibility_reasons=("TAPE:coverage_gap",),
    ).with_hash()
    rows = [_row(11, venue=None), invalid, _row(13, markouts=False)]
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        _register(registry)
        ContinuousCohortEvaluator(registry).evaluate(
            rows=rows,
            as_of_watermarks=_watermarks(),
            materialization_diagnostics={"row_set_hash": "rows-1", "counts": {"INELIGIBLE": 1}},
            created_at_utc="2026-01-15T00:00:00+00:00",
        )

        forward = _scorecard(registry, "FORWARD_SHADOW")
        rejection = json.loads(registry.connection.execute(
            "select rejection_counts_json from candidate_scorecards where evidence_kind='FORWARD_SHADOW'"
        ).fetchone()[0])
        assert forward["counts"]["invalid"] == 1
        assert forward["settlement"]["research_only_not_credited"] == 1
        assert forward["markouts"]["missing_or_invalid_executions"] == 1
        assert forward["review_state"] == "CONTINUE_COLLECTING"
        assert "MARKOUTS_UNAVAILABLE_OR_INVALID" in forward["blockers"]
        assert rejection["TAPE:coverage_gap"] == 1


def test_c4_common_date_comparisons_are_deterministic_within_family(
    tmp_path: Path,
) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        candidates = _register(registry, second=True)
        ContinuousCohortEvaluator(registry).evaluate(
            rows=[_row(11), _row(12), _row(13)],
            as_of_watermarks=_watermarks(),
            materialization_diagnostics={"row_set_hash": "rows-1", "counts": {"ELIGIBLE": 3}},
            created_at_utc="2026-01-15T00:00:00+00:00",
        )

        rows = registry.connection.execute(
            """select candidate_version_id,statistics_json from candidate_scorecards
               where evidence_kind='COMMON_DATE' order by candidate_version_id"""
        ).fetchall()
        assert len(rows) == 2
        for candidate_id, payload in rows:
            statistics = json.loads(payload)
            assert statistics["comparison_status"] == "AVAILABLE"
            assert statistics["comparisons"][0]["common_market_dates"] == [
                "2026-01-11", "2026-01-12", "2026-01-13"
            ]
            assert statistics["comparisons"][0]["peer_candidate_version_id"] in candidates
            assert statistics["comparisons"][0]["peer_candidate_version_id"] != candidate_id
