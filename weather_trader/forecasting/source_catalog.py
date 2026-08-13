"""Causal source-vintage storage and bounded forward collection.

F1 keeps forecast inputs in a runtime catalog that is separate from the research
ledger.  A source artifact becomes replay-visible only at its recorded causal
availability; provider modification dates never silently backdate an artifact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Iterable, Mapping, Protocol, Sequence
from uuid import uuid4

import requests


SOURCE_VINTAGE_CONTRACT_VERSION = "forecast_source_vintage_v1"
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("source-vintage timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    provider: str
    model_family: str
    operational_version: str
    access_method: str
    cadence_hours: int | None
    selected_fields: tuple[str, ...]
    availability_rule: str
    provider_availability_field: str | None
    raw_retention: str
    notes: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        payload = {
            "contract_version": SOURCE_VINTAGE_CONTRACT_VERSION,
            **asdict(self),
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


SOURCE_CONTRACTS: tuple[SourceContract, ...] = (
    SourceContract(
        source_id="weathernext_2",
        provider="Google DeepMind",
        model_family="probabilistic_global_ensemble",
        operational_version="2.0.0",
        access_method="BigQuery/Earth Engine/GCS manifest import",
        cadence_hours=6,
        selected_fields=("2m_temperature:64_members", "10m_wind", "humidity", "mean_sea_level_pressure"),
        availability_rule="PROVIDER_INGESTION_TIME",
        provider_availability_field="ingestion_time",
        raw_retention="content-addressed manifest plus fetched/exported payload",
        notes=("Access is gated by Google approval.", "All members for a run are released together."),
    ),
    SourceContract(
        source_id="nbm_v5",
        provider="NOAA/NWS/NCEP",
        model_family="calibrated_public_blend",
        operational_version="5.0",
        access_method="NOMADS filter_blend GRIB2 subset",
        cadence_hours=1,
        selected_fields=("TMP:2m:mean", "TMP:2m:ensemble_stddev", "TMAX:2m"),
        availability_rule="FIRST_SUCCESSFUL_OBSERVATION",
        provider_availability_field=None,
        raw_retention="content-addressed station-bounded GRIB2 subset",
        notes=("NBM version changes create a new contract version.",),
    ),
    SourceContract(
        source_id="hrrr_v4",
        provider="NOAA/NWS/NCEP",
        model_family="convection_allowing_deterministic",
        operational_version="v4",
        access_method="NOMADS filter_hrrr_2d GRIB2 subset",
        cadence_hours=1,
        selected_fields=("TMP:2m", "DPT:2m", "RH:2m", "TCDC:entire_atmosphere", "DSWRF:surface", "WIND:10m"),
        availability_rule="FIRST_SUCCESSFUL_OBSERVATION",
        provider_availability_field=None,
        raw_retention="content-addressed station-bounded GRIB2 subset",
    ),
    SourceContract(
        source_id="rrfs",
        provider="NOAA/NWS/NCEP",
        model_family="rapid_refresh_successor",
        operational_version="UNFROZEN",
        access_method="explicit manifest import only",
        cadence_hours=None,
        selected_fields=("TMP:2m", "DPT:2m", "TCDC", "DSWRF", "WIND:10m"),
        availability_rule="FIRST_SUCCESSFUL_OBSERVATION",
        provider_availability_field=None,
        raw_retention="content-addressed manifest plus fetched payload",
        notes=("Fail closed until an operational product/version and endpoint are frozen.",),
    ),
    SourceContract(
        source_id="iem_metar",
        provider="Iowa Environmental Mesonet / NWS ASOS",
        model_family="routine_special_observations",
        operational_version="iem_asos_csv_v1",
        access_method="IEM ASOS request CSV",
        cadence_hours=None,
        selected_fields=("valid", "tmpf", "dwpf", "relh", "drct", "sknt", "gust", "mslp", "skyc1-4"),
        availability_rule="FIRST_SUCCESSFUL_OBSERVATION",
        provider_availability_field=None,
        raw_retention="content-addressed station/day query response",
        notes=("Observation valid time is not publication availability.",),
    ),
    SourceContract(
        source_id="goes_abi_dsr",
        provider="NOAA/NESDIS GOES-R",
        model_family="observed_surface_downward_shortwave_radiation",
        operational_version="ABI-L2-DSRF-v02r00",
        access_method="NOAA Open Data S3 full-disk NetCDF4",
        cadence_hours=None,
        selected_fields=("DSR", "DQF", "time_coverage_start", "time_coverage_end"),
        availability_rule="FIRST_SUCCESSFUL_OBSERVATION",
        provider_availability_field=None,
        raw_retention="content-addressed full-disk NetCDF4 artifact",
        notes=(
            "GOES-18 is the western view and GOES-19 is the eastern view.",
            "Embedded creation time and S3 Last-Modified are provenance only.",
        ),
    ),
)


@dataclass(frozen=True)
class MarketTarget:
    station: str
    market_date: date
    first_supported_listing_at_utc: str
    listing_source: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ArtifactRequest:
    source_id: str
    source_key: str
    url: str
    station: str | None = None
    market_date: str | None = None
    cycle_at_utc: str | None = None
    valid_start_at_utc: str | None = None
    valid_end_at_utc: str | None = None
    provider_available_at_utc: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchResult:
    content: bytes
    final_url: str
    headers: Mapping[str, str]
    observed_at_utc: str


class ArtifactFetcher(Protocol):
    def fetch(self, request: ArtifactRequest, timeout_seconds: float) -> FetchResult: ...


class RequestsFetcher:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        max_retries: int = 3,
        iem_min_interval_seconds: float = 1.0,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "RoboWeather forecast-source research/1.0")
        self.max_retries = max_retries
        self.iem_min_interval_seconds = iem_min_interval_seconds
        self._last_iem_request_at = 0.0

    def fetch(self, request: ArtifactRequest, timeout_seconds: float) -> FetchResult:
        response = None
        for attempt in range(self.max_retries + 1):
            if request.source_id == "iem_metar":
                elapsed = time.monotonic() - self._last_iem_request_at
                if elapsed < self.iem_min_interval_seconds:
                    time.sleep(self.iem_min_interval_seconds - elapsed)
            response = self.session.get(
                request.url,
                params=dict(request.params),
                headers=dict(request.headers),
                timeout=timeout_seconds,
            )
            if request.source_id == "iem_metar":
                self._last_iem_request_at = time.monotonic()
            if response.status_code not in {429, 503} or attempt >= self.max_retries:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 1.5 * (2 ** attempt)
            except ValueError:
                delay = 1.5 * (2 ** attempt)
            time.sleep(min(8.0, max(0.0, delay)))
        assert response is not None
        observed = utc_now().isoformat()
        response.raise_for_status()
        return FetchResult(
            content=response.content,
            final_url=response.url,
            headers={str(key): str(value) for key, value in response.headers.items()},
            observed_at_utc=observed,
        )


class ForecastSourceCatalog:
    def __init__(self, path: Path, raw_dir: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.raw_dir = raw_dir
        self.read_only = read_only
        if read_only:
            self.connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw_dir.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("pragma foreign_keys=on")
        self.connection.execute("pragma busy_timeout=30000")
        if read_only:
            self.connection.execute("pragma query_only=on")
        else:
            self.connection.execute("pragma journal_mode=wal")
            self._initialize()

    def __enter__(self) -> "ForecastSourceCatalog":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            create table if not exists source_contracts (
                source_id text not null,
                contract_fingerprint text not null,
                contract_version text not null,
                registered_at_utc text not null,
                raw_json text not null,
                primary key (source_id, contract_fingerprint)
            );
            create table if not exists market_targets (
                station text not null,
                market_date text not null,
                first_supported_listing_at_utc text not null,
                listing_source text not null,
                latitude real not null,
                longitude real not null,
                raw_json text not null,
                primary key (station, market_date)
            );
            create table if not exists collection_runs (
                run_id text primary key,
                started_at_utc text not null,
                finished_at_utc text,
                status text not null,
                max_artifacts integer not null,
                max_bytes integer not null,
                requested_sources_json text not null,
                summary_json text
            );
            create table if not exists collection_attempts (
                id integer primary key autoincrement,
                run_id text not null,
                source_id text not null,
                source_key text not null,
                attempted_at_utc text not null,
                finished_at_utc text not null,
                status text not null,
                http_status integer,
                bytes_received integer not null default 0,
                artifact_id text,
                error text,
                raw_json text not null,
                foreign key (run_id) references collection_runs(run_id)
            );
            create index if not exists idx_source_attempts_source_time
                on collection_attempts(source_id, attempted_at_utc);
            create table if not exists source_artifacts (
                artifact_id text primary key,
                source_id text not null,
                contract_fingerprint text not null,
                source_key text not null,
                station text,
                market_date text,
                cycle_at_utc text,
                valid_start_at_utc text,
                valid_end_at_utc text,
                provider_available_at_utc text,
                first_observed_at_utc text not null,
                causal_available_at_utc text not null,
                retrieved_at_utc text not null,
                source_url text not null,
                etag text,
                last_modified_at_utc text,
                content_sha256 text not null,
                byte_count integer not null,
                raw_path text not null,
                metadata_json text not null,
                unique (source_id, source_key, content_sha256)
            );
            create index if not exists idx_source_artifacts_replay
                on source_artifacts(source_id, causal_available_at_utc, station, market_date);
            """
        )
        self.connection.commit()
        self.register_contracts(SOURCE_CONTRACTS)

    def register_contracts(self, contracts: Iterable[SourceContract]) -> None:
        now = utc_now().isoformat()
        for contract in contracts:
            self.connection.execute(
                """insert or ignore into source_contracts
                   (source_id, contract_fingerprint, contract_version, registered_at_utc, raw_json)
                   values (?, ?, ?, ?, ?)""",
                (
                    contract.source_id,
                    contract.fingerprint,
                    SOURCE_VINTAGE_CONTRACT_VERSION,
                    now,
                    canonical_json(asdict(contract)),
                ),
            )
        self.connection.commit()

    def upsert_targets(self, targets: Iterable[MarketTarget]) -> None:
        for target in targets:
            self.connection.execute(
                """insert into market_targets
                   (station, market_date, first_supported_listing_at_utc, listing_source,
                    latitude, longitude, raw_json)
                   values (?, ?, ?, ?, ?, ?, ?)
                   on conflict(station, market_date) do update set
                     first_supported_listing_at_utc=min(
                         market_targets.first_supported_listing_at_utc,
                         excluded.first_supported_listing_at_utc
                     ),
                     listing_source=excluded.listing_source,
                     latitude=excluded.latitude,
                     longitude=excluded.longitude,
                     raw_json=excluded.raw_json""",
                (
                    target.station,
                    target.market_date.isoformat(),
                    target.first_supported_listing_at_utc,
                    target.listing_source,
                    target.latitude,
                    target.longitude,
                    canonical_json(asdict(target)),
                ),
            )
        self.connection.commit()

    def start_run(self, *, sources: Sequence[str], max_artifacts: int, max_bytes: int) -> str:
        run_id = uuid4().hex
        self.connection.execute(
            """insert into collection_runs
               (run_id, started_at_utc, status, max_artifacts, max_bytes, requested_sources_json)
               values (?, ?, 'RUNNING', ?, ?, ?)""",
            (run_id, utc_now().isoformat(), max_artifacts, max_bytes, canonical_json(list(sources))),
        )
        self.connection.commit()
        return run_id

    def finish_run(self, run_id: str, *, status: str, summary: Mapping[str, Any]) -> None:
        self.connection.execute(
            """update collection_runs
               set finished_at_utc=?, status=?, summary_json=?
               where run_id=?""",
            (utc_now().isoformat(), status, canonical_json(summary), run_id),
        )
        self.connection.commit()

    def record_failure(
        self,
        run_id: str,
        request: ArtifactRequest,
        *,
        attempted_at: str,
        error: str,
        raw: Mapping[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """insert into collection_attempts
               (run_id, source_id, source_key, attempted_at_utc, finished_at_utc,
                status, error, raw_json)
               values (?, ?, ?, ?, ?, 'FAILED', ?, ?)""",
            (
                run_id,
                request.source_id,
                request.source_key,
                attempted_at,
                utc_now().isoformat(),
                error,
                canonical_json(dict(raw or {})),
            ),
        )
        self.connection.commit()

    def record_success(self, run_id: str, request: ArtifactRequest, result: FetchResult) -> tuple[str, bool]:
        contract = contract_for(request.source_id)
        provider_available = iso_utc(request.provider_available_at_utc)
        observed = iso_utc(result.observed_at_utc)
        if contract.availability_rule == "PROVIDER_INGESTION_TIME":
            if provider_available is None:
                raise ValueError(
                    f"{request.source_id} requires {contract.provider_availability_field}"
                )
            causal_available = provider_available
        else:
            causal_available = observed
        digest = hashlib.sha256(result.content).hexdigest()
        artifact_id = hashlib.sha256(
            f"{request.source_id}|{request.source_key}|{digest}".encode()
        ).hexdigest()
        existing = self.connection.execute(
            "select artifact_id from source_artifacts where artifact_id=?", (artifact_id,)
        ).fetchone()
        raw_path = self._persist_raw(request.source_id, digest, result.content)
        last_modified = parse_http_timestamp(result.headers.get("Last-Modified"))
        metadata = {
            **dict(request.metadata),
            "request_params": dict(request.params),
            "response_headers": {
                key: value
                for key, value in result.headers.items()
                if key.lower() in {"etag", "last-modified", "content-type", "content-length"}
            },
        }
        self.connection.execute(
            """insert or ignore into source_artifacts
               (artifact_id, source_id, contract_fingerprint, source_key, station,
                market_date, cycle_at_utc, valid_start_at_utc, valid_end_at_utc,
                provider_available_at_utc, first_observed_at_utc,
                causal_available_at_utc, retrieved_at_utc, source_url, etag,
                last_modified_at_utc, content_sha256, byte_count, raw_path,
                metadata_json)
               values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                request.source_id,
                contract.fingerprint,
                request.source_key,
                request.station,
                request.market_date,
                iso_utc(request.cycle_at_utc),
                iso_utc(request.valid_start_at_utc),
                iso_utc(request.valid_end_at_utc),
                provider_available,
                observed,
                causal_available,
                observed,
                result.final_url,
                result.headers.get("ETag"),
                last_modified,
                digest,
                len(result.content),
                str(raw_path),
                canonical_json(metadata),
            ),
        )
        self.connection.execute(
            """insert into collection_attempts
               (run_id, source_id, source_key, attempted_at_utc, finished_at_utc,
                status, bytes_received, artifact_id, raw_json)
               values (?, ?, ?, ?, ?, 'SUCCESS', ?, ?, ?)""",
            (
                run_id,
                request.source_id,
                request.source_key,
                observed,
                utc_now().isoformat(),
                len(result.content),
                artifact_id,
                canonical_json({"deduplicated": existing is not None}),
            ),
        )
        self.connection.commit()
        return artifact_id, existing is None

    def replay_visible(
        self,
        source_id: str,
        *,
        as_of_utc: datetime | str,
        station: str | None = None,
        market_date: date | str | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["source_id=?", "causal_available_at_utc<=?"]
        params: list[Any] = [source_id, iso_utc(as_of_utc)]
        if station:
            clauses.append("station=?")
            params.append(station.upper())
        if market_date:
            clauses.append("market_date=?")
            params.append(market_date.isoformat() if isinstance(market_date, date) else market_date)
        return self.connection.execute(
            f"""select * from source_artifacts
                where {' and '.join(clauses)}
                order by causal_available_at_utc, artifact_id""",
            params,
        ).fetchall()

    def coverage(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """select c.source_id, c.contract_fingerprint,
                      (select count(*) from source_artifacts a
                       where a.source_id=c.source_id and a.contract_fingerprint=c.contract_fingerprint) artifacts,
                      coalesce((select sum(a.byte_count) from source_artifacts a
                       where a.source_id=c.source_id and a.contract_fingerprint=c.contract_fingerprint), 0) bytes,
                      (select min(a.causal_available_at_utc) from source_artifacts a
                       where a.source_id=c.source_id and a.contract_fingerprint=c.contract_fingerprint) first_causal_at,
                      (select max(a.causal_available_at_utc) from source_artifacts a
                       where a.source_id=c.source_id and a.contract_fingerprint=c.contract_fingerprint) latest_causal_at,
                      (select count(distinct a.station || '|' || coalesce(a.market_date, ''))
                       from source_artifacts a where a.source_id=c.source_id
                         and a.contract_fingerprint=c.contract_fingerprint and a.station is not null) station_dates,
                      (select count(distinct a.source_key) from source_artifacts a
                       where a.source_id=c.source_id and a.contract_fingerprint=c.contract_fingerprint
                         and exists (
                           select 1 from source_artifacts b
                           where b.source_id=a.source_id and b.source_key=a.source_key
                             and b.content_sha256<>a.content_sha256
                         )) revised_keys,
                      (select count(*) from collection_attempts t
                       where t.source_id=c.source_id and t.status='FAILED') failed_attempts,
                      (select count(*) from collection_attempts t
                       where t.source_id=c.source_id and t.status='SUCCESS') successful_attempts
               from source_contracts c order by c.source_id"""
        ).fetchall()
        targets = self.connection.execute("select count(*) n from market_targets").fetchone()["n"]
        runs = self.connection.execute(
            "select status, count(*) n from collection_runs group by status order by status"
        ).fetchall()
        return {
            "contract_version": SOURCE_VINTAGE_CONTRACT_VERSION,
            "sources": [dict(row) for row in rows],
            "target_count": int(targets),
            "runs": {str(row["status"]): int(row["n"]) for row in runs},
        }

    def _persist_raw(self, source_id: str, digest: str, content: bytes) -> Path:
        target = self.raw_dir / source_id / digest[:2] / f"{digest}.bin"
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target


class BoundedCollector:
    def __init__(
        self,
        catalog: ForecastSourceCatalog,
        *,
        fetcher: ArtifactFetcher | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.catalog = catalog
        self.fetcher = fetcher or RequestsFetcher()
        self.timeout_seconds = timeout_seconds

    def collect(
        self,
        requests_to_fetch: Sequence[ArtifactRequest],
        *,
        max_artifacts: int,
        max_bytes: int,
    ) -> dict[str, Any]:
        if max_artifacts <= 0 or max_bytes <= 0:
            raise ValueError("collector bounds must be positive")
        selected = list(requests_to_fetch[:max_artifacts])
        run_id = self.catalog.start_run(
            sources=sorted({request.source_id for request in selected}),
            max_artifacts=max_artifacts,
            max_bytes=max_bytes,
        )
        summary: dict[str, Any] = {
            "run_id": run_id,
            "requested": len(selected),
            "success": 0,
            "failed": 0,
            "new_artifacts": 0,
            "bytes": 0,
            "stopped_reason": None,
        }
        for request in selected:
            attempted = utc_now().isoformat()
            try:
                result = self.fetcher.fetch(request, self.timeout_seconds)
                validate_payload(request.source_id, result.content, result.headers)
                projected = int(summary["bytes"]) + len(result.content)
                if projected > max_bytes:
                    summary["stopped_reason"] = "MAX_BYTES"
                    self.catalog.record_failure(
                        run_id,
                        request,
                        attempted_at=attempted,
                        error="artifact would exceed max_bytes",
                        raw={"artifact_bytes": len(result.content), "bytes_before": summary["bytes"]},
                    )
                    summary["failed"] += 1
                    break
                _, is_new = self.catalog.record_success(run_id, request, result)
                summary["success"] += 1
                summary["new_artifacts"] += int(is_new)
                summary["bytes"] = projected
            except Exception as exc:
                self.catalog.record_failure(
                    run_id,
                    request,
                    attempted_at=attempted,
                    error=f"{type(exc).__name__}: {exc}",
                )
                summary["failed"] += 1
        status = "COMPLETE" if not summary["failed"] else (
            "PARTIAL" if summary["success"] else "FAILED"
        )
        self.catalog.finish_run(run_id, status=status, summary=summary)
        summary["status"] = status
        return summary


def contract_for(source_id: str) -> SourceContract:
    for contract in SOURCE_CONTRACTS:
        if contract.source_id == source_id:
            return contract
    raise KeyError(f"unknown forecast source: {source_id}")




def validate_payload(
    source_id: str,
    content: bytes,
    headers: Mapping[str, str] | None = None,
) -> None:
    if not content:
        raise ValueError("empty source artifact")
    content_type = str((headers or {}).get("Content-Type", "")).lower()
    prefix = content[:512].lstrip().lower()
    if source_id in {"nbm_v5", "hrrr_v4", "rrfs"}:
        if not content.startswith(b"GRIB"):
            raise ValueError("forecast artifact is not GRIB2")
    elif source_id == "iem_metar":
        header = content.splitlines()[0].lower() if content.splitlines() else b""
        if b"station" not in header or b"valid" not in header:
            raise ValueError("IEM artifact is not an observation CSV")
    elif source_id == "goes_abi_dsr":
        if not content.startswith(b"\x89HDF\r\n\x1a\n"):
            raise ValueError("GOES ABI DSR artifact is not NetCDF4/HDF5")
    elif "text/html" in content_type or prefix.startswith((b"<html", b"<!doctype html")):
        raise ValueError("source returned HTML instead of a data artifact")

def parse_http_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return None


def manifest_requests(path: Path, *, allowed_sources: set[str] | None = None) -> list[ArtifactRequest]:
    payload = json.loads(path.read_text())
    items = payload.get("artifacts") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("manifest must be a list or contain an artifacts list")
    output: list[ArtifactRequest] = []
    for item in items:
        source_id = str(item["source_id"])
        contract_for(source_id)
        if allowed_sources is not None and source_id not in allowed_sources:
            continue
        if source_id == "weathernext_2" and not item.get("provider_available_at_utc"):
            raise ValueError("WeatherNext manifest rows require provider_available_at_utc/ingestion_time")
        output.append(
            ArtifactRequest(
                source_id=source_id,
                source_key=str(item["source_key"]),
                url=str(item["url"]),
                station=item.get("station"),
                market_date=item.get("market_date"),
                cycle_at_utc=item.get("cycle_at_utc"),
                valid_start_at_utc=item.get("valid_start_at_utc"),
                valid_end_at_utc=item.get("valid_end_at_utc"),
                provider_available_at_utc=item.get("provider_available_at_utc"),
                params=item.get("params") or {},
                headers=item.get("headers") or {},
                metadata=item.get("metadata") or {},
            )
        )
    return output
