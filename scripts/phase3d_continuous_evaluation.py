#!/usr/bin/env python3
"""Append one idempotent C4 scorecard watermark for every active candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.contracts import DiscoveryRunSpec
from weather_trader.discovery.evaluator import ContinuousCohortEvaluator
from weather_trader.discovery.materializer import materialize_broad_discovery_view
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.pricing.contracts import stable_hash


DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_TAPE_CATALOG = Path.home() / ".local/state/roboweather/market_tape/catalog.sqlite"
DEFAULT_REGISTRY = Path.home() / ".local/state/roboweather/discovery/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--tape-catalog", type=Path, default=DEFAULT_TAPE_CATALOG)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--end-date-exclusive", required=True)
    parser.add_argument("--as-of-timestamp")
    args = parser.parse_args()

    end_date = date.fromisoformat(args.end_date_exclusive)
    as_of = _utc(args.as_of_timestamp) if args.as_of_timestamp else datetime.now(timezone.utc)
    if as_of < datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc):
        raise ValueError("as-of timestamp must reach the exclusive evaluation end date")

    build_hash = _code_hash()
    with DiscoveryRegistry(args.registry) as registry:
        candidates = registry.active_candidate_versions()
        if not candidates:
            print(json.dumps({
                "status": "NO_ACTIVE_CANDIDATES",
                "active_candidate_count": 0,
                "scorecards": [],
                "funded_authorization": False,
                "role_transitions_applied": False,
            }, indent=2, sort_keys=True))
            return 0
        mature_candidates = [
            candidate for candidate in candidates
            if _utc(str(candidate["activation_timestamp_utc"])).date() < end_date
        ]
        if not mature_candidates:
            print(json.dumps({
                "status": "NO_MATURE_ACTIVE_CANDIDATES",
                "active_candidate_count": len(candidates),
                "scorecards": [],
                "funded_authorization": False,
                "role_transitions_applied": False,
            }, indent=2, sort_keys=True))
            return 0
        with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
            research.row_factory = sqlite3.Row
            tape.row_factory = sqlite3.Row
            run = _evaluation_view_spec(
                candidates, research, tape, args.end_date_exclusive, as_of, build_hash
            )
            rows, diagnostics = materialize_broad_discovery_view(research, tape, run)
            watermarks = {
                "research_prediction_snapshot_id": run.research_watermark,
                "outcome_resolved_at": run.outcome_watermark,
                "venue_settlement_resolved_at": run.venue_settlement_watermark,
                "tape_membership_hash": stable_hash({
                    "sessions": run.tape_session_ids,
                    "partitions": run.tape_partition_ids,
                }),
                "row_set_hash": diagnostics["row_set_hash"],
                "end_date_exclusive": args.end_date_exclusive,
                "build_hash": build_hash,
            }
            result = ContinuousCohortEvaluator(registry).evaluate(
                rows=rows,
                as_of_watermarks=watermarks,
                materialization_diagnostics=diagnostics,
                created_at_utc=as_of.isoformat(),
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _evaluation_view_spec(
    candidates: list[dict],
    research: sqlite3.Connection,
    tape: sqlite3.Connection,
    end_date_exclusive: str,
    as_of: datetime,
    build_hash: str,
) -> DiscoveryRunSpec:
    activation_dates = [
        _utc(str(candidate["activation_timestamp_utc"])).date()
        for candidate in candidates
    ]
    source_start = min(activation_dates).isoformat()
    if source_start >= end_date_exclusive:
        raise ValueError("evaluation end must follow at least one active-candidate activation")
    pairs = tuple(sorted({
        (
            str(candidate["definition"]["rule"]["model_id"]),
            str(candidate["definition"]["rule"]["market_family"]),
        )
        for candidate in candidates
    }))
    models = tuple(sorted({model for model, _ in pairs}))
    watermark = int(research.execute(
        """select coalesce(max(id),0) from prediction_snapshots
           where timestamp<? and market_date<?""",
        (as_of.isoformat(), end_date_exclusive),
    ).fetchone()[0])
    outcome_watermark = str(research.execute(
        """select coalesce(max(resolved_at),'NONE') from station_date_outcomes
           where resolved_at<?""",
        (as_of.isoformat(),),
    ).fetchone()[0])
    has_resolutions = research.execute(
        "select 1 from sqlite_master where type='table' and name='resolutions'"
    ).fetchone() is not None
    venue_watermark = str(research.execute(
        "select coalesce(max(resolved_at),'NONE') from resolutions where resolved_at<?",
        (as_of.isoformat(),),
    ).fetchone()[0]) if has_resolutions else "TABLE_ABSENT"
    sessions = tuple(str(row[0]) for row in tape.execute(
        "select session_id from tape_collector_sessions where started_at_utc<? order by session_id",
        (as_of.isoformat(),),
    ))
    partitions = tuple(str(row[0]) for row in tape.execute(
        """select partition_id from tape_raw_partitions
           where closed_at_utc is not null and closed_at_utc<?
           order by partition_id""",
        (as_of.isoformat(),),
    ))
    minimum_dates = min(
        int(candidate["source_run_spec"]["minimum_effective_dates"])
        for candidate in candidates
    )
    minimum_station_dates = min(
        int(candidate["source_run_spec"]["minimum_executable_station_dates"])
        for candidate in candidates
    )
    return DiscoveryRunSpec(
        source_start_date=source_start,
        discovery_cutoff_exclusive=end_date_exclusive,
        earliest_activation_timestamp=f"{end_date_exclusive}T00:00:00+00:00",
        research_watermark=watermark,
        tape_session_ids=sessions,
        tape_partition_ids=partitions,
        build_hash=build_hash,
        outcome_watermark=outcome_watermark,
        venue_settlement_watermark=venue_watermark,
        model_ids=models,
        model_market_families=pairs,
        minimum_effective_dates=minimum_dates,
        minimum_executable_station_dates=minimum_station_dates,
    )


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.expanduser()}?mode=ro", uri=True)
    connection.execute("pragma query_only=ON")
    connection.execute("pragma busy_timeout=30000")
    return connection


def _code_hash() -> str:
    paths = [
        Path(__file__),
        REPO_ROOT / "weather_trader/discovery/contracts.py",
        REPO_ROOT / "weather_trader/discovery/materializer.py",
        REPO_ROOT / "weather_trader/discovery/evaluator.py",
        REPO_ROOT / "weather_trader/discovery/registry.py",
        REPO_ROOT / "weather_trader/tape/replay.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(REPO_ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
