from __future__ import annotations

from datetime import date

from weather_trader.execution.contracts import MarketSnapshot, PredictionSnapshot, StrategyBucket, TradeAction
from weather_trader.execution.store import ExecutionStore
from weather_trader.research.policies import MVP_MODEL, POLICIES, ResearchPolicyEvaluator, ResearchPolicySpec


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
        "pm_us12_consensus_hc_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_10m_first",
        "pm_us12_consensus_hc_15m_first",
        "pm_us12_consensus_hc_15m_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_15m_entry_00_10_first",
        "pm_us12_consensus_hc_15m_entry_10_25_first",
        "pm_us12_consensus_hc_15m_entry_25_50_first",
        "pm_us12_consensus_hc_15m_entry_50_75_first",
        "pm_us12_consensus_hc_15m_entry_75_100_first",
        "pm_us12_consensus_hc_15m_entry_25_75_first",
        "pm_us12_consensus_hc_15m_entry_25_75_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_15m_entry_50_75_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_15m_no_tiny_first",
        "pm_us12_consensus_hc_late_first",
        "pm_us12_consensus_hc_late_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_late_entry_00_10_first",
        "pm_us12_consensus_hc_late_entry_10_25_first",
        "pm_us12_consensus_hc_late_entry_25_50_first",
        "pm_us12_consensus_hc_late_entry_50_75_first",
        "pm_us12_consensus_hc_late_entry_50_75_by_bucket_side_delay_first",
        "pm_us12_consensus_hc_late_entry_75_100_first",
        "pm_us12_consensus_hc_early_first",
        "pm_us12_consensus_best_15m_first",
        "pm_us12_consensus_per_strategy_first",
        "pm_us12_mvp_hc_first",
        "pm_us12_mvp_hc_10m_first",
        "pm_us12_mvp_hc_15m_first",
        "pm_us12_mvp_best_15m_first",
        "pm_us12_dynamic_hc_first",
        "pm_us12_dynamic_hc_10m_first",
        "pm_us12_dynamic_hc_15m_first",
        "pm_us12_dynamic_hc_15m_entry_00_10_first",
        "pm_us12_dynamic_hc_15m_entry_10_25_first",
        "pm_us12_dynamic_hc_15m_entry_25_50_first",
        "pm_us12_dynamic_hc_15m_entry_50_75_first",
        "pm_us12_dynamic_hc_15m_entry_75_100_first",
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


def test_new_pm_consensus_policies_are_registered_and_idempotent(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for model_name in ["dynamic_bucket_pm_active_us12_obs_2022_2025", MVP_MODEL]:
        store.insert_prediction_snapshot(
            _snapshot(
                model_name=model_name,
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                selected_edge=0.3,
                selected_fair_no=0.9,
                selected_no_ask=0.6,
            )
        )

    evaluator = ResearchPolicyEvaluator(store)

    assert evaluator.evaluate() > 0
    assert evaluator.evaluate() == 0

    names = {position["policy_name"] for position in store.recent_research_policy_positions(limit=100)}
    assert {
        "pm_us12_consensus_hc_15m_entry_25_75_first",
        "pm_us12_consensus_hc_15m_no_tiny_first",
        "pm_us12_consensus_hc_late_first",
    } <= names


def test_entry_band_filter_skips_tiny_then_records_next_candidate(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    store.insert_prediction_snapshot(
        _snapshot(
            model_name=MVP_MODEL,
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_side=TradeAction.BUY_NO,
            selected_bucket="74-75F",
            selected_edge=0.2,
            selected_fair_no=0.85,
            selected_no_ask=0.04,
            timestamp="2026-05-07T15:55:00+00:00",
            latest_obs_time_utc="2026-05-07T15:40:00+00:00",
        )
    )
    store.insert_prediction_snapshot(
        _snapshot(
            model_name=MVP_MODEL,
            strategy_bucket=StrategyBucket.HIGH_CONVICTION,
            selected_side=TradeAction.BUY_NO,
            selected_bucket="74-75F",
            selected_edge=0.3,
            selected_fair_no=0.9,
            selected_no_ask=0.6,
            timestamp="2026-05-07T16:00:00+00:00",
            latest_obs_time_utc="2026-05-07T15:45:00+00:00",
        )
    )
    policy = ResearchPolicySpec(
        "entry_filter",
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=MVP_MODEL,
        entry_price_min=0.05,
        entry_price_max=0.75,
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 1

    positions = store.recent_research_policy_positions(limit=10)
    assert positions[0]["entry_price"] == 0.6
    assert positions[0]["source_prediction_snapshot_ids"] == [2]


def test_station_exclude_filter_suppresses_only_configured_station(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for station, latest_obs in [("KATL", "2026-05-07T15:45:00+00:00"), ("KORD", "2026-05-07T15:46:00+00:00")]:
        store.insert_prediction_snapshot(
            _snapshot(
                model_name=MVP_MODEL,
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                selected_edge=0.2,
                selected_fair_no=0.85,
                selected_no_ask=0.6,
                station=station,
                latest_obs_time_utc=latest_obs,
            )
        )
    policy = ResearchPolicySpec(
        "station_exclude",
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=MVP_MODEL,
        station_exclude_set=frozenset({"KATL"}),
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 1

    positions = store.recent_research_policy_positions(limit=10)
    assert positions[0]["station"] == "KORD"


def test_uniqueness_key_suppresses_duplicate_bucket_side_exposure(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for idx, bucket in enumerate(["74-75F", "74-75F", "76-77F"]):
        store.insert_prediction_snapshot(
            _snapshot(
                model_name=MVP_MODEL,
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                selected_side=TradeAction.BUY_NO,
                selected_bucket=bucket,
                selected_edge=0.2 + idx,
                selected_fair_no=0.85,
                selected_no_ask=0.6,
                timestamp=f"2026-05-07T16:0{idx}:00+00:00",
                latest_obs_time_utc=f"2026-05-07T15:4{idx}:00+00:00",
            )
        )
    policy = ResearchPolicySpec(
        "bucket_side_unique",
        "model",
        StrategyBucket.HIGH_CONVICTION,
        model_name=MVP_MODEL,
        uniqueness_key_mode="station_date_bucket_side",
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 2

    positions = store.recent_research_policy_positions(limit=10)
    assert {position["selected_bucket"] for position in positions} == {"74-75F", "76-77F"}


def test_consensus_by_bucket_side_delay_allows_distinct_trade_ideas(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    rows = [
        ("74-75F", TradeAction.BUY_NO, "2026-05-07T15:40:00+00:00"),
        ("76-77F", TradeAction.BUY_NO, "2026-05-07T15:41:00+00:00"),
        ("74-75F", TradeAction.BUY_YES, "2026-05-07T15:42:00+00:00"),
    ]
    for idx, (bucket, side, latest_obs) in enumerate(rows):
        for model_name in ["dynamic_bucket_pm_active_us12_obs_2022_2025", MVP_MODEL]:
            store.insert_prediction_snapshot(
                _snapshot(
                    model_name=model_name,
                    strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                    selected_side=side,
                    selected_bucket=bucket,
                    selected_edge=0.2 + idx,
                    selected_fair_no=0.85,
                    selected_no_ask=0.6,
                    timestamp=f"2026-05-07T16:0{idx}:00+00:00",
                    latest_obs_time_utc=latest_obs,
                )
            )
    policy = ResearchPolicySpec(
        "consensus_by_bucket_side_delay",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 3

    positions = store.recent_research_policy_positions(limit=10)
    assert {(position["selected_bucket"], position["selected_side"]) for position in positions} == {
        ("74-75F", "BUY_NO"),
        ("76-77F", "BUY_NO"),
        ("74-75F", "BUY_YES"),
    }
    assert all(position["scope_key"].endswith(":15m") for position in positions)


def test_consensus_by_bucket_side_delay_dedupes_repeated_loop_snapshots(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for idx, latest_obs in enumerate(["2026-05-07T15:40:00+00:00", "2026-05-07T15:43:00+00:00"]):
        for model_name in ["dynamic_bucket_pm_active_us12_obs_2022_2025", MVP_MODEL]:
            store.insert_prediction_snapshot(
                _snapshot(
                    model_name=model_name,
                    strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                    selected_side=TradeAction.BUY_NO,
                    selected_bucket="74-75F",
                    selected_edge=0.2 + idx,
                    selected_fair_no=0.85,
                    selected_no_ask=0.6,
                    timestamp=f"2026-05-07T16:0{idx}:00+00:00",
                    latest_obs_time_utc=latest_obs,
                )
            )
    policy = ResearchPolicySpec(
        "consensus_by_bucket_side_delay",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 1

    positions = store.recent_research_policy_positions(limit=10)
    assert positions[0]["selected_bucket"] == "74-75F"
    assert positions[0]["selected_side"] == "BUY_NO"


def test_consensus_station_date_first_still_keeps_one_row_per_station_date(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for idx, bucket in enumerate(["74-75F", "76-77F"]):
        for model_name in ["dynamic_bucket_pm_active_us12_obs_2022_2025", MVP_MODEL]:
            store.insert_prediction_snapshot(
                _snapshot(
                    model_name=model_name,
                    strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                    selected_side=TradeAction.BUY_NO,
                    selected_bucket=bucket,
                    selected_edge=0.2 + idx,
                    selected_fair_no=0.85,
                    selected_no_ask=0.6,
                    timestamp=f"2026-05-07T16:0{idx}:00+00:00",
                    latest_obs_time_utc=f"2026-05-07T15:4{idx}:00+00:00",
                )
            )
    policy = ResearchPolicySpec(
        "consensus_station_date_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
    )

    assert ResearchPolicyEvaluator(store, (policy,)).evaluate() == 1

    positions = store.recent_research_policy_positions(limit=10)
    assert positions[0]["scope_key"] == "station_date"
    assert positions[0]["selected_bucket"] == "74-75F"


def test_local_decision_time_filters_split_early_and_late(tmp_path) -> None:
    store = ExecutionStore(tmp_path / "research.sqlite")
    store.upsert_market(_market())
    for idx, (timestamp, local_time) in enumerate(
        [
            ("2026-05-07T15:00:00+00:00", "2026-05-07T11:00:00-04:00"),
            ("2026-05-07T17:00:00+00:00", "2026-05-07T13:00:00-04:00"),
        ]
    ):
        store.insert_prediction_snapshot(
            _snapshot(
                model_name=MVP_MODEL,
                strategy_bucket=StrategyBucket.HIGH_CONVICTION,
                selected_side=TradeAction.BUY_NO,
                selected_bucket="74-75F",
                selected_edge=0.2,
                selected_fair_no=0.85,
                selected_no_ask=0.6,
                timestamp=timestamp,
                decision_time_local=local_time,
                latest_obs_time_utc=f"2026-05-07T15:4{idx}:00+00:00",
            )
        )
    policies = (
        ResearchPolicySpec(
            "early",
            "model",
            StrategyBucket.HIGH_CONVICTION,
            model_name=MVP_MODEL,
            local_decision_start="10:00",
            local_decision_end="12:00",
        ),
        ResearchPolicySpec(
            "late",
            "model",
            StrategyBucket.HIGH_CONVICTION,
            model_name=MVP_MODEL,
            local_decision_start="12:00",
            local_decision_end="15:00",
        ),
    )

    assert ResearchPolicyEvaluator(store, policies).evaluate() == 2

    by_policy = {position["policy_name"]: position for position in store.recent_research_policy_positions(limit=10)}
    assert by_policy["early"]["timestamp"] == "2026-05-07T15:00:00+00:00"
    assert by_policy["late"]["timestamp"] == "2026-05-07T17:00:00+00:00"


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
    station: str = "KATL",
    timestamp: str = "2026-05-07T16:00:01+00:00",
    decision_time_local: str = "2026-05-07T12:00:00-04:00",
    latest_obs_time_utc: str = "2026-05-07T15:45:00+00:00",
) -> PredictionSnapshot:
    return PredictionSnapshot(
        timestamp=timestamp,
        station=station,
        market_date=date(2026, 5, 7),
        decision_time_utc="2026-05-07T16:00:00+00:00",
        decision_time_local=decision_time_local,
        latest_obs_time_utc=latest_obs_time_utc,
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
