from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from weather_trader.discovery.cache_analysis import (
    COMPLETED_WITH_EMERGED_STRATEGIES,
    CachedAnalysisRow,
    HistoricalDiscoveryConfig,
)
from weather_trader.discovery.emergence import (
    attach_emerged_candidate_plan,
    candidate_rule_from_discovery_payload,
    register_emerged_candidate_plan,
)
from weather_trader.discovery.forward_analysis import (
    evaluate_existing_candidates,
    load_existing_candidate_versions,
)
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.pricing.contracts import stable_hash


def _historical_rule() -> dict[str, object]:
    return {
        "model_id": "model-a",
        "market_family": "HIGH_TEMP",
        "selected_side": "BUY_YES",
        "observation_delay_bucket": "ANY",
        "local_window": ["12:00", "15:00"],
        "entry_band": [0.05, 0.35],
        "minimum_model_edge_at_best_ask": 0.10,
        "maximum_spread": 0.05,
        "require_high_conviction": True,
        "dedupe_scope": "first_station_date",
        "execution": "first_post_ready_checkpoint_taker_v1",
    }


def _completed_result(config: HistoricalDiscoveryConfig) -> dict[str, object]:
    rule = candidate_rule_from_discovery_payload(_historical_rule())
    payload: dict[str, object] = {
        "status": COMPLETED_WITH_EMERGED_STRATEGIES,
        "plain_language_answer": "One family emerged.",
        "manifest": {
            "configuration": asdict(config),
            "manifest_hash": "manifest-hash",
            "decision_contract_hash": "decision-contract",
            "sealed_research_watermark": 10,
            "sealed_outcome_watermark": "outcome-watermark",
        },
        "grid": {"surviving_holdout_families": 1},
        "family_representatives": [{
            "survives_holdout": True,
            "rule_id": "rule-1",
            "family_id": rule.correlated_family_id,
            "rule": _historical_rule(),
        }],
        "existing_candidates": [],
        "funded_authorization": False,
    }
    payload["result_content_hash"] = stable_hash(payload)
    return payload


def _row(
    index: int,
    *,
    day: str,
    fair: float = 0.40,
    spread: float | None = 0.04,
) -> CachedAnalysisRow:
    summaries = {
        "cap=0.35000000|target=25.00000000": {
            "cost_usd": 25.0,
            "shares": 125.0,
            "vwap": 0.20,
            "fill_fraction": 1.0,
        }
    }
    return CachedAnalysisRow(
        mapping_id=f"mapping-{index}",
        source_snapshot_id=index,
        decision_id=f"decision-{index}",
        quote_ready_timestamp_utc=f"{day}T20:00:00+00:00",
        execution_timestamp_utc=f"{day}T20:00:01+00:00",
        station=f"K{index:03d}",
        market_date=day,
        model_id="model-a",
        market_family="HIGH_TEMP",
        selected_side="BUY_YES",
        strategy_bucket="A_DIFFERENT_BUCKET",
        high_conviction=True,
        observation_delay_bucket="15m",
        local_hhmm="13:00",
        best_bid=0.16,
        best_ask=0.20,
        spread=spread,
        model_fair=fair,
        edge_at_best=fair - 0.20,
        label=1,
        execution_summaries=summaries,
        decision_result_hash=f"decision-hash-{index}",
        outcome_result_hash=f"outcome-hash-{index}",
    )


def test_emerged_rule_registers_once_and_only_exact_post_activation_rows_score(
    tmp_path: Path,
) -> None:
    config = HistoricalDiscoveryConfig(
        source_start_date="2026-01-01",
        cutoff_exclusive="2026-01-10",
    )
    result = attach_emerged_candidate_plan(
        _completed_result(config),
        activation_timestamp_utc="2026-01-10T00:00:00+00:00",
        execution_version="first_post_ready_checkpoint_taker_v1",
        config=config,
    )
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    for name in ("result.json", "report.md", "ranked_rules.csv"):
        (report_dir / name).write_text(name, encoding="utf-8")

    registry_path = tmp_path / "registry.sqlite"
    with DiscoveryRegistry(registry_path) as registry:
        first = register_emerged_candidate_plan(
            registry,
            result,
            report_dir=report_dir,
            registered_at_utc="2026-01-09T12:00:00+00:00",
        )
    with DiscoveryRegistry(registry_path) as registry:
        repeated = register_emerged_candidate_plan(
            registry,
            result,
            report_dir=report_dir,
            registered_at_utc="2026-01-09T13:00:00+00:00",
        )
        assert registry.table_counts()["candidate_versions"] == 1
        assert registry.table_counts()["candidate_lifecycle_events"] == 1
        candidates = load_existing_candidate_versions(registry.connection)

    assert first["candidate_version_ids"] == repeated["candidate_version_ids"]
    assert repeated["reused"] is True
    assert candidates[0].rule.strategy_bucket == "ANY"
    assert candidates[0].rule.minimum_model_edge_at_best_ask == 0.10
    assert candidates[0].rule.maximum_spread == 0.05

    evaluation = evaluate_existing_candidates(
        [
            _row(1, day="2026-01-09"),
            _row(2, day="2026-01-10"),
            _row(3, day="2026-01-11", fair=0.25),
            _row(4, day="2026-01-12", spread=0.06),
        ],
        candidates,
    )["candidates"][0]
    assert evaluation["matching_rows_before_activation_excluded"] == 1
    assert evaluation["matching_rows_at_or_after_activation"] == 1
    assert evaluation["weather_outcome_diagnostic"]["source_mapping_ids"] == [
        "mapping-2"
    ]


def test_atomic_registration_rejects_a_corrupt_sealed_identity_without_partial_rows(
    tmp_path: Path,
) -> None:
    config = HistoricalDiscoveryConfig(
        source_start_date="2026-01-01",
        cutoff_exclusive="2026-01-10",
    )
    result = attach_emerged_candidate_plan(
        _completed_result(config),
        activation_timestamp_utc="2026-01-10T00:00:00+00:00",
        execution_version="first_post_ready_checkpoint_taker_v1",
        config=config,
    )
    result["emerged_candidate_registration"]["candidates"][0][
        "candidate_version_id"
    ] = "corrupt"
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    for name in ("result.json", "report.md", "ranked_rules.csv"):
        (report_dir / name).write_text(name, encoding="utf-8")

    registry_path = tmp_path / "registry.sqlite"
    with DiscoveryRegistry(registry_path) as registry:
        with pytest.raises(ValueError, match="sealed candidate identity"):
            register_emerged_candidate_plan(
                registry,
                result,
                report_dir=report_dir,
                registered_at_utc="2026-01-09T12:00:00+00:00",
            )
        counts = registry.table_counts()

    assert counts["discovery_runs"] == 0
    assert counts["strategy_families"] == 0
    assert counts["candidate_versions"] == 0
