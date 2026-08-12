from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


STATUS_VERSION = "discovery_latest_complete_v1"
COMPLETED_STATES = {
    "COMPLETED_WITH_EMERGED_STRATEGIES",
    "COMPLETED_NO_EMERGED_STRATEGIES",
}


def write_latest_complete_status(
    path: Path,
    *,
    result: Mapping[str, Any],
    report_dir: Path,
    cache_path: Path,
    cache: sqlite3.Connection,
    refresh_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically point operator status at one fully written completed report."""
    status = str(result.get("status", ""))
    if status not in COMPLETED_STATES:
        raise ValueError("latest-complete status accepts completed analytical runs only")
    artifacts = {}
    for name in ("result.json", "report.md", "ranked_rules.csv"):
        artifact = report_dir / name
        data = artifact.read_bytes()
        artifacts[name] = {
            "path": str(artifact.resolve()),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    decision_counts = {
        str(row[0]): int(row[1])
        for row in cache.execute(
            "select status,count(*) from executable_decisions group by status order by status"
        )
    }
    manifest = dict(result["manifest"])
    record = {
        "status_version": STATUS_VERSION,
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "analytical_status": status,
        "plain_language_answer": str(result["plain_language_answer"]),
        "result_content_hash": str(result["result_content_hash"]),
        "report_dir": str(report_dir.resolve()),
        "artifacts": artifacts,
        "cutoff_exclusive": manifest["configuration"]["cutoff_exclusive"],
        "manifest_hash": manifest["manifest_hash"],
        "decision_contract_hash": manifest["decision_contract_hash"],
        "sealed_research_watermark": manifest["sealed_research_watermark"],
        "sealed_outcome_watermark": manifest["sealed_outcome_watermark"],
        "cache": {
            "path": str(cache_path.expanduser().resolve()),
            "bytes": cache_path.expanduser().stat().st_size,
            "model_mappings": int(cache.execute(
                "select count(*) from model_decision_mappings"
            ).fetchone()[0]),
            "decisions": sum(decision_counts.values()),
            "decision_status_counts": decision_counts,
            "pending_decisions": decision_counts.get("PENDING", 0),
            "refresh_id": refresh_result.get("refresh_id"),
            "refresh_diagnostics": dict(refresh_result.get("diagnostics") or {}),
        },
        "existing_candidate_evaluation_status": result.get(
            "existing_candidate_evaluation_status"
        ),
        "funded_authorization": False,
    }
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)
    return record


def latest_complete_status_report(path: Path) -> dict[str, Any]:
    """Validate and summarize the latest-complete pointer for operator display."""
    target = path.expanduser()
    if not target.exists():
        return {
            "status": "NOT_INITIALIZED",
            "status_path": str(target),
            "alerts": ["NO_COMPLETE_DISCOVERY_REPORT"],
            "funded_authorization": False,
        }
    alerts: list[str] = []
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "ATTENTION",
            "status_path": str(target),
            "alerts": ["LATEST_STATUS_UNREADABLE"],
            "detail": f"{type(exc).__name__}: {exc}",
            "funded_authorization": False,
        }
    if record.get("status_version") != STATUS_VERSION:
        alerts.append("LATEST_STATUS_VERSION_MISMATCH")
    if record.get("analytical_status") not in COMPLETED_STATES:
        alerts.append("LATEST_REPORT_NOT_COMPLETE")
    result_artifact = (record.get("artifacts") or {}).get("result.json") or {}
    result_path = Path(str(result_artifact.get("path", "")))
    if not result_path.is_file():
        alerts.append("LATEST_RESULT_MISSING")
    else:
        data = result_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != result_artifact.get("sha256"):
            alerts.append("LATEST_RESULT_HASH_MISMATCH")
        else:
            try:
                result = json.loads(data)
            except json.JSONDecodeError:
                alerts.append("LATEST_RESULT_UNREADABLE")
            else:
                if result.get("result_content_hash") != record.get("result_content_hash"):
                    alerts.append("LATEST_RESULT_CONTENT_HASH_MISMATCH")
    cache = dict(record.get("cache") or {})
    if int(cache.get("pending_decisions", 0)):
        alerts.append("LATEST_CACHE_HAS_PENDING_DECISIONS")
    return {
        "status": "HEALTHY" if not alerts else "ATTENTION",
        "status_path": str(target),
        "latest_complete": record,
        "alerts": alerts,
        "funded_authorization": False,
    }
