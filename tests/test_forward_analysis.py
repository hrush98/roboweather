from __future__ import annotations

from pathlib import Path

from weather_trader.discovery.cache_analysis import CachedAnalysisRow
from weather_trader.discovery.contracts import CandidateRule
from weather_trader.discovery.forward_analysis import (
    ExistingCandidateVersion,
    evaluate_existing_candidates,
    load_existing_candidate_versions,
)
from weather_trader.discovery.registry import DiscoveryRegistry


def _row(index: int, *, label: int = 1) -> CachedAnalysisRow:
    day = f"2026-01-{index:02d}"
    summaries = {
        f"cap={cap:.8f}|target={target:.8f}": {
            "cost_usd": target,
            "shares": target / 0.20,
            "vwap": 0.20,
            "fill_fraction": 1.0,
        }
        for cap in (0.35, 0.50)
        for target in (25.0, 50.0, 100.0)
    }
    return CachedAnalysisRow(
        mapping_id=f"mapping-{index}",
        source_snapshot_id=index,
        decision_id=f"decision-{index}",
        quote_ready_timestamp_utc=f"{day}T20:00:00.250000+00:00",
        execution_timestamp_utc=f"{day}T20:00:10+00:00",
        station="KATL",
        market_date=day,
        model_id="model-a",
        market_family="HIGH_TEMP",
        selected_side="BUY_YES",
        strategy_bucket="HIGH_CONVICTION",
        high_conviction=True,
        observation_delay_bucket="10m",
        local_hhmm="13:00",
        best_bid=0.19,
        best_ask=0.20,
        spread=0.01,
        model_fair=0.80,
        edge_at_best=0.60,
        label=label,
        execution_summaries=summaries,
        decision_result_hash=f"decision-hash-{index}",
        outcome_result_hash=f"outcome-hash-{index}-{label}",
    )


def _rule() -> CandidateRule:
    return CandidateRule(
        model_id="model-a",
        market_family="HIGH_TEMP",
        selected_side="BUY_YES",
        strategy_bucket="HIGH_CONVICTION",
        observation_delay_bucket="10m",
        local_start="12:00",
        local_end="15:00",
        entry_price_min=0.05,
        entry_price_max=0.50,
        require_high_conviction=True,
    )


def _candidate(candidate_id: str, *, activation: str = "2026-01-03T00:00:00+00:00") -> ExistingCandidateVersion:
    return ExistingCandidateVersion(
        candidate_version_id=candidate_id,
        family_id="family-1",
        family_version=1,
        definition_hash=f"hash-{candidate_id}",
        source_run_id="run-1",
        activation_timestamp_utc=activation,
        pricing_version="pricing-1",
        execution_version="first_post_ready_checkpoint_taker_v1",
        risk_version="risk-1",
        current_role="NOMINATED",
        rule=_rule(),
        sizing_and_risk={"target_cost_usd": 25.0},
    )


def test_forward_evaluation_excludes_every_pre_activation_row_and_fails_promotion_closed() -> None:
    rows = [_row(index) for index in range(1, 6)]
    candidate = _candidate("candidate-a")

    first = evaluate_existing_candidates(rows, [candidate])
    repeated = evaluate_existing_candidates(list(reversed(rows)), [candidate])
    result = first["candidates"][0]

    assert first["forward_content_hash"] == repeated["forward_content_hash"]
    assert result["matching_rows_before_activation_excluded"] == 2
    assert result["matching_rows_at_or_after_activation"] == 3
    assert result["weather_outcome_diagnostic"]["source_mapping_ids"] == [
        "mapping-3", "mapping-4", "mapping-5"
    ]
    assert result["promotion_disposition"] == "CONTINUE_COLLECTING"
    assert "VENUE_SETTLEMENT_UNAVAILABLE" in result["promotion_blockers"]
    assert "MARKOUTS_UNAVAILABLE" in result["promotion_blockers"]
    assert result["funded_authorization"] is False


def test_forward_evaluation_uses_registered_execution_price_cap() -> None:
    candidate = _candidate("candidate-a")
    candidate = ExistingCandidateVersion(
        **{
            **candidate.__dict__,
            "sizing_and_risk": {
                "price_cap": 0.35,
                "target_cost_usd": 25.0,
            },
        }
    )

    result = evaluate_existing_candidates([_row(3)], [candidate])["candidates"][0]

    assert result["execution_evidence"]["execution_summary_key"] == (
        "cap=0.35000000|target=25.00000000"
    )
    assert result["weather_outcome_diagnostic"]["trades"] == 1


def test_aligned_and_incremental_comparisons_apply_shared_caps_in_declared_order() -> None:
    rows = [_row(index) for index in range(3, 6)]
    first = _candidate("candidate-a")
    second = _candidate("candidate-b")

    result = evaluate_existing_candidates(rows, [second, first])

    assert result["common_dates"] == ["2026-01-03", "2026-01-04", "2026-01-05"]
    assert all(item["trades"] == 3 for item in result["aligned_common_date_comparison"])
    incremental = result["incremental_cap_aware_comparison"]
    assert incremental[0]["candidate_version_id"] == "candidate-a"
    assert incremental[0]["incremental_after_prior_candidates"]["trades"] == 3
    assert incremental[1]["candidate_version_id"] == "candidate-b"
    assert incremental[1]["incremental_after_prior_candidates"]["trades"] == 0
    assert incremental[1]["shared_cap_rejections"]["SHARED_STATION_DATE_CAP"] == 3


def test_registry_loader_preserves_immutable_definition_and_activation(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"
    with DiscoveryRegistry(path) as registry:
        run_id = registry.register_discovery_run(
            {
                "run_id": "run-1",
                "source_start_date": "2026-01-01",
                "discovery_cutoff_exclusive": "2026-01-03",
                "research_watermark": 1,
                "outcome_watermark": "outcome-1",
                "venue_settlement_watermark": "venue-1",
                "grammar_version": "grammar-1",
            },
            created_at_utc="2026-01-03T00:00:00+00:00",
        )
        family_id = registry.register_family(
            definition={"family_id": "family-1", "model_id": "model-a"},
            economic_rationale="test",
            grammar_provenance="test",
            correlation_group="test",
            created_at_utc="2026-01-03T00:00:00+00:00",
        )
        candidate_id = registry.register_candidate_version(
            family_id=family_id,
            source_run_id=run_id,
            rule=_rule(),
            activation_timestamp_utc="2026-01-03T00:00:00+00:00",
            pricing_version="pricing-1",
            execution_version="first_post_ready_checkpoint_taker_v1",
            risk_version="risk-1",
            created_at_utc="2026-01-03T00:00:00+00:00",
            sizing_and_risk={"target_cost_usd": 25.0},
        )

    with DiscoveryRegistry(path, read_only=True) as registry:
        loaded = load_existing_candidate_versions(registry.connection)

    assert len(loaded) == 1
    assert loaded[0].candidate_version_id == candidate_id
    assert loaded[0].activation_timestamp_utc == "2026-01-03T00:00:00+00:00"
    assert loaded[0].rule == _rule()
    assert loaded[0].sizing_and_risk == {"target_cost_usd": 25.0}
