from __future__ import annotations

import sqlite3

from weather_trader.execution.store import ExecutionStore


def test_execution_store_adds_nullable_liquidity_columns_to_existing_db(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        create table prediction_snapshots (
            id integer primary key autoincrement,
            timestamp text not null,
            station text not null,
            market_date text not null,
            decision_time_utc text not null,
            decision_time_local text not null,
            latest_obs_time_utc text not null,
            latest_obs_time_local text not null,
            obs_age_minutes real not null,
            obs_delay_bucket text not null,
            current_temp real not null,
            high_so_far real not null,
            hrrr_remaining_max real,
            strategy_bucket text not null,
            selected_market_id text,
            selected_bucket text,
            selected_side text not null,
            selected_edge real,
            selected_fair_yes real,
            selected_fair_no real,
            selected_yes_ask real,
            selected_no_ask real,
            high_conviction integer not null,
            skip_reason text,
            candidate_count integer not null,
            model_name text not null default '',
            raw_json text not null,
            unique(station, market_date, latest_obs_time_utc, obs_delay_bucket, strategy_bucket, model_name)
        );
        create table research_policy_positions (
            id integer primary key autoincrement,
            timestamp text not null,
            policy_name text not null,
            station text not null,
            market_date text not null,
            scope_key text not null,
            model_group text not null,
            strategy_bucket text not null,
            obs_delay_bucket text not null,
            selected_market_id text not null,
            selected_side text not null,
            selected_bucket text,
            entry_price real not null,
            entry_edge real,
            entry_fair real,
            source_prediction_snapshot_ids text not null,
            raw_json text not null,
            unique(policy_name, station, market_date, scope_key)
        );
        """
    )
    connection.close()

    store = ExecutionStore(db_path)

    snapshot_columns = _columns(store, "prediction_snapshots")
    policy_columns = _columns(store, "research_policy_positions")
    assert "selected_depth_ask_plus_0_05" in snapshot_columns
    assert "selected_liquidity_json" in snapshot_columns
    assert "hrrr_current_temp" in snapshot_columns
    assert "hrrr_remaining_max_minus_selected_lower" in snapshot_columns
    assert "selected_book_age_seconds" in policy_columns
    assert "selected_liquidity_json" in policy_columns
    assert "hrrr_current_temp" in policy_columns
    assert "hrrr_remaining_max_minus_selected_upper" in policy_columns


def _columns(store: ExecutionStore, table: str) -> set[str]:
    return {str(row["name"]) for row in store.connection.execute(f"pragma table_info({table})").fetchall()}
