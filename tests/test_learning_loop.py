from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from weather_trader.learning_loop import (
    LearningLoopError,
    capture_learning,
    check_learning,
    discover_learnings,
    revisit_learning,
)


def _capture_args(**overrides: object) -> argparse.Namespace:
    values = {
        "title": "Facts and judgment age differently",
        "kind": "DESIGN",
        "origin": "T0001",
        "why": "Agents need fresh machine truth without silently rewriting human judgment.",
        "happened": "Several living documents described different moments in the system's evolution.",
        "concept": "Separate regenerated facts from reviewed state and append-only history.",
        "intuition": "Fast-changing observations and slow-changing decisions decay at different rates.",
        "pattern": "The same split appears in caches versus configuration and telemetry versus incident reviews.",
        "application": "Generate runtime facts while requiring approval for strategic state changes.",
        "questions": "Which freshness warnings deserve automatic escalation?",
        "evidence": ["T0001 and agent_loop/facts.json"],
        "revisit_on": "2026-08-25",
        "status": "CAPTURED",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_capture_and_revisit_preserve_history_and_maturity(tmp_path: Path) -> None:
    path = capture_learning(tmp_path, _capture_args())

    assert path.name == "L0001-facts-and-judgment-age-differently.md"
    record = discover_learnings(tmp_path)[0]
    assert record.status == "CAPTURED"
    assert "L0001" in (tmp_path / "learning" / "INDEX.md").read_text(encoding="utf-8")

    revisit_learning(
        tmp_path,
        argparse.Namespace(
            learning_id="L0001",
            reflection="The separation also makes uncertainty visible instead of averaging it into prose.",
            connection="Event sourcing preserves observations while projections provide current views.",
            action="Keep generated projections reproducible and judgment changes reviewable.",
            status="INTEGRATED",
            revisit_on="2026-09-25",
        ),
    )

    record = discover_learnings(tmp_path)[0]
    assert record.status == "INTEGRATED"
    assert "Event sourcing" in record.body
    assert "Several living documents" in record.body
    assert "INTEGRATED" in (tmp_path / "learning" / "INDEX.md").read_text(encoding="utf-8")
    assert check_learning(tmp_path) == []


def test_capture_rejects_invalid_revisit_date(tmp_path: Path) -> None:
    with pytest.raises(LearningLoopError, match="revisit_on must be YYYY-MM-DD"):
        capture_learning(tmp_path, _capture_args(revisit_on="someday"))
