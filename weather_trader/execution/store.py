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
    Position,
    PositionMark,
    Resolution,
    RiskState,
    Signal,
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
            """
        )
        self.connection.commit()

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
