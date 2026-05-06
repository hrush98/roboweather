from __future__ import annotations

from datetime import date, datetime, time, timezone

from weather_trader.execution.contracts import StationDateOutcome, TradeAction
from weather_trader.execution.weather import StationWeatherState
from weather_trader.research.collector import ResearchConfig, due_delay_buckets
from weather_trader.research.resolver import score_snapshot


def test_due_delay_buckets_use_actual_obs_age_bucket() -> None:
    config = ResearchConfig(
        entry_start_local=time(10, 0),
        entry_end_local=time(15, 0),
        delay_minutes=(0, 5, 10, 15, 30),
        instant_max_age_minutes=3,
        max_obs_age_minutes=30,
    )

    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 2, tzinfo=timezone.utc), config) == ["instant"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 6, tzinfo=timezone.utc), config) == ["5m"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 12, tzinfo=timezone.utc), config) == ["10m"]
    assert due_delay_buckets(_weather("2026-05-06T16:00:00+00:00"), datetime(2026, 5, 6, 16, 31, tzinfo=timezone.utc), config) == []


def test_due_delay_buckets_require_obs_inside_entry_window() -> None:
    config = ResearchConfig(entry_start_local=time(10, 0), entry_end_local=time(15, 0))

    assert due_delay_buckets(_weather("2026-05-06T13:30:00+00:00"), datetime(2026, 5, 6, 13, 36, tzinfo=timezone.utc), config) == []


def test_score_snapshot_scores_selected_side_against_final_high() -> None:
    snapshot = {
        "id": 10,
        "obs_delay_bucket": "5m",
        "selected_market_id": "m1",
        "selected_bucket": "80-81F",
        "selected_side": "BUY_YES",
        "selected_yes_ask": 0.4,
        "selected_no_ask": 0.7,
        "selected_edge": 0.2,
        "decision_time_local": "2026-05-06T12:05:00-04:00",
        "obs_age_minutes": 5.0,
    }
    outcome = StationDateOutcome(
        timestamp="now",
        station="KATL",
        market_date=date(2026, 5, 6),
        final_high_tmpf=81.0,
        source="IEM_ASOS",
        resolved_at="later",
    )

    result = score_snapshot(snapshot, outcome)

    assert result.winning_side == TradeAction.BUY_YES
    assert result.correct is True
    assert result.entry_price == 0.4
    assert result.paper_pnl == 0.6


def _weather(latest_obs_time: str) -> StationWeatherState:
    return StationWeatherState(
        station="KATL",
        local_date=date(2026, 5, 6),
        latest_obs_time=latest_obs_time,
        latest_obs_age_minutes=0,
        current_temp=75,
        high_so_far=80,
        hour_local=12,
        day_of_year=126,
        temp_change_1h=0,
        temp_change_3h=0,
        dewpoint=60,
        wind_speed=5,
        wind_dir_sin=0,
        wind_dir_cos=1,
        cloud_cover_code=0,
        hrrr_current_temp=None,
        hrrr_remaining_max=None,
        stale=False,
    )
