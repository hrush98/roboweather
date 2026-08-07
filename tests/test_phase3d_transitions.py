from __future__ import annotations

from pathlib import Path

import pytest

from weather_trader.discovery.contracts import CandidateRule, DiscoveryRunSpec
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.discovery.transitions import ResearchRoleTransitionEngine


ACTIVATION = "2026-01-10T00:00:00+00:00"


def _run() -> DiscoveryRunSpec:
    return DiscoveryRunSpec(
        source_start_date="2026-01-01",
        discovery_cutoff_exclusive="2026-01-10",
        earliest_activation_timestamp=ACTIVATION,
        research_watermark=10,
        tape_session_ids=("session",),
        tape_partition_ids=("partition",),
        build_hash="build",
        outcome_watermark="outcome",
        venue_settlement_watermark="venue",
        model_ids=("model",),
        model_market_families=(("model", "HIGH_TEMP"),),
        fold_count=2,
        minimum_effective_dates=2,
        minimum_executable_station_dates=2,
    )


def _rule(entry_max: float) -> CandidateRule:
    return CandidateRule(
        model_id="model",
        market_family="HIGH_TEMP",
        selected_side="BUY_NO",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        local_start="12:00",
        local_end="15:00",
        entry_price_min=0.05,
        entry_price_max=entry_max,
        require_high_conviction=True,
    )


def _register(registry: DiscoveryRegistry, count: int = 1) -> list[str]:
    run = _run()
    run_id = registry.register_discovery_run(run, created_at_utc=ACTIVATION)
    family_id = registry.register_family(
        definition={"family_id": _rule(0.5).correlated_family_id, "model": "model"},
        economic_rationale="test",
        grammar_provenance=run.grammar_version,
        correlation_group=_rule(0.5).correlated_family_id,
        created_at_utc=ACTIVATION,
    )
    candidates = []
    for index in range(count):
        candidate = registry.register_candidate_version(
            family_id=family_id,
            source_run_id=run_id,
            rule=_rule(0.5 - index * 0.05),
            activation_timestamp_utc=ACTIVATION,
            pricing_version="price",
            execution_version="stable-taker",
            risk_version="caps",
            sizing_and_risk={
                "target_cost_usd": 25.0,
                "station_date_cap_usd": 25.0,
                "daily_risk_cap_usd": 300.0,
            },
            created_at_utc=ACTIVATION,
        )
        registry.append_lifecycle_event(
            candidate_version_id=candidate,
            event_type="NOMINATED",
            from_role="GENERATED",
            to_role="NOMINATED",
            occurred_at_utc=f"2026-01-10T00:0{index + 1}:00+00:00",
            reason="test",
            source_run_id=run_id,
        )
        candidates.append(candidate)
    return candidates


def _statistics(*, conservative_rr: float, base_rr: float, ready: bool = True) -> dict:
    conservative_pnl = conservative_rr * 100.0
    base_pnl = base_rr * 100.0
    return {
        "review_state": "READY_FOR_C5_REVIEW" if ready else "CONTINUE_COLLECTING",
        "funded_authorization": False,
        "blockers": [] if ready else ["NONPOSITIVE_OR_ABSENT_BASE_ECONOMICS"],
        "counts": {"venue_resolved_executions": 4},
        "requirements": {"minimum_effective_dates": 2, "minimum_station_dates": 2},
        "effective_venue_resolved_market_dates": ["2026-01-11", "2026-01-12"],
        "effective_venue_resolved_station_dates": 4,
        "fill_scenarios": {
            "conservative_half_displayed_depth": {
                "venue_cost_usd": 100.0,
                "venue_pnl_usd": conservative_pnl,
                "venue_rr": conservative_rr,
                "uncertainty": {
                    "sample_size": 4,
                    "mean_pnl_usd": conservative_pnl / 4,
                    "standard_error_usd": 0.0,
                },
            },
            "base_displayed_depth": {
                "venue_cost_usd": 100.0,
                "venue_pnl_usd": base_pnl,
                "venue_rr": base_rr,
                "uncertainty": {
                    "sample_size": 4,
                    "mean_pnl_usd": base_pnl / 4,
                    "standard_error_usd": 0.0,
                },
            },
            "optimistic_full_displayed_depth": {
                "venue_cost_usd": 100.0,
                "venue_pnl_usd": max(base_pnl, 50.0),
                "venue_rr": max(base_rr, 0.5),
            },
        },
        "settlement": {"venue_resolved": 4, "disagreements": 0},
        "markouts": {"missing_or_invalid_executions": 0},
        "concentration": {
            "maximum_station_cost_share": 0.50,
            "maximum_market_date_cost_share": 0.25,
        },
    }


def _forward(
    registry: DiscoveryRegistry,
    candidate: str,
    *,
    sequence: int,
    conservative_rr: float,
    base_rr: float,
    ready: bool = True,
) -> str:
    return registry.append_scorecard(
        candidate_version_id=candidate,
        cohort_id=None,
        evidence_kind="FORWARD_SHADOW",
        as_of_watermarks={"sequence": sequence},
        statistics=_statistics(
            conservative_rr=conservative_rr, base_rr=base_rr, ready=ready
        ),
        rejection_counts={},
        source_refs={"test": True},
        created_at_utc=f"2026-01-{12 + sequence:02d}T00:00:00+00:00",
    )


def _common(
    registry: DiscoveryRegistry,
    candidate: str,
    peer: str,
    *,
    sequence: int,
    pnl_delta: float,
    rr_delta: float,
) -> None:
    registry.append_scorecard(
        candidate_version_id=candidate,
        cohort_id=None,
        evidence_kind="COMMON_DATE",
        as_of_watermarks={"sequence": sequence},
        statistics={
            "comparison_status": "AVAILABLE",
            "funded_authorization": False,
            "comparisons": [{
                "peer_candidate_version_id": peer,
                "common_market_date_count": 2,
                "replacement_incremental": {
                    "pnl_delta_usd": pnl_delta,
                    "rr_delta": rr_delta,
                },
                "additive_after_peer_caps": {
                    "venue_cost_usd": 0.0,
                    "venue_pnl_usd": 0.0,
                },
            }],
        },
        rejection_counts={},
        source_refs={"test": True},
        created_at_utc=f"2026-01-{12 + sequence:02d}T00:00:00+00:00",
    )


def test_c5_assigns_one_deterministic_research_champion_and_is_idempotent(
    tmp_path: Path,
) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        candidates = _register(registry, 2)
        _forward(registry, candidates[0], sequence=1, conservative_rr=0.30, base_rr=0.35)
        _forward(registry, candidates[1], sequence=1, conservative_rr=0.20, base_rr=0.25)
        engine = ResearchRoleTransitionEngine(registry)
        first = engine.apply(effective_at_utc="2026-01-20T00:00:00+00:00")
        second = engine.apply(effective_at_utc="2026-01-20T00:00:00+00:00")

        assert registry.current_role(candidates[0]) == "CHAMPION"
        assert registry.current_role(candidates[1]) == "CHALLENGER"
        assert any(item["to_role"] == "CHAMPION" for item in first["decisions"])
        assert second["decisions"] == []
        assert first["funded_authorization"] is False
        assert all(
            event["metadata"].get("funded_authorization") is False
            for candidate in candidates
            for event in registry.candidate_lifecycle_history(candidate)
            if event["event_type"] != "GENERATED"
        )


def test_c5_optimistic_only_evidence_cannot_create_a_champion(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        candidate = _register(registry)[0]
        _forward(
            registry,
            candidate,
            sequence=1,
            conservative_rr=-0.05,
            base_rr=0.10,
            ready=True,
        )
        result = ResearchRoleTransitionEngine(registry).apply(
            effective_at_utc="2026-01-20T00:00:00+00:00"
        )

        assert registry.current_role(candidate) == "CHALLENGER"
        assert not any(item["to_role"] == "CHAMPION" for item in result["decisions"])


def test_c5_replacement_requires_aligned_incremental_evidence(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        incumbent, challenger = _register(registry, 2)
        _forward(registry, incumbent, sequence=1, conservative_rr=0.25, base_rr=0.30)
        _forward(registry, challenger, sequence=1, conservative_rr=0.20, base_rr=0.25)
        engine = ResearchRoleTransitionEngine(registry)
        engine.apply(effective_at_utc="2026-01-20T00:00:00+00:00")

        _forward(registry, challenger, sequence=2, conservative_rr=0.40, base_rr=0.45)
        _common(
            registry,
            challenger,
            incumbent,
            sequence=2,
            pnl_delta=15.0,
            rr_delta=0.15,
        )
        result = engine.apply(effective_at_utc="2026-01-21T00:00:00+00:00")

        assert registry.current_role(incumbent) == "PROBATION"
        assert registry.current_role(challenger) == "CHAMPION"
        assert [item["to_role"] for item in result["decisions"]] == [
            "PROBATION",
            "CHAMPION",
        ]
        champion_event = registry.candidate_lifecycle_history(challenger)[-1]
        assert champion_event["metadata"]["common_date_scorecard_id"] is not None


def test_c5_retains_family_failure_history_across_versions(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        failed, survivor = _register(registry, 2)
        _forward(registry, failed, sequence=1, conservative_rr=-0.10, base_rr=-0.05, ready=False)
        _forward(registry, failed, sequence=2, conservative_rr=-0.12, base_rr=-0.08, ready=False)
        _forward(registry, survivor, sequence=1, conservative_rr=0.20, base_rr=0.25)
        ResearchRoleTransitionEngine(registry).apply(
            effective_at_utc="2026-01-20T00:00:00+00:00"
        )

        assert registry.current_role(failed) == "RETIRED"
        family_id = registry.connection.execute(
            "select family_id from candidate_versions where candidate_version_id=?",
            (failed,),
        ).fetchone()[0]
        history = registry.family_failure_history(str(family_id))
        assert history["failed_candidate_version_ids"] == [failed]
        assert registry.current_role(survivor) == "CHAMPION"


def test_registry_rejects_role_jumps_and_funded_transition_metadata(tmp_path: Path) -> None:
    with DiscoveryRegistry(tmp_path / "registry.sqlite") as registry:
        candidate = _register(registry)[0]
        with pytest.raises(ValueError, match="unsupported role transition"):
            registry.append_lifecycle_event(
                candidate_version_id=candidate,
                event_type="CHAMPION_ASSIGNED",
                from_role="NOMINATED",
                to_role="CHAMPION",
                occurred_at_utc="2026-01-20T00:00:00+00:00",
                reason="invalid jump",
            )
        with pytest.raises(ValueError, match="cannot authorize funded"):
            registry.append_lifecycle_event(
                candidate_version_id=candidate,
                event_type="SHADOW_ACTIVATED",
                from_role="NOMINATED",
                to_role="SHADOW_ACTIVE",
                occurred_at_utc="2026-01-20T00:00:00+00:00",
                reason="invalid authority",
                metadata={"funded_authorization": True},
            )
        with pytest.raises(ValueError, match="backdated"):
            registry.append_lifecycle_event(
                candidate_version_id=candidate,
                event_type="SHADOW_ACTIVATED",
                from_role="NOMINATED",
                to_role="SHADOW_ACTIVE",
                occurred_at_utc="2026-01-09T00:00:00+00:00",
                reason="invalid chronology",
            )
