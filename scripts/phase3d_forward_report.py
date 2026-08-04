#!/usr/bin/env python3
"""Evaluate one immutable Phase 3D manifest only on post-activation tape."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.contracts import CandidateRule, DiscoveryRunSpec, StrategyManifest
from weather_trader.discovery.engine import evaluate_frozen_manifest, select_frozen_rows
from weather_trader.discovery.materializer import materialize_broad_discovery_view


DEFAULT_RESEARCH_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"
DEFAULT_TAPE_CATALOG = Path.home() / ".local/state/roboweather/market_tape/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--research-db", type=Path, default=DEFAULT_RESEARCH_DB)
    parser.add_argument("--tape-catalog", type=Path, default=DEFAULT_TAPE_CATALOG)
    parser.add_argument("--end-date-exclusive", required=True)
    parser.add_argument("--out", type=Path, required=True, help="Generated forward artifact directory; do not commit.")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
        research.row_factory = sqlite3.Row
        tape.row_factory = sqlite3.Row
        watermark = int(research.execute("select coalesce(max(id),0) from prediction_snapshots").fetchone()[0])
        outcome_watermark = str(research.execute(
            "select coalesce(max(resolved_at),'NONE') from station_date_outcomes"
        ).fetchone()[0])
        has_resolutions = research.execute(
            "select 1 from sqlite_master where type='table' and name='resolutions'"
        ).fetchone() is not None
        venue_watermark = str(research.execute(
            "select coalesce(max(resolved_at),'NONE') from resolutions"
        ).fetchone()[0]) if has_resolutions else "TABLE_ABSENT"
        sessions = tuple(str(row[0]) for row in tape.execute("select session_id from tape_collector_sessions order by session_id"))
        partitions = tuple(str(row[0]) for row in tape.execute("select partition_id from tape_raw_partitions order by partition_id"))
        run = DiscoveryRunSpec(
            source_start_date=manifest.untouched_holdout_start[:10],
            discovery_cutoff_exclusive=args.end_date_exclusive,
            earliest_activation_timestamp=f"{args.end_date_exclusive}T00:00:00+00:00",
            research_watermark=watermark,
            tape_session_ids=sessions,
            tape_partition_ids=partitions,
            build_hash=manifest.code_hash,
            outcome_watermark=outcome_watermark,
            venue_settlement_watermark=venue_watermark,
            model_ids=(manifest.candidate.model_id,),
            model_market_families=((manifest.candidate.model_id, manifest.candidate.market_family),),
            fold_count=2,
            minimum_effective_dates=max(2, manifest.minimum_forward_effective_dates),
            minimum_executable_station_dates=max(2, manifest.minimum_forward_station_dates),
            latency_ms=manifest.latency_ms,
            pre_signal_seconds=manifest.pre_signal_seconds,
            target_cost_usd=manifest.target_cost_usd,
        )
        rows, diagnostics = materialize_broad_discovery_view(research, tape, run)

    selected = select_frozen_rows(rows, manifest)
    report = evaluate_frozen_manifest(rows, manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "frozen_evaluation_view.jsonl").write_text(
        "".join(json.dumps(row.canonical_payload(), sort_keys=True, separators=(",", ":")) + "\n" for row in selected),
        encoding="utf-8",
    )
    (args.out / "forward_report.json").write_text(
        json.dumps({"materialization": diagnostics, "forward": report}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "manifest_hash": manifest.manifest_hash,
        "selected_rows": len(selected),
        "venue_resolved_executions": report["venue_resolved_executions"],
        "disposition": report["disposition"],
        "reasons": report["reasons"],
    }, indent=2, sort_keys=True))
    return 0


def _load_manifest(path: Path) -> StrategyManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidate"] = CandidateRule(**payload["candidate"])
    allowed = {field.name for field in fields(StrategyManifest)}
    manifest = StrategyManifest(**{key: value for key, value in payload.items() if key in allowed})
    if manifest.frozen().manifest_hash != manifest.manifest_hash:
        raise ValueError("strategy manifest hash does not match its canonical contents")
    return manifest


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.expanduser()}?mode=ro", uri=True)
    connection.execute("pragma query_only=ON")
    connection.execute("pragma busy_timeout=30000")
    return connection


if __name__ == "__main__":
    raise SystemExit(main())
