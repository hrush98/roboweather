from __future__ import annotations

import fcntl
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from weather_trader.discovery.contracts import CandidateRule, DiscoveryRunSpec
from weather_trader.pricing.contracts import stable_hash


REGISTRY_SCHEMA_VERSION = 1
LIFECYCLE_EVENT_TYPES = {
    "GENERATED",
    "NOMINATED",
    "SHADOW_ACTIVATED",
    "CHAMPION_ASSIGNED",
    "CHALLENGER_ASSIGNED",
    "DEGRADED",
    "RETIRED",
    "REJECTED",
    "PHASE4_REQUESTED",
}
RESEARCH_ROLES = {
    "GENERATED",
    "NOMINATED",
    "SHADOW_ACTIVE",
    "CHALLENGER",
    "CHAMPION",
    "PROBATION",
    "RETIRED",
    "REJECTED",
    "PHASE4_REQUESTED",
}
EVIDENCE_KINDS = {"DISCOVERY", "FORWARD_SHADOW", "ACTUAL_ORDER", "COMMON_DATE"}


class RegistryError(RuntimeError):
    """Base error for invalid or unsafe registry operations."""


class RegistryWriterLocked(RegistryError):
    """Raised when another scheduler already owns the registry writer lock."""


class ImmutableRegistryConflict(RegistryError):
    """Raised when an existing immutable identity is supplied different content."""


class DiscoveryRegistry:
    """Compact append-only Phase 3D registry with one-writer ownership.

    Writable instances hold a nonblocking advisory lock for their lifetime.
    Observers should open the registry with ``read_only=True``; SQLite then
    enforces query-only access in addition to the repository API boundary.
    """

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path.expanduser()
        self.read_only = read_only
        self._lock_handle: Any | None = None
        try:
            if read_only:
                uri = self.path.resolve().as_uri() + "?mode=ro"
                self.connection = sqlite3.connect(uri, uri=True, timeout=30.0)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._acquire_writer_lock()
                self.connection = sqlite3.connect(self.path, timeout=30.0)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("pragma foreign_keys = ON")
            self.connection.execute("pragma busy_timeout = 30000")
            if read_only:
                self.connection.execute("pragma query_only = ON")
                self._verify_schema()
            else:
                self.connection.execute("pragma journal_mode = WAL")
                self._migrate()
        except Exception:
            self._release_writer_lock()
            raise

    def __enter__(self) -> DiscoveryRegistry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "connection", None)
        if connection is not None:
            connection.close()
            self.connection = None
        self._release_writer_lock()

    def _acquire_writer_lock(self) -> None:
        lock_path = Path(f"{self.path}.writer.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RegistryWriterLocked(
                f"another discovery scheduler owns {lock_path}"
            ) from exc
        self._lock_handle = handle

    def _release_writer_lock(self) -> None:
        if self._lock_handle is None:
            return
        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        self._lock_handle.close()
        self._lock_handle = None

    def _verify_schema(self) -> None:
        version = int(self.connection.execute("pragma user_version").fetchone()[0])
        if version != REGISTRY_SCHEMA_VERSION:
            raise RegistryError(
                f"unsupported discovery registry schema {version}; expected {REGISTRY_SCHEMA_VERSION}"
            )

    def _migrate(self) -> None:
        version = int(self.connection.execute("pragma user_version").fetchone()[0])
        if version > REGISTRY_SCHEMA_VERSION:
            raise RegistryError(
                f"registry schema {version} is newer than supported {REGISTRY_SCHEMA_VERSION}"
            )
        if version == 0:
            self._migrate_v1()
            self.connection.execute(f"pragma user_version = {REGISTRY_SCHEMA_VERSION}")
            self.connection.commit()
        self._verify_schema()

    def _migrate_v1(self) -> None:
        self.connection.executescript(
            """
            create table if not exists registry_schema_migrations (
                version integer primary key,
                applied_at_utc text not null,
                description text not null
            );

            create table if not exists discovery_runs (
                run_id text primary key,
                run_hash text not null unique,
                source_kind text not null,
                status text not null,
                created_at_utc text not null,
                source_start_date text not null,
                cutoff_exclusive text not null,
                research_watermark text not null,
                tape_watermark_hash text not null,
                outcome_watermark text not null,
                venue_settlement_watermark text not null,
                grammar_version text not null,
                spec_json text not null,
                diagnostics_json text not null,
                output_refs_json text not null
            );

            create table if not exists strategy_families (
                family_id text primary key,
                definition_hash text not null unique,
                economic_rationale text not null,
                grammar_provenance text not null,
                correlation_group text not null,
                created_at_utc text not null,
                definition_json text not null
            );

            create table if not exists candidate_versions (
                candidate_version_id text primary key,
                family_id text not null references strategy_families(family_id),
                family_version integer not null,
                definition_hash text not null unique,
                source_run_id text not null references discovery_runs(run_id),
                activation_timestamp_utc text not null,
                pricing_version text not null,
                execution_version text not null,
                risk_version text not null,
                source_kind text not null,
                created_at_utc text not null,
                definition_json text not null,
                unique(family_id, family_version)
            );

            create table if not exists evaluation_cohorts (
                cohort_id text primary key,
                cohort_hash text not null unique,
                candidate_version_id text not null references candidate_versions(candidate_version_id),
                activation_timestamp_utc text not null,
                eligible_start_utc text not null,
                eligible_end_utc text,
                created_at_utc text not null,
                source_watermarks_json text not null,
                requirements_json text not null,
                initial_completeness_json text not null
            );

            create table if not exists candidate_scorecards (
                scorecard_id text primary key,
                scorecard_hash text not null unique,
                candidate_version_id text not null references candidate_versions(candidate_version_id),
                cohort_id text references evaluation_cohorts(cohort_id),
                evidence_kind text not null,
                as_of_watermark_hash text not null,
                created_at_utc text not null,
                statistics_json text not null,
                rejection_counts_json text not null,
                source_refs_json text not null,
                unique(candidate_version_id, cohort_id, evidence_kind, as_of_watermark_hash)
            );

            create table if not exists candidate_lifecycle_events (
                event_id text primary key,
                event_hash text not null unique,
                candidate_version_id text not null references candidate_versions(candidate_version_id),
                event_type text not null,
                from_role text,
                to_role text not null,
                occurred_at_utc text not null,
                reason text not null,
                source_run_id text references discovery_runs(run_id),
                metadata_json text not null
            );

            create index if not exists idx_candidate_versions_family
                on candidate_versions(family_id, family_version);
            create index if not exists idx_cohorts_candidate
                on evaluation_cohorts(candidate_version_id, eligible_start_utc);
            create index if not exists idx_scorecards_candidate_asof
                on candidate_scorecards(candidate_version_id, created_at_utc);
            create index if not exists idx_lifecycle_candidate_time
                on candidate_lifecycle_events(candidate_version_id, occurred_at_utc, event_id);

            insert or ignore into registry_schema_migrations values (
                1, strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                'initial append-only continuous discovery registry'
            );
            """
        )
        for table in (
            "discovery_runs",
            "strategy_families",
            "candidate_versions",
            "evaluation_cohorts",
            "candidate_scorecards",
            "candidate_lifecycle_events",
        ):
            self.connection.executescript(
                f"""
                create trigger if not exists {table}_append_only_update
                before update on {table}
                begin
                    select raise(abort, '{table} is append-only');
                end;
                create trigger if not exists {table}_append_only_delete
                before delete on {table}
                begin
                    select raise(abort, '{table} is append-only');
                end;
                """
            )

    def register_discovery_run(
        self,
        spec: DiscoveryRunSpec | Mapping[str, Any],
        *,
        created_at_utc: str,
        status: str = "SEALED",
        source_kind: str = "continuous_v1",
        diagnostics: Mapping[str, Any] | None = None,
        output_refs: Mapping[str, Any] | None = None,
    ) -> str:
        payload = spec.canonical_payload() if isinstance(spec, DiscoveryRunSpec) else dict(spec)
        supplied_run_id = payload.pop("run_id", None)
        run_hash = stable_hash(payload)
        run_id = spec.run_id if isinstance(spec, DiscoveryRunSpec) else str(
            supplied_run_id or f"p3d_run_{run_hash[:24]}"
        )
        tape_hash = stable_hash({
            "session_ids": payload.get("tape_session_ids", ()),
            "partition_ids": payload.get("tape_partition_ids", ()),
        })
        values = {
            "run_id": run_id,
            "run_hash": run_hash,
            "source_kind": source_kind,
            "status": status,
            "created_at_utc": _utc_text(created_at_utc),
            "source_start_date": str(payload.get("source_start_date", "UNKNOWN")),
            "cutoff_exclusive": str(payload.get("discovery_cutoff_exclusive", "UNKNOWN")),
            "research_watermark": str(payload.get("research_watermark", "UNKNOWN")),
            "tape_watermark_hash": tape_hash,
            "outcome_watermark": str(payload.get("outcome_watermark", "UNKNOWN")),
            "venue_settlement_watermark": str(payload.get("venue_settlement_watermark", "UNKNOWN")),
            "grammar_version": str(payload.get("grammar_version", "UNKNOWN")),
            "spec_json": _json(payload),
            "diagnostics_json": _json(diagnostics or {}),
            "output_refs_json": _json(output_refs or {}),
        }
        with self.connection:
            self._insert_immutable("discovery_runs", "run_id", values)
        return run_id

    def register_family(
        self,
        *,
        definition: Mapping[str, Any],
        economic_rationale: str,
        grammar_provenance: str,
        correlation_group: str,
        created_at_utc: str,
    ) -> str:
        payload = dict(definition)
        definition_hash = stable_hash(payload)
        family_id = str(payload.get("family_id") or f"p3d_family_{definition_hash[:20]}")
        with self.connection:
            self._insert_immutable("strategy_families", "family_id", {
                "family_id": family_id,
                "definition_hash": definition_hash,
                "economic_rationale": economic_rationale,
                "grammar_provenance": grammar_provenance,
                "correlation_group": correlation_group,
                "created_at_utc": _utc_text(created_at_utc),
                "definition_json": _json(payload),
            })
        return family_id

    def register_candidate_version(
        self,
        *,
        family_id: str,
        source_run_id: str,
        rule: CandidateRule | Mapping[str, Any],
        activation_timestamp_utc: str,
        pricing_version: str,
        execution_version: str,
        risk_version: str,
        created_at_utc: str,
        sizing_and_risk: Mapping[str, Any] | None = None,
        source_kind: str = "continuous_v1",
    ) -> str:
        rule_payload = asdict(rule) if isinstance(rule, CandidateRule) else dict(rule)
        CandidateRule(**rule_payload)
        definition = {
            "family_id": family_id,
            "rule": rule_payload,
            "pricing_version": pricing_version,
            "execution_version": execution_version,
            "risk_version": risk_version,
            "sizing_and_risk": dict(sizing_and_risk or {}),
        }
        definition_hash = stable_hash(definition)
        existing = self.connection.execute(
            "select candidate_version_id from candidate_versions where definition_hash=?",
            (definition_hash,),
        ).fetchone()
        if existing is not None:
            return str(existing[0])

        candidate_id = f"p3d_version_{definition_hash[:24]}"
        created = _utc_text(created_at_utc)
        activation = _utc_text(activation_timestamp_utc)
        run = self.connection.execute(
            "select cutoff_exclusive from discovery_runs where run_id=?", (source_run_id,)
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown source discovery run: {source_run_id}")
        if activation < f"{run['cutoff_exclusive']}T00:00:00+00:00":
            raise ValueError("candidate activation must not precede its discovery cutoff")
        with self.connection:
            version = int(self.connection.execute(
                "select coalesce(max(family_version),0)+1 from candidate_versions where family_id=?",
                (family_id,),
            ).fetchone()[0])
            values = {
                "candidate_version_id": candidate_id,
                "family_id": family_id,
                "family_version": version,
                "definition_hash": definition_hash,
                "source_run_id": source_run_id,
                "activation_timestamp_utc": activation,
                "pricing_version": pricing_version,
                "execution_version": execution_version,
                "risk_version": risk_version,
                "source_kind": source_kind,
                "created_at_utc": created,
                "definition_json": _json(definition),
            }
            self._insert_immutable("candidate_versions", "candidate_version_id", values)
            self._append_lifecycle_event_in_transaction(
                candidate_version_id=candidate_id,
                event_type="GENERATED",
                from_role=None,
                to_role="GENERATED",
                occurred_at_utc=created,
                reason="candidate version registered",
                source_run_id=source_run_id,
                metadata={"source_kind": source_kind},
            )
        return candidate_id

    def register_evaluation_cohort(
        self,
        *,
        candidate_version_id: str,
        activation_timestamp_utc: str,
        eligible_start_utc: str,
        eligible_end_utc: str | None,
        source_watermarks: Mapping[str, Any],
        requirements: Mapping[str, Any],
        initial_completeness: Mapping[str, Any],
        created_at_utc: str,
    ) -> str:
        activation = _utc_text(activation_timestamp_utc)
        start = _utc_text(eligible_start_utc)
        end = _utc_text(eligible_end_utc) if eligible_end_utc else None
        if start < activation or (end is not None and end <= start):
            raise ValueError("cohort interval must begin at/after activation and increase")
        candidate = self.connection.execute(
            "select activation_timestamp_utc from candidate_versions where candidate_version_id=?",
            (candidate_version_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"unknown candidate version: {candidate_version_id}")
        if activation != str(candidate[0]):
            raise ValueError("cohort activation must equal the candidate-version activation")
        definition = {
            "candidate_version_id": candidate_version_id,
            "activation_timestamp_utc": activation,
            "eligible_start_utc": start,
            "eligible_end_utc": end,
            "source_watermarks": dict(source_watermarks),
            "requirements": dict(requirements),
            "initial_completeness": dict(initial_completeness),
        }
        cohort_hash = stable_hash(definition)
        cohort_id = f"p3d_cohort_{cohort_hash[:24]}"
        with self.connection:
            self._insert_immutable("evaluation_cohorts", "cohort_id", {
                "cohort_id": cohort_id,
                "cohort_hash": cohort_hash,
                "candidate_version_id": candidate_version_id,
                "activation_timestamp_utc": activation,
                "eligible_start_utc": start,
                "eligible_end_utc": end,
                "created_at_utc": _utc_text(created_at_utc),
                "source_watermarks_json": _json(source_watermarks),
                "requirements_json": _json(requirements),
                "initial_completeness_json": _json(initial_completeness),
            })
        return cohort_id

    def append_scorecard(
        self,
        *,
        candidate_version_id: str,
        cohort_id: str | None,
        evidence_kind: str,
        as_of_watermarks: Mapping[str, Any],
        statistics: Mapping[str, Any],
        rejection_counts: Mapping[str, int],
        source_refs: Mapping[str, Any],
        created_at_utc: str,
    ) -> str:
        if evidence_kind not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported evidence kind: {evidence_kind}")
        watermark_hash = stable_hash(dict(as_of_watermarks))
        payload = {
            "candidate_version_id": candidate_version_id,
            "cohort_id": cohort_id,
            "evidence_kind": evidence_kind,
            "as_of_watermarks": dict(as_of_watermarks),
            "statistics": dict(statistics),
            "rejection_counts": dict(rejection_counts),
            "source_refs": dict(source_refs),
        }
        scorecard_hash = stable_hash(payload)
        scorecard_id = f"p3d_score_{scorecard_hash[:24]}"
        prior = self.connection.execute(
            """select scorecard_id,scorecard_hash from candidate_scorecards
               where candidate_version_id=? and cohort_id is ? and evidence_kind=?
                 and as_of_watermark_hash=?""",
            (candidate_version_id, cohort_id, evidence_kind, watermark_hash),
        ).fetchone()
        if prior is not None:
            if str(prior["scorecard_hash"]) == scorecard_hash:
                return str(prior["scorecard_id"])
            raise ImmutableRegistryConflict(
                "scorecard evidence already exists with different content at this watermark"
            )
        values = {
            "scorecard_id": scorecard_id,
            "scorecard_hash": scorecard_hash,
            "candidate_version_id": candidate_version_id,
            "cohort_id": cohort_id,
            "evidence_kind": evidence_kind,
            "as_of_watermark_hash": watermark_hash,
            "created_at_utc": _utc_text(created_at_utc),
            "statistics_json": _json(statistics),
            "rejection_counts_json": _json(rejection_counts),
            "source_refs_json": _json({**dict(source_refs), "as_of_watermarks": dict(as_of_watermarks)}),
        }
        with self.connection:
            self._insert_immutable("candidate_scorecards", "scorecard_id", values)
        return scorecard_id

    def append_lifecycle_event(
        self,
        *,
        candidate_version_id: str,
        event_type: str,
        from_role: str | None,
        to_role: str,
        occurred_at_utc: str,
        reason: str,
        source_run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        with self.connection:
            return self._append_lifecycle_event_in_transaction(
                candidate_version_id=candidate_version_id,
                event_type=event_type,
                from_role=from_role,
                to_role=to_role,
                occurred_at_utc=occurred_at_utc,
                reason=reason,
                source_run_id=source_run_id,
                metadata=metadata or {},
            )

    def _append_lifecycle_event_in_transaction(
        self,
        *,
        candidate_version_id: str,
        event_type: str,
        from_role: str | None,
        to_role: str,
        occurred_at_utc: str,
        reason: str,
        source_run_id: str | None,
        metadata: Mapping[str, Any],
    ) -> str:
        if event_type not in LIFECYCLE_EVENT_TYPES:
            raise ValueError(f"unsupported lifecycle event: {event_type}")
        if from_role is not None and from_role not in RESEARCH_ROLES:
            raise ValueError(f"unsupported source role: {from_role}")
        if to_role not in RESEARCH_ROLES:
            raise ValueError(f"unsupported target role: {to_role}")
        payload = {
            "candidate_version_id": candidate_version_id,
            "event_type": event_type,
            "from_role": from_role,
            "to_role": to_role,
            "occurred_at_utc": _utc_text(occurred_at_utc),
            "reason": reason,
            "source_run_id": source_run_id,
            "metadata": dict(metadata),
        }
        event_hash = stable_hash(payload)
        event_id = f"p3d_event_{event_hash[:24]}"
        self._insert_immutable("candidate_lifecycle_events", "event_id", {
            "event_id": event_id,
            "event_hash": event_hash,
            "candidate_version_id": candidate_version_id,
            "event_type": event_type,
            "from_role": from_role,
            "to_role": to_role,
            "occurred_at_utc": payload["occurred_at_utc"],
            "reason": reason,
            "source_run_id": source_run_id,
            "metadata_json": _json(metadata),
        })
        return event_id

    def current_role(self, candidate_version_id: str) -> str | None:
        row = self.connection.execute(
            """select to_role from candidate_lifecycle_events
               where candidate_version_id=?
               order by occurred_at_utc desc, rowid desc limit 1""",
            (candidate_version_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def import_batch_v1(self, artifact_dir: Path) -> dict[str, Any]:
        """Import batch compatibility identity only, never its forward report."""
        run_path = artifact_dir / "discovery_run.json"
        run_artifact = json.loads(run_path.read_text(encoding="utf-8"))
        spec = dict(run_artifact["spec"])
        expected_run_hash = stable_hash(spec)
        if run_artifact.get("run_hash") != expected_run_hash:
            raise ValueError("batch_v1 discovery run hash does not match its contents")
        run_id = self.register_discovery_run(
            {**spec, "run_id": run_artifact.get("run_id")},
            created_at_utc=spec["earliest_activation_timestamp"],
            status="IMPORTED_BATCH_V1",
            source_kind="batch_v1",
            output_refs={"artifact_dir": str(artifact_dir)},
        )
        manifest_path = artifact_dir / "strategy_manifest.json"
        if not manifest_path.exists():
            if not (artifact_dir / "no_winner.json").exists():
                raise FileNotFoundError("batch_v1 directory has neither manifest nor no_winner artifact")
            return {"run_id": run_id, "candidate_version_id": None, "forward_evidence_imported": False}

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_hash = str(manifest.get("manifest_hash", ""))
        unhashed = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        if stable_hash(unhashed) != manifest_hash:
            raise ValueError("batch_v1 strategy manifest hash does not match its contents")
        rule = dict(manifest["candidate"])
        family_definition = {
            "family_id": CandidateRule(**rule).correlated_family_id,
            "model_id": rule["model_id"],
            "market_family": rule["market_family"],
            "selected_side": rule["selected_side"],
            "strategy_bucket": rule["strategy_bucket"],
            "require_high_conviction": rule["require_high_conviction"],
            "dedupe_scope": rule["dedupe_scope"],
            "execution_arm": rule["execution_arm"],
        }
        family_id = self.register_family(
            definition=family_definition,
            economic_rationale="Imported batch_v1 compatibility family; no forward-evidence claim.",
            grammar_provenance=str(spec.get("grammar_version", "batch_v1")),
            correlation_group=family_definition["family_id"],
            created_at_utc=manifest["activation_timestamp"],
        )
        candidate_id = self.register_candidate_version(
            family_id=family_id,
            source_run_id=run_id,
            rule=rule,
            activation_timestamp_utc=manifest["activation_timestamp"],
            pricing_version=manifest["pricing_version"],
            execution_version=manifest["execution_arm"],
            risk_version="batch_v1_manifest",
            sizing_and_risk={
                key: manifest[key]
                for key in (
                    "target_cost_usd", "price_cap", "station_date_cap_usd",
                    "daily_risk_cap_usd", "latency_ms", "pre_signal_seconds",
                )
            },
            created_at_utc=manifest["activation_timestamp"],
            source_kind="batch_v1",
        )
        return {"run_id": run_id, "candidate_version_id": candidate_id, "forward_evidence_imported": False}

    def table_counts(self) -> dict[str, int]:
        tables = (
            "discovery_runs", "strategy_families", "candidate_versions",
            "evaluation_cohorts", "candidate_scorecards", "candidate_lifecycle_events",
        )
        return {
            table: int(self.connection.execute(f"select count(*) from {table}").fetchone()[0])
            for table in tables
        }

    def _insert_immutable(self, table: str, identity_column: str, values: Mapping[str, Any]) -> None:
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        try:
            self.connection.execute(
                f"insert into {table} ({','.join(columns)}) values ({placeholders})",
                tuple(values[column] for column in columns),
            )
        except sqlite3.IntegrityError as exc:
            existing = self.connection.execute(
                f"select {','.join(columns)} from {table} where {identity_column}=?",
                (values[identity_column],),
            ).fetchone()
            if existing is not None and all(existing[column] == values[column] for column in columns):
                return
            raise ImmutableRegistryConflict(
                f"immutable {table} identity {values[identity_column]!r} has different content"
            ) from exc


def _json(value: Mapping[str, Any] | Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _utc_text(value: str) -> str:
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc).isoformat()
