from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from weather_trader.discovery.contracts import CandidateRule, DiscoveryRunSpec, StrategyManifest
from weather_trader.discovery.registry import (
    DiscoveryRegistry,
    ImmutableRegistryConflict,
    RegistryWriterLocked,
)
from weather_trader.pricing.contracts import stable_hash


CREATED = "2026-01-10T00:00:00+00:00"


def run_spec(*, cutoff: str = "2026-01-10") -> DiscoveryRunSpec:
    return DiscoveryRunSpec(
        source_start_date="2026-01-01",
        discovery_cutoff_exclusive=cutoff,
        earliest_activation_timestamp=f"{cutoff}T00:00:00+00:00",
        research_watermark=100,
        tape_session_ids=("session-1",),
        tape_partition_ids=("partition-1",),
        build_hash="build-hash",
        outcome_watermark="outcome-watermark",
        venue_settlement_watermark="venue-watermark",
        model_ids=("model-1",),
        model_market_families=(("model-1", "HIGH_TEMP"),),
        minimum_effective_dates=3,
        minimum_executable_station_dates=3,
    )


def rule(*, entry_price_max: float = 0.5) -> CandidateRule:
    return CandidateRule(
        model_id="model-1",
        market_family="HIGH_TEMP",
        selected_side="BUY_NO",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        local_start="12:00",
        local_end="15:00",
        entry_price_min=0.05,
        entry_price_max=entry_price_max,
        require_high_conviction=True,
    )


def register_base(registry: DiscoveryRegistry) -> tuple[str, str]:
    run_id = registry.register_discovery_run(run_spec(), created_at_utc=CREATED)
    family_id = registry.register_family(
        definition={
            "family_id": rule().correlated_family_id,
            "model_id": "model-1",
            "market_family": "HIGH_TEMP",
            "selected_side": "BUY_NO",
            "economic_feature": "late high-conviction disagreement",
        },
        economic_rationale="Model-versus-market disagreement near the close.",
        grammar_provenance="phase3d_simple_rules_v1",
        correlation_group="late-model-1-buy-no",
        created_at_utc=CREATED,
    )
    return run_id, family_id


def register_candidate(
    registry: DiscoveryRegistry,
    run_id: str,
    family_id: str,
    candidate_rule: CandidateRule,
) -> str:
    return registry.register_candidate_version(
        family_id=family_id,
        source_run_id=run_id,
        rule=candidate_rule,
        activation_timestamp_utc=CREATED,
        pricing_version="price-v1",
        execution_version="stable-taker-v1",
        risk_version="risk-v1",
        sizing_and_risk={"target_cost_usd": 25.0, "daily_cap_usd": 300.0},
        created_at_utc=CREATED,
    )


def test_registry_reuses_unchanged_definition_and_versions_changed_rule(tmp_path: Path) -> None:
    path = tmp_path / "discovery.sqlite"
    with DiscoveryRegistry(path) as registry:
        run_id, family_id = register_base(registry)
        first = register_candidate(registry, run_id, family_id, rule())
        repeated = register_candidate(registry, run_id, family_id, rule())
        changed = register_candidate(registry, run_id, family_id, rule(entry_price_max=0.35))

        assert repeated == first
        assert changed != first
        versions = registry.connection.execute(
            "select candidate_version_id,family_version from candidate_versions order by family_version"
        ).fetchall()
        assert [(row[0], row[1]) for row in versions] == [(first, 1), (changed, 2)]
        assert registry.table_counts()["candidate_lifecycle_events"] == 2
        assert registry.current_role(first) == "GENERATED"

    with DiscoveryRegistry(path, read_only=True) as observer:
        assert observer.table_counts()["candidate_versions"] == 2
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            observer.connection.execute("delete from candidate_versions")


def test_registry_enforces_one_writer_and_database_append_only_guards(tmp_path: Path) -> None:
    path = tmp_path / "discovery.sqlite"
    with DiscoveryRegistry(path) as writer:
        run_id, _ = register_base(writer)
        with pytest.raises(RegistryWriterLocked):
            DiscoveryRegistry(path)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            writer.connection.execute(
                "update discovery_runs set status='CHANGED' where run_id=?", (run_id,)
            )

    with DiscoveryRegistry(path) as replacement_writer:
        assert replacement_writer.table_counts()["discovery_runs"] == 1


def test_cohorts_and_scorecards_are_causal_idempotent_and_nonrewritable(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "discovery.sqlite") as registry:
        run_id, family_id = register_base(registry)
        candidate_id = register_candidate(registry, run_id, family_id, rule())
        cohort_id = registry.register_evaluation_cohort(
            candidate_version_id=candidate_id,
            activation_timestamp_utc=CREATED,
            eligible_start_utc=CREATED,
            eligible_end_utc=None,
            source_watermarks={"research": 100, "tape": "partition-1"},
            requirements={"venue_settlement": True, "markouts": True, "valid_tape": True},
            initial_completeness={"state": "PENDING"},
            created_at_utc=CREATED,
        )
        args = {
            "candidate_version_id": candidate_id,
            "cohort_id": cohort_id,
            "evidence_kind": "FORWARD_SHADOW",
            "as_of_watermarks": {"research": 120, "venue": "2026-01-12T00:00:00Z"},
            "statistics": {"effective_dates": 2, "cost_usd": 50.0, "pnl_usd": 8.0},
            "rejection_counts": {"INVALID_TAPE": 1},
            "source_refs": {"report_hash": "report-1"},
            "created_at_utc": "2026-01-12T01:00:00+00:00",
        }
        scorecard_id = registry.append_scorecard(**args)
        assert registry.append_scorecard(**args) == scorecard_id

        changed = {**args, "statistics": {**args["statistics"], "pnl_usd": 99.0}}
        with pytest.raises(ImmutableRegistryConflict, match="different content"):
            registry.append_scorecard(**changed)
        with pytest.raises(ValueError, match="activation"):
            registry.register_evaluation_cohort(
                candidate_version_id=candidate_id,
                activation_timestamp_utc="2026-01-11T00:00:00+00:00",
                eligible_start_utc="2026-01-11T00:00:00+00:00",
                eligible_end_utc=None,
                source_watermarks={},
                requirements={},
                initial_completeness={},
                created_at_utc=CREATED,
            )


def test_lifecycle_history_is_appended_and_current_role_is_derived(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "discovery.sqlite") as registry:
        run_id, family_id = register_base(registry)
        candidate_id = register_candidate(registry, run_id, family_id, rule())
        event_id = registry.append_lifecycle_event(
            candidate_version_id=candidate_id,
            event_type="NOMINATED",
            from_role="GENERATED",
            to_role="NOMINATED",
            occurred_at_utc="2026-01-10T00:01:00+00:00",
            reason="passed sealed discovery gates",
            source_run_id=run_id,
        )
        assert registry.append_lifecycle_event(
            candidate_version_id=candidate_id,
            event_type="NOMINATED",
            from_role="GENERATED",
            to_role="NOMINATED",
            occurred_at_utc="2026-01-10T00:01:00+00:00",
            reason="passed sealed discovery gates",
            source_run_id=run_id,
        ) == event_id
        assert registry.current_role(candidate_id) == "NOMINATED"
        assert registry.table_counts()["candidate_lifecycle_events"] == 2


def test_batch_v1_import_preserves_identity_without_forward_evidence(tmp_path: Path) -> None:
    artifacts = tmp_path / "batch"
    artifacts.mkdir()
    run = run_spec()
    spec_payload = run.canonical_payload()
    (artifacts / "discovery_run.json").write_text(json.dumps({
        "run_id": run.run_id,
        "run_hash": stable_hash(spec_payload),
        "spec": spec_payload,
    }), encoding="utf-8")
    manifest = StrategyManifest(
        strategy_id=rule().candidate_id,
        version=1,
        discovery_run_id=run.run_id,
        discovery_run_hash=stable_hash(spec_payload),
        candidate=rule(),
        activation_timestamp=CREATED,
        untouched_holdout_start=CREATED,
        source_cutoff_exclusive="2026-01-10",
        pricing_version="snapshot_fair_v1",
        cost_version="binary_payout_no_fee_discovery_v1",
        execution_arm="stable_taker",
        latency_ms=250,
        pre_signal_seconds=60,
        target_cost_usd=25.0,
        price_cap=0.5,
        station_date_cap_usd=25.0,
        daily_risk_cap_usd=300.0,
        minimum_forward_effective_dates=3,
        minimum_forward_station_dates=3,
        settlement_source="venue_authoritative",
        source_hash="rows-hash",
        code_hash="code-hash",
    ).frozen()
    (artifacts / "strategy_manifest.json").write_text(
        json.dumps(manifest.canonical_payload()), encoding="utf-8"
    )

    with DiscoveryRegistry(tmp_path / "discovery.sqlite") as registry:
        imported = registry.import_batch_v1(artifacts)
        repeated = registry.import_batch_v1(artifacts)
        assert repeated == imported
        assert imported["candidate_version_id"] is not None
        assert imported["forward_evidence_imported"] is False
        counts = registry.table_counts()
        assert counts["discovery_runs"] == 1
        assert counts["candidate_versions"] == 1
        assert counts["evaluation_cohorts"] == 0
        assert counts["candidate_scorecards"] == 0
