from __future__ import annotations

from pathlib import Path

from weather_trader.discovery.cache_analysis import (
    COMPLETED_NO_EMERGED_STRATEGIES,
    COMPLETED_WITH_EMERGED_STRATEGIES,
    INCOMPLETE_CACHE,
    CachedAnalysisRow,
    HistoricalDiscoveryConfig,
    run_historical_discovery,
    write_discovery_report,
)


def _row(index: int, *, label: int = 1) -> CachedAnalysisRow:
    market_date = f"2026-01-{index:02d}"
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
        quote_ready_timestamp_utc=f"{market_date}T20:00:00.250000+00:00",
        execution_timestamp_utc=f"{market_date}T20:00:10+00:00",
        station="KATL",
        market_date=market_date,
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


def _config() -> HistoricalDiscoveryConfig:
    return HistoricalDiscoveryConfig(
        source_start_date="2026-01-01",
        cutoff_exclusive="2026-01-09",
        holdout_dates=2,
        fold_count=3,
        minimum_discovery_dates=3,
        minimum_discovery_trades=3,
        bootstrap_repetitions=50,
    )


def _diagnostics(*, pending: int = 0) -> dict[str, object]:
    return {
        "contract_hash": "contract-1",
        "total_source_mappings": 8,
        "pending_decisions": pending,
        "eligible_analysis_rows": 8,
        "invalid_cached_rows": 0,
        "row_set_hash": "rows-1",
    }


def _manifest() -> dict[str, object]:
    return {
        "decision_contract_hash": "contract-1",
        "sealed_research_watermark": 100,
        "sealed_outcome_watermark": "2026-01-10T00:00:00+00:00",
        "code_hash": "code-1",
    }


def test_cache_grid_is_deterministic_and_collapses_variants_before_holdout(tmp_path: Path) -> None:
    rows = [_row(index) for index in range(1, 9)]

    first = run_historical_discovery(
        rows,
        config=_config(),
        cache_diagnostics=_diagnostics(),
        sealed_manifest=_manifest(),
    )
    second = run_historical_discovery(
        list(reversed(rows)),
        config=_config(),
        cache_diagnostics=_diagnostics(),
        sealed_manifest=_manifest(),
    )

    assert first["status"] == COMPLETED_WITH_EMERGED_STRATEGIES
    assert first["result_content_hash"] == second["result_content_hash"]
    assert first["grid"]["passing_rules"] > first["grid"]["passing_correlated_families"]
    assert first["grid"]["passing_correlated_families"] <= 2
    assert all(
        row["representative_freeze_hash"] == first["grid"]["representative_freeze_hash"]
        for row in first["family_representatives"]
    )

    write_discovery_report(first, tmp_path / "first")
    write_discovery_report(second, tmp_path / "second")
    for name in ("result.json", "report.md", "ranked_rules.csv"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_holdout_labels_cannot_change_frozen_discovery_representatives() -> None:
    discovery = [_row(index) for index in range(1, 7)]
    winning_holdout = [_row(7, label=1), _row(8, label=1)]
    losing_holdout = [_row(7, label=0), _row(8, label=0)]

    won = run_historical_discovery(
        discovery + winning_holdout,
        config=_config(),
        cache_diagnostics=_diagnostics(),
        sealed_manifest=_manifest(),
    )
    lost = run_historical_discovery(
        discovery + losing_holdout,
        config=_config(),
        cache_diagnostics=_diagnostics(),
        sealed_manifest=_manifest(),
    )

    won_frozen = [
        (row["rule_id"], row["family_id"], row["discovery_score_hash"])
        for row in won["family_representatives"]
    ]
    lost_frozen = [
        (row["rule_id"], row["family_id"], row["discovery_score_hash"])
        for row in lost["family_representatives"]
    ]
    assert won_frozen == lost_frozen
    assert won["grid"]["representative_freeze_hash"] == lost["grid"]["representative_freeze_hash"]
    assert won["status"] == COMPLETED_WITH_EMERGED_STRATEGIES
    assert lost["status"] == COMPLETED_NO_EMERGED_STRATEGIES


def test_pending_cache_is_not_reported_as_no_strategy() -> None:
    result = run_historical_discovery(
        [],
        config=_config(),
        cache_diagnostics=_diagnostics(pending=2),
        sealed_manifest=_manifest(),
    )

    assert result["status"] == INCOMPLETE_CACHE
    assert result["funded_authorization"] is False
