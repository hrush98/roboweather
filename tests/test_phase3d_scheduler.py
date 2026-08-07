from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pytest

from weather_trader.discovery.registry import DiscoveryRegistry, RegistryWriterLocked
from weather_trader.discovery.scheduler import (
    BoundedDiscoveryScheduler,
    SchedulerConfig,
    SchedulerProcessLock,
)
from weather_trader.discovery.status import discovery_status_report


NOW = datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc)


class FakeRunner:
    def __init__(self, *, failure: Exception | BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, tuple[str, ...], int]] = []

    def run(
        self, name: str, command: Sequence[str], *, timeout_seconds: int
    ) -> dict[str, Any]:
        self.calls.append((name, tuple(command), timeout_seconds))
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        return {
            "status": "COMPLETED",
            "task": name,
            "funded_authorization": False,
        }


def _config(tmp_path: Path, **overrides: Any) -> SchedulerConfig:
    values = {
        "research_db": tmp_path / "research.sqlite",
        "tape_catalog": tmp_path / "tape.sqlite",
        "registry": tmp_path / "registry.sqlite",
        "source_start_date": "2026-07-23",
        "python_executable": Path("/test/python"),
        "interval_seconds": 3600,
        "discovery_cadence_seconds": 7 * 24 * 60 * 60,
        "task_timeout_seconds": 60,
        "maximum_cycle_runtime_seconds": 180,
    }
    values.update(overrides)
    return SchedulerConfig(**values)


def test_c6_cycle_is_bounded_idempotent_and_records_operator_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = FakeRunner()
    scheduler = BoundedDiscoveryScheduler(config, task_runner=runner)

    with SchedulerProcessLock(config.registry):
        first = scheduler.run_cycle(now_utc=NOW)
        repeated = scheduler.run_cycle(now_utc=NOW.replace(minute=45))

    assert first["status"] == "COMPLETED"
    assert first["tasks"] == [
        "continuous_evaluation",
        "role_transitions",
        "recurring_discovery",
    ]
    assert repeated["status"] == "NOOP_TERMINAL_CYCLE"
    assert [call[0] for call in runner.calls] == first["tasks"]
    assert all(call[2] <= config.task_timeout_seconds for call in runner.calls)
    with DiscoveryRegistry(config.registry, read_only=True) as registry:
        events = registry.recent_scheduler_events()
        assert [event["event_type"] for event in reversed(events)] == [
            "STARTED",
            "COMPLETED",
        ]
        assert events[0]["details"]["funded_authorization"] is False

    report = discovery_status_report(config.registry, now_utc=NOW)
    assert report["status"] == "HEALTHY"
    assert report["latest_scheduler_event"]["event_type"] == "COMPLETED"
    assert report["funded_authorization"] is False


def test_c6_unmatched_cycle_resumes_after_process_crash_without_duplicate_start(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    crashing = FakeRunner(failure=KeyboardInterrupt())
    scheduler = BoundedDiscoveryScheduler(config, task_runner=crashing)
    with SchedulerProcessLock(config.registry):
        with pytest.raises(KeyboardInterrupt):
            scheduler.run_cycle(now_utc=NOW)

    with DiscoveryRegistry(config.registry, read_only=True) as registry:
        assert [event["event_type"] for event in registry.recent_scheduler_events()] == [
            "STARTED"
        ]

    resumed = FakeRunner()
    with SchedulerProcessLock(config.registry):
        result = BoundedDiscoveryScheduler(config, task_runner=resumed).run_cycle(
            now_utc=NOW
        )
    assert result["status"] == "COMPLETED"
    with DiscoveryRegistry(config.registry, read_only=True) as registry:
        assert registry.table_counts()["scheduler_events"] == 2


def test_c6_failure_and_storage_budget_are_append_only_and_alertable(tmp_path: Path) -> None:
    config = _config(tmp_path, maximum_registry_bytes=1)
    scheduler = BoundedDiscoveryScheduler(config, task_runner=FakeRunner())
    with SchedulerProcessLock(config.registry):
        with pytest.raises(RuntimeError, match="storage budget exceeded"):
            scheduler.run_cycle(now_utc=NOW)

    report = discovery_status_report(config.registry, now_utc=NOW)
    assert report["status"] == "ATTENTION"
    assert "LATEST_SCHEDULER_CYCLE_FAILED" in report["alerts"]
    assert report["recent_failures"][0]["details"]["failed_task"] == "startup"


def test_c6_scheduler_process_lock_is_exclusive(tmp_path: Path) -> None:
    registry = tmp_path / "registry.sqlite"
    with SchedulerProcessLock(registry):
        with pytest.raises(RegistryWriterLocked, match="another discovery scheduler"):
            with SchedulerProcessLock(registry):
                pass


def test_c6_service_is_durable_bounded_and_systemd_owned() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "deploy/systemd/roboweather-phase3d-discovery.service"
    ).read_text(encoding="utf-8")
    assert "scripts/run_phase3d_scheduler.py" in text
    assert "Restart=on-failure" in text
    assert "MemoryMax=4G" in text
    assert "ReadWritePaths=%h/.local/state/roboweather" in text
    assert "WantedBy=default.target" in text
    assert "textual" not in text.lower()
