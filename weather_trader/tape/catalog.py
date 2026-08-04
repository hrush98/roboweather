from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from weather_trader.tape.contracts import (
    BookCheckpoint,
    CollectorMetric,
    CollectorSession,
    DecisionTapeJoin,
    CoverageInterval,
    SubscriptionGeneration,
    TokenOutcome,
    TokenRegistryEntry,
    contract_to_dict,
)
from weather_trader.tape.storage import SegmentStats


class TapeCatalog:
    """Compact metadata catalog kept separate from raw market-tape segments."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        if read_only:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("pragma foreign_keys = ON")
        self.connection.execute("pragma busy_timeout = 30000")
        if read_only:
            self.connection.execute("pragma query_only = ON")
        else:
            self.connection.execute("pragma journal_mode = WAL")
            self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> TapeCatalog:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            create table if not exists tape_tokens (
                token_id text primary key,
                market_id text not null,
                condition_id text,
                outcome text not null,
                station text not null,
                market_date text not null,
                market_family text not null,
                lower_bound real,
                upper_bound real,
                sibling_token_id text,
                sibling_market_id text,
                market_end_at_utc text,
                discovered_at_utc text not null,
                active_from_utc text not null,
                listing_timestamp_source text not null default 'discovery_fallback',
                active_until_utc text,
                resolution_source text not null,
                subscription_state text not null,
                last_health_status text,
                raw_json text not null
            );

            create index if not exists idx_tape_tokens_active
                on tape_tokens(active_until_utc, market_date, station);

            create table if not exists tape_collector_sessions (
                session_id text primary key,
                started_at_utc text not null,
                started_monotonic_ns integer not null,
                collector_version text not null,
                hostname text not null,
                validation_run_id text not null default 'unspecified',
                build_fingerprint text not null default 'unspecified',
                config_fingerprint text not null default 'unspecified',
                finished_at_utc text,
                finish_reason text,
                raw_json text not null
            );

            create table if not exists tape_subscription_generations (
                session_id text not null,
                generation integer not null,
                effective_at_utc text not null,
                reason text not null,
                raw_json text not null,
                primary key (session_id, generation),
                foreign key (session_id) references tape_collector_sessions(session_id)
            );

            create table if not exists tape_subscription_members (
                session_id text not null,
                generation integer not null,
                token_id text not null,
                primary key (session_id, generation, token_id),
                foreign key (session_id, generation)
                    references tape_subscription_generations(session_id, generation),
                foreign key (token_id) references tape_tokens(token_id)
            );

            create table if not exists tape_coverage_intervals (
                id integer primary key autoincrement,
                session_id text not null,
                token_id text not null,
                state text not null,
                started_at_utc text not null,
                ended_at_utc text,
                subscription_generation integer not null,
                reason text,
                gap_id text,
                raw_json text not null
            );

            create index if not exists idx_tape_coverage_token_time
                on tape_coverage_intervals(token_id, started_at_utc, ended_at_utc);

            create table if not exists tape_raw_partitions (
                session_id text not null,
                partition_id text not null,
                path text not null,
                events integer not null,
                bytes_written integer not null,
                first_event_id text,
                last_event_id text,
                closed_at_utc text not null,
                primary key (session_id, partition_id),
                foreign key (session_id) references tape_collector_sessions(session_id)
            );

            create table if not exists tape_collector_metrics (
                id integer primary key autoincrement,
                session_id text not null,
                captured_at_utc text not null,
                messages integer not null,
                events integer not null,
                queue_depth integer not null,
                queue_capacity integer not null,
                queue_high_water integer not null,
                rss_bytes integer not null,
                raw_disk_bytes integer not null,
                receipt_lag_ms real,
                reconnect_attempt integer not null,
                raw_json text not null,
                foreign key (session_id) references tape_collector_sessions(session_id)
            );

            create index if not exists idx_tape_metrics_session_time
                on tape_collector_metrics(session_id, captured_at_utc);

            create table if not exists tape_discovery_refreshes (
                id integer primary key autoincrement,
                session_id text not null,
                attempted_at_utc text not null,
                completed_at_utc text not null,
                status text not null,
                complete integer not null,
                token_count integer not null,
                market_count integer not null,
                warning_count integer not null,
                error text,
                raw_json text not null,
                foreign key (session_id) references tape_collector_sessions(session_id)
            );

            create index if not exists idx_tape_discovery_refreshes_session_time
                on tape_discovery_refreshes(session_id, attempted_at_utc);

            create table if not exists tape_discovery_refresh_members (
                refresh_id integer not null,
                token_id text not null,
                market_id text not null,
                primary key (refresh_id, token_id),
                foreign key (refresh_id) references tape_discovery_refreshes(id),
                foreign key (token_id) references tape_tokens(token_id)
            );

            create table if not exists tape_book_checkpoints (
                checkpoint_id text primary key,
                session_id text not null,
                token_id text not null,
                event_id text not null,
                event_offset integer not null,
                captured_at_utc text not null,
                reconstruction_hash text not null,
                coverage_state text not null,
                raw_json text not null
            );

            create index if not exists idx_tape_checkpoints_token_event
                on tape_book_checkpoints(token_id, event_offset);

            create table if not exists tape_reconstruction_errors (
                id integer primary key autoincrement,
                session_id text not null,
                token_id text not null,
                event_id text,
                receipt_sequence integer not null,
                captured_at_utc text not null,
                reason text not null
            );

            create table if not exists tape_decision_joins (
                decision_id text not null,
                hypothesis_version text not null,
                token_id text not null,
                session_id text not null,
                quote_ready_at_utc text not null,
                first_visible_event_id text,
                first_visible_event_at_utc text,
                coverage_valid integer not null,
                invalid_reason text,
                pre_signal_seconds real not null,
                reconstruction_hash text,
                quote_termination_at_utc text,
                termination_event_id text,
                termination_event_at_utc text,
                termination_reconstruction_hash text,
                tape_observed_through_at_utc text,
                coverage_interval_id integer,
                coverage_started_at_utc text,
                coverage_ended_at_utc text,
                source_type text,
                source_ref text,
                raw_json text not null,
                primary key (decision_id, hypothesis_version)
            );
            """
        )
        self._migrate_session_schema()
        self._migrate_token_schema()
        self._migrate_decision_join_schema()
        self.connection.commit()

    def _migrate_session_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(
                "pragma table_info(tape_collector_sessions)"
            ).fetchall()
        }
        additions = {
            "validation_run_id": "text not null default 'unspecified'",
            "build_fingerprint": "text not null default 'unspecified'",
            "config_fingerprint": "text not null default 'unspecified'",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                self.connection.execute(
                    f"alter table tape_collector_sessions add column {name} {sql_type}"
                )

    def _migrate_token_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute("pragma table_info(tape_tokens)").fetchall()
        }
        if "listing_timestamp_source" not in columns:
            self.connection.execute(
                "alter table tape_tokens add column listing_timestamp_source text not null default 'discovery_fallback'"
            )

    def _migrate_decision_join_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute("pragma table_info(tape_decision_joins)").fetchall()
        }
        additions = {
            "quote_termination_at_utc": "text",
            "termination_event_id": "text",
            "termination_event_at_utc": "text",
            "termination_reconstruction_hash": "text",
            "tape_observed_through_at_utc": "text",
            "coverage_interval_id": "integer",
            "coverage_started_at_utc": "text",
            "coverage_ended_at_utc": "text",
            "source_type": "text",
            "source_ref": "text",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                self.connection.execute(
                    f"alter table tape_decision_joins add column {name} {sql_type}"
                )

    def upsert_tokens(self, entries: list[TokenRegistryEntry]) -> int:
        if not entries:
            return 0
        with self.connection:
            for entry in entries:
                payload = contract_to_dict(entry)
                self.connection.execute(
                    """
                    insert into tape_tokens (
                        token_id, market_id, condition_id, outcome, station,
                        market_date, market_family, lower_bound, upper_bound,
                        sibling_token_id, sibling_market_id, market_end_at_utc,
                        discovered_at_utc, active_from_utc, listing_timestamp_source, active_until_utc,
                        resolution_source, subscription_state, last_health_status,
                        raw_json
                    ) values (
                        :token_id, :market_id, :condition_id, :outcome, :station,
                        :market_date, :market_family, :lower_bound, :upper_bound,
                        :sibling_token_id, :sibling_market_id, :market_end_at_utc,
                        :discovered_at_utc, :active_from_utc, :listing_timestamp_source, :active_until_utc,
                        :resolution_source, :subscription_state, :last_health_status,
                        :raw_json
                    )
                    on conflict(token_id) do update set
                        market_id = excluded.market_id,
                        condition_id = excluded.condition_id,
                        outcome = excluded.outcome,
                        station = excluded.station,
                        market_date = excluded.market_date,
                        market_family = excluded.market_family,
                        lower_bound = excluded.lower_bound,
                        upper_bound = excluded.upper_bound,
                        sibling_token_id = excluded.sibling_token_id,
                        sibling_market_id = excluded.sibling_market_id,
                        market_end_at_utc = excluded.market_end_at_utc,
                        active_from_utc = case
                            when excluded.listing_timestamp_source != 'discovery_fallback'
                                then excluded.active_from_utc
                            when tape_tokens.listing_timestamp_source = 'discovery_fallback'
                                 and excluded.active_from_utc < tape_tokens.active_from_utc
                                then excluded.active_from_utc
                            else tape_tokens.active_from_utc
                        end,
                        listing_timestamp_source = case
                            when excluded.listing_timestamp_source != 'discovery_fallback'
                                then excluded.listing_timestamp_source
                            else tape_tokens.listing_timestamp_source
                        end,
                        resolution_source = excluded.resolution_source,
                        active_until_utc = excluded.active_until_utc,
                        subscription_state = excluded.subscription_state,
                        last_health_status = excluded.last_health_status,
                        raw_json = excluded.raw_json
                    """,
                    {**payload, "raw_json": _json(payload)},
                )
        return len(entries)

    def active_tokens(self) -> list[TokenRegistryEntry]:
        rows = self.connection.execute(
            "select * from tape_tokens where active_until_utc is null order by token_id"
        ).fetchall()
        return [_token_from_row(row) for row in rows]

    def set_subscription_state(self, token_ids: tuple[str, ...], state: str) -> None:
        if not token_ids:
            return
        placeholders = ",".join("?" for _ in token_ids)
        with self.connection:
            self.connection.execute(
                f"update tape_tokens set subscription_state = ? where token_id in ({placeholders})",
                (state, *token_ids),
            )

    def retire_missing_tokens(self, active_token_ids: tuple[str, ...], *, retired_at_utc: str) -> int:
        normalized = tuple(sorted(set(active_token_ids)))
        with self.connection:
            if normalized:
                placeholders = ",".join("?" for _ in normalized)
                cursor = self.connection.execute(
                    f"""
                    update tape_tokens
                    set active_until_utc = ?, subscription_state = 'RETIRED'
                    where active_until_utc is null and token_id not in ({placeholders})
                    """,
                    (retired_at_utc, *normalized),
                )
            else:
                cursor = self.connection.execute(
                    """
                    update tape_tokens
                    set active_until_utc = ?, subscription_state = 'RETIRED'
                    where active_until_utc is null
                    """,
                    (retired_at_utc,),
                )
        return int(cursor.rowcount)

    def start_session(self, session: CollectorSession) -> None:
        payload = contract_to_dict(session)
        with self.connection:
            self.connection.execute(
                """
                insert into tape_collector_sessions (
                    session_id, started_at_utc, started_monotonic_ns,
                    collector_version, hostname, validation_run_id,
                    build_fingerprint, config_fingerprint, finished_at_utc,
                    finish_reason, raw_json
                ) values (
                    :session_id, :started_at_utc, :started_monotonic_ns,
                    :collector_version, :hostname, :validation_run_id,
                    :build_fingerprint, :config_fingerprint, :finished_at_utc,
                    :finish_reason, :raw_json
                )
                """,
                {**payload, "raw_json": _json(payload)},
            )

    def record_discovery_refresh(
        self,
        *,
        session_id: str,
        attempted_at_utc: str,
        completed_at_utc: str,
        complete: bool,
        token_ids_and_markets: tuple[tuple[str, str], ...] = (),
        warnings: tuple[str, ...] = (),
        error: str | None = None,
    ) -> int:
        normalized = tuple(sorted(set(token_ids_and_markets)))
        status = "ERROR" if error else ("COMPLETE" if complete else "INCOMPLETE")
        payload = {
            "session_id": session_id,
            "attempted_at_utc": attempted_at_utc,
            "completed_at_utc": completed_at_utc,
            "status": status,
            "complete": bool(complete),
            "token_count": len(normalized),
            "market_count": len({market_id for _, market_id in normalized}),
            "warnings": list(warnings),
            "error": error,
        }
        with self.connection:
            cursor = self.connection.execute(
                """
                insert into tape_discovery_refreshes (
                    session_id, attempted_at_utc, completed_at_utc, status,
                    complete, token_count, market_count, warning_count, error,
                    raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    attempted_at_utc,
                    completed_at_utc,
                    status,
                    int(complete),
                    payload["token_count"],
                    payload["market_count"],
                    len(warnings),
                    error,
                    _json(payload),
                ),
            )
            refresh_id = int(cursor.lastrowid)
            self.connection.executemany(
                """
                insert into tape_discovery_refresh_members (
                    refresh_id, token_id, market_id
                ) values (?, ?, ?)
                """,
                (
                    (refresh_id, token_id, market_id)
                    for token_id, market_id in normalized
                ),
            )
        return refresh_id

    def finish_session(self, session_id: str, *, finished_at_utc: str, reason: str) -> None:
        with self.connection:
            row = self.connection.execute(
                "select raw_json from tape_collector_sessions where session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            payload = json.loads(row["raw_json"])
            payload.update({"finished_at_utc": finished_at_utc, "finish_reason": reason})
            self.connection.execute(
                """
                update tape_collector_sessions
                set finished_at_utc = ?, finish_reason = ?, raw_json = ?
                where session_id = ?
                """,
                (finished_at_utc, reason, _json(payload), session_id),
            )

    def latest_subscription(self, session_id: str) -> SubscriptionGeneration | None:
        row = self.connection.execute(
            """
            select raw_json from tape_subscription_generations
            where session_id = ? order by generation desc limit 1
            """,
            (session_id,),
        ).fetchone()
        return _subscription_from_json(row["raw_json"]) if row else None

    def reconcile_subscription(
        self,
        session_id: str,
        *,
        token_ids: tuple[str, ...],
        effective_at_utc: str,
        reason: str,
    ) -> SubscriptionGeneration | None:
        normalized = tuple(sorted(set(token_ids)))
        if not normalized:
            raise ValueError("cannot create an empty subscription generation")
        previous = self.latest_subscription(session_id)
        if previous is not None and previous.token_ids == normalized:
            return None
        generation = 1 if previous is None else previous.generation + 1
        item = SubscriptionGeneration(
            session_id=session_id,
            generation=generation,
            effective_at_utc=effective_at_utc,
            token_ids=normalized,
            reason=reason,
        )
        payload = contract_to_dict(item)
        with self.connection:
            self.connection.execute(
                """
                insert into tape_subscription_generations (
                    session_id, generation, effective_at_utc, reason, raw_json
                ) values (?, ?, ?, ?, ?)
                """,
                (session_id, generation, effective_at_utc, reason, _json(payload)),
            )
            self.connection.executemany(
                """
                insert into tape_subscription_members (session_id, generation, token_id)
                values (?, ?, ?)
                """,
                ((session_id, generation, token_id) for token_id in normalized),
            )
        self.set_subscription_state(normalized, "SUBSCRIBED")
        return item

    def insert_coverage_interval(self, interval: CoverageInterval) -> int:
        payload = contract_to_dict(interval)
        with self.connection:
            cursor = self.connection.execute(
                """
                insert into tape_coverage_intervals (
                    session_id, token_id, state, started_at_utc, ended_at_utc,
                    subscription_generation, reason, gap_id, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interval.session_id,
                    interval.token_id,
                    interval.state.value,
                    interval.started_at_utc,
                    interval.ended_at_utc,
                    interval.subscription_generation,
                    interval.reason,
                    interval.gap_id,
                    _json(payload),
                ),
            )
        return int(cursor.lastrowid)

    def transition_coverage(self, interval: CoverageInterval) -> int:
        """Close the prior open interval and append the new token state atomically."""
        payload = contract_to_dict(interval)
        with self.connection:
            self.connection.execute(
                """
                update tape_coverage_intervals set ended_at_utc = ?
                where session_id = ? and token_id = ? and ended_at_utc is null
                """,
                (interval.started_at_utc, interval.session_id, interval.token_id),
            )
            cursor = self.connection.execute(
                """
                insert into tape_coverage_intervals (
                    session_id, token_id, state, started_at_utc, ended_at_utc,
                    subscription_generation, reason, gap_id, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interval.session_id,
                    interval.token_id,
                    interval.state.value,
                    interval.started_at_utc,
                    interval.ended_at_utc,
                    interval.subscription_generation,
                    interval.reason,
                    interval.gap_id,
                    _json(payload),
                ),
            )
        return int(cursor.lastrowid)

    def record_partition(self, session_id: str, stats: SegmentStats, *, closed_at_utc: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                insert into tape_raw_partitions (
                    session_id, partition_id, path, events, bytes_written,
                    first_event_id, last_event_id, closed_at_utc
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(session_id, partition_id) do update set
                    path = excluded.path,
                    events = excluded.events,
                    bytes_written = excluded.bytes_written,
                    first_event_id = excluded.first_event_id,
                    last_event_id = excluded.last_event_id,
                    closed_at_utc = excluded.closed_at_utc
                """,
                (
                    session_id,
                    stats.partition_id,
                    str(stats.path),
                    stats.events,
                    stats.bytes_written,
                    stats.first_event_id,
                    stats.last_event_id,
                    closed_at_utc,
                ),
            )

    def record_metric(self, metric: CollectorMetric) -> int:
        payload = contract_to_dict(metric)
        with self.connection:
            cursor = self.connection.execute(
                """
                insert into tape_collector_metrics (
                    session_id, captured_at_utc, messages, events, queue_depth,
                    queue_capacity, queue_high_water, rss_bytes, raw_disk_bytes,
                    receipt_lag_ms, reconnect_attempt, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metric.session_id,
                    metric.captured_at_utc,
                    metric.messages,
                    metric.events,
                    metric.queue_depth,
                    metric.queue_capacity,
                    metric.queue_high_water,
                    metric.rss_bytes,
                    metric.raw_disk_bytes,
                    metric.receipt_lag_ms,
                    metric.reconnect_attempt,
                    _json(payload),
                ),
            )
        return int(cursor.lastrowid)

    def record_checkpoint(self, checkpoint: BookCheckpoint) -> None:
        payload = contract_to_dict(checkpoint)
        with self.connection:
            self.connection.execute(
                """
                insert or replace into tape_book_checkpoints (
                    checkpoint_id, session_id, token_id, event_id, event_offset,
                    captured_at_utc, reconstruction_hash, coverage_state, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id, checkpoint.session_id, checkpoint.token_id,
                    checkpoint.event_id, checkpoint.event_offset, checkpoint.captured_at_utc,
                    checkpoint.reconstruction_hash, checkpoint.coverage_state.value, _json(payload),
                ),
            )

    def record_reconstruction_error(
        self,
        *,
        session_id: str,
        token_id: str,
        event_id: str | None,
        receipt_sequence: int,
        captured_at_utc: str,
        reason: str,
    ) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                insert into tape_reconstruction_errors (
                    session_id, token_id, event_id, receipt_sequence, captured_at_utc, reason
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (session_id, token_id, event_id, receipt_sequence, captured_at_utc, reason),
            )
        return int(cursor.lastrowid)

    def record_decision_join(self, item: DecisionTapeJoin) -> None:
        payload = contract_to_dict(item)
        with self.connection:
            self.connection.execute(
                """
                insert or replace into tape_decision_joins (
                    decision_id, hypothesis_version, token_id, session_id,
                    quote_ready_at_utc, first_visible_event_id,
                    first_visible_event_at_utc, coverage_valid, invalid_reason,
                    pre_signal_seconds, reconstruction_hash,
                    quote_termination_at_utc, termination_event_id,
                    termination_event_at_utc, termination_reconstruction_hash,
                    tape_observed_through_at_utc, coverage_interval_id,
                    coverage_started_at_utc, coverage_ended_at_utc,
                    source_type, source_ref, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.decision_id, item.hypothesis_version, item.token_id, item.session_id,
                    item.quote_ready_at_utc, item.first_visible_event_id,
                    item.first_visible_event_at_utc, int(item.coverage_valid), item.invalid_reason,
                    item.pre_signal_seconds, item.reconstruction_hash,
                    item.quote_termination_at_utc, item.termination_event_id,
                    item.termination_event_at_utc, item.termination_reconstruction_hash,
                    item.tape_observed_through_at_utc, item.coverage_interval_id,
                    item.coverage_started_at_utc, item.coverage_ended_at_utc,
                    item.source_type, item.source_ref, _json(payload),
                ),
            )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _token_from_row(row: sqlite3.Row) -> TokenRegistryEntry:
    return TokenRegistryEntry(
        token_id=row["token_id"],
        market_id=row["market_id"],
        condition_id=row["condition_id"],
        outcome=TokenOutcome(row["outcome"]),
        station=row["station"],
        market_date=row["market_date"],
        market_family=row["market_family"],
        lower_bound=row["lower_bound"],
        upper_bound=row["upper_bound"],
        sibling_token_id=row["sibling_token_id"],
        sibling_market_id=row["sibling_market_id"],
        market_end_at_utc=row["market_end_at_utc"],
        discovered_at_utc=row["discovered_at_utc"],
        active_from_utc=row["active_from_utc"],
        listing_timestamp_source=row["listing_timestamp_source"],
        active_until_utc=row["active_until_utc"],
        resolution_source=row["resolution_source"],
        subscription_state=row["subscription_state"],
        last_health_status=row["last_health_status"],
    )


def _subscription_from_json(value: str) -> SubscriptionGeneration:
    payload = json.loads(value)
    payload["token_ids"] = tuple(payload["token_ids"])
    return SubscriptionGeneration(**payload)
