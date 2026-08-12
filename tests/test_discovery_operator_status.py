from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from weather_trader.discovery.operator_status import (
    latest_complete_status_report,
    write_latest_complete_status,
)


def _cache() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        create table executable_decisions(status text not null);
        create table model_decision_mappings(mapping_id text primary key);
        insert into executable_decisions values ('SUCCESS'),('REJECTED');
        insert into model_decision_mappings values ('mapping-1'),('mapping-2');
        """
    )
    return connection


def _result() -> dict[str, object]:
    return {
        "status": "COMPLETED_NO_EMERGED_STRATEGIES",
        "plain_language_answer": "No strategy emerged.",
        "result_content_hash": "content-hash",
        "manifest": {
            "configuration": {"cutoff_exclusive": "2026-01-10"},
            "manifest_hash": "manifest-hash",
            "decision_contract_hash": "contract-hash",
            "sealed_research_watermark": 123,
            "sealed_outcome_watermark": "outcome-watermark",
        },
        "existing_candidate_evaluation_status": "NO_EXISTING_CANDIDATES",
        "funded_authorization": False,
    }


def _artifacts(path: Path) -> None:
    path.mkdir()
    (path / "result.json").write_text(
        json.dumps(_result(), sort_keys=True) + "\n", encoding="utf-8"
    )
    (path / "report.md").write_text("report\n", encoding="utf-8")
    (path / "ranked_rules.csv").write_text("rule_id\n", encoding="utf-8")


def test_latest_complete_status_validates_report_and_cache(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    status_path = tmp_path / "state" / "latest.json"
    cache_path = tmp_path / "cache.sqlite"
    cache_path.write_bytes(b"cache")
    _artifacts(report_dir)
    connection = _cache()

    record = write_latest_complete_status(
        status_path,
        result=_result(),
        report_dir=report_dir,
        cache_path=cache_path,
        cache=connection,
        refresh_result={
            "refresh_id": "refresh-1",
            "diagnostics": {"REPLAY_CALLS": 0},
        },
    )
    status = latest_complete_status_report(status_path)

    assert record["cache"]["model_mappings"] == 2
    assert record["cache"]["pending_decisions"] == 0
    assert status["status"] == "HEALTHY"
    assert status["latest_complete"]["result_content_hash"] == "content-hash"
    assert not list(status_path.parent.glob("*.tmp"))


def test_failed_or_incomplete_run_cannot_replace_latest_complete(tmp_path: Path) -> None:
    status_path = tmp_path / "latest.json"
    status_path.write_text("previous complete\n", encoding="utf-8")
    failed = {**_result(), "status": "FAILED_ANALYSIS"}

    try:
        write_latest_complete_status(
            status_path,
            result=failed,
            report_dir=tmp_path,
            cache_path=tmp_path / "missing.sqlite",
            cache=_cache(),
            refresh_result={},
        )
    except ValueError as exc:
        assert "completed analytical runs only" in str(exc)
    else:
        raise AssertionError("failed run replaced the latest-complete pointer")

    assert status_path.read_text(encoding="utf-8") == "previous complete\n"


def test_latest_status_detects_artifact_tampering(tmp_path: Path) -> None:
    report_dir = tmp_path / "report"
    status_path = tmp_path / "latest.json"
    cache_path = tmp_path / "cache.sqlite"
    cache_path.write_bytes(b"cache")
    _artifacts(report_dir)
    write_latest_complete_status(
        status_path,
        result=_result(),
        report_dir=report_dir,
        cache_path=cache_path,
        cache=_cache(),
        refresh_result={},
    )
    (report_dir / "result.json").write_text("tampered\n", encoding="utf-8")

    status = latest_complete_status_report(status_path)

    assert status["status"] == "ATTENTION"
    assert "LATEST_RESULT_HASH_MISMATCH" in status["alerts"]
