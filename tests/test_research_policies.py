from __future__ import annotations

from datetime import date

from weather_trader.execution.contracts import MarketSnapshot, PredictionSnapshot, StrategyBucket, TradeAction
from weather_trader.execution.store import ExecutionStore
from weather_trader.research.policies import POLICIES, ResearchPolicyEvaluator


def test_research_policy_evaluator_records_consensus_and_dedupes(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    store.insert_prediction_snapshot(
        _snapshot(
            model_name="dynamic_bucket_obs_2022_2025",
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_side=TradeAction.BUY_NO,
            selected_bucket="74-75F",
            selected_edge=0.2,
            selected_fair_no=0.85,
            selected_no_ask=0.6,
        )
    )
    store.insert_prediction_snapshot(
        _snapshot(
            model_name="mvp_obs_corrected",
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_side=TradeAction.BUY_NO,
            selected_bucket="74-75F",
            selected_edge=0.3,
            selected_fair_no=0.9,
            selected_no_ask=0.6,
            timestamp="2026-05-07T16:00:02+00:00",
        )
    )

    evaluator = ResearchPolicyEvaluator(store)

    inserted = evaluator.evaluate()
    assert inserted >= 2
    assert evaluator.evaluate() == 0

    positions = store.recent_research_policy_positions(limit=10)
    assert len(positions) == inserted
    names = {position["policy_name"] for position in positions}
    assert "consensus_hc_first" in names
    assert "mvp_hc_first" in names
    consensus = next(position for position in positions if position["policy_name"] == "consensus_hc_first")
    assert consensus["model_group"] == "consensus_dynamic_mvp"
    assert consensus["entry_price"] == 0.6
    assert consensus["entry_edge"] == 0.25
    assert sorted(consensus["source_prediction_snapshot_ids"]) == [1, 2]


def test_research_policy_registry_tracks_expected_policies() -> None:
    names = {policy.name for policy in POLICIES}

    assert {
        "pm_us12_consensus_hc_first",
        "pm_us12_consensus_hc_10m_first",
        "pm_us12_consensus_hc_15m_first",
        "pm_us12_consensus_best_15m_first",
        "pm_us12_consensus_per_strategy_first",
        "pm_us12_mvp_hc_first",
        "pm_us12_mvp_hc_10m_first",
        "pm_us12_mvp_hc_15m_first",
        "pm_us12_mvp_best_15m_first",
        "pm_us12_dynamic_hc_first",
        "pm_us12_dynamic_hc_10m_first",
        "pm_us12_dynamic_hc_15m_first",
        "pm_us12_dynamic_best_15m_first",
        "consensus_hc_first",
        "consensus_hc_10m_first",
        "consensus_hc_15m_first",
        "consensus_best_15m_first",
        "consensus_per_strategy_first",
        "mvp_hc_first",
        "mvp_hc_10m_first",
        "mvp_hc_15m_first",
        "mvp_best_15m_first",
        "dynamic_hc_first",
        "dynamic_hc_10m_first",
        "dynamic_hc_15m_first",
        "dynamic_best_15m_first",
        "max_so_far_first",
        "max_so_far_10m_first",
        "max_so_far_15m_first",
    } <= names


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        market_id="m1",
        condition_id="c1",
        question="Will temp be between 74-75F?",
        slug="slug",
        city="Atlanta",
        station="KATL",
        market_date=date(2026, 5, 7),
        lower_f=74,
        upper_f=75,
        yes_token_id="yes",
        no_token_id="no",
        end_date="",
        resolution_source="",
        discovered_at="now",
    )


def _snapshot(
    *,
    model_name: str,
    strategy_bucket: StrategyBucket,
    selected_side: TradeAction,
    selected_bucket: str,
    selected_edge: float,
    selected_fair_no: float,
    selected_no_ask: float,
    timestamp: str = "2026-05-07T16:00:01+00:00",
) -> PredictionSnapshot:
    return PredictionSnapshot(
        timestamp=timestamp,
        station="KATL",
        market_date=date(2026, 5, 7),
        decision_time_utc="2026-05-07T16:00:00+00:00",
        decision_time_local="2026-05-07T12:00:00-04:00",
        latest_obs_time_utc="2026-05-07T15:45:00+00:00",
        latest_obs_time_local="2026-05-07T11:45:00-04:00",
        obs_age_minutes=15,
        obs_delay_bucket="15m",
        current_temp=72,
        high_so_far=72,
        hrrr_remaining_max=73,
        strategy_bucket=strategy_bucket,
        selected_market_id="m1",
        selected_bucket=selected_bucket,
        selected_side=selected_side,
        selected_edge=selected_edge,
        selected_fair_yes=1.0 - selected_fair_no,
        selected_fair_no=selected_fair_no,
        selected_yes_ask=0.4,
        selected_no_ask=selected_no_ask,
        model_name=model_name,
        high_conviction=strategy_bucket == StrategyBucket.HIGH_CONVICTION,
        skip_reason=None,
        candidate_count=1,
        candidate_distribution=[],
    )
