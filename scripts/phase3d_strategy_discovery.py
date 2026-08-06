#!/usr/bin/env python3
"""Freeze and run policy-neutral Phase 3D discovery before a future activation."""

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

from weather_trader.discovery.contracts import DiscoveryRunSpec, write_immutable_json
from weather_trader.discovery.engine import discover, freeze_winner_manifest
from weather_trader.discovery.materializer import materialize_broad_discovery_view, write_broad_view
from weather_trader.pricing.contracts import stable_hash


DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_TAPE_CATALOG = Path.home() / ".local/state/roboweather/market_tape/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--tape-catalog", type=Path, default=DEFAULT_TAPE_CATALOG)
    parser.add_argument("--out", type=Path, required=True, help="Generated artifact directory; do not commit.")
    parser.add_argument("--source-start-date", required=True)
    parser.add_argument("--discovery-cutoff-exclusive", required=True)
    parser.add_argument("--activation-timestamp", required=True)
    parser.add_argument("--untouched-holdout-start", help="Defaults to activation timestamp.")
    parser.add_argument("--fold-count", type=int, default=3)
    parser.add_argument("--minimum-effective-dates", type=int, default=8)
    parser.add_argument("--minimum-executable-station-dates", type=int, default=20)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--pre-signal-seconds", type=int, default=60)
    parser.add_argument("--target-cost-usd", type=float, default=25.0)
    args = parser.parse_args()

    date.fromisoformat(args.source_start_date)
    date.fromisoformat(args.discovery_cutoff_exclusive)
    _parse_utc(args.activation_timestamp)
    holdout_start = args.untouched_holdout_start or args.activation_timestamp
    _parse_utc(holdout_start)
    build_hash = _code_hash()

    with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
        research.row_factory = sqlite3.Row
        tape.row_factory = sqlite3.Row
        watermark = int(research.execute(
            "select coalesce(max(id),0) from prediction_snapshots where market_date<?",
            (args.discovery_cutoff_exclusive,),
        ).fetchone()[0])
        outcome_watermark = str(research.execute(
            "select coalesce(max(resolved_at),'NONE') from station_date_outcomes where market_date<?",
            (args.discovery_cutoff_exclusive,),
        ).fetchone()[0])
        has_resolutions = research.execute(
            "select 1 from sqlite_master where type='table' and name='resolutions'"
        ).fetchone() is not None
        venue_watermark = str(research.execute(
            "select coalesce(max(resolved_at),'NONE') from resolutions where market_date<?",
            (args.discovery_cutoff_exclusive,),
        ).fetchone()[0]) if has_resolutions else "TABLE_ABSENT"
        models = tuple(str(row[0]) for row in research.execute(
            "select distinct model_name from prediction_snapshots where id<=? and market_date>=? and market_date<? and model_name<>'' order by model_name",
            (watermark, args.source_start_date, args.discovery_cutoff_exclusive),
        ))
        model_market_families = tuple((str(row[0]), str(row[1])) for row in research.execute(
            "select distinct model_name,market_family from prediction_snapshots where id<=? and market_date>=? and market_date<? and model_name<>'' order by model_name,market_family",
            (watermark, args.source_start_date, args.discovery_cutoff_exclusive),
        ))
        sessions = tuple(str(row[0]) for row in tape.execute(
            "select session_id from tape_collector_sessions where started_at_utc<? order by session_id",
            (f"{args.discovery_cutoff_exclusive}T00:00:00+00:00",),
        ))
        partitions = tuple(str(row[0]) for row in tape.execute(
            "select partition_id from tape_raw_partitions where closed_at_utc<? order by partition_id",
            (f"{args.discovery_cutoff_exclusive}T00:00:00+00:00",),
        ))
        run = DiscoveryRunSpec(
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
            maximum_challengers=1,
        )
        args.out.mkdir(parents=True, exist_ok=True)
        # This artifact is persisted before any ranking begins.
        write_immutable_json(args.out / "discovery_run.json", {
            "run_id": run.run_id,
            "run_hash": stable_hash(run.canonical_payload()),
            "spec": run.canonical_payload(),
        })
        rows, diagnostics = materialize_broad_discovery_view(research, tape, run)

    write_broad_view(rows, diagnostics, args.out)
    report = discover(rows, run)
    (args.out / "ranked_families.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = freeze_winner_manifest(
        report,
        run,
        source_hash=diagnostics["row_set_hash"],
        code_hash=build_hash,
        activation_timestamp=args.activation_timestamp,
        untouched_holdout_start=holdout_start,
    )
    if manifest is not None:
        write_immutable_json(args.out / "strategy_manifest.json", manifest.canonical_payload())
    else:
        write_immutable_json(args.out / "no_winner.json", {
            "run_id": run.run_id,
            "reason": "NO_SIMPLE_FAMILY_PASSED_PREDECLARED_SELECTION_GATE",
        })
    print(json.dumps({
        "run_id": run.run_id,
        "rows": len(rows),
        "eligible_rows": diagnostics["counts"].get("ELIGIBLE", 0),
        "candidates": report["candidate_count"],
        "families": report["correlated_family_count"],
        "winner": report["winner_candidate_id"],
        "manifest": str(args.out / "strategy_manifest.json") if manifest else None,
    }, indent=2, sort_keys=True))
    return 0


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
