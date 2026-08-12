#!/usr/bin/env python3
"""Compatibility-only C3 discovery CLI; operators use run_discovery.py."""

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
from weather_trader.discovery.materializer import materialize_broad_discovery_view
from weather_trader.discovery.orchestrator import (
    DiscoveryBudgets,
    RecurringDiscoveryOrchestrator,
)
from weather_trader.discovery.registry import DiscoveryRegistry


DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_TAPE_CATALOG = Path.home() / ".local/state/roboweather/market_tape/catalog.sqlite"
DEFAULT_REGISTRY = Path.home() / ".local/state/roboweather/discovery/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--tape-catalog", type=Path, default=DEFAULT_TAPE_CATALOG)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--source-start-date", required=True)
    parser.add_argument("--discovery-cutoff-exclusive", required=True)
    parser.add_argument("--activation-timestamp", required=True)
    parser.add_argument("--fold-count", type=int, default=3)
    parser.add_argument("--minimum-effective-dates", type=int, default=8)
    parser.add_argument("--minimum-executable-station-dates", type=int, default=20)
    parser.add_argument("--maximum-challengers", type=int, default=3)
    parser.add_argument("--maximum-candidate-rules", type=int, default=5_000)
    parser.add_argument("--maximum-active-candidates", type=int, default=12)
    parser.add_argument("--maximum-runtime-seconds", type=float, default=900.0)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--pre-signal-seconds", type=int, default=60)
    parser.add_argument("--target-cost-usd", type=float, default=25.0)
    args = parser.parse_args()

    date.fromisoformat(args.source_start_date)
    date.fromisoformat(args.discovery_cutoff_exclusive)
    _parse_utc(args.activation_timestamp)
    started_at = datetime.now(timezone.utc).isoformat()
    build_hash = _code_hash()

    with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
        research.row_factory = sqlite3.Row
        tape.row_factory = sqlite3.Row
        run = _build_run_spec(args, research, tape, build_hash)
        with DiscoveryRegistry(args.registry) as registry:
            orchestrator = RecurringDiscoveryOrchestrator(
                registry,
                budgets=DiscoveryBudgets(
                    maximum_active_candidates=args.maximum_active_candidates,
                    maximum_runtime_seconds=args.maximum_runtime_seconds,
                ),
            )
            decision = orchestrator.execution_decision(run)
            if decision["action"].startswith("NOOP_"):
                result = {
                    **decision,
                    "run_id": run.run_id,
                    "funded_authorization": False,
                }
            else:
                rows, diagnostics = materialize_broad_discovery_view(research, tape, run)
                result = orchestrator.execute(
                    run=run,
                    rows=rows,
                    materialization_diagnostics=diagnostics,
                    started_at_utc=started_at,
                )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _build_run_spec(
    args: argparse.Namespace,
    research: sqlite3.Connection,
    tape: sqlite3.Connection,
    build_hash: str,
) -> DiscoveryRunSpec:
    cutoff_timestamp = f"{args.discovery_cutoff_exclusive}T00:00:00+00:00"
    watermark = int(research.execute(
        "select coalesce(max(id),0) from prediction_snapshots where market_date<?",
        (args.discovery_cutoff_exclusive,),
    ).fetchone()[0])
    outcome_watermark = str(research.execute(
        """select coalesce(max(resolved_at),'NONE') from station_date_outcomes
           where market_date<? and resolved_at<?""",
        (args.discovery_cutoff_exclusive, args.activation_timestamp),
    ).fetchone()[0])
    has_resolutions = research.execute(
        "select 1 from sqlite_master where type='table' and name='resolutions'"
    ).fetchone() is not None
    venue_watermark = str(research.execute(
        "select coalesce(max(resolved_at),'NONE') from resolutions where resolved_at<?",
        (args.activation_timestamp,),
    ).fetchone()[0]) if has_resolutions else "TABLE_ABSENT"
    models = tuple(str(row[0]) for row in research.execute(
        """select distinct model_name from prediction_snapshots
           where id<=? and market_date>=? and market_date<? and model_name<>''
           order by model_name""",
        (watermark, args.source_start_date, args.discovery_cutoff_exclusive),
    ))
    model_market_families = tuple((str(row[0]), str(row[1])) for row in research.execute(
        """select distinct model_name,market_family from prediction_snapshots
           where id<=? and market_date>=? and market_date<? and model_name<>''
           order by model_name,market_family""",
        (watermark, args.source_start_date, args.discovery_cutoff_exclusive),
    ))
    sessions = tuple(str(row[0]) for row in tape.execute(
        "select session_id from tape_collector_sessions where started_at_utc<? order by session_id",
        (cutoff_timestamp,),
    ))
    partitions = tuple(str(row[0]) for row in tape.execute(
        "select partition_id from tape_raw_partitions where closed_at_utc<? order by partition_id",
        (cutoff_timestamp,),
    ))
    return DiscoveryRunSpec(
        source_start_date=args.source_start_date,
        discovery_cutoff_exclusive=args.discovery_cutoff_exclusive,
        earliest_activation_timestamp=args.activation_timestamp,
        research_watermark=watermark,
        tape_session_ids=sessions,
        tape_partition_ids=partitions,
        build_hash=build_hash,
        outcome_watermark=outcome_watermark,
        venue_settlement_watermark=venue_watermark,
        model_ids=models,
        model_market_families=model_market_families,
        fold_count=args.fold_count,
        minimum_effective_dates=args.minimum_effective_dates,
        minimum_executable_station_dates=args.minimum_executable_station_dates,
        latency_ms=args.latency_ms,
        pre_signal_seconds=args.pre_signal_seconds,
        target_cost_usd=args.target_cost_usd,
        maximum_challengers=args.maximum_challengers,
        maximum_candidate_rules=args.maximum_candidate_rules,
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
        REPO_ROOT / "weather_trader/discovery/engine.py",
        REPO_ROOT / "weather_trader/discovery/orchestrator.py",
        REPO_ROOT / "weather_trader/discovery/registry.py",
        REPO_ROOT / "weather_trader/tape/replay.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(REPO_ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
