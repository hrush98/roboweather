from __future__ import annotations

import fcntl
import json
import math
import resource
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

from weather_trader.discovery.materializer import (
    _lifecycle_horizon,
    _local_hhmm,
    _selected_value,
    _source_reasons,
    _timestamp,
)
from weather_trader.pricing.contracts import stable_hash
from weather_trader.tape.replay import CausalBookProvider, sweep_asks


DECISION_CACHE_SCHEMA_VERSION = 1


class DecisionCacheLockedError(RuntimeError):
    pass


class BookProvider(Protocol):
    def book_at(
        self,
        token_id: str,
        ready: datetime,
        *,
        pre_signal_seconds: int,
    ) -> tuple[dict[str, Any] | None, str | None]: ...


BookProviderFactory = Callable[[sqlite3.Connection, Iterable[str]], BookProvider]
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class DecisionCacheContract:
    """Versioned causal timing and executable-book summary contract.

    Snapshot rows are persisted serially, so their exact write timestamps differ
    even when multiple models belong to one collection cycle.  Quote readiness
    is therefore the ceiling of the source availability timestamp to a declared
    bucket, followed by the declared execution latency.  The ceiling is causal:
    it can delay a row but can never expose a book before that model output was
    available.
    """

    replay_version: str = "causal_l2_replay_v1"
    execution_version: str = "immediate_taker_summary_v1"
    availability_bucket_seconds: int = 60
    latency_ms: int = 250
    pre_signal_seconds: int = 60
    price_caps: tuple[float, ...] = (0.35, 0.50)
    target_costs_usd: tuple[float, ...] = (25.0, 50.0, 100.0)

    def __post_init__(self) -> None:
        if not self.replay_version or not self.execution_version:
            raise ValueError("decision cache versions must be nonempty")
        if self.availability_bucket_seconds < 1:
            raise ValueError("availability bucket must be positive")
        if self.latency_ms < 0 or self.pre_signal_seconds < 0:
            raise ValueError("latency and pre-signal coverage must be nonnegative")
        if not self.price_caps or any(not 0 < value <= 1 for value in self.price_caps):
            raise ValueError("price caps must be inside (0, 1]")
        if tuple(sorted(set(self.price_caps))) != self.price_caps:
            raise ValueError("price caps must be unique and increasing")
        if not self.target_costs_usd or any(value <= 0 for value in self.target_costs_usd):
            raise ValueError("target costs must be positive")
        if tuple(sorted(set(self.target_costs_usd))) != self.target_costs_usd:
            raise ValueError("target costs must be unique and increasing")

    @property
    def contract_hash(self) -> str:
        return stable_hash({"schema_version": DECISION_CACHE_SCHEMA_VERSION, **asdict(self)})


@dataclass(frozen=True)
class DerivedDecision:
    decision_id: str
    token_id: str
    selected_market_id: str
    outcome: str
    quote_ready_timestamp_utc: str
    source_rejection_reason: str | None


def quote_ready_timestamp(source_timestamp: str, contract: DecisionCacheContract) -> datetime:
    available = _timestamp(source_timestamp)
    if available is None:
        raise ValueError(f"invalid source availability timestamp: {source_timestamp!r}")
    bucket_us = contract.availability_bucket_seconds * 1_000_000
    epoch_us = int(available.timestamp() * 1_000_000)
    ceiling_us = ((epoch_us + bucket_us - 1) // bucket_us) * bucket_us
    bucketed = datetime.fromtimestamp(ceiling_us / 1_000_000, tz=timezone.utc)
    return bucketed + timedelta(milliseconds=contract.latency_ms)


def decision_identity(
    *,
    token_id: str,
    selected_market_id: str,
    outcome: str,
    quote_ready: datetime,
    contract: DecisionCacheContract,
) -> str:
    payload = {
        "token_id": token_id,
        "selected_market_id": selected_market_id,
        "outcome": outcome,
        "quote_ready_timestamp_utc": quote_ready.isoformat(),
        "latency_ms": contract.latency_ms,
        "pre_signal_seconds": contract.pre_signal_seconds,
        "availability_bucket_seconds": contract.availability_bucket_seconds,
        "replay_version": contract.replay_version,
        "execution_version": contract.execution_version,
    }
    return f"p3d_decision_{stable_hash(payload)[:32]}"


class ExecutableDecisionCache:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("pragma foreign_keys=ON")
        self.connection.execute("pragma journal_mode=WAL")
        self.connection.execute("pragma synchronous=FULL")
        self.connection.execute("pragma busy_timeout=30000")
        self._create_schema()

    def __enter__(self) -> ExecutableDecisionCache:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            create table if not exists cache_metadata (
                key text primary key,
                value text not null
            );

            create table if not exists cache_contracts (
                contract_hash text primary key,
                contract_json text not null,
                created_at_utc text not null
            );

            create table if not exists executable_decisions (
                decision_id text primary key,
                contract_hash text not null references cache_contracts(contract_hash),
                token_id text not null,
                selected_market_id text not null,
                outcome text not null,
                quote_ready_timestamp_utc text not null,
                status text not null check(status in ('PENDING','SUCCESS','REJECTED')),
                rejection_reason text,
                best_bid real,
                best_ask real,
                spread real,
                depth_at_best_ask real,
                ask_levels_json text,
                execution_summaries_json text,
                tape_session_id text,
                coverage_interval_id integer,
                checkpoint_event_id text,
                checkpoint_captured_at_utc text,
                checkpoint_reconstruction_hash text,
                partition_ids_json text,
                reconstruction_hash text,
                replay_provenance_json text,
                result_hash text,
                replayed_at_utc text,
                created_at_utc text not null,
                updated_at_utc text not null,
                unique(
                    contract_hash, token_id, selected_market_id, outcome,
                    quote_ready_timestamp_utc
                )
            );

            create table if not exists model_decision_mappings (
                mapping_id text primary key,
                contract_hash text not null references cache_contracts(contract_hash),
                source_snapshot_id integer not null,
                decision_id text not null references executable_decisions(decision_id),
                source_snapshot_payload_hash text not null,
                source_availability_timestamp_utc text not null,
                decision_time_utc text not null,
                latest_observation_time_utc text not null,
                station text not null,
                market_date text not null,
                market_family text not null,
                model_id text not null,
                strategy_bucket text not null,
                observation_delay_bucket text not null,
                local_decision_hhmm text not null,
                lifecycle_horizon text not null,
                selected_bucket text not null,
                selected_side text not null,
                raw_model_fair real,
                raw_model_edge real,
                snapshot_entry_price real,
                high_conviction integer not null,
                observation_age_minutes real,
                source_reasons_json text not null,
                mapping_hash text not null,
                created_at_utc text not null,
                unique(contract_hash, source_snapshot_id)
            );

            create table if not exists cache_refresh_state (
                contract_hash text not null references cache_contracts(contract_hash),
                source_scope_hash text not null,
                source_start_date text not null,
                last_snapshot_id integer not null,
                last_sealed_watermark integer not null,
                updated_at_utc text not null,
                primary key(contract_hash, source_scope_hash)
            );

            create table if not exists cache_refresh_runs (
                refresh_id text primary key,
                contract_hash text not null references cache_contracts(contract_hash),
                source_scope_hash text not null,
                sealed_research_watermark integer not null,
                status text not null check(status in ('RUNNING','COMPLETED','FAILED')),
                started_at_utc text not null,
                completed_at_utc text,
                diagnostics_json text,
                error text
            );

            create table if not exists decision_enrichments (
                decision_id text not null references executable_decisions(decision_id),
                enrichment_kind text not null,
                enrichment_version text not null,
                source_watermark text not null,
                status text not null,
                value_json text,
                result_hash text not null,
                updated_at_utc text not null,
                primary key(decision_id, enrichment_kind, enrichment_version)
            );

            create index if not exists idx_decisions_contract_status_ready
                on executable_decisions(contract_hash, status, quote_ready_timestamp_utc, decision_id);
            create index if not exists idx_mappings_contract_date_model
                on model_decision_mappings(contract_hash, market_date, model_id, source_snapshot_id);
            create index if not exists idx_mappings_decision
                on model_decision_mappings(decision_id);
            """
        )
        existing = self.connection.execute(
            "select value from cache_metadata where key='schema_version'"
        ).fetchone()
        if existing is not None and int(existing["value"]) != DECISION_CACHE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported decision cache schema {existing['value']}; "
                f"expected {DECISION_CACHE_SCHEMA_VERSION}"
            )
        self.connection.execute(
            "insert or ignore into cache_metadata(key,value) values('schema_version',?)",
            (str(DECISION_CACHE_SCHEMA_VERSION),),
        )
        self.connection.commit()

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        lock_path = Path(f"{self.path}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DecisionCacheLockedError(f"decision cache writer is locked: {lock_path}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def refresh(
        self,
        research: sqlite3.Connection,
        tape: sqlite3.Connection,
        *,
        contract: DecisionCacheContract,
        source_start_date: str,
        sealed_research_watermark: int | None = None,
        mapping_batch_size: int = 2_000,
        replay_batch_size: int = 100,
        book_provider_factory: BookProviderFactory | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if mapping_batch_size < 1 or replay_batch_size < 1:
            raise ValueError("cache batch sizes must be positive")
        datetime.fromisoformat(source_start_date)
        research.row_factory = sqlite3.Row
        tape.row_factory = sqlite3.Row
        self._validate_research_schema(research)
        watermark = (
            int(sealed_research_watermark)
            if sealed_research_watermark is not None
            else int(research.execute("select coalesce(max(id),0) from prediction_snapshots").fetchone()[0])
        )
        if watermark < 0:
            raise ValueError("sealed research watermark must be nonnegative")

        source_scope_hash = stable_hash({"source_start_date": source_start_date})
        started_wall = time.monotonic()
        started_at = _now()
        refresh_id = f"p3d_cache_refresh_{stable_hash({'contract': contract.contract_hash, 'scope': source_scope_hash, 'watermark': watermark, 'started': started_at})[:24]}"
        diagnostics: Counter[str] = Counter()
        diagnostics["SEALED_RESEARCH_WATERMARK"] = watermark

        with self.writer_lock():
            self._register_contract(contract, started_at)
            self.connection.execute(
                """insert into cache_refresh_runs(
                       refresh_id,contract_hash,source_scope_hash,sealed_research_watermark,
                       status,started_at_utc
                   ) values(?,?,?,?,?,?)""",
                (refresh_id, contract.contract_hash, source_scope_hash, watermark, "RUNNING", started_at),
            )
            self.connection.commit()
            try:
                last_snapshot_id = self._last_snapshot_id(contract.contract_hash, source_scope_hash)
                while last_snapshot_id < watermark:
                    rows = self._load_source_batch(
                        research,
                        source_start_date=source_start_date,
                        after_snapshot_id=last_snapshot_id,
                        sealed_research_watermark=watermark,
                        limit=mapping_batch_size,
                    )
                    if not rows:
                        # Rows below the watermark may all precede source_start_date.
                        last_snapshot_id = watermark
                        self._save_refresh_state(
                            contract.contract_hash,
                            source_scope_hash,
                            source_start_date,
                            last_snapshot_id,
                            watermark,
                        )
                        self.connection.commit()
                        break
                    token_lookup = self._token_lookup(tape, rows)
                    batch_counts = self._persist_mapping_batch(
                        rows,
                        token_lookup,
                        contract=contract,
                        now_utc=_now(),
                    )
                    diagnostics.update(batch_counts)
                    last_snapshot_id = int(rows[-1]["id"])
                    self._save_refresh_state(
                        contract.contract_hash,
                        source_scope_hash,
                        source_start_date,
                        last_snapshot_id,
                        watermark,
                    )
                    self.connection.commit()
                    if progress:
                        progress({
                            "stage": "MAPPINGS",
                            "last_snapshot_id": last_snapshot_id,
                            "sealed_research_watermark": watermark,
                            **dict(batch_counts),
                        })

                pending_rows = self.connection.execute(
                    """select token_id from executable_decisions
                       where contract_hash=? and status='PENDING'
                       order by token_id""",
                    (contract.contract_hash,),
                ).fetchall()
                target_tokens = {
                    str(row["token_id"])
                    for row in pending_rows
                    if not str(row["token_id"]).startswith("UNCATALOGED:")
                    and not str(row["token_id"]).startswith("INVALID:")
                }
                provider_factory = book_provider_factory or _default_book_provider
                provider = provider_factory(tape, target_tokens)
                while True:
                    pending = self.connection.execute(
                        """select * from executable_decisions
                           where contract_hash=? and status='PENDING'
                           order by quote_ready_timestamp_utc,decision_id limit ?""",
                        (contract.contract_hash, replay_batch_size),
                    ).fetchall()
                    if not pending:
                        break
                    for decision in pending:
                        outcome = self._replay_decision(decision, provider, contract)
                        self._store_decision_outcome(str(decision["decision_id"]), outcome, _now())
                        diagnostics["REPLAY_CALLS"] += int(outcome["provider_called"])
                        diagnostics[f"DECISION_{outcome['status']}"] += 1
                        if outcome.get("rejection_reason"):
                            diagnostics[f"REJECTION:{outcome['rejection_reason']}"] += 1
                    self.connection.commit()
                    if progress:
                        progress({
                            "stage": "REPLAY",
                            "completed": diagnostics["DECISION_SUCCESS"] + diagnostics["DECISION_REJECTED"],
                            "replay_calls": diagnostics["REPLAY_CALLS"],
                        })

                diagnostics["PENDING_DECISIONS"] = int(self.connection.execute(
                    "select count(*) from executable_decisions where contract_hash=? and status='PENDING'",
                    (contract.contract_hash,),
                ).fetchone()[0])
                diagnostics["TOTAL_MAPPINGS"] = int(self.connection.execute(
                    "select count(*) from model_decision_mappings where contract_hash=?",
                    (contract.contract_hash,),
                ).fetchone()[0])
                diagnostics["TOTAL_DECISIONS"] = int(self.connection.execute(
                    "select count(*) from executable_decisions where contract_hash=?",
                    (contract.contract_hash,),
                ).fetchone()[0])
                diagnostics["ELAPSED_SECONDS"] = round(time.monotonic() - started_wall, 6)
                diagnostics["PEAK_RSS_KIB"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                result = {
                    "refresh_id": refresh_id,
                    "status": "COMPLETED",
                    "contract_hash": contract.contract_hash,
                    "source_scope_hash": source_scope_hash,
                    "diagnostics": dict(sorted(diagnostics.items())),
                    "funded_authorization": False,
                }
                self.connection.execute(
                    """update cache_refresh_runs set status='COMPLETED',completed_at_utc=?,diagnostics_json=?
                       where refresh_id=?""",
                    (_now(), _json(result["diagnostics"]), refresh_id),
                )
                self.connection.commit()
                return result
            except BaseException as exc:
                self.connection.rollback()
                diagnostics["ELAPSED_SECONDS"] = round(time.monotonic() - started_wall, 6)
                self.connection.execute(
                    """update cache_refresh_runs set status='FAILED',completed_at_utc=?,diagnostics_json=?,error=?
                       where refresh_id=?""",
                    (_now(), _json(dict(sorted(diagnostics.items()))), f"{type(exc).__name__}: {exc}", refresh_id),
                )
                self.connection.commit()
                raise

    def table_counts(self) -> dict[str, int]:
        return {
            table: int(self.connection.execute(f"select count(*) from {table}").fetchone()[0])
            for table in (
                "cache_contracts",
                "executable_decisions",
                "model_decision_mappings",
                "cache_refresh_state",
                "cache_refresh_runs",
                "decision_enrichments",
            )
        }

    def decision_rows(self, contract_hash: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "select * from executable_decisions where contract_hash=? order by decision_id",
            (contract_hash,),
        ).fetchall()

    def _register_contract(self, contract: DecisionCacheContract, now_utc: str) -> None:
        payload = _json(asdict(contract))
        existing = self.connection.execute(
            "select contract_json from cache_contracts where contract_hash=?",
            (contract.contract_hash,),
        ).fetchone()
        if existing is not None and str(existing["contract_json"]) != payload:
            raise ValueError("decision cache contract hash collision")
        self.connection.execute(
            "insert or ignore into cache_contracts(contract_hash,contract_json,created_at_utc) values(?,?,?)",
            (contract.contract_hash, payload, now_utc),
        )

    def _last_snapshot_id(self, contract_hash: str, source_scope_hash: str) -> int:
        row = self.connection.execute(
            """select last_snapshot_id from cache_refresh_state
               where contract_hash=? and source_scope_hash=?""",
            (contract_hash, source_scope_hash),
        ).fetchone()
        return int(row["last_snapshot_id"]) if row else 0

    def _save_refresh_state(
        self,
        contract_hash: str,
        source_scope_hash: str,
        source_start_date: str,
        last_snapshot_id: int,
        watermark: int,
    ) -> None:
        self.connection.execute(
            """insert into cache_refresh_state(
                   contract_hash,source_scope_hash,source_start_date,last_snapshot_id,
                   last_sealed_watermark,updated_at_utc
               ) values(?,?,?,?,?,?)
               on conflict(contract_hash,source_scope_hash) do update set
                   last_snapshot_id=excluded.last_snapshot_id,
                   last_sealed_watermark=excluded.last_sealed_watermark,
                   updated_at_utc=excluded.updated_at_utc""",
            (contract_hash, source_scope_hash, source_start_date, last_snapshot_id, watermark, _now()),
        )

    @staticmethod
    def _validate_research_schema(research: sqlite3.Connection) -> None:
        required = {
            "id", "timestamp", "station", "market_date", "decision_time_utc",
            "decision_time_local", "latest_obs_time_utc", "obs_age_minutes",
            "obs_delay_bucket", "strategy_bucket", "selected_market_id",
            "selected_bucket", "selected_side", "selected_edge", "selected_fair_yes",
            "selected_fair_no", "selected_yes_ask", "selected_no_ask", "high_conviction",
            "model_name", "market_family", "raw_json",
        }
        columns = {str(row[1]) for row in research.execute("pragma table_info(prediction_snapshots)")}
        missing = sorted(required - columns)
        if missing:
            raise ValueError(f"prediction_snapshots lacks decision-cache columns: {', '.join(missing)}")

    @staticmethod
    def _load_source_batch(
        research: sqlite3.Connection,
        *,
        source_start_date: str,
        after_snapshot_id: int,
        sealed_research_watermark: int,
        limit: int,
    ) -> list[sqlite3.Row]:
        return list(research.execute(
            """select * from prediction_snapshots
               where id>? and id<=? and market_date>=?
                 and selected_market_id is not null and selected_bucket is not null
               order by id limit ?""",
            (after_snapshot_id, sealed_research_watermark, source_start_date, limit),
        ))

    @staticmethod
    def _token_lookup(
        tape: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> dict[tuple[str, str], str]:
        market_ids = sorted({str(row["selected_market_id"]) for row in rows})
        lookup: dict[tuple[str, str], str] = {}
        for offset in range(0, len(market_ids), 800):
            chunk = market_ids[offset:offset + 800]
            placeholders = ",".join("?" for _ in chunk)
            for token in tape.execute(
                f"select market_id,outcome,token_id from tape_tokens where market_id in ({placeholders})",
                chunk,
            ):
                lookup[(str(token["market_id"]), str(token["outcome"]).upper())] = str(token["token_id"])
        return lookup

    def _persist_mapping_batch(
        self,
        rows: list[sqlite3.Row],
        token_lookup: dict[tuple[str, str], str],
        *,
        contract: DecisionCacheContract,
        now_utc: str,
    ) -> Counter[str]:
        counts: Counter[str] = Counter()
        for source in rows:
            row = dict(source)
            counts["SOURCE_ROWS_SCANNED"] += 1
            side = str(row.get("selected_side") or "")
            outcome = "YES" if side == "BUY_YES" else "NO" if side == "BUY_NO" else "INVALID"
            market_id = str(row.get("selected_market_id") or "")
            token = token_lookup.get((market_id, outcome))
            source_reasons = _source_reasons(row)
            source_rejection: str | None = None
            try:
                ready = quote_ready_timestamp(str(row.get("timestamp") or ""), contract)
            except ValueError:
                ready = datetime(1970, 1, 1, tzinfo=timezone.utc)
                source_reasons.append("INVALID_SOURCE_AVAILABILITY_TIMESTAMP")
            if outcome == "INVALID":
                source_rejection = "SOURCE:INVALID_SELECTED_SIDE"
                token = f"INVALID:{market_id}:{side or 'EMPTY'}"
            elif token is None:
                source_rejection = "TOKEN_NOT_CATALOGED"
                token = f"UNCATALOGED:{market_id}:{outcome}"
            elif source_reasons:
                source_rejection = f"SOURCE:{sorted(set(source_reasons))[0]}"
            decision_id = decision_identity(
                token_id=token,
                selected_market_id=market_id,
                outcome=outcome,
                quote_ready=ready,
                contract=contract,
            )
            existed = self.connection.execute(
                "select status from executable_decisions where decision_id=?",
                (decision_id,),
            ).fetchone()
            initial_status = "REJECTED" if source_rejection else "PENDING"
            initial_hash = stable_hash({
                "decision_id": decision_id,
                "status": initial_status,
                "rejection_reason": source_rejection,
            }) if source_rejection else None
            self.connection.execute(
                """insert or ignore into executable_decisions(
                       decision_id,contract_hash,token_id,selected_market_id,outcome,
                       quote_ready_timestamp_utc,status,rejection_reason,result_hash,
                       created_at_utc,updated_at_utc
                   ) values(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id, contract.contract_hash, token, market_id, outcome,
                    ready.isoformat(), initial_status, source_rejection, initial_hash,
                    now_utc, now_utc,
                ),
            )
            counts["DECISION_CACHE_HITS" if existed else "UNIQUE_DECISIONS_CREATED"] += 1

            snapshot_id = int(row["id"])
            model_payload = {
                "contract_hash": contract.contract_hash,
                "source_snapshot_id": snapshot_id,
                "decision_id": decision_id,
                "source_snapshot_payload_hash": stable_hash(str(row.get("raw_json") or "")),
                "source_availability_timestamp_utc": str(row.get("timestamp") or ""),
                "decision_time_utc": str(row.get("decision_time_utc") or ""),
                "latest_observation_time_utc": str(row.get("latest_obs_time_utc") or ""),
                "station": str(row.get("station") or ""),
                "market_date": str(row.get("market_date") or ""),
                "market_family": str(row.get("market_family") or "HIGH_TEMP"),
                "model_id": str(row.get("model_name") or ""),
                "strategy_bucket": str(row.get("strategy_bucket") or ""),
                "observation_delay_bucket": str(row.get("obs_delay_bucket") or ""),
                "local_decision_hhmm": _local_hhmm(row.get("decision_time_local")),
                "lifecycle_horizon": _lifecycle_horizon(row.get("decision_time_local"), row.get("market_date")),
                "selected_bucket": str(row.get("selected_bucket") or ""),
                "selected_side": side,
                "raw_model_fair": _selected_value(row, "selected_fair_yes", "selected_fair_no"),
                "raw_model_edge": _finite(row.get("selected_edge")),
                "snapshot_entry_price": _selected_value(row, "selected_yes_ask", "selected_no_ask"),
                "high_conviction": int(bool(row.get("high_conviction"))),
                "observation_age_minutes": _finite(row.get("obs_age_minutes")),
                "source_reasons_json": _json(sorted(set(source_reasons))),
            }
            mapping_hash = stable_hash(model_payload)
            mapping_id = f"p3d_mapping_{stable_hash({'contract': contract.contract_hash, 'snapshot_id': snapshot_id})[:28]}"
            cursor = self.connection.execute(
                """insert or ignore into model_decision_mappings(
                       mapping_id,contract_hash,source_snapshot_id,decision_id,
                       source_snapshot_payload_hash,source_availability_timestamp_utc,
                       decision_time_utc,latest_observation_time_utc,station,market_date,
                       market_family,model_id,strategy_bucket,observation_delay_bucket,
                       local_decision_hhmm,lifecycle_horizon,selected_bucket,selected_side,
                       raw_model_fair,raw_model_edge,snapshot_entry_price,high_conviction,
                       observation_age_minutes,source_reasons_json,mapping_hash,created_at_utc
                   ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    mapping_id,
                    model_payload["contract_hash"],
                    model_payload["source_snapshot_id"],
                    model_payload["decision_id"],
                    model_payload["source_snapshot_payload_hash"],
                    model_payload["source_availability_timestamp_utc"],
                    model_payload["decision_time_utc"],
                    model_payload["latest_observation_time_utc"],
                    model_payload["station"],
                    model_payload["market_date"],
                    model_payload["market_family"],
                    model_payload["model_id"],
                    model_payload["strategy_bucket"],
                    model_payload["observation_delay_bucket"],
                    model_payload["local_decision_hhmm"],
                    model_payload["lifecycle_horizon"],
                    model_payload["selected_bucket"],
                    model_payload["selected_side"],
                    model_payload["raw_model_fair"],
                    model_payload["raw_model_edge"],
                    model_payload["snapshot_entry_price"],
                    model_payload["high_conviction"],
                    model_payload["observation_age_minutes"],
                    model_payload["source_reasons_json"],
                    mapping_hash,
                    now_utc,
                ),
            )
            counts["MAPPINGS_INSERTED" if cursor.rowcount else "MAPPINGS_ALREADY_PRESENT"] += 1
        return counts

    @staticmethod
    def _replay_decision(
        decision: sqlite3.Row,
        provider: BookProvider,
        contract: DecisionCacheContract,
    ) -> dict[str, Any]:
        token_id = str(decision["token_id"])
        if token_id.startswith("UNCATALOGED:") or token_id.startswith("INVALID:"):
            return {
                "status": "REJECTED",
                "rejection_reason": str(decision["rejection_reason"] or "INVALID_TOKEN_IDENTITY"),
                "provider_called": False,
            }
        ready = datetime.fromisoformat(str(decision["quote_ready_timestamp_utc"]))
        book, reason = provider.book_at(
            token_id,
            ready,
            pre_signal_seconds=contract.pre_signal_seconds,
        )
        if reason or book is None:
            return {
                "status": "REJECTED",
                "rejection_reason": f"TAPE:{reason or 'UNKNOWN_REPLAY_FAILURE'}",
                "provider_called": True,
            }
        asks = tuple(sorted((float(price), float(size)) for price, size in book.get("asks", {}).items()))
        bids = tuple(sorted((float(price), float(size)) for price, size in book.get("bids", {}).items()))
        if not asks:
            return {"status": "REJECTED", "rejection_reason": "TAPE:NO_ASKS", "provider_called": True}
        best_ask = asks[0][0]
        best_bid = bids[-1][0] if bids else None
        if best_bid is not None and best_bid > best_ask + 1e-12:
            return {"status": "REJECTED", "rejection_reason": "TAPE:CROSSED_BOOK", "provider_called": True}
        summaries: dict[str, dict[str, float | None]] = {}
        for cap in contract.price_caps:
            for target in contract.target_costs_usd:
                cost, shares, vwap = sweep_asks(asks, price_cap=cap, target_cost=target)
                summaries[f"cap={cap:.8f}|target={target:.8f}"] = {
                    "cost_usd": cost,
                    "shares": shares,
                    "vwap": vwap,
                    "fill_fraction": min(cost / target, 1.0),
                }
        provenance = {
            "actual_fill_status": "UNAVAILABLE_PUBLIC_TAPE_COUNTERFACTUAL",
            "checkpoint_age_s": book.get("checkpoint_age_s"),
            "replay_version": contract.replay_version,
            "execution_version": contract.execution_version,
            "pre_signal_seconds": contract.pre_signal_seconds,
        }
        payload = {
            "status": "SUCCESS",
            "rejection_reason": None,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid if best_bid is not None else None,
            "depth_at_best_ask": asks[0][1],
            "ask_levels_json": _json(asks),
            "execution_summaries_json": _json(summaries),
            "tape_session_id": str(book.get("session_id")) if book.get("session_id") else None,
            "coverage_interval_id": book.get("coverage_interval_id"),
            "checkpoint_event_id": book.get("checkpoint_event_id"),
            "checkpoint_captured_at_utc": book.get("checkpoint_captured_at_utc"),
            "checkpoint_reconstruction_hash": book.get("checkpoint_reconstruction_hash"),
            "partition_ids_json": _json(book.get("partition_ids") or ()),
            "reconstruction_hash": book.get("reconstruction_hash"),
            "replay_provenance_json": _json(provenance),
            "provider_called": True,
        }
        payload["result_hash"] = stable_hash({
            key: value for key, value in payload.items() if key not in {"provider_called", "result_hash"}
        })
        return payload

    def _store_decision_outcome(self, decision_id: str, outcome: dict[str, Any], now_utc: str) -> None:
        if outcome["status"] == "REJECTED":
            result_hash = stable_hash({
                "decision_id": decision_id,
                "status": "REJECTED",
                "rejection_reason": outcome["rejection_reason"],
            })
            self.connection.execute(
                """update executable_decisions set
                       status='REJECTED',rejection_reason=?,result_hash=?,replayed_at_utc=?,updated_at_utc=?
                   where decision_id=?""",
                (outcome["rejection_reason"], result_hash, now_utc, now_utc, decision_id),
            )
            return
        self.connection.execute(
            """update executable_decisions set
                   status='SUCCESS',rejection_reason=null,best_bid=?,best_ask=?,spread=?,
                   depth_at_best_ask=?,ask_levels_json=?,execution_summaries_json=?,
                   tape_session_id=?,coverage_interval_id=?,checkpoint_event_id=?,
                   checkpoint_captured_at_utc=?,checkpoint_reconstruction_hash=?,
                   partition_ids_json=?,reconstruction_hash=?,replay_provenance_json=?,
                   result_hash=?,replayed_at_utc=?,updated_at_utc=?
               where decision_id=?""",
            (
                outcome["best_bid"], outcome["best_ask"], outcome["spread"],
                outcome["depth_at_best_ask"], outcome["ask_levels_json"],
                outcome["execution_summaries_json"], outcome["tape_session_id"],
                outcome["coverage_interval_id"], outcome["checkpoint_event_id"],
                outcome["checkpoint_captured_at_utc"], outcome["checkpoint_reconstruction_hash"],
                outcome["partition_ids_json"], outcome["reconstruction_hash"],
                outcome["replay_provenance_json"], outcome["result_hash"], now_utc, now_utc,
                decision_id,
            ),
        )


def benchmark_decision_grain(
    research: sqlite3.Connection,
    tape: sqlite3.Connection,
    *,
    contract: DecisionCacheContract,
    source_start_date: str,
    sealed_research_watermark: int | None = None,
    batch_size: int = 10_000,
) -> dict[str, Any]:
    """Measure replay cardinality without reconstructing or writing any book."""
    if batch_size < 1:
        raise ValueError("benchmark batch size must be positive")
    research.row_factory = sqlite3.Row
    tape.row_factory = sqlite3.Row
    ExecutableDecisionCache._validate_research_schema(research)
    watermark = (
        int(sealed_research_watermark)
        if sealed_research_watermark is not None
        else int(research.execute("select coalesce(max(id),0) from prediction_snapshots").fetchone()[0])
    )
    started = time.monotonic()
    after_snapshot_id = 0
    exact_keys: set[str] = set()
    bucketed_keys: set[str] = set()
    provider_keys: set[str] = set()
    counts: Counter[str] = Counter()
    while after_snapshot_id < watermark:
        rows = ExecutableDecisionCache._load_source_batch(
            research,
            source_start_date=source_start_date,
            after_snapshot_id=after_snapshot_id,
            sealed_research_watermark=watermark,
            limit=batch_size,
        )
        if not rows:
            break
        lookup = ExecutableDecisionCache._token_lookup(tape, rows)
        for source in rows:
            row = dict(source)
            counts["raw_model_rows"] += 1
            side = str(row.get("selected_side") or "")
            outcome = "YES" if side == "BUY_YES" else "NO" if side == "BUY_NO" else "INVALID"
            market_id = str(row.get("selected_market_id") or "")
            token = lookup.get((market_id, outcome))
            if token is None:
                counts["uncataloged_or_invalid_rows"] += 1
                token = f"UNCATALOGED:{market_id}:{outcome}"
            try:
                ready = quote_ready_timestamp(str(row.get("timestamp") or ""), contract)
                exact_ready = _timestamp(str(row.get("timestamp") or ""))
                if exact_ready is None:
                    raise ValueError("invalid source timestamp")
                exact_ready += timedelta(milliseconds=contract.latency_ms)
            except ValueError:
                counts["invalid_source_timestamp_rows"] += 1
                continue
            exact_keys.add(stable_hash({
                "token_id": token,
                "selected_market_id": market_id,
                "outcome": outcome,
                "quote_ready_timestamp_utc": exact_ready.isoformat(),
            }))
            bucketed = decision_identity(
                token_id=token,
                selected_market_id=market_id,
                outcome=outcome,
                quote_ready=ready,
                contract=contract,
            )
            bucketed_keys.add(bucketed)
            if not token.startswith("UNCATALOGED:") and outcome != "INVALID":
                provider_keys.add(bucketed)
        after_snapshot_id = int(rows[-1]["id"])
    raw_rows = counts["raw_model_rows"]
    return {
        "status": "COMPLETED",
        "contract_hash": contract.contract_hash,
        "sealed_research_watermark": watermark,
        "source_start_date": source_start_date,
        "raw_model_rows": raw_rows,
        "legacy_exact_timestamp_decisions": len(exact_keys),
        "bucketed_executable_decisions": len(bucketed_keys),
        "bucketed_provider_calls": len(provider_keys),
        "minimum_replay_reduction_factor": round(
            len(exact_keys) / len(provider_keys), 6
        ) if provider_keys else None,
        "uncataloged_or_invalid_rows": counts["uncataloged_or_invalid_rows"],
        "invalid_source_timestamp_rows": counts["invalid_source_timestamp_rows"],
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "tape_reconstruction_performed": False,
        "funded_authorization": False,
    }


def _default_book_provider(tape: sqlite3.Connection, target_tokens: Iterable[str]) -> BookProvider:
    return CausalBookProvider(tape, target_tokens)


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
