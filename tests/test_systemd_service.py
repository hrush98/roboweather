from __future__ import annotations

import asyncio

import pytest

from weather_trader.ui.systemd_service import (
    SystemdUserServiceController,
    UserServiceSpec,
    _CommandResult,
    _parse_properties,
    _service_status,
)


class _FakeController(SystemdUserServiceController):
    def __init__(self, results: list[_CommandResult]) -> None:
        super().__init__([UserServiceSpec("tape", "Market Tape", "tape.service")])
        self.results = list(results)
        self.commands: list[tuple[str, ...]] = []

    async def _run(self, command: tuple[str, ...]) -> _CommandResult:
        self.commands.append(command)
        return self.results.pop(0)


def test_parse_properties_and_status_mapping() -> None:
    assert _parse_properties("LoadState=loaded\nActiveState=active\n") == {
        "LoadState": "loaded",
        "ActiveState": "active",
    }
    assert _service_status("active") == "RUNNING"
    assert _service_status("failed") == "FAILED"
    assert _service_status("inactive") == "STOPPED"
    assert _service_status("new-state") == "UNKNOWN"


def test_refresh_maps_running_service_and_journal() -> None:
    async def scenario() -> None:
        controller = _FakeController(
            [
                _CommandResult(
                    0,
                    "\n".join(
                        [
                            "LoadState=loaded",
                            "ActiveState=active",
                            "SubState=running",
                            "MainPID=123",
                            "NRestarts=2",
                            "ExecMainStatus=0",
                            "ActiveEnterTimestampMonotonic=1",
                        ]
                    ),
                ),
                _CommandResult(0, "first line\nlatest line\n"),
            ]
        )

        snapshot = await controller.refresh("tape")
        assert snapshot.status == "RUNNING"
        assert snapshot.pid == 123
        assert snapshot.restart_count == 2
        assert snapshot.substate == "running"
        assert snapshot.uptime_seconds is not None

        assert await controller.refresh_logs("tape") == ["first line", "latest line"]
        assert controller.snapshot("tape").latest_log == "latest line"

    asyncio.run(scenario())


def test_refresh_distinguishes_not_installed_and_unavailable() -> None:
    async def scenario() -> None:
        not_installed = _FakeController(
            [_CommandResult(1, "LoadState=not-found\nActiveState=inactive\n")]
        )
        unavailable = _FakeController([_CommandResult(1, "Failed to connect to bus")])

        assert (await not_installed.refresh("tape")).status == "NOT_INSTALLED"
        snapshot = await unavailable.refresh("tape")
        assert snapshot.status == "UNAVAILABLE"
        assert snapshot.error == "Failed to connect to bus"

    asyncio.run(scenario())


def test_actions_use_systemctl_and_surface_failures() -> None:
    async def scenario() -> None:
        controller = _FakeController(
            [
                _CommandResult(0, ""),
                _CommandResult(0, "LoadState=loaded\nActiveState=active\nSubState=running\n"),
                _CommandResult(1, "permission denied"),
            ]
        )

        assert (await controller.start("tape")).status == "RUNNING"
        assert controller.commands[0] == ("systemctl", "--user", "start", "tape.service")
        with pytest.raises(RuntimeError, match="permission denied"):
            await controller.stop("tape")

    asyncio.run(scenario())
