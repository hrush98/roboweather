from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from scripts.run_market_tape import _remaining_overall_seconds


def test_overall_deadline_is_preserved_across_process_restarts(tmp_path: Path) -> None:
    state_path = tmp_path / "runtime" / "deadline.json"
    started = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    first = _remaining_overall_seconds(
        state_path,
        72 * 60 * 60,
        now=started,
    )
    after_restart = _remaining_overall_seconds(
        state_path,
        72 * 60 * 60,
        now=started + timedelta(hours=60),
    )
    elapsed = _remaining_overall_seconds(
        state_path,
        72 * 60 * 60,
        now=started + timedelta(hours=73),
    )
    payload = json.loads(state_path.read_text())

    assert first == 72 * 60 * 60
    assert after_restart == 12 * 60 * 60
    assert elapsed == 0
    assert payload["started_at_utc"] == started.isoformat()


def test_overall_deadline_state_fails_closed_when_malformed(tmp_path: Path) -> None:
    state_path = tmp_path / "deadline.json"
    state_path.write_text(json.dumps({"started_at_utc": "missing deadline"}))

    try:
        _remaining_overall_seconds(state_path, 60)
    except ValueError as exc:
        assert "invalid lifecycle deadline state" in str(exc)
    else:
        raise AssertionError("malformed deadline state must fail closed")
