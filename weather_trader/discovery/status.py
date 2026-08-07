from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from weather_trader.discovery.registry import DiscoveryRegistry


def discovery_status_report(
    registry_path: Path,
    *,
    now_utc: datetime | None = None,
    stale_after_seconds: int = 36 * 60 * 60,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = registry_path.expanduser()
    if not path.exists():
        return {
            "status": "NOT_INITIALIZED",
            "registry": str(path),
            "alerts": ["REGISTRY_NOT_INITIALIZED"],
            "funded_authorization": False,
        }
    with DiscoveryRegistry(path, read_only=True) as registry:
        events = registry.recent_scheduler_events(limit=50)
        latest_event = events[0] if events else None
        active = registry.active_candidate_versions()
        roles = Counter(str(candidate["to_role"]) for candidate in active)
        latest_scorecard = registry.connection.execute(
            "select max(created_at_utc) from candidate_scorecards where evidence_kind='FORWARD_SHADOW'"
        ).fetchone()[0]
        latest_run = registry.connection.execute(
            """select runs.run_id,runs.research_watermark,runs.outcome_watermark,
                      runs.venue_settlement_watermark,outcomes.status,outcomes.completed_at_utc
               from discovery_runs runs
               left join discovery_run_outcomes outcomes using(run_id)
               order by coalesce(outcomes.completed_at_utc,runs.created_at_utc) desc limit 1"""
        ).fetchone()
        scorecards = registry.latest_scorecards("FORWARD_SHADOW")

    alerts = []
    if latest_event and latest_event["event_type"] == "FAILED":
        alerts.append("LATEST_SCHEDULER_CYCLE_FAILED")
    unmatched = {
        event["cycle_id"] for event in events if event["event_type"] == "STARTED"
    } - {
        event["cycle_id"]
        for event in events
        if event["event_type"] in {"COMPLETED", "FAILED", "SKIPPED"}
    }
    if unmatched:
        alerts.append("SCHEDULER_CYCLE_INCOMPLETE")
    stale = False
    if active:
        if latest_scorecard is None:
            stale = True
        else:
            score_time = datetime.fromisoformat(str(latest_scorecard).replace("Z", "+00:00"))
            stale = now - score_time.astimezone(timezone.utc) > timedelta(
                seconds=stale_after_seconds
            )
    if stale:
        alerts.append("ACTIVE_CANDIDATE_EVALUATION_STALE")
    summaries = []
    for scorecard in scorecards:
        stats = scorecard["statistics"]
        candidate_id = str(scorecard["candidate_version_id"])
        candidate = next(
            (item for item in active if item["candidate_version_id"] == candidate_id), None
        )
        base = stats.get("fill_scenarios", {}).get("base_displayed_depth", {})
        summaries.append({
            "candidate_version_id": candidate_id,
            "role": candidate["to_role"] if candidate else "INACTIVE",
            "review_state": stats.get("review_state"),
            "venue_dates": len(stats.get("effective_venue_resolved_market_dates", [])),
            "base_rr": base.get("venue_rr"),
            "blockers": stats.get("blockers", []),
            "scorecard_created_at_utc": scorecard["created_at_utc"],
        })
    run_payload = dict(latest_run) if latest_run is not None else None
    return {
        "status": "HEALTHY" if not alerts else "ATTENTION",
        "registry": str(path),
        "registry_bytes": path.stat().st_size,
        "latest_scheduler_event": latest_event,
        "latest_discovery_run": run_payload,
        "latest_forward_scorecard_at_utc": latest_scorecard,
        "active_candidate_count": len(active),
        "roles": dict(sorted(roles.items())),
        "stale_evaluation": stale,
        "alerts": alerts,
        "scorecard_summaries": summaries,
        "recent_failures": [
            event for event in events if event["event_type"] == "FAILED"
        ][:5],
        "funded_authorization": False,
    }
