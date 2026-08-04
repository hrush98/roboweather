from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from scripts.run_market_tape import (
    _build_fingerprint,
    _exclusive_catalog_writer_lock,
    _remaining_overall_seconds,
    _validation_run_id_from_state,
)


def test_catalog_writer_lock_rejects_duplicate_and_releases(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"

    with _exclusive_catalog_writer_lock(catalog) as lock_path:
        assert lock_path.read_text().startswith("pid=")
        try:
            with _exclusive_catalog_writer_lock(catalog):
                raise AssertionError("duplicate lock unexpectedly succeeded")
        except RuntimeError as exc:
            assert "already owns catalog lock" in str(exc)

    with _exclusive_catalog_writer_lock(catalog):
        pass


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
    assert payload["validation_run_id"].startswith("tape-validation-")
    assert _validation_run_id_from_state(state_path) == payload["validation_run_id"]


def test_overall_deadline_state_fails_closed_when_malformed(tmp_path: Path) -> None:
    state_path = tmp_path / "deadline.json"
    state_path.write_text(json.dumps({"started_at_utc": "missing deadline"}))

    try:
        _remaining_overall_seconds(state_path, 60)
    except ValueError as exc:
        assert "invalid lifecycle deadline state" in str(exc)
    else:
        raise AssertionError("malformed deadline state must fail closed")


def test_validation_run_state_fails_closed_when_identity_is_missing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "deadline.json"
    state_path.write_text(
        json.dumps(
            {
                "started_at_utc": "2026-07-30T12:00:00+00:00",
                "deadline_at_utc": "2026-07-30T13:00:00+00:00",
            }
        )
    )

    try:
        _validation_run_id_from_state(state_path)
    except ValueError as exc:
        assert "lacks validation_run_id" in str(exc)
    else:
        raise AssertionError("missing validation identity must fail closed")


def test_unrelated_documentation_change_does_not_change_build_fingerprint(
    tmp_path: Path,
) -> None:
    recorder = tmp_path / "weather_trader" / "tape" / "collector.py"
    recorder.parent.mkdir(parents=True)
    recorder.write_text("RECORDER_VERSION = 1\n")
    docs = tmp_path / "docs" / "forecast.md"
    docs.parent.mkdir()
    docs.write_text("before\n")
    paths = ("weather_trader/tape/collector.py",)
    dependencies = {"requests": "2.32.0", "websockets": "15.0"}

    before = _build_fingerprint(
        tmp_path,
        source_paths=paths,
        dependency_versions=dependencies,
    )
    docs.write_text("after\n")
    after = _build_fingerprint(
        tmp_path,
        source_paths=paths,
        dependency_versions=dependencies,
    )

    assert after == before


def test_recorder_source_change_changes_build_fingerprint(tmp_path: Path) -> None:
    recorder = tmp_path / "weather_trader" / "tape" / "collector.py"
    recorder.parent.mkdir(parents=True)
    recorder.write_text("RECORDER_VERSION = 1\n")
    paths = ("weather_trader/tape/collector.py",)
    dependencies = {"requests": "2.32.0", "websockets": "15.0"}

    before = _build_fingerprint(
        tmp_path,
        source_paths=paths,
        dependency_versions=dependencies,
    )
    recorder.write_text("RECORDER_VERSION = 2\n")
    after = _build_fingerprint(
        tmp_path,
        source_paths=paths,
        dependency_versions=dependencies,
    )

    assert after != before
