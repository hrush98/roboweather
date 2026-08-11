from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_trader.agent_loop import (
    _service_state,
    check,
    close_thread,
    discover_threads,
    park_thread,
    refresh,
    resume_thread,
    start_thread,
)


def _root(tmp_path: Path) -> Path:
    (tmp_path / "agent_loop").mkdir()
    (tmp_path / "agent_loop" / "STATE.md").write_text(
        "# Agent Loop State\n\nLast reviewed: 2026-08-11\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    for name in (
        "current-trading-system-audit.md",
        "execution-rebuild-roadmap.md",
        "live-trading-journal.md",
        "project-overview.md",
        "continuous-improvement-loop.md",
    ):
        (tmp_path / "docs" / name).write_text("# Document\n\nLast updated: 2026-08-11\n", encoding="utf-8")
    return tmp_path


def _start_args(**overrides: object) -> argparse.Namespace:
    values = {
        "title": "Verify handoff",
        "question": "Can another agent resume this work without re-deriving context?",
        "next_action": "Run the lifecycle test.",
        "closure_output": "tests/test_agent_loop.py",
        "priority": "normal",
        "owner": "test-agent",
        "status": "ACTIVE",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_refresh_generates_facts_and_empty_index(tmp_path: Path) -> None:
    root = _root(tmp_path)

    refresh(root)

    facts = json.loads((root / "agent_loop" / "facts.json").read_text(encoding="utf-8"))
    assert facts["schema_version"] == 1
    assert facts["board"]["open"] == 0
    assert "facts_fingerprint" in facts
    assert "No open threads" in (root / "board" / "INDEX.md").read_text(encoding="utf-8")
    assert check(root) == []


def test_thread_lifecycle_preserves_handoff_and_closed_history(tmp_path: Path) -> None:
    root = _root(tmp_path)
    refresh(root)

    path = start_thread(root, _start_args())
    assert path.name.startswith("T0001-")
    assert discover_threads(root)[0].status == "ACTIVE"
    assert "T0001-verify-handoff.md" in (root / "board" / "INDEX.md").read_text(encoding="utf-8")

    park_thread(
        root,
        argparse.Namespace(
            thread_id="T0001",
            answer="Yes, if evidence and the next action remain explicit.",
            evidence=["The lifecycle fixture passed through start and park."],
            next_action="Resume T0001 and verify its fact fingerprint.",
            status="PARKED",
        ),
    )
    assert discover_threads(root)[0].status == "PARKED"

    summary = resume_thread(root, argparse.Namespace(thread_id="T0001"))
    assert "Facts since park: unchanged" in summary
    assert "Resume T0001" in summary

    destination = close_thread(
        root,
        argparse.Namespace(
            thread_id="T0001",
            outcome="The bounded lifecycle is mechanically resumable.",
            durable_output="tests/test_agent_loop.py",
            evidence=["Start, park, resume, and close completed."],
        ),
    )
    assert destination == root / "board" / "closed" / destination.parent.name / path.name
    record = discover_threads(root)[0]
    assert record.status == "CLOSED"
    assert not path.exists()
    assert "The bounded lifecycle is mechanically resumable" in (root / "board" / "INDEX.md").read_text(encoding="utf-8")
    assert json.loads((root / "agent_loop" / "facts.json").read_text(encoding="utf-8"))["board"]["open"] == 0
    assert check(root) == []


def test_check_enforces_active_and_open_caps(tmp_path: Path) -> None:
    root = _root(tmp_path)
    refresh(root)
    for index in range(4):
        start_thread(
            root,
            _start_args(
                title=f"Question {index}",
                question=f"Is question {index} answered?",
                status="PARKED" if index == 3 else "ACTIVE",
            ),
        )

    fourth = next(record for record in discover_threads(root) if record.thread_id == "T0004")
    text = fourth.path.read_text(encoding="utf-8").replace("status: PARKED", "status: ACTIVE")
    fourth.path.write_text(text, encoding="utf-8")

    assert "active-thread cap exceeded: 4/3" in check(root)


def test_inactive_service_is_observed_not_unavailable(tmp_path: Path, monkeypatch) -> None:
    class Result:
        returncode = 3
        stdout = "inactive\n"
        stderr = ""

    monkeypatch.setattr("weather_trader.agent_loop.subprocess.run", lambda *args, **kwargs: Result())

    assert _service_state("example.service", tmp_path) == {
        "available": True,
        "value": "inactive",
        "returncode": 3,
    }
