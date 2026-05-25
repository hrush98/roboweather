from __future__ import annotations

import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any

from weather_trader.execution.contracts import (
    BookSnapshot,
    Decision,
    EngineState,
    LiveOrderAttempt,
    LivePolicyPosition,
    LivePositionState,
    LiveRiskSnapshot,
    LiveStrategy,
    LiveTradeEvent,
    MarketSnapshot,
    PaperOrder,
    PaperPolicyFinalState,
    PaperPolicyOrderAttempt,
    PaperPolicyPosition,
    PaperPolicyRiskSnapshot,
    PaperPolicyTradeEvent,
    PredictionResult,
    PredictionSnapshot,
    Position,
    PositionMark,
    Resolution,
    ResearchPolicyPosition,
    RiskState,
    Signal,
    StationDateOutcome,
    StationDateDecisionTrace,
    dataclass_to_jsonable,
)


HRRR_POLICY_CONTEXT_COLUMNS: dict[str, str] = {
    "hrrr_current_temp": "real",
    "hrrr_current_temp_minus_current_temp": "real",
    "hrrr_remaining_max_minus_selected_lower": "real",
    "hrrr_remaining_max_minus_selected_upper": "real",
    "hrrr_remaining_min_minus_selected_lower": "real",
    "hrrr_remaining_min_minus_selected_upper": "real",
    "hrrr_temp_next_3h_max": "real",
    "hrrr_temp_next_3h_mean": "real",
    "hrrr_remaining_min": "real",
    "hrrr_wind_speed_current": "real",
    "hrrr_wind_speed_next_3h_mean": "real",
    "hrrr_wind_speed_remaining_max": "real",
    "hrrr_gust_remaining_max": "real",
    "hrrr_cloud_cover_current": "real",
    "hrrr_cloud_cover_next_3h_mean": "real",
    "hrrr_cloud_cover_remaining_mean": "real",
    "hrrr_cloud_cover_remaining_max": "real",
    "hrrr_rh_current": "real",
    "hrrr_rh_next_3h_mean": "real",
    "hrrr_rh_remaining_mean": "real",
    "hrrr_shortwave_next_3h_mean": "real",
    "hrrr_shortwave_remaining_max": "real",
}
HRRR_POLICY_POSITION_COLUMNS: dict[str, str] = {
    "hrrr_remaining_max": "real",
    **HRRR_POLICY_CONTEXT_COLUMNS,
}


def _date_text(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class ExecutionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            create table if not exists markets (
                market_id text primary key,
                condition_id text,
                question text not null,
                slug text not null,
                city text not null,
                station text not null,
                market_date text,
                lower_f real,
                upper_f real,
                yes_token_id text,
                no_token_id text,
                end_date text,
                resolution_source text,
                discovered_at text not null,
                active integer not null,
                market_family text not null default 'HIGH_TEMP',
                raw_json text not null
            );

            create table if not exists book_snapshots (
                id integer primary key autoincrement,
                token_id text not null,
                timestamp text not null,
                source text not null,
                best_bid real,
                best_ask real,
                raw_json text not null
            );

            create table if not exists signals (
                id integer primary key autoincrement,
                timestamp text not null,
                market_id text not null,
                station text not null,
                fair_yes real not null,
                fair_no real not null,
                signal_side text not null,
                edge_yes real,
                edge_no real,
                raw_json text not null
            );

            create table if not exists decisions (
                id integer primary key autoincrement,
                timestamp text not null,
                market_id text not null,
                token_id text,
                action text not null,
                strategy_bucket text not null,
                target_usd real not null,
                raw_json text not null
            );

            create table if not exists paper_orders (
                order_id text primary key,
                timestamp text not null,
                market_id text not null,
                token_id text not null,
                action text not null,
                state text not null,
                cost real not null,
                raw_json text not null
            );

            create table if not exists positions (
                position_id text primary key,
                market_id text not null,
                token_id text not null,
                side text not null,
                station text not null,
                market_date text,
                cost real not null,
                state text not null,
                raw_json text not null
            );

            create table if not exists position_marks (
                id integer primary key autoincrement,
                timestamp text not null,
                position_id text not null,
                market_id text not null,
                token_id text not null,
                side text not null,
                station text not null,
                current_bid real,
                mark_value real,
                unrealized_pnl real,
                unrealized_pnl_pct real,
                effective_status text not null,
                raw_json text not null
            );

            create table if not exists station_date_decisions (
                id integer primary key autoincrement,
                timestamp text not null,
                station text not null,
                market_date text,
                candidate_count integer not null,
                selected_market_id text,
                selected_action text not null,
                selected_strategy_bucket text not null,
                selected_edge real,
                selected_score real,
                skip_reason text,
                raw_json text not null
            );

            create table if not exists resolutions (
                market_id text primary key,
                station text not null,
                market_date text not null,
                final_high real not null,
                winning_side text not null,
                source text not null,
                resolved_at text not null,
                raw_json text not null
            );

            create table if not exists risk_state (
                id integer primary key autoincrement,
                timestamp text not null,
                bankroll_usd real not null,
                open_positions integer not null,
                portfolio_exposure_usd real not null,
                kill_switch_active integer not null,
                raw_json text not null
            );

            create table if not exists engine_state (
                id integer primary key autoincrement,
                timestamp text not null,
                mode text not null,
                discovered_markets integer not null,
                actionable_signals integer not null,
                orders_submitted integer not null,
                skipped integer not null,
                raw_json text not null
            );

            create table if not exists prediction_snapshots (
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
                hrrr_current_temp real,
                hrrr_current_temp_minus_current_temp real,
                hrrr_remaining_max_minus_selected_lower real,
                hrrr_remaining_max_minus_selected_upper real,
                hrrr_remaining_min_minus_selected_lower real,
                hrrr_remaining_min_minus_selected_upper real,
                hrrr_temp_next_3h_max real,
                hrrr_temp_next_3h_mean real,
                hrrr_remaining_min real,
                hrrr_wind_speed_current real,
                hrrr_wind_speed_next_3h_mean real,
                hrrr_wind_speed_remaining_max real,
                hrrr_gust_remaining_max real,
                hrrr_cloud_cover_current real,
                hrrr_cloud_cover_next_3h_mean real,
                hrrr_cloud_cover_remaining_mean real,
                hrrr_cloud_cover_remaining_max real,
                hrrr_rh_current real,
                hrrr_rh_next_3h_mean real,
                hrrr_rh_remaining_mean real,
                hrrr_shortwave_next_3h_mean real,
                hrrr_shortwave_remaining_max real,
                strategy_bucket text not null,
                selected_market_id text,
                selected_bucket text,
                selected_side text not null,
                selected_edge real,
                selected_fair_yes real,
                selected_fair_no real,
                selected_yes_ask real,
                selected_no_ask real,
                selected_best_bid real,
                selected_best_ask real,
                selected_spread real,
                selected_depth_at_ask real,
                selected_depth_ask_plus_0_01 real,
                selected_depth_ask_plus_0_03 real,
                selected_depth_ask_plus_0_05 real,
                selected_book_timestamp text,
                selected_book_age_seconds real,
                selected_liquidity_json text,
                selected_ask_sweep_json text,
                selected_bid_ladder_json text,
                selected_sweep_price_cap real,
                selected_sweep_depth_to_cap real,
                selected_sweep_fillable_25_usd real,
                selected_sweep_fillable_50_usd real,
                selected_sweep_fillable_100_usd real,
                selected_sweep_vwap_25 real,
                selected_sweep_vwap_50 real,
                selected_sweep_vwap_100 real,
                selected_bid_ladder_top_price real,
                selected_bid_ladder_low_price real,
                selected_bid_ladder_levels integer,
                selected_bid_ladder_total_notional_usd real,
                selected_bid_ladder_top_distance_from_ask real,
                selected_bid_ladder_top_improvement_over_best_bid real,
                selected_bid_ladder_min_edge real,
                selected_bid_ladder_max_edge real,
                high_conviction integer not null,
                skip_reason text,
                candidate_count integer not null,
                model_name text not null default '',
                market_family text not null default 'HIGH_TEMP',
                low_so_far real,
                raw_json text not null,
                unique(station, market_date, market_family, latest_obs_time_utc, obs_delay_bucket, strategy_bucket, model_name)
            );

            create table if not exists station_date_outcomes (
                station text not null,
                market_date text not null,
                timestamp text not null,
                final_high_tmpf real not null,
                final_low_tmpf real,
                source text not null,
                resolved_at text not null,
                raw_json text not null,
                primary key(station, market_date)
            );

            create table if not exists prediction_results (
                prediction_snapshot_id integer primary key,
                timestamp text not null,
                station text not null,
                market_date text not null,
                obs_delay_bucket text not null,
                selected_market_id text,
                selected_bucket text,
                selected_side text not null,
                final_high_tmpf real not null,
                final_low_tmpf real,
                market_family text not null default 'HIGH_TEMP',
                winning_side text,
                correct integer,
                entry_price real,
                paper_pnl real,
                edge real,
                decision_time_local text not null,
                obs_age_minutes real not null,
                resolved_at text not null,
                raw_json text not null
            );

            create table if not exists research_policy_positions (
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
                hrrr_remaining_max real,
                hrrr_current_temp real,
                hrrr_current_temp_minus_current_temp real,
                hrrr_remaining_max_minus_selected_lower real,
                hrrr_remaining_max_minus_selected_upper real,
                hrrr_remaining_min_minus_selected_lower real,
                hrrr_remaining_min_minus_selected_upper real,
                hrrr_temp_next_3h_max real,
                hrrr_temp_next_3h_mean real,
                hrrr_remaining_min real,
                hrrr_wind_speed_current real,
                hrrr_wind_speed_next_3h_mean real,
                hrrr_wind_speed_remaining_max real,
                hrrr_gust_remaining_max real,
                hrrr_cloud_cover_current real,
                hrrr_cloud_cover_next_3h_mean real,
                hrrr_cloud_cover_remaining_mean real,
                hrrr_cloud_cover_remaining_max real,
                hrrr_rh_current real,
                hrrr_rh_next_3h_mean real,
                hrrr_rh_remaining_mean real,
                hrrr_shortwave_next_3h_mean real,
                hrrr_shortwave_remaining_max real,
                selected_best_bid real,
                selected_best_ask real,
                selected_spread real,
                selected_depth_at_ask real,
                selected_depth_ask_plus_0_01 real,
                selected_depth_ask_plus_0_03 real,
                selected_depth_ask_plus_0_05 real,
                selected_book_timestamp text,
                selected_book_age_seconds real,
                selected_liquidity_json text,
                selected_ask_sweep_json text,
                selected_bid_ladder_json text,
                selected_sweep_price_cap real,
                selected_sweep_depth_to_cap real,
                selected_sweep_fillable_25_usd real,
                selected_sweep_fillable_50_usd real,
                selected_sweep_fillable_100_usd real,
                selected_sweep_vwap_25 real,
                selected_sweep_vwap_50 real,
                selected_sweep_vwap_100 real,
                selected_bid_ladder_top_price real,
                selected_bid_ladder_low_price real,
                selected_bid_ladder_levels integer,
                selected_bid_ladder_total_notional_usd real,
                selected_bid_ladder_top_distance_from_ask real,
                selected_bid_ladder_top_improvement_over_best_bid real,
                selected_bid_ladder_min_edge real,
                selected_bid_ladder_max_edge real,
                market_family text not null default 'HIGH_TEMP',
                source_prediction_snapshot_ids text not null,
                raw_json text not null,
                unique(policy_name, station, market_date, market_family, scope_key)
            );

            create table if not exists paper_policy_positions (
                id integer primary key autoincrement,
                timestamp text not null,
                research_policy_position_id integer not null,
                policy_name text not null,
                station text not null,
                market_date text not null,
                selected_market_id text not null,
                selected_token_id text not null,
                selected_side text not null,
                selected_bucket text,
                entry_limit_price real not null,
                target_notional_usd real not null,
                filled_shares real not null default 0,
                avg_entry_price real,
                cost_usd real not null default 0,
                state text not null,
                realized_pnl real,
                realized_rr real,
                mark_value real,
                unrealized_pnl real,
                raw_json text not null,
                unique(research_policy_position_id)
            );

            create table if not exists paper_policy_order_attempts (
                id integer primary key autoincrement,
                timestamp text not null,
                paper_position_id integer not null,
                research_policy_position_id integer not null,
                attempt_seq integer not null,
                token_id text not null,
                side text not null,
                order_mode text not null,
                limit_price real not null,
                target_notional_usd real not null,
                external_order_id text,
                external_status text,
                not_found_count integer not null,
                final_state text not null,
                final_reason text not null,
                filled_shares real not null,
                avg_price real,
                cost_usd real not null,
                levels_consumed text not null,
                raw_payload text not null,
                unique(paper_position_id, attempt_seq)
            );

            create table if not exists paper_policy_trade_events (
                id integer primary key autoincrement,
                timestamp text not null,
                paper_position_id integer,
                research_policy_position_id integer,
                event_type text not null,
                message text not null,
                raw_payload text not null
            );

            create table if not exists paper_policy_risk_snapshots (
                id integer primary key autoincrement,
                timestamp text not null,
                bankroll_usd real not null,
                open_positions integer not null,
                open_risk_usd real not null,
                station_date_exposure_usd text not null,
                raw_payload text not null
            );

            create table if not exists live_strategies (
                name text primary key,
                active integer not null,
                source text not null,
                model_group text not null,
                model_names text not null,
                strategy_bucket text not null,
                market_family text not null,
                local_decision_start text not null,
                local_decision_end text not null,
                entry_price_min real not null,
                uniqueness_key_mode text not null,
                max_notional_usd real not null,
                raw_json text not null
            );

            create table if not exists live_policy_positions (
                id integer primary key autoincrement,
                timestamp text not null,
                strategy_name text not null,
                station text not null,
                market_date text not null,
                market_family text not null,
                scope_key text not null,
                selected_market_id text not null,
                selected_token_id text not null,
                selected_side text not null,
                selected_bucket text,
                obs_delay_bucket text not null,
                entry_price real not null,
                entry_fair real,
                entry_edge real,
                target_notional_usd real not null,
                target_shares real not null,
                filled_shares real not null default 0,
                avg_entry_price real,
                cost_usd real not null default 0,
                mark_value real,
                unrealized_pnl real,
                state text not null,
                resolved_at text,
                resolution_source text,
                winning_token_id text,
                winning_side text,
                settlement_value_usd real,
                realized_pnl real,
                realized_rr real,
                source_prediction_snapshot_ids text not null,
                raw_json text not null,
                unique(strategy_name, station, market_date, market_family, scope_key)
            );

            create table if not exists live_order_attempts (
                id integer primary key autoincrement,
                timestamp text not null,
                live_position_id integer not null,
                attempt_seq integer not null,
                token_id text not null,
                side text not null,
                order_mode text not null,
                limit_price real not null,
                target_notional_usd real not null,
                target_shares real not null,
                external_order_id text,
                external_status text,
                final_state text not null,
                final_reason text not null,
                filled_shares real not null,
                avg_price real,
                cost_usd real not null,
                raw_payload text not null,
                unique(live_position_id, attempt_seq)
            );

            create table if not exists live_trade_events (
                id integer primary key autoincrement,
                timestamp text not null,
                live_position_id integer,
                strategy_name text,
                event_type text not null,
                message text not null,
                raw_payload text not null
            );

            create table if not exists live_risk_snapshots (
                id integer primary key autoincrement,
                timestamp text not null,
                open_positions integer not null,
                open_risk_usd real not null,
                station_date_exposure_usd text not null,
                raw_payload text not null
            );

            create table if not exists hermes_insights (
                id integer primary key autoincrement,
                created_at text not null,
                insight_type text not null,
                target_date text,
                severity text not null default 'info',
                title text not null,
                body text not null,
                metrics_json text,
                raw_json text not null
            );
            """
        )
        self._migrate_prediction_snapshots_schema()
        self._add_nullable_columns(
            "prediction_snapshots",
            {
                **HRRR_POLICY_CONTEXT_COLUMNS,
                "selected_best_bid": "real",
                "selected_best_ask": "real",
                "selected_spread": "real",
                "selected_depth_at_ask": "real",
                "selected_depth_ask_plus_0_01": "real",
                "selected_depth_ask_plus_0_03": "real",
                "selected_depth_ask_plus_0_05": "real",
                "selected_book_timestamp": "text",
                "selected_book_age_seconds": "real",
                "selected_liquidity_json": "text",
                "selected_ask_sweep_json": "text",
                "selected_bid_ladder_json": "text",
                "selected_sweep_price_cap": "real",
                "selected_sweep_depth_to_cap": "real",
                "selected_sweep_fillable_25_usd": "real",
                "selected_sweep_fillable_50_usd": "real",
                "selected_sweep_fillable_100_usd": "real",
                "selected_sweep_vwap_25": "real",
                "selected_sweep_vwap_50": "real",
                "selected_sweep_vwap_100": "real",
                "selected_bid_ladder_top_price": "real",
                "selected_bid_ladder_low_price": "real",
                "selected_bid_ladder_levels": "integer",
                "selected_bid_ladder_total_notional_usd": "real",
                "selected_bid_ladder_top_distance_from_ask": "real",
                "selected_bid_ladder_top_improvement_over_best_bid": "real",
                "selected_bid_ladder_min_edge": "real",
                "selected_bid_ladder_max_edge": "real",
                "market_family": "text not null default 'HIGH_TEMP'",
                "low_so_far": "real",
            },
        )
        self._add_nullable_columns(
            "research_policy_positions",
            {
                **HRRR_POLICY_POSITION_COLUMNS,
                "selected_best_bid": "real",
                "selected_best_ask": "real",
                "selected_spread": "real",
                "selected_depth_at_ask": "real",
                "selected_depth_ask_plus_0_01": "real",
                "selected_depth_ask_plus_0_03": "real",
                "selected_depth_ask_plus_0_05": "real",
                "selected_book_timestamp": "text",
                "selected_book_age_seconds": "real",
                "selected_liquidity_json": "text",
                "selected_ask_sweep_json": "text",
                "selected_bid_ladder_json": "text",
                "selected_sweep_price_cap": "real",
                "selected_sweep_depth_to_cap": "real",
                "selected_sweep_fillable_25_usd": "real",
                "selected_sweep_fillable_50_usd": "real",
                "selected_sweep_fillable_100_usd": "real",
                "selected_sweep_vwap_25": "real",
                "selected_sweep_vwap_50": "real",
                "selected_sweep_vwap_100": "real",
                "selected_bid_ladder_top_price": "real",
                "selected_bid_ladder_low_price": "real",
                "selected_bid_ladder_levels": "integer",
                "selected_bid_ladder_total_notional_usd": "real",
                "selected_bid_ladder_top_distance_from_ask": "real",
                "selected_bid_ladder_top_improvement_over_best_bid": "real",
                "selected_bid_ladder_min_edge": "real",
                "selected_bid_ladder_max_edge": "real",
                "market_family": "text not null default 'HIGH_TEMP'",
            },
        )
        self._add_nullable_columns("markets", {"market_family": "text not null default 'HIGH_TEMP'"})
        self._add_nullable_columns(
            "live_policy_positions",
            {
                "resolved_at": "text",
                "resolution_source": "text",
                "winning_token_id": "text",
                "winning_side": "text",
                "settlement_value_usd": "real",
                "realized_pnl": "real",
                "realized_rr": "real",
            },
        )
        self._add_nullable_columns("station_date_outcomes", {"final_low_tmpf": "real"})
        self._add_nullable_columns("prediction_results", {"market_family": "text not null default 'HIGH_TEMP'", "final_low_tmpf": "real"})
        self.connection.commit()

    def _add_nullable_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            str(item["name"])
            for item in self.connection.execute(f"pragma table_info({table})").fetchall()
        }
        for name, column_type in columns.items():
            if name in existing:
                continue
            self.connection.execute(f"alter table {table} add column {name} {column_type}")

    def _migrate_prediction_snapshots_schema(self) -> None:
        row = self.connection.execute(
            "select sql from sqlite_master where type = 'table' and name = 'prediction_snapshots'"
        ).fetchone()
        if row is None:
            return
        schema_sql = str(row["sql"] or "")
        if "strategy_bucket text not null" in schema_sql and "model_name" in schema_sql:
            return

        columns = {
            str(item["name"])
            for item in self.connection.execute("pragma table_info(prediction_snapshots)").fetchall()
        }
        strategy_expr = "strategy_bucket" if "strategy_bucket" in columns else "'LEGACY'"
        self.connection.executescript(
            """
            alter table prediction_snapshots rename to prediction_snapshots_old;

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
                hrrr_current_temp real,
                hrrr_current_temp_minus_current_temp real,
                hrrr_remaining_max_minus_selected_lower real,
                hrrr_remaining_max_minus_selected_upper real,
                hrrr_remaining_min_minus_selected_lower real,
                hrrr_remaining_min_minus_selected_upper real,
                hrrr_temp_next_3h_max real,
                hrrr_temp_next_3h_mean real,
                hrrr_remaining_min real,
                hrrr_wind_speed_current real,
                hrrr_wind_speed_next_3h_mean real,
                hrrr_wind_speed_remaining_max real,
                hrrr_gust_remaining_max real,
                hrrr_cloud_cover_current real,
                hrrr_cloud_cover_next_3h_mean real,
                hrrr_cloud_cover_remaining_mean real,
                hrrr_cloud_cover_remaining_max real,
                hrrr_rh_current real,
                hrrr_rh_next_3h_mean real,
                hrrr_rh_remaining_mean real,
                hrrr_shortwave_next_3h_mean real,
                hrrr_shortwave_remaining_max real,
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
                market_family text not null default 'HIGH_TEMP',
                low_so_far real,
                raw_json text not null,
                unique(station, market_date, market_family, latest_obs_time_utc, obs_delay_bucket, strategy_bucket, model_name)
            );
            """
        )
        self.connection.execute(
            f"""
            insert or ignore into prediction_snapshots (
                id, timestamp, station, market_date, decision_time_utc, decision_time_local,
                latest_obs_time_utc, latest_obs_time_local, obs_age_minutes,
                obs_delay_bucket, current_temp, high_so_far, hrrr_remaining_max,
                strategy_bucket, selected_market_id, selected_bucket, selected_side, selected_edge,
                selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask,
                high_conviction, skip_reason, candidate_count, model_name, market_family, low_so_far, raw_json
            )
            select
                id, timestamp, station, market_date, decision_time_utc, decision_time_local,
                latest_obs_time_utc, latest_obs_time_local, obs_age_minutes,
                obs_delay_bucket, current_temp, high_so_far, hrrr_remaining_max,
                {strategy_expr}, selected_market_id, selected_bucket, selected_side, selected_edge,
                selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask,
                high_conviction, skip_reason, candidate_count, '', 'HIGH_TEMP', null, raw_json
            from prediction_snapshots_old
            """
        )
        self.connection.execute("drop table prediction_snapshots_old")

    def upsert_market(self, market: MarketSnapshot) -> None:
        data = dataclass_to_jsonable(market)
        self.connection.execute(
            """
            insert into markets (
                market_id, condition_id, question, slug, city, station, market_date,
                lower_f, upper_f, yes_token_id, no_token_id, end_date,
                resolution_source, discovered_at, active, market_family, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(market_id) do update set
                condition_id=excluded.condition_id,
                question=excluded.question,
                slug=excluded.slug,
                city=excluded.city,
                station=excluded.station,
                market_date=excluded.market_date,
                lower_f=excluded.lower_f,
                upper_f=excluded.upper_f,
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                end_date=excluded.end_date,
                resolution_source=excluded.resolution_source,
                discovered_at=excluded.discovered_at,
                active=excluded.active,
                market_family=excluded.market_family,
                raw_json=excluded.raw_json
            """,
            (
                market.market_id,
                market.condition_id,
                market.question,
                market.slug,
                market.city,
                market.station,
                data["market_date"],
                market.lower_f,
                market.upper_f,
                market.yes_token_id,
                market.no_token_id,
                market.end_date,
                market.resolution_source,
                market.discovered_at,
                int(market.active),
                str(market.market_family),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_book_snapshot(self, book: BookSnapshot) -> None:
        data = dataclass_to_jsonable(book)
        self.connection.execute(
            """
            insert into book_snapshots (token_id, timestamp, source, best_bid, best_ask, raw_json)
            values (?, ?, ?, ?, ?, ?)
            """,
            (book.token_id, book.timestamp, book.source, book.best_bid, book.best_ask, json.dumps(data, sort_keys=True)),
        )
        self.connection.commit()

    def insert_signal(self, signal: Signal) -> int:
        data = dataclass_to_jsonable(signal)
        cursor = self.connection.execute(
            """
            insert into signals (timestamp, market_id, station, fair_yes, fair_no, signal_side, edge_yes, edge_no, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.timestamp,
                signal.market_id,
                signal.station,
                signal.fair_yes,
                signal.fair_no,
                str(signal.signal_side),
                signal.edge_yes,
                signal.edge_no,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_decision(self, decision: Decision) -> int:
        data = dataclass_to_jsonable(decision)
        cursor = self.connection.execute(
            """
            insert into decisions (timestamp, market_id, token_id, action, strategy_bucket, target_usd, raw_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.timestamp,
                decision.market_id,
                decision.token_id,
                str(decision.action),
                str(decision.strategy_bucket),
                decision.target_usd,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_paper_order(self, order: PaperOrder) -> None:
        data = dataclass_to_jsonable(order)
        self.connection.execute(
            """
            insert or replace into paper_orders (order_id, timestamp, market_id, token_id, action, state, cost, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.timestamp,
                order.market_id,
                order.token_id,
                str(order.action),
                str(order.state),
                order.cost,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def upsert_position(self, position: Position) -> None:
        data = dataclass_to_jsonable(position)
        self.connection.execute(
            """
            insert into positions (position_id, market_id, token_id, side, station, market_date, cost, state, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(position_id) do update set
                cost=excluded.cost,
                state=excluded.state,
                raw_json=excluded.raw_json
            """,
            (
                position.position_id,
                position.market_id,
                position.token_id,
                str(position.side),
                position.station,
                data["market_date"],
                position.cost,
                position.state,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_position_mark(self, mark: PositionMark) -> None:
        data = dataclass_to_jsonable(mark)
        self.connection.execute(
            """
            insert into position_marks (
                timestamp, position_id, market_id, token_id, side, station,
                current_bid, mark_value, unrealized_pnl, unrealized_pnl_pct,
                effective_status, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mark.timestamp,
                mark.position_id,
                mark.market_id,
                mark.token_id,
                str(mark.side),
                mark.station,
                mark.current_bid,
                mark.mark_value,
                mark.unrealized_pnl,
                mark.unrealized_pnl_pct,
                str(mark.effective_status),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_station_date_decision(self, trace: StationDateDecisionTrace) -> None:
        data = dataclass_to_jsonable(trace)
        self.connection.execute(
            """
            insert into station_date_decisions (
                timestamp, station, market_date, candidate_count, selected_market_id,
                selected_action, selected_strategy_bucket, selected_edge, selected_score,
                skip_reason, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.timestamp,
                trace.station,
                data["market_date"],
                trace.candidate_count,
                trace.selected_market_id,
                str(trace.selected_action),
                str(trace.selected_strategy_bucket),
                trace.selected_edge,
                trace.selected_score,
                trace.skip_reason,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_resolution(self, resolution: Resolution) -> None:
        data = dataclass_to_jsonable(resolution)
        self.connection.execute(
            """
            insert or replace into resolutions (
                market_id, station, market_date, final_high, winning_side, source, resolved_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution.market_id,
                resolution.station,
                data["market_date"],
                resolution.final_high,
                str(resolution.winning_side),
                resolution.source,
                resolution.resolved_at,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_risk_state(self, risk_state: RiskState) -> None:
        data = dataclass_to_jsonable(risk_state)
        self.connection.execute(
            """
            insert into risk_state (
                timestamp, bankroll_usd, open_positions, portfolio_exposure_usd, kill_switch_active, raw_json
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                risk_state.timestamp,
                risk_state.bankroll_usd,
                risk_state.open_positions,
                risk_state.portfolio_exposure_usd,
                int(risk_state.kill_switch_active),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_engine_state(self, engine_state: EngineState) -> None:
        data = dataclass_to_jsonable(engine_state)
        self.connection.execute(
            """
            insert into engine_state (
                timestamp, mode, discovered_markets, actionable_signals, orders_submitted, skipped, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                engine_state.timestamp,
                engine_state.mode,
                engine_state.discovered_markets,
                engine_state.actionable_signals,
                engine_state.orders_submitted,
                engine_state.skipped,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_prediction_snapshot(self, snapshot: PredictionSnapshot) -> int | None:
        data = dataclass_to_jsonable(snapshot)
        cursor = self.connection.execute(
            """
            insert or ignore into prediction_snapshots (
                timestamp, station, market_date, decision_time_utc, decision_time_local,
                latest_obs_time_utc, latest_obs_time_local, obs_age_minutes,
                obs_delay_bucket, current_temp, high_so_far, hrrr_remaining_max,
                hrrr_current_temp, hrrr_current_temp_minus_current_temp,
                hrrr_remaining_max_minus_selected_lower, hrrr_remaining_max_minus_selected_upper,
                hrrr_remaining_min_minus_selected_lower, hrrr_remaining_min_minus_selected_upper,
                hrrr_temp_next_3h_max, hrrr_temp_next_3h_mean, hrrr_remaining_min,
                hrrr_wind_speed_current, hrrr_wind_speed_next_3h_mean,
                hrrr_wind_speed_remaining_max, hrrr_gust_remaining_max,
                hrrr_cloud_cover_current, hrrr_cloud_cover_next_3h_mean,
                hrrr_cloud_cover_remaining_mean, hrrr_cloud_cover_remaining_max,
                hrrr_rh_current, hrrr_rh_next_3h_mean, hrrr_rh_remaining_mean,
                hrrr_shortwave_next_3h_mean, hrrr_shortwave_remaining_max,
                strategy_bucket, selected_market_id, selected_bucket, selected_side, selected_edge,
                selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask,
                selected_best_bid, selected_best_ask, selected_spread, selected_depth_at_ask,
                selected_depth_ask_plus_0_01, selected_depth_ask_plus_0_03,
                selected_depth_ask_plus_0_05, selected_book_timestamp, selected_book_age_seconds,
                selected_liquidity_json,
                selected_ask_sweep_json, selected_bid_ladder_json, selected_sweep_price_cap,
                selected_sweep_depth_to_cap, selected_sweep_fillable_25_usd,
                selected_sweep_fillable_50_usd, selected_sweep_fillable_100_usd,
                selected_sweep_vwap_25, selected_sweep_vwap_50, selected_sweep_vwap_100,
                selected_bid_ladder_top_price, selected_bid_ladder_low_price,
                selected_bid_ladder_levels, selected_bid_ladder_total_notional_usd,
                selected_bid_ladder_top_distance_from_ask,
                selected_bid_ladder_top_improvement_over_best_bid,
                selected_bid_ladder_min_edge, selected_bid_ladder_max_edge,
                high_conviction, skip_reason, candidate_count, model_name, market_family, low_so_far, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp,
                snapshot.station,
                data["market_date"],
                snapshot.decision_time_utc,
                snapshot.decision_time_local,
                snapshot.latest_obs_time_utc,
                snapshot.latest_obs_time_local,
                snapshot.obs_age_minutes,
                snapshot.obs_delay_bucket,
                snapshot.current_temp,
                snapshot.high_so_far,
                snapshot.hrrr_remaining_max,
                snapshot.hrrr_current_temp,
                snapshot.hrrr_current_temp_minus_current_temp,
                snapshot.hrrr_remaining_max_minus_selected_lower,
                snapshot.hrrr_remaining_max_minus_selected_upper,
                snapshot.hrrr_remaining_min_minus_selected_lower,
                snapshot.hrrr_remaining_min_minus_selected_upper,
                snapshot.hrrr_temp_next_3h_max,
                snapshot.hrrr_temp_next_3h_mean,
                snapshot.hrrr_remaining_min,
                snapshot.hrrr_wind_speed_current,
                snapshot.hrrr_wind_speed_next_3h_mean,
                snapshot.hrrr_wind_speed_remaining_max,
                snapshot.hrrr_gust_remaining_max,
                snapshot.hrrr_cloud_cover_current,
                snapshot.hrrr_cloud_cover_next_3h_mean,
                snapshot.hrrr_cloud_cover_remaining_mean,
                snapshot.hrrr_cloud_cover_remaining_max,
                snapshot.hrrr_rh_current,
                snapshot.hrrr_rh_next_3h_mean,
                snapshot.hrrr_rh_remaining_mean,
                snapshot.hrrr_shortwave_next_3h_mean,
                snapshot.hrrr_shortwave_remaining_max,
                str(snapshot.strategy_bucket),
                snapshot.selected_market_id,
                snapshot.selected_bucket,
                str(snapshot.selected_side),
                snapshot.selected_edge,
                snapshot.selected_fair_yes,
                snapshot.selected_fair_no,
                snapshot.selected_yes_ask,
                snapshot.selected_no_ask,
                snapshot.selected_best_bid,
                snapshot.selected_best_ask,
                snapshot.selected_spread,
                snapshot.selected_depth_at_ask,
                snapshot.selected_depth_ask_plus_0_01,
                snapshot.selected_depth_ask_plus_0_03,
                snapshot.selected_depth_ask_plus_0_05,
                snapshot.selected_book_timestamp,
                snapshot.selected_book_age_seconds,
                json.dumps(snapshot.selected_liquidity, sort_keys=True) if snapshot.selected_liquidity is not None else None,
                json.dumps(snapshot.selected_ask_sweep, sort_keys=True) if snapshot.selected_ask_sweep is not None else None,
                json.dumps(snapshot.selected_bid_ladder, sort_keys=True) if snapshot.selected_bid_ladder is not None else None,
                snapshot.selected_sweep_price_cap,
                snapshot.selected_sweep_depth_to_cap,
                snapshot.selected_sweep_fillable_25_usd,
                snapshot.selected_sweep_fillable_50_usd,
                snapshot.selected_sweep_fillable_100_usd,
                snapshot.selected_sweep_vwap_25,
                snapshot.selected_sweep_vwap_50,
                snapshot.selected_sweep_vwap_100,
                snapshot.selected_bid_ladder_top_price,
                snapshot.selected_bid_ladder_low_price,
                snapshot.selected_bid_ladder_levels,
                snapshot.selected_bid_ladder_total_notional_usd,
                snapshot.selected_bid_ladder_top_distance_from_ask,
                snapshot.selected_bid_ladder_top_improvement_over_best_bid,
                snapshot.selected_bid_ladder_min_edge,
                snapshot.selected_bid_ladder_max_edge,
                int(snapshot.high_conviction),
                snapshot.skip_reason,
                snapshot.candidate_count,
                snapshot.model_name,
                str(snapshot.market_family),
                snapshot.low_so_far,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def upsert_station_date_outcome(self, outcome: StationDateOutcome) -> None:
        data = dataclass_to_jsonable(outcome)
        self.connection.execute(
            """
            insert into station_date_outcomes (
                station, market_date, timestamp, final_high_tmpf, final_low_tmpf, source, resolved_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(station, market_date) do update set
                timestamp=excluded.timestamp,
                final_high_tmpf=excluded.final_high_tmpf,
                final_low_tmpf=excluded.final_low_tmpf,
                source=excluded.source,
                resolved_at=excluded.resolved_at,
                raw_json=excluded.raw_json
            """,
            (
                outcome.station,
                data["market_date"],
                outcome.timestamp,
                outcome.final_high_tmpf,
                outcome.final_low_tmpf,
                outcome.source,
                outcome.resolved_at,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def upsert_prediction_result(self, result: PredictionResult) -> None:
        data = dataclass_to_jsonable(result)
        self.connection.execute(
            """
            insert into prediction_results (
                prediction_snapshot_id, timestamp, station, market_date, obs_delay_bucket,
                selected_market_id, selected_bucket, selected_side, final_high_tmpf,
                final_low_tmpf, market_family, winning_side, correct, entry_price, paper_pnl, edge,
                decision_time_local, obs_age_minutes, resolved_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(prediction_snapshot_id) do update set
                timestamp=excluded.timestamp,
                final_high_tmpf=excluded.final_high_tmpf,
                final_low_tmpf=excluded.final_low_tmpf,
                market_family=excluded.market_family,
                winning_side=excluded.winning_side,
                correct=excluded.correct,
                entry_price=excluded.entry_price,
                paper_pnl=excluded.paper_pnl,
                edge=excluded.edge,
                resolved_at=excluded.resolved_at,
                raw_json=excluded.raw_json
            """,
            (
                result.prediction_snapshot_id,
                result.timestamp,
                result.station,
                data["market_date"],
                result.obs_delay_bucket,
                result.selected_market_id,
                result.selected_bucket,
                str(result.selected_side),
                result.final_high_tmpf,
                result.final_low_tmpf,
                str(result.market_family),
                str(result.winning_side) if result.winning_side is not None else None,
                None if result.correct is None else int(result.correct),
                result.entry_price,
                result.paper_pnl,
                result.edge,
                result.decision_time_local,
                result.obs_age_minutes,
                result.resolved_at,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def insert_research_policy_position(self, position: ResearchPolicyPosition) -> int | None:
        data = dataclass_to_jsonable(position)
        cursor = self.connection.execute(
            """
            insert or ignore into research_policy_positions (
                timestamp, policy_name, station, market_date, scope_key, model_group,
                strategy_bucket, obs_delay_bucket, selected_market_id, selected_side,
                selected_bucket, entry_price, entry_edge, entry_fair,
                hrrr_remaining_max, hrrr_current_temp, hrrr_current_temp_minus_current_temp,
                hrrr_remaining_max_minus_selected_lower, hrrr_remaining_max_minus_selected_upper,
                hrrr_remaining_min_minus_selected_lower, hrrr_remaining_min_minus_selected_upper,
                hrrr_temp_next_3h_max, hrrr_temp_next_3h_mean, hrrr_remaining_min,
                hrrr_wind_speed_current, hrrr_wind_speed_next_3h_mean,
                hrrr_wind_speed_remaining_max, hrrr_gust_remaining_max,
                hrrr_cloud_cover_current, hrrr_cloud_cover_next_3h_mean,
                hrrr_cloud_cover_remaining_mean, hrrr_cloud_cover_remaining_max,
                hrrr_rh_current, hrrr_rh_next_3h_mean, hrrr_rh_remaining_mean,
                hrrr_shortwave_next_3h_mean, hrrr_shortwave_remaining_max,
                selected_best_bid, selected_best_ask, selected_spread, selected_depth_at_ask,
                selected_depth_ask_plus_0_01, selected_depth_ask_plus_0_03,
                selected_depth_ask_plus_0_05, selected_book_timestamp, selected_book_age_seconds,
                selected_liquidity_json,
                selected_ask_sweep_json, selected_bid_ladder_json, selected_sweep_price_cap,
                selected_sweep_depth_to_cap, selected_sweep_fillable_25_usd,
                selected_sweep_fillable_50_usd, selected_sweep_fillable_100_usd,
                selected_sweep_vwap_25, selected_sweep_vwap_50, selected_sweep_vwap_100,
                selected_bid_ladder_top_price, selected_bid_ladder_low_price,
                selected_bid_ladder_levels, selected_bid_ladder_total_notional_usd,
                selected_bid_ladder_top_distance_from_ask,
                selected_bid_ladder_top_improvement_over_best_bid,
                selected_bid_ladder_min_edge, selected_bid_ladder_max_edge,
                market_family, source_prediction_snapshot_ids, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.timestamp,
                position.policy_name,
                position.station,
                data["market_date"],
                position.scope_key,
                position.model_group,
                str(position.strategy_bucket),
                position.obs_delay_bucket,
                position.selected_market_id,
                str(position.selected_side),
                position.selected_bucket,
                position.entry_price,
                position.entry_edge,
                position.entry_fair,
                position.hrrr_remaining_max,
                position.hrrr_current_temp,
                position.hrrr_current_temp_minus_current_temp,
                position.hrrr_remaining_max_minus_selected_lower,
                position.hrrr_remaining_max_minus_selected_upper,
                position.hrrr_remaining_min_minus_selected_lower,
                position.hrrr_remaining_min_minus_selected_upper,
                position.hrrr_temp_next_3h_max,
                position.hrrr_temp_next_3h_mean,
                position.hrrr_remaining_min,
                position.hrrr_wind_speed_current,
                position.hrrr_wind_speed_next_3h_mean,
                position.hrrr_wind_speed_remaining_max,
                position.hrrr_gust_remaining_max,
                position.hrrr_cloud_cover_current,
                position.hrrr_cloud_cover_next_3h_mean,
                position.hrrr_cloud_cover_remaining_mean,
                position.hrrr_cloud_cover_remaining_max,
                position.hrrr_rh_current,
                position.hrrr_rh_next_3h_mean,
                position.hrrr_rh_remaining_mean,
                position.hrrr_shortwave_next_3h_mean,
                position.hrrr_shortwave_remaining_max,
                position.selected_best_bid,
                position.selected_best_ask,
                position.selected_spread,
                position.selected_depth_at_ask,
                position.selected_depth_ask_plus_0_01,
                position.selected_depth_ask_plus_0_03,
                position.selected_depth_ask_plus_0_05,
                position.selected_book_timestamp,
                position.selected_book_age_seconds,
                json.dumps(position.selected_liquidity, sort_keys=True) if position.selected_liquidity is not None else None,
                json.dumps(position.selected_ask_sweep, sort_keys=True) if position.selected_ask_sweep is not None else None,
                json.dumps(position.selected_bid_ladder, sort_keys=True) if position.selected_bid_ladder is not None else None,
                position.selected_sweep_price_cap,
                position.selected_sweep_depth_to_cap,
                position.selected_sweep_fillable_25_usd,
                position.selected_sweep_fillable_50_usd,
                position.selected_sweep_fillable_100_usd,
                position.selected_sweep_vwap_25,
                position.selected_sweep_vwap_50,
                position.selected_sweep_vwap_100,
                position.selected_bid_ladder_top_price,
                position.selected_bid_ladder_low_price,
                position.selected_bid_ladder_levels,
                position.selected_bid_ladder_total_notional_usd,
                position.selected_bid_ladder_top_distance_from_ask,
                position.selected_bid_ladder_top_improvement_over_best_bid,
                position.selected_bid_ladder_min_edge,
                position.selected_bid_ladder_max_edge,
                str(position.market_family),
                json.dumps(position.source_prediction_snapshot_ids),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def recent_research_policy_positions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select id, raw_json from research_policy_positions order by id desc limit ?",
            (limit,),
        ).fetchall()
        positions = []
        for row in rows:
            payload = json.loads(row["raw_json"])
            payload["id"] = int(row["id"])
            positions.append(payload)
        return positions

    def promotable_research_policy_positions(
        self,
        promoted_policies: set[str],
        market_date: date | str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not promoted_policies:
            return []
        policy_placeholders = ",".join("?" for _ in promoted_policies)
        market_date_text = _date_text(market_date)
        date_filter = "and rpp.market_date = ?" if market_date_text is not None else ""
        params: list[Any] = [*sorted(promoted_policies)]
        if market_date_text is not None:
            params.append(market_date_text)
        params.append(limit)
        rows = self.connection.execute(
            f"""
            select
                rpp.id,
                rpp.timestamp,
                rpp.policy_name,
                rpp.station,
                rpp.market_date,
                rpp.scope_key,
                rpp.model_group,
                rpp.strategy_bucket,
                rpp.obs_delay_bucket,
                rpp.selected_market_id,
                rpp.selected_side,
                rpp.selected_bucket,
                rpp.entry_price,
                rpp.entry_edge,
                rpp.entry_fair,
                rpp.market_family,
                rpp.source_prediction_snapshot_ids,
                rpp.raw_json,
                m.yes_token_id,
                m.no_token_id,
                m.lower_f,
                m.upper_f,
                case
                    when rpp.selected_side = 'BUY_YES' then m.yes_token_id
                    else m.no_token_id
                end as selected_token_id
            from research_policy_positions rpp
            join markets m on m.market_id = rpp.selected_market_id
            left join paper_policy_positions ppp
                on ppp.research_policy_position_id = rpp.id
            where rpp.policy_name in ({policy_placeholders})
                and ppp.id is null
                and rpp.selected_side in ('BUY_YES', 'BUY_NO')
                {date_filter}
            order by rpp.timestamp, rpp.id
            limit ?
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def insert_paper_policy_position(self, position: PaperPolicyPosition) -> int | None:
        data = dataclass_to_jsonable(position)
        cursor = self.connection.execute(
            """
            insert or ignore into paper_policy_positions (
                timestamp, research_policy_position_id, policy_name, station, market_date,
                selected_market_id, selected_token_id, selected_side, selected_bucket,
                entry_limit_price, target_notional_usd, filled_shares, avg_entry_price,
                cost_usd, state, realized_pnl, realized_rr, mark_value,
                unrealized_pnl, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.timestamp,
                position.research_policy_position_id,
                position.policy_name,
                position.station,
                data["market_date"],
                position.selected_market_id,
                position.selected_token_id,
                str(position.selected_side),
                position.selected_bucket,
                position.entry_limit_price,
                position.target_notional_usd,
                position.filled_shares,
                position.avg_entry_price,
                position.cost_usd,
                str(position.state),
                position.realized_pnl,
                position.realized_rr,
                position.mark_value,
                position.unrealized_pnl,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def update_paper_policy_position_execution(
        self,
        paper_position_id: int,
        *,
        state: PaperPolicyFinalState,
        filled_shares: float,
        avg_entry_price: float | None,
        cost_usd: float,
        raw_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.connection.execute(
            "select raw_json from paper_policy_positions where id = ?",
            (paper_position_id,),
        ).fetchone()
        raw_json: dict[str, Any] = {}
        if row is not None:
            raw_json = json.loads(row["raw_json"])
        if raw_patch:
            raw_json.update(raw_patch)
        raw_json.update(
            {
                "state": str(state),
                "filled_shares": filled_shares,
                "avg_entry_price": avg_entry_price,
                "cost_usd": cost_usd,
            }
        )
        self.connection.execute(
            """
            update paper_policy_positions
            set state = ?,
                filled_shares = ?,
                avg_entry_price = ?,
                cost_usd = ?,
                raw_json = ?
            where id = ?
            """,
            (
                str(state),
                filled_shares,
                avg_entry_price,
                cost_usd,
                json.dumps(raw_json, sort_keys=True),
                paper_position_id,
            ),
        )
        self.connection.commit()

    def update_paper_policy_position_mark(
        self,
        paper_position_id: int,
        *,
        mark_value: float | None,
        unrealized_pnl: float | None,
        raw_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.connection.execute(
            "select raw_json from paper_policy_positions where id = ?",
            (paper_position_id,),
        ).fetchone()
        raw_json = json.loads(row["raw_json"]) if row is not None else {}
        if raw_patch:
            raw_json.update(raw_patch)
        self.connection.execute(
            """
            update paper_policy_positions
            set mark_value = ?,
                unrealized_pnl = ?,
                raw_json = ?
            where id = ?
            """,
            (mark_value, unrealized_pnl, json.dumps(raw_json, sort_keys=True), paper_position_id),
        )
        self.connection.commit()

    def update_paper_policy_position_settlement(
        self,
        paper_position_id: int,
        *,
        state: PaperPolicyFinalState,
        realized_pnl: float,
        realized_rr: float | None,
        raw_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.connection.execute(
            "select raw_json from paper_policy_positions where id = ?",
            (paper_position_id,),
        ).fetchone()
        raw_json = json.loads(row["raw_json"]) if row is not None else {}
        if raw_patch:
            raw_json.update(raw_patch)
        raw_json.update({"state": str(state), "realized_pnl": realized_pnl, "realized_rr": realized_rr})
        self.connection.execute(
            """
            update paper_policy_positions
            set state = ?,
                realized_pnl = ?,
                realized_rr = ?,
                raw_json = ?
            where id = ?
            """,
            (str(state), realized_pnl, realized_rr, json.dumps(raw_json, sort_keys=True), paper_position_id),
        )
        self.connection.commit()

    def insert_paper_policy_order_attempt(self, attempt: PaperPolicyOrderAttempt) -> int | None:
        data = dataclass_to_jsonable(attempt)
        cursor = self.connection.execute(
            """
            insert or ignore into paper_policy_order_attempts (
                timestamp, paper_position_id, research_policy_position_id, attempt_seq,
                token_id, side, order_mode, limit_price, target_notional_usd,
                external_order_id, external_status, not_found_count, final_state,
                final_reason, filled_shares, avg_price, cost_usd, levels_consumed,
                raw_payload
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.timestamp,
                attempt.paper_position_id,
                attempt.research_policy_position_id,
                attempt.attempt_seq,
                attempt.token_id,
                str(attempt.side),
                str(attempt.order_mode),
                attempt.limit_price,
                attempt.target_notional_usd,
                attempt.external_order_id,
                attempt.external_status,
                attempt.not_found_count,
                str(attempt.final_state),
                attempt.final_reason,
                attempt.filled_shares,
                attempt.avg_price,
                attempt.cost_usd,
                json.dumps(data["levels_consumed"], sort_keys=True),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def insert_paper_policy_trade_event(self, event: PaperPolicyTradeEvent) -> int:
        data = dataclass_to_jsonable(event)
        cursor = self.connection.execute(
            """
            insert into paper_policy_trade_events (
                timestamp, paper_position_id, research_policy_position_id,
                event_type, message, raw_payload
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp,
                event.paper_position_id,
                event.research_policy_position_id,
                str(event.event_type),
                event.message,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_paper_policy_risk_snapshot(self, snapshot: PaperPolicyRiskSnapshot) -> int:
        data = dataclass_to_jsonable(snapshot)
        cursor = self.connection.execute(
            """
            insert into paper_policy_risk_snapshots (
                timestamp, bankroll_usd, open_positions, open_risk_usd,
                station_date_exposure_usd, raw_payload
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp,
                snapshot.bankroll_usd,
                snapshot.open_positions,
                snapshot.open_risk_usd,
                json.dumps(snapshot.station_date_exposure_usd, sort_keys=True),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def upsert_live_strategy(self, strategy: LiveStrategy) -> None:
        data = dataclass_to_jsonable(strategy)
        self.connection.execute(
            """
            insert into live_strategies (
                name, active, source, model_group, model_names, strategy_bucket,
                market_family, local_decision_start, local_decision_end,
                entry_price_min, uniqueness_key_mode, max_notional_usd, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(name) do update set
                active=excluded.active,
                source=excluded.source,
                model_group=excluded.model_group,
                model_names=excluded.model_names,
                strategy_bucket=excluded.strategy_bucket,
                market_family=excluded.market_family,
                local_decision_start=excluded.local_decision_start,
                local_decision_end=excluded.local_decision_end,
                entry_price_min=excluded.entry_price_min,
                uniqueness_key_mode=excluded.uniqueness_key_mode,
                max_notional_usd=excluded.max_notional_usd,
                raw_json=excluded.raw_json
            """,
            (
                strategy.name,
                int(strategy.active),
                strategy.source,
                strategy.model_group,
                json.dumps(strategy.model_names),
                str(strategy.strategy_bucket),
                str(strategy.market_family),
                strategy.local_decision_start,
                strategy.local_decision_end,
                strategy.entry_price_min,
                strategy.uniqueness_key_mode,
                strategy.max_notional_usd,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()

    def live_strategies(self, active_only: bool = True) -> list[dict[str, Any]]:
        where = "where active = 1" if active_only else ""
        rows = self.connection.execute(f"select * from live_strategies {where} order by name").fetchall()
        return [dict(row) for row in rows]

    def insert_live_policy_position(self, position: LivePolicyPosition) -> int | None:
        data = dataclass_to_jsonable(position)
        cursor = self.connection.execute(
            """
            insert or ignore into live_policy_positions (
                timestamp, strategy_name, station, market_date, market_family, scope_key,
                selected_market_id, selected_token_id, selected_side, selected_bucket,
                obs_delay_bucket, entry_price, entry_fair, entry_edge,
                target_notional_usd, target_shares, state,
                source_prediction_snapshot_ids, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.timestamp,
                position.strategy_name,
                position.station,
                data["market_date"],
                str(position.market_family),
                position.scope_key,
                position.selected_market_id,
                position.selected_token_id,
                str(position.selected_side),
                position.selected_bucket,
                position.obs_delay_bucket,
                position.entry_price,
                position.entry_fair,
                position.entry_edge,
                position.target_notional_usd,
                position.target_shares,
                str(position.state),
                json.dumps(position.source_prediction_snapshot_ids),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def update_live_policy_position_execution(
        self,
        live_position_id: int,
        *,
        state: str,
        filled_shares: float = 0.0,
        avg_entry_price: float | None = None,
        cost_usd: float = 0.0,
        raw_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.connection.execute("select raw_json from live_policy_positions where id = ?", (live_position_id,)).fetchone()
        raw_json = json.loads(row["raw_json"]) if row is not None else {}
        if raw_patch:
            raw_json.update(raw_patch)
        raw_json.update({"state": state, "filled_shares": filled_shares, "avg_entry_price": avg_entry_price, "cost_usd": cost_usd})
        self.connection.execute(
            """
            update live_policy_positions
            set state = ?, filled_shares = ?, avg_entry_price = ?, cost_usd = ?, raw_json = ?
            where id = ?
            """,
            (state, filled_shares, avg_entry_price, cost_usd, json.dumps(raw_json, sort_keys=True), live_position_id),
        )
        self.connection.commit()

    def live_unsettled_positions(self, *, max_market_date: date | str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        date_filter = "and lpp.market_date <= ?" if max_market_date is not None else ""
        params: tuple[Any, ...] = ((_date_text(max_market_date), limit) if max_market_date is not None else (limit,))
        rows = self.connection.execute(
            f"""
            select
                lpp.*,
                m.condition_id,
                m.yes_token_id,
                m.no_token_id
            from live_policy_positions lpp
            left join markets m on m.market_id = lpp.selected_market_id
            where lpp.state in ('FILLED', 'PARTIAL')
                and lpp.resolved_at is null
                {date_filter}
            order by lpp.market_date, lpp.id
            limit ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def update_live_policy_position_settlement(
        self,
        live_position_id: int,
        *,
        resolved_at: str,
        resolution_source: str,
        winning_token_id: str | None,
        winning_side: str | None,
        settlement_value_usd: float,
        realized_pnl: float,
        realized_rr: float | None,
        raw_patch: dict[str, Any] | None = None,
    ) -> None:
        row = self.connection.execute("select raw_json from live_policy_positions where id = ?", (live_position_id,)).fetchone()
        raw_json = json.loads(row["raw_json"]) if row is not None else {}
        if raw_patch:
            raw_json.update(raw_patch)
        raw_json.update(
            {
                "state": str(LivePositionState.SETTLED),
                "resolved_at": resolved_at,
                "resolution_source": resolution_source,
                "winning_token_id": winning_token_id,
                "winning_side": winning_side,
                "settlement_value_usd": settlement_value_usd,
                "realized_pnl": realized_pnl,
                "realized_rr": realized_rr,
            }
        )
        self.connection.execute(
            """
            update live_policy_positions
            set state = ?,
                mark_value = ?,
                unrealized_pnl = ?,
                resolved_at = ?,
                resolution_source = ?,
                winning_token_id = ?,
                winning_side = ?,
                settlement_value_usd = ?,
                realized_pnl = ?,
                realized_rr = ?,
                raw_json = ?
            where id = ?
            """,
            (
                str(LivePositionState.SETTLED),
                settlement_value_usd,
                realized_pnl,
                resolved_at,
                resolution_source,
                winning_token_id,
                winning_side,
                settlement_value_usd,
                realized_pnl,
                realized_rr,
                json.dumps(raw_json, sort_keys=True),
                live_position_id,
            ),
        )
        self.connection.commit()

    def has_live_trade_event(self, live_position_id: int, event_type: str) -> bool:
        row = self.connection.execute(
            """
            select 1
            from live_trade_events
            where live_position_id = ? and event_type = ?
            limit 1
            """,
            (live_position_id, event_type),
        ).fetchone()
        return row is not None

    def insert_live_order_attempt(self, attempt: LiveOrderAttempt) -> int | None:
        data = dataclass_to_jsonable(attempt)
        cursor = self.connection.execute(
            """
            insert or ignore into live_order_attempts (
                timestamp, live_position_id, attempt_seq, token_id, side, order_mode,
                limit_price, target_notional_usd, target_shares, external_order_id,
                external_status, final_state, final_reason, filled_shares,
                avg_price, cost_usd, raw_payload
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.timestamp,
                attempt.live_position_id,
                attempt.attempt_seq,
                attempt.token_id,
                str(attempt.side),
                str(attempt.order_mode),
                attempt.limit_price,
                attempt.target_notional_usd,
                attempt.target_shares,
                attempt.external_order_id,
                attempt.external_status,
                str(attempt.final_state),
                attempt.final_reason,
                attempt.filled_shares,
                attempt.avg_price,
                attempt.cost_usd,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        if cursor.rowcount == 0:
            return None
        return int(cursor.lastrowid)

    def insert_live_trade_event(self, event: LiveTradeEvent) -> int:
        data = dataclass_to_jsonable(event)
        cursor = self.connection.execute(
            """
            insert into live_trade_events (
                timestamp, live_position_id, strategy_name, event_type, message, raw_payload
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp,
                event.live_position_id,
                event.strategy_name,
                str(event.event_type),
                event.message,
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_live_risk_snapshot(self, snapshot: LiveRiskSnapshot) -> int:
        data = dataclass_to_jsonable(snapshot)
        cursor = self.connection.execute(
            """
            insert into live_risk_snapshots (
                timestamp, open_positions, open_risk_usd, station_date_exposure_usd, raw_payload
            )
            values (?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp,
                snapshot.open_positions,
                snapshot.open_risk_usd,
                json.dumps(snapshot.station_date_exposure_usd, sort_keys=True),
                json.dumps(data, sort_keys=True),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def live_open_positions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select *
            from live_policy_positions
            where state in ('RESERVED', 'SUBMITTED', 'FILLED', 'PARTIAL', 'DELAYED', 'UNKNOWN')
            order by id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_live_market_date(self) -> str | None:
        row = self.connection.execute(
            """
            select max(market_date) as market_date
            from live_policy_positions
            """
        ).fetchone()
        if row is None:
            return None
        return row["market_date"]

    def live_dashboard_positions(self, limit: int = 1000, market_date: date | str | None = None) -> list[dict[str, Any]]:
        market_date_text = _date_text(market_date)
        date_filter = "where lpp.market_date = ?" if market_date_text is not None else ""
        params: tuple[Any, ...] = (market_date_text, limit) if market_date_text is not None else (limit,)
        rows = self.connection.execute(
            f"""
            with latest_books as (
                select bs.token_id, bs.best_bid, bs.best_ask, bs.timestamp
                from book_snapshots bs
                join (
                    select token_id, max(id) as id
                    from book_snapshots
                    group by token_id
                ) latest on latest.id = bs.id
            ),
            station_highs as (
                select
                    station,
                    market_date,
                    max(high_so_far) as high_so_far,
                    max(hrrr_remaining_max) as hrrr_remaining_max
                from prediction_snapshots
                group by station, market_date
            )
            select
                lpp.id,
                lpp.timestamp,
                lpp.strategy_name,
                lpp.strategy_name as policy_name,
                coalesce(ls.model_group, '') as model_group,
                coalesce(ls.strategy_bucket, '') as strategy_bucket,
                lpp.station,
                lpp.market_date,
                lpp.market_family,
                lpp.scope_key,
                lpp.selected_market_id,
                lpp.selected_token_id,
                lpp.selected_side,
                lpp.selected_bucket,
                lpp.obs_delay_bucket,
                lpp.entry_price,
                lpp.entry_fair,
                lpp.entry_edge,
                lpp.target_notional_usd,
                lpp.target_shares,
                lpp.filled_shares,
                lpp.avg_entry_price,
                lpp.cost_usd,
                lpp.mark_value as stored_mark_value,
                lpp.unrealized_pnl as stored_unrealized_pnl,
                lpp.state,
                lb.best_bid as current_bid,
                lb.best_ask as current_ask,
                lb.timestamp as current_book_time,
                highs.high_so_far,
                highs.hrrr_remaining_max,
                outcomes.final_high_tmpf,
                outcomes.resolved_at as outcome_resolved_at,
                case
                    when lb.best_bid is not null and lpp.filled_shares > 0 then lb.best_bid * lpp.filled_shares
                    else lpp.mark_value
                end as mark_value,
                case
                    when lb.best_bid is not null and lpp.filled_shares > 0 then (lb.best_bid * lpp.filled_shares) - lpp.cost_usd
                    else lpp.unrealized_pnl
                end as unrealized_pnl
            from live_policy_positions lpp
            left join live_strategies ls on ls.name = lpp.strategy_name
            left join latest_books lb on lb.token_id = lpp.selected_token_id
            left join station_highs highs
                on highs.station = lpp.station
                and highs.market_date = lpp.market_date
            left join station_date_outcomes outcomes
                on outcomes.station = lpp.station
                and outcomes.market_date = lpp.market_date
            {date_filter}
            order by lpp.timestamp desc, lpp.id desc
            limit ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_live_order_attempts(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select
                loa.*,
                lpp.strategy_name,
                lpp.station,
                lpp.market_date,
                lpp.selected_bucket
            from live_order_attempts loa
            left join live_policy_positions lpp on lpp.id = loa.live_position_id
            order by loa.id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_live_trade_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select *
            from live_trade_events
            order by id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_live_risk_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select *
            from live_risk_snapshots
            order by id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def live_exposure_summary(self) -> dict[str, Any]:
        rows = self.live_open_positions()
        station_date: dict[str, float] = {}
        total = 0.0
        for row in rows:
            risk = float(row["cost_usd"] or row["target_notional_usd"] or 0.0)
            total += risk
            key = f"{row['station']}:{row['market_date']}"
            station_date[key] = station_date.get(key, 0.0) + risk
        return {"open_positions": len(rows), "open_risk_usd": total, "station_date_exposure_usd": station_date}

    def next_live_attempt_seq(self, live_position_id: int) -> int:
        row = self.connection.execute(
            "select max(attempt_seq) seq from live_order_attempts where live_position_id = ?",
            (live_position_id,),
        ).fetchone()
        return int(row["seq"] or 0) + 1

    def paper_policy_open_positions(self, policy_name: str | None = None) -> list[dict[str, Any]]:
        policy_filter = "and policy_name = ?" if policy_name is not None else ""
        params: tuple[Any, ...] = (policy_name,) if policy_name is not None else ()
        rows = self.connection.execute(
            f"""
            select *
            from paper_policy_positions
            where state in ('FILLED', 'PARTIAL', 'DELAYED', 'UNKNOWN', 'RESERVED', 'SUBMITTED')
                {policy_filter}
            order by id
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def paper_policy_exposure_summary(self, policy_name: str | None = None) -> dict[str, Any]:
        rows = self.paper_policy_open_positions(policy_name=policy_name)
        station_date: dict[str, float] = {}
        total = 0.0
        for row in rows:
            risk = float(row["cost_usd"] or row["target_notional_usd"] or 0.0)
            total += risk
            key = f"{row['station']}:{row['market_date']}"
            station_date[key] = station_date.get(key, 0.0) + risk
        return {"open_positions": len(rows), "open_risk_usd": total, "station_date_exposure_usd": station_date}

    def has_open_paper_policy_exposure(
        self,
        *,
        policy_name: str,
        station: str,
        market_date: date | str,
        selected_bucket: str | None,
        selected_side: str,
    ) -> bool:
        row = self.connection.execute(
            """
            select 1
            from paper_policy_positions
            where station = ?
                and market_date = ?
                and policy_name = ?
                and coalesce(selected_bucket, '') = coalesce(?, '')
                and selected_side = ?
                and state in ('FILLED', 'PARTIAL', 'DELAYED', 'UNKNOWN', 'RESERVED', 'SUBMITTED')
            limit 1
            """,
            (station, _date_text(market_date), policy_name, selected_bucket, selected_side),
        ).fetchone()
        return row is not None

    def paper_policy_retryable_positions(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select *
            from paper_policy_positions
            where state in ('RESERVED', 'SUBMITTED', 'DELAYED', 'UNKNOWN')
            order by id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_paper_policy_attempt_seq(self, paper_position_id: int) -> int:
        row = self.connection.execute(
            """
            select max(attempt_seq) as attempt_seq
            from paper_policy_order_attempts
            where paper_position_id = ?
            """,
            (paper_position_id,),
        ).fetchone()
        if row is None or row["attempt_seq"] is None:
            return 0
        return int(row["attempt_seq"])

    def latest_paper_policy_attempts(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select *
            from paper_policy_order_attempts
            order by id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_research_market_date(self) -> str | None:
        row = self.connection.execute(
            """
            select max(market_date) as market_date
            from research_policy_positions
            """
        ).fetchone()
        if row is None:
            return None
        return row["market_date"]

    def live_research_policy_positions(self, limit: int = 1000, market_date: date | str | None = None) -> list[dict[str, Any]]:
        market_date_text = _date_text(market_date)
        date_filter = "where rpp.market_date = ?" if market_date_text is not None else ""
        params: tuple[Any, ...] = (market_date_text, limit) if market_date_text is not None else (limit,)
        rows = self.connection.execute(
            f"""
            with latest_books as (
                select bs.token_id, bs.best_bid, bs.best_ask, bs.timestamp
                from book_snapshots bs
                join (
                    select token_id, max(id) as id
                    from book_snapshots
                    group by token_id
                ) latest on latest.id = bs.id
            ),
            station_highs as (
                select
                    station,
                    market_date,
                    max(high_so_far) as high_so_far,
                    max(hrrr_remaining_max) as hrrr_remaining_max
                from prediction_snapshots
                group by station, market_date
            )
            select
                rpp.id,
                rpp.timestamp,
                rpp.policy_name,
                case
                    when rpp.policy_name like 'max_so_far%' then 'max_so_far'
                    else rpp.model_group
                end as model_group,
                rpp.station,
                rpp.market_date,
                rpp.scope_key,
                rpp.strategy_bucket,
                rpp.obs_delay_bucket,
                rpp.selected_market_id,
                rpp.selected_side,
                rpp.selected_bucket,
                rpp.entry_price,
                rpp.entry_edge,
                rpp.entry_fair,
                m.yes_token_id,
                m.no_token_id,
                case
                    when rpp.selected_side = 'BUY_YES' then m.yes_token_id
                    else m.no_token_id
                end as selected_token_id,
                lb.best_bid as current_bid,
                lb.timestamp as current_book_time,
                highs.high_so_far,
                highs.hrrr_remaining_max,
                outcomes.final_high_tmpf,
                outcomes.resolved_at as outcome_resolved_at,
                case
                    when lb.best_bid is null then null
                    else lb.best_bid - rpp.entry_price
                end as unrealized_pnl
            from research_policy_positions rpp
            join markets m on m.market_id = rpp.selected_market_id
            left join latest_books lb on lb.token_id = case
                when rpp.selected_side = 'BUY_YES' then m.yes_token_id
                else m.no_token_id
            end
            left join station_highs highs
                on highs.station = rpp.station
                and highs.market_date = rpp.market_date
            left join station_date_outcomes outcomes
                on outcomes.station = rpp.station
                and outcomes.market_date = rpp.market_date
            {date_filter}
            order by rpp.timestamp desc, rpp.id desc
            limit ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def research_status_overview(self, market_date: date | str) -> dict[str, Any]:
        market_date_text = _date_text(market_date)
        coverage = self.connection.execute(
            """
            select
                (select count(*) from prediction_snapshots) as snapshots,
                (select count(*) from prediction_snapshots where market_date = ?) as snapshots_today,
                (select count(*) from research_policy_positions) as policy_positions,
                (select count(*) from research_policy_positions where market_date = ?) as policy_positions_today,
                (select count(distinct policy_name) from research_policy_positions) as policies,
                (select count(*) from book_snapshots) as book_snapshots,
                (select max(timestamp) from book_snapshots) as latest_book_ts
            """,
            (market_date_text, market_date_text),
        ).fetchone()
        stations = self.connection.execute(
            """
            select
                station,
                count(*) as snapshots,
                min(timestamp) as first_ts,
                max(timestamp) as last_ts,
                max(current_temp) as current_temp,
                max(high_so_far) as high,
                max(hrrr_remaining_max) as hrrr,
                max(obs_age_minutes) as max_obs_age
            from prediction_snapshots
            where market_date = ?
            group by station
            order by station
            """,
            (market_date_text,),
        ).fetchall()
        outcomes = self.connection.execute(
            """
            select station, final_high_tmpf, source, resolved_at
            from station_date_outcomes
            where market_date = ?
            order by station
            """,
            (market_date_text,),
        ).fetchall()
        markets = self.connection.execute(
            """
            select
                station,
                count(*) as markets,
                sum(case when yes_token_id is not null and no_token_id is not null then 1 else 0 end) as tokenized,
                min(lower_f) as min_low,
                max(coalesce(upper_f, lower_f)) as max_bucket
            from markets
            where market_date = ?
            group by station
            order by station
            """,
            (market_date_text,),
        ).fetchall()
        return {
            **(dict(coverage) if coverage is not None else {}),
            "market_date": market_date_text,
            "station_temps": [dict(row) for row in stations],
            "outcomes": [dict(row) for row in outcomes],
            "market_stations": [dict(row) for row in markets],
        }

    def latest_insights(self, limit: int = 5, insight_type: str | None = None) -> list[dict[str, Any]]:
        if insight_type is None:
            rows = self.connection.execute(
                """
                select id, created_at, insight_type, target_date, severity, title, body, metrics_json, raw_json
                from hermes_insights
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                select id, created_at, insight_type, target_date, severity, title, body, metrics_json, raw_json
                from hermes_insights
                where insight_type = ?
                order by id desc
                limit ?
                """,
                (insight_type, limit),
            ).fetchall()
        insights = []
        for row in rows:
            item = dict(row)
            for key in ("metrics_json", "raw_json"):
                raw_value = item.get(key)
                if raw_value:
                    try:
                        item[key.replace("_json", "")] = json.loads(raw_value)
                    except json.JSONDecodeError:
                        item[key.replace("_json", "")] = None
            insights.append(item)
        return insights

    def policy_research_status_summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            with joined as (
                select
                    rpp.id,
                    rpp.policy_name,
                    case
                        when rpp.policy_name like 'max_so_far%' then 'max_so_far'
                        else rpp.model_group
                    end as model_group,
                    rpp.strategy_bucket,
                    rpp.obs_delay_bucket,
                    rpp.station,
                    rpp.market_date,
                    rpp.entry_price as policy_entry_price,
                    rpp.entry_edge,
                    pr.correct,
                    pr.paper_pnl
                from research_policy_positions rpp
                left join json_each(rpp.source_prediction_snapshot_ids) je
                left join prediction_results pr on pr.prediction_snapshot_id = je.value
            ),
            per_pos as (
                select
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge,
                    max(correct) as correct,
                    avg(paper_pnl) as paper_pnl
                from joined
                group by
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge
            )
            select
                policy_name,
                model_group,
                strategy_bucket,
                obs_delay_bucket,
                count(*) as total_positions,
                count(distinct station || market_date) as station_days,
                sum(case when paper_pnl is not null then 1 else 0 end) as resolved_positions,
                sum(case when correct = 1 then 1 else 0 end) as wins,
                avg(case when correct is not null then correct else null end) as hit_rate,
                sum(case when paper_pnl is not null then paper_pnl else 0 end) as total_pnl,
                sum(case when paper_pnl is not null then policy_entry_price else 0 end) as resolved_risk,
                avg(policy_entry_price) as avg_entry,
                avg(entry_edge) as avg_edge,
                group_concat(case when paper_pnl is not null then paper_pnl else null end) as resolved_pnls
            from per_pos
            group by policy_name, model_group, strategy_bucket, obs_delay_bucket
            order by total_pnl desc, policy_name
            """
        ).fetchall()
        summaries = []
        for row in rows:
            item = dict(row)
            pnl_text = str(item.pop("resolved_pnls") or "")
            pnls = [float(value) for value in pnl_text.split(",") if value]
            if pnls:
                mean_pnl = sum(pnls) / len(pnls)
                variance = sum((pnl - mean_pnl) ** 2 for pnl in pnls) / len(pnls)
                item["position_sharpe"] = None if variance == 0 else mean_pnl / (variance**0.5)
            else:
                item["position_sharpe"] = None
            risk = float(item.get("resolved_risk") or 0.0)
            item["return_on_risk"] = (float(item.get("total_pnl") or 0.0) / risk) if risk > 0 else None
            summaries.append(item)
        return summaries

    def recent_positions(self, state: str = "OPEN") -> list[dict[str, Any]]:
        rows = self.connection.execute("select raw_json from positions where state = ?", (state,)).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def recent_paper_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select raw_json from paper_orders order by timestamp desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select raw_json from decisions order by id desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def latest_position_marks(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select mark.raw_json
            from position_marks mark
            join (
                select position_id, max(id) as id
                from position_marks
                group by position_id
            ) latest on latest.id = mark.id
            order by mark.id desc
            limit ?
            """,
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def recent_station_date_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select raw_json from station_date_decisions order by id desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def recent_engine_states(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select raw_json from engine_state order by id desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def recent_prediction_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select id, raw_json from prediction_snapshots order by id desc limit ?",
            (limit,),
        ).fetchall()
        snapshots = []
        for row in rows:
            payload = json.loads(row["raw_json"])
            payload["id"] = int(row["id"])
            snapshots.append(payload)
        return snapshots

    def unresolved_snapshot_groups(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select distinct snapshots.station, snapshots.market_date
            from prediction_snapshots snapshots
            left join station_date_outcomes outcomes
                on outcomes.station = snapshots.station
                and outcomes.market_date = snapshots.market_date
            where outcomes.station is null
            order by snapshots.market_date, snapshots.station
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def prediction_snapshots_for_group(self, station: str, market_date: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select id, raw_json
            from prediction_snapshots
            where station = ? and market_date = ?
            order by id
            """,
            (station, market_date),
        ).fetchall()
        snapshots = []
        for row in rows:
            payload = json.loads(row["raw_json"])
            payload["id"] = int(row["id"])
            snapshots.append(payload)
        return snapshots

    def prediction_result_summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select
                snapshots.strategy_bucket,
                results.obs_delay_bucket,
                count(*) as snapshots,
                sum(case when correct is not null then 1 else 0 end) as scored,
                sum(case when correct = 1 then 1 else 0 end) as correct,
                avg(case when correct is not null then correct else null end) as win_rate,
                avg(edge) as avg_edge,
                avg(paper_pnl) as avg_pnl
            from prediction_results results
            left join prediction_snapshots snapshots
                on snapshots.id = results.prediction_snapshot_id
            group by snapshots.strategy_bucket, results.obs_delay_bucket
            order by
                snapshots.strategy_bucket,
                case results.obs_delay_bucket
                    when 'instant' then 0
                    when '5m' then 5
                    when '10m' then 10
                    when '15m' then 15
                    when '30m' then 30
                    else 999
                end
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def policy_performance_summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            with joined as (
                select
                    rpp.id,
                    rpp.policy_name,
                    case
                        when rpp.policy_name like 'max_so_far%' then 'max_so_far'
                        else rpp.model_group
                    end as model_group,
                    rpp.strategy_bucket,
                    rpp.obs_delay_bucket,
                    rpp.station,
                    rpp.market_date,
                    rpp.entry_price as policy_entry_price,
                    rpp.entry_edge,
                    pr.correct,
                    pr.paper_pnl
                from research_policy_positions rpp
                join json_each(rpp.source_prediction_snapshot_ids) je
                join prediction_results pr on pr.prediction_snapshot_id = je.value
            ),
            per_pos as (
                select
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge,
                    max(correct) as correct,
                    avg(paper_pnl) as paper_pnl
                from joined
                group by
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge
            )
            select
                policy_name,
                model_group,
                strategy_bucket,
                obs_delay_bucket,
                count(*) as resolved_positions,
                count(distinct station || market_date) as station_days,
                sum(case when correct = 1 then 1 else 0 end) as wins,
                avg(correct) as hit_rate,
                sum(paper_pnl) as total_pnl,
                avg(policy_entry_price) as avg_entry,
                avg(entry_edge) as avg_edge
            from per_pos
            group by policy_name, model_group, strategy_bucket, obs_delay_bucket
            order by total_pnl desc, policy_name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def policy_station_performance_summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            with joined as (
                select
                    rpp.id,
                    rpp.policy_name,
                    case
                        when rpp.policy_name like 'max_so_far%' then 'max_so_far'
                        else rpp.model_group
                    end as model_group,
                    rpp.strategy_bucket,
                    rpp.obs_delay_bucket,
                    rpp.station,
                    rpp.market_date,
                    rpp.entry_price as policy_entry_price,
                    rpp.entry_edge,
                    pr.correct,
                    pr.paper_pnl
                from research_policy_positions rpp
                join json_each(rpp.source_prediction_snapshot_ids) je
                join prediction_results pr on pr.prediction_snapshot_id = je.value
            ),
            per_pos as (
                select
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge,
                    max(correct) as correct,
                    avg(paper_pnl) as paper_pnl
                from joined
                group by
                    id,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    market_date,
                    policy_entry_price,
                    entry_edge
            )
            select
                policy_name,
                model_group,
                station,
                strategy_bucket,
                obs_delay_bucket,
                count(*) as resolved_positions,
                sum(case when correct = 1 then 1 else 0 end) as wins,
                avg(correct) as hit_rate,
                sum(paper_pnl) as total_pnl,
                avg(policy_entry_price) as avg_entry,
                avg(entry_edge) as avg_edge
            from per_pos
            group by policy_name, model_group, station, strategy_bucket, obs_delay_bucket
            order by total_pnl desc, policy_name, station
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def policy_daily_summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            with joined as (
                select
                    rpp.id,
                    rpp.market_date,
                    rpp.policy_name,
                    case
                        when rpp.policy_name like 'max_so_far%' then 'max_so_far'
                        else rpp.model_group
                    end as model_group,
                    rpp.strategy_bucket,
                    rpp.obs_delay_bucket,
                    rpp.station,
                    rpp.entry_price as policy_entry_price,
                    rpp.entry_edge,
                    pr.correct,
                    pr.paper_pnl
                from research_policy_positions rpp
                join json_each(rpp.source_prediction_snapshot_ids) je
                join prediction_results pr on pr.prediction_snapshot_id = je.value
            ),
            per_pos as (
                select
                    id,
                    market_date,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    policy_entry_price,
                    entry_edge,
                    max(correct) as correct,
                    avg(paper_pnl) as paper_pnl
                from joined
                group by
                    id,
                    market_date,
                    policy_name,
                    model_group,
                    strategy_bucket,
                    obs_delay_bucket,
                    station,
                    policy_entry_price,
                    entry_edge
            )
            select
                market_date,
                policy_name,
                model_group,
                strategy_bucket,
                obs_delay_bucket,
                count(*) as resolved_positions,
                sum(case when correct = 1 then 1 else 0 end) as wins,
                avg(correct) as hit_rate,
                sum(paper_pnl) as total_pnl,
                avg(policy_entry_price) as avg_entry,
                avg(entry_edge) as avg_edge
            from per_pos
            group by market_date, policy_name, model_group, strategy_bucket, obs_delay_bucket
            order by market_date desc, total_pnl desc, policy_name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def paper_order_summary(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            select
                count(*) as orders,
                sum(case when state = 'FILLED' then 1 else 0 end) as filled,
                sum(case when state = 'REJECTED' then 1 else 0 end) as rejected,
                coalesce(sum(cost), 0.0) as total_cost
            from paper_orders
            """
        ).fetchone()
        return dict(row) if row is not None else {"orders": 0, "filled": 0, "rejected": 0, "total_cost": 0.0}

    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "select raw_json from signals order by id desc limit ?",
            (limit,),
        ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]


def dataclass_field_names(instance: Any) -> set[str]:
    if not is_dataclass(instance):
        return set()
    return {field.name for field in fields(instance)}
