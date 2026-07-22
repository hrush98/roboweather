#!/usr/bin/env python3
"""Build reconstructable Price Sheet V2a fit and frozen-policy datasets."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.pricing.contracts import PILOT_SIGNAL_SPECS
from weather_trader.pricing.dataset import materialize_v2a_dataset, write_v2a_dataset_artifact


DEFAULT_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, required=True, help="Generated artifact directory (do not commit).")
    parser.add_argument("--signal-spec", choices=[spec.signal_spec_id for spec in PILOT_SIGNAL_SPECS])
    parser.add_argument("--fit-cutoff-date-exclusive")
    parser.add_argument("--evaluation-start-date")
    parser.add_argument("--evaluation-end-date")
    args = parser.parse_args()

    selected_specs = [spec for spec in PILOT_SIGNAL_SPECS if args.signal_spec in (None, spec.signal_spec_id)]
    summaries = []
    source_uri = f"file:{args.db.expanduser()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for spec in selected_specs:
            artifact = materialize_v2a_dataset(
                connection,
                spec,
                fit_cutoff_date_exclusive=args.fit_cutoff_date_exclusive,
                evaluation_start_date=args.evaluation_start_date,
                evaluation_end_date=args.evaluation_end_date,
            )
            output_dir = args.out / spec.signal_spec_id
            write_v2a_dataset_artifact(artifact, output_dir)
            summaries.append({"output_dir": str(output_dir), **artifact.manifest()})
    print(json.dumps({"source_db": str(args.db), "artifacts": summaries}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
