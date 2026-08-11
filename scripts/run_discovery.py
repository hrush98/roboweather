#!/usr/bin/env python3
"""Run deterministic cache-backed Phase 3D discovery.

The command is under D3-D5 implementation.  It is not the accepted operator
surface until exact existing-candidate evaluation and three manual production
cycles also pass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.discovery.cache_analysis import (
    FAILED_ANALYSIS,
    HistoricalDiscoveryConfig,
    load_cached_analysis_rows,
    run_historical_discovery,
    write_discovery_report,
)
from weather_trader.discovery.decision_cache import (
    DecisionCacheContract,
    ExecutableDecisionCache,
)
from weather_trader.pricing.contracts import stable_hash


DEFAULT_STATE = Path.home() / ".local/state/roboweather"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff-exclusive", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-start-date", default="2026-07-23")
    parser.add_argument(
        "--research-db",
        type=Path,
        default=DEFAULT_STATE / "research_2026-05-08_multimodel.sqlite",
    )
    parser.add_argument(
        "--tape-catalog",
        type=Path,
        default=DEFAULT_STATE / "market_tape/catalog.sqlite",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_STATE / "discovery/decision_cache.sqlite",
    )
    parser.add_argument("--holdout-dates", type=int, default=5)
    parser.add_argument("--fold-count", type=int, default=3)
    parser.add_argument("--minimum-discovery-dates", type=int, default=6)
    parser.add_argument("--minimum-discovery-trades", type=int, default=20)
    parser.add_argument("--bootstrap-repetitions", type=int, default=2_000)
    parser.add_argument("--mapping-batch-size", type=int, default=2_000)
    parser.add_argument("--replay-batch-size", type=int, default=500)
    args = parser.parse_args()

    config = HistoricalDiscoveryConfig(
        source_start_date=args.source_start_date,
        cutoff_exclusive=args.cutoff_exclusive,
        holdout_dates=args.holdout_dates,
        fold_count=args.fold_count,
        minimum_discovery_dates=args.minimum_discovery_dates,
        minimum_discovery_trades=args.minimum_discovery_trades,
        bootstrap_repetitions=args.bootstrap_repetitions,
    )
    contract = DecisionCacheContract()
    try:
        with _readonly(args.research_db) as research, _readonly(args.tape_catalog) as tape:
            research_watermark = int(research.execute(
                "select coalesce(max(id),0) from prediction_snapshots"
            ).fetchone()[0])
            outcome_watermark = str(research.execute(
                "select coalesce(max(resolved_at),'0000-00-00T00:00:00+00:00') from station_date_outcomes"
            ).fetchone()[0])
            with ExecutableDecisionCache(args.cache) as cache:
                cache.refresh(
                    research,
                    tape,
                    contract=contract,
                    source_start_date=args.source_start_date,
                    sealed_research_watermark=research_watermark,
                    mapping_batch_size=args.mapping_batch_size,
                    replay_batch_size=args.replay_batch_size,
                    progress=_progress,
                )
                cache.enrich_research_outcomes(
                    research,
                    contract_hash=contract.contract_hash,
                    outcome_watermark=outcome_watermark,
                )
                rows, cache_diagnostics = load_cached_analysis_rows(
                    cache.connection,
                    contract_hash=contract.contract_hash,
                    config=config,
                )
                result = run_historical_discovery(
                    rows,
                    config=config,
                    cache_diagnostics=cache_diagnostics,
                    sealed_manifest={
                        "analysis_version": "cache_backed_historical_discovery_v1",
                        "decision_contract_hash": contract.contract_hash,
                        "sealed_research_watermark": research_watermark,
                        "sealed_outcome_watermark": outcome_watermark,
                        "code_hash": _code_hash(),
                    },
                )
        write_discovery_report(result, args.out)
        print(json.dumps({
            "status": result["status"],
            "result_content_hash": result["result_content_hash"],
            "out": str(args.out),
            "funded_authorization": False,
        }, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        result = _failed_result(args, config, contract, exc)
        _write_failed_report(result, args.out)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2


def _failed_result(
    args: argparse.Namespace,
    config: HistoricalDiscoveryConfig,
    contract: DecisionCacheContract,
    error: Exception,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": FAILED_ANALYSIS,
        "plain_language_answer": "Discovery analysis failed; this is not a no-strategy result.",
        "error_type": type(error).__name__,
        "error": str(error),
        "configuration": config.__dict__,
        "decision_contract_hash": contract.contract_hash,
        "code_hash": _code_hash(),
        "funded_authorization": False,
    }
    payload["result_content_hash"] = stable_hash(payload)
    return payload


def _write_failed_report(result: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        "\n".join((
            "# Deterministic Tape-Backed Discovery",
            "",
            f"Status: **{result['status']}**",
            "",
            str(result["plain_language_answer"]),
            "",
            f"- Error: `{result['error_type']}: {result['error']}`",
            f"- Result content hash: `{result['result_content_hash']}`",
            "- Funded authorization: `false`",
            "",
        )),
        encoding="utf-8",
    )
    with (output_dir / "ranked_rules.csv").open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(("rule_id", "status"))


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.expanduser()}?mode=ro", uri=True)
    connection.execute("pragma query_only=ON")
    connection.execute("pragma busy_timeout=30000")
    return connection


def _progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def _code_hash() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        REPO_ROOT / "weather_trader/discovery/cache_analysis.py",
        REPO_ROOT / "weather_trader/discovery/decision_cache.py",
        REPO_ROOT / "weather_trader/tape/replay.py",
    ):
        digest.update(str(path.relative_to(REPO_ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
