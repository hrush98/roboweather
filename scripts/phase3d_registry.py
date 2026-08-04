#!/usr/bin/env python3
"""Initialize, inspect, or import compatibility artifacts into the Phase 3D registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.registry import DiscoveryRegistry, REGISTRY_SCHEMA_VERSION


DEFAULT_REGISTRY = Path.home() / ".local/state/roboweather/discovery/catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create or migrate the compact registry.")
    subparsers.add_parser("status", help="Inspect the registry read-only.")
    importer = subparsers.add_parser(
        "import-batch-v1", help="Import a legacy batch run/manifest without forward evidence."
    )
    importer.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()

    if args.command == "status":
        with DiscoveryRegistry(args.registry, read_only=True) as registry:
            result = _status(registry)
    elif args.command == "init":
        with DiscoveryRegistry(args.registry) as registry:
            result = _status(registry)
    else:
        with DiscoveryRegistry(args.registry) as registry:
            imported = registry.import_batch_v1(args.artifact_dir)
            result = {**_status(registry), "imported": imported}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _status(registry: DiscoveryRegistry) -> dict[str, object]:
    active_roles = {
        str(row["to_role"]): int(row["count"])
        for row in registry.connection.execute(
            """
            with ranked as (
                select rowid event_rowid,
                       row_number() over (
                           partition by candidate_version_id
                           order by occurred_at_utc desc,rowid desc
                       ) event_rank
                from candidate_lifecycle_events
            )
            select events.to_role, count(*) count
            from ranked join candidate_lifecycle_events events on events.rowid=ranked.event_rowid
            where ranked.event_rank=1
            group by events.to_role order by events.to_role
            """
        )
    }
    latest_run = registry.connection.execute(
        """select run_id,status,created_at_utc,cutoff_exclusive
           from discovery_runs order by created_at_utc desc,run_id desc limit 1"""
    ).fetchone()
    return {
        "registry": str(registry.path),
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "counts": registry.table_counts(),
        "current_roles": active_roles,
        "latest_run": dict(latest_run) if latest_run is not None else None,
        "funded_authorization": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
