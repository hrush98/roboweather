from __future__ import annotations

from datetime import date, datetime, timezone

from scripts import policy_leaderboard
from weather_trader.execution.contracts import (
    PredictionSnapshot,
    ResearchPolicyPosition,
    StationDateOutcome,
    StrategyBucket,
    TradeAction,
)
from weather_trader.execution.store import ExecutionStore


def test_daily_leaderboard_treats_missing_outcomes_as_pending(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.insert_research_policy_position(_position(policy_name="pm_us12_consensus_hc_first"))

    data = policy_leaderboard.compute_leaderboard("2026-05-07")

    assert data["resolved_positions"] == 0
    assert data["pending_positions"] == 1
    assert data["leaderboard"][0]["status"] == "PENDING"


def test_daily_leaderboard_uses_prelim_high_after_local_cutoff(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.insert_research_policy_position(_position(policy_name="pm_us12_consensus_hc_first"))
    store.insert_prediction_snapshot(_snapshot(high_so_far=74.0))

    data = policy_leaderboard.compute_leaderboard(
        "2026-05-07",
        as_of_utc=datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc),
    )

    assert data["resolved_positions"] == 1
    assert data["pending_positions"] == 0
    assert data["leaderboard"][0]["won"] == 1
    assert data["leaderboard"][0]["bets"][0]["final_high"] == 74.0
    assert data["leaderboard"][0]["bets"][0]["outcome_source"] == "prelim_high_so_far"


def test_daily_leaderboard_keeps_prelim_high_pending_before_local_cutoff(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.insert_research_policy_position(_position(policy_name="pm_us12_consensus_hc_first"))
    store.insert_prediction_snapshot(_snapshot(high_so_far=74.0))

    data = policy_leaderboard.compute_leaderboard(
        "2026-05-07",
        as_of_utc=datetime(2026, 5, 7, 22, 0, tzinfo=timezone.utc),
    )

    assert data["resolved_positions"] == 0
    assert data["pending_positions"] == 1
    assert data["leaderboard"][0]["bets"][0]["outcome_source"] is None


def test_opportunity_totals_do_not_exceed_raw_positions(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.insert_research_policy_position(_position(policy_name="pm_us12_consensus_hc_first"))
    store.insert_research_policy_position(_position(policy_name="pm_us12_dynamic_hc_first"))
    store.upsert_station_date_outcome(_outcome())

    data = policy_leaderboard.compute_leaderboard("2026-05-07")

    assert data["total_positions"] == 2
    assert data["unique_opportunities"] == 1
    assert data["duplicate_opportunities"] == 1
    assert all(row["opportunities"] <= row["total"] for row in data["leaderboard"])


def test_calibration_bands_handle_missing_fair_and_edge(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    store.insert_research_policy_position(_position(entry_fair=None, entry_edge=None))
    store.upsert_station_date_outcome(_outcome())

    data = policy_leaderboard.compute_leaderboard("2026-05-07")
    calibration = {(row["type"], row["band"]): row for row in data["calibration"]}

    assert ("fair", "missing") in calibration
    assert ("edge", "missing") in calibration
    assert calibration[("fair", "missing")]["resolved"] == 1


def test_rolling_windows_use_distinct_samples(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    for market_date in [date(2026, 5, 7), date(2026, 5, 6), date(2026, 5, 4)]:
        store.insert_research_policy_position(_position(market_date=market_date, selected_market_id=f"m-{market_date}"))
        store.upsert_station_date_outcome(_outcome(market_date=market_date))

    rolling = policy_leaderboard.compute_rolling_summary("2026-05-07", active_only=False)
    by_window = {row["window"]: row for row in rolling}

    assert by_window["1d"]["resolved"] == 1
    assert by_window["3d"]["resolved"] == 2
    assert by_window["7d"]["resolved"] == 3
    assert by_window["all"]["resolved"] == 3


def _store(tmp_path, monkeypatch) -> ExecutionStore:
    db_path = tmp_path / "research.sqlite"
    monkeypatch.setattr(policy_leaderboard, "DB_PATH", db_path)
    return ExecutionStore(db_path)


def _position(
    *,
    policy_name: str = "pm_us12_consensus_hc_first",
    market_date: date = date(2026, 5, 7),
    selected_market_id: str = "m1",
    entry_fair: float | None = 0.7,
    entry_edge: float | None = 0.2,
) -> ResearchPolicyPosition:
    return ResearchPolicyPosition(
        timestamp=f"{market_date.isoformat()}T16:00:00+00:00",
        policy_name=policy_name,
        station="KATL",
        market_date=market_date,
        scope_key="station_date",
        model_group="test",
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        obs_delay_bucket="15m",
        selected_market_id=selected_market_id,
        selected_side=TradeAction.BUY_YES,
        selected_bucket="74-75F",
        entry_price=0.4,
        entry_edge=entry_edge,
        entry_fair=entry_fair,
        source_prediction_snapshot_ids=[1],
        raw_policy={},
    )


def _outcome(market_date: date = date(2026, 5, 7)) -> StationDateOutcome:
    return StationDateOutcome(
        timestamp=f"{market_date.isoformat()}T23:59:00+00:00",
        station="KATL",
        market_date=market_date,
        final_high_tmpf=74.5,
        source="test",
        resolved_at=f"{market_date.isoformat()}T23:59:00+00:00",
    )


def _snapshot(*, high_so_far: float, market_date: date = date(2026, 5, 7)) -> PredictionSnapshot:
    return PredictionSnapshot(
        timestamp=f"{market_date.isoformat()}T20:00:00+00:00",
        station="KATL",
        market_date=market_date,
        decision_time_utc=f"{market_date.isoformat()}T20:00:00+00:00",
        decision_time_local=f"{market_date.isoformat()}T16:00:00-04:00",
        latest_obs_time_utc=f"{market_date.isoformat()}T20:00:00+00:00",
        latest_obs_time_local=f"{market_date.isoformat()}T16:00:00-04:00",
        obs_age_minutes=0.0,
        obs_delay_bucket="15m",
        current_temp=high_so_far,
        high_so_far=high_so_far,
        hrrr_remaining_max=None,
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        selected_market_id="m1",
        selected_bucket="74-75F",
        selected_side=TradeAction.BUY_YES,
        selected_edge=0.2,
        selected_fair_yes=0.7,
        selected_fair_no=0.3,
        selected_yes_ask=0.4,
        selected_no_ask=0.6,
        model_name="test",
        high_conviction=True,
        skip_reason=None,
        candidate_count=1,
        candidate_distribution=[],
    )
