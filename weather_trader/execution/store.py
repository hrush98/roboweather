from __future__ import annotations

import json
import sqlite3
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from weather_trader.execution.contracts import (
    BookSnapshot,
    Decision,
    EngineState,
    MarketSnapshot,
    PaperOrder,
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

            create table if not exists station_date_outcomes (
                station text not null,
                market_date text not null,
                timestamp text not null,
                final_high_tmpf real not null,
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
                source_prediction_snapshot_ids text not null,
                raw_json text not null,
                unique(policy_name, station, market_date, scope_key)
            );
            """
        )
        self._migrate_prediction_snapshots_schema()
        self.connection.commit()

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
                high_conviction, skip_reason, candidate_count, model_name, raw_json
            )
            select
                id, timestamp, station, market_date, decision_time_utc, decision_time_local,
                latest_obs_time_utc, latest_obs_time_local, obs_age_minutes,
                obs_delay_bucket, current_temp, high_so_far, hrrr_remaining_max,
                {strategy_expr}, selected_market_id, selected_bucket, selected_side, selected_edge,
                selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask,
                high_conviction, skip_reason, candidate_count, '', raw_json
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
                resolution_source, discovered_at, active, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                strategy_bucket, selected_market_id, selected_bucket, selected_side, selected_edge,
                selected_fair_yes, selected_fair_no, selected_yes_ask, selected_no_ask,
                high_conviction, skip_reason, candidate_count, model_name, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(snapshot.strategy_bucket),
                snapshot.selected_market_id,
                snapshot.selected_bucket,
                str(snapshot.selected_side),
                snapshot.selected_edge,
                snapshot.selected_fair_yes,
                snapshot.selected_fair_no,
                snapshot.selected_yes_ask,
                snapshot.selected_no_ask,
                int(snapshot.high_conviction),
                snapshot.skip_reason,
                snapshot.candidate_count,
                snapshot.model_name,
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
                station, market_date, timestamp, final_high_tmpf, source, resolved_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(station, market_date) do update set
                timestamp=excluded.timestamp,
                final_high_tmpf=excluded.final_high_tmpf,
                source=excluded.source,
                resolved_at=excluded.resolved_at,
                raw_json=excluded.raw_json
            """,
            (
                outcome.station,
                data["market_date"],
                outcome.timestamp,
                outcome.final_high_tmpf,
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
                winning_side, correct, entry_price, paper_pnl, edge,
                decision_time_local, obs_age_minutes, resolved_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(prediction_snapshot_id) do update set
                timestamp=excluded.timestamp,
                final_high_tmpf=excluded.final_high_tmpf,
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
                source_prediction_snapshot_ids, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
