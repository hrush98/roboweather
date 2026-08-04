from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import os
import time


@dataclass(frozen=True)
class UserServiceSpec:
    name: str
    label: str
    unit: str


@dataclass(frozen=True)
class UserServiceSnapshot:
    name: str
    label: str
    unit: str
    status: str
    substate: str
    pid: int | None
    uptime_seconds: float | None
    exit_code: int | None
    restart_count: int
    latest_log: str
    error: str | None = None


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    output: str


class SystemdUserServiceController:
    """Observe and control durable user services without owning their lifetime."""

    def __init__(
        self,
        specs: list[UserServiceSpec],
        *,
        env: dict[str, str] | None = None,
        command_timeout_seconds: float = 10.0,
    ) -> None:
        self.env = dict(env or os.environ)
        self.command_timeout_seconds = command_timeout_seconds
        self._specs = {spec.name: spec for spec in specs}
        self._logs = {spec.name: [] for spec in specs}
        self._snapshots = {
            spec.name: UserServiceSnapshot(
                name=spec.name,
                label=spec.label,
                unit=spec.unit,
                status="UNKNOWN",
                substate="",
                pid=None,
                uptime_seconds=None,
                exit_code=None,
                restart_count=0,
                latest_log="",
            )
            for spec in specs
        }

    def names(self) -> list[str]:
        return list(self._specs)

    def snapshot(self, name: str) -> UserServiceSnapshot:
        return self._snapshots[name]

    def snapshots(self) -> list[UserServiceSnapshot]:
        return [self.snapshot(name) for name in self.names()]

    def logs(self, name: str) -> list[str]:
        return list(self._logs[name])

    async def refresh(self, name: str) -> UserServiceSnapshot:
        spec = self._specs[name]
        result = await self._run(
            (
                "systemctl",
                "--user",
                "show",
                spec.unit,
                "--no-pager",
                "--property=LoadState,ActiveState,SubState,MainPID,NRestarts,ExecMainStatus,ActiveEnterTimestampMonotonic",
            )
        )
        properties = _parse_properties(result.output)
        load_state = properties.get("LoadState", "")
        if load_state == "not-found":
            snapshot = self._unavailable_snapshot(spec, "NOT_INSTALLED", result.output.strip() or None)
        elif result.returncode != 0:
            snapshot = self._unavailable_snapshot(
                spec,
                "UNAVAILABLE",
                result.output.strip() or f"systemctl exited {result.returncode}",
            )
        else:
            status = _service_status(properties.get("ActiveState", ""))
            pid = _positive_int(properties.get("MainPID"))
            started_micros = _positive_int(properties.get("ActiveEnterTimestampMonotonic"))
            uptime = None
            if status == "RUNNING" and started_micros is not None:
                uptime = max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - started_micros / 1_000_000)
            snapshot = UserServiceSnapshot(
                name=spec.name,
                label=spec.label,
                unit=spec.unit,
                status=status,
                substate=properties.get("SubState", ""),
                pid=pid,
                uptime_seconds=uptime,
                exit_code=_nonnegative_int(properties.get("ExecMainStatus")),
                restart_count=_nonnegative_int(properties.get("NRestarts")) or 0,
                latest_log=self._logs[name][-1] if self._logs[name] else "",
            )
        self._snapshots[name] = snapshot
        return snapshot

    async def refresh_logs(self, name: str, *, limit: int = 200) -> list[str]:
        spec = self._specs[name]
        result = await self._run(
            (
                "journalctl",
                "--user",
                "-u",
                spec.unit,
                "-n",
                str(limit),
                "--output=cat",
                "--no-pager",
            )
        )
        if result.returncode == 0:
            self._logs[name] = [line for line in result.output.splitlines() if line.strip()][-limit:]
            current = self._snapshots[name]
            self._snapshots[name] = replace(
                current,
                latest_log=self._logs[name][-1] if self._logs[name] else "",
            )
        return self.logs(name)

    async def start(self, name: str) -> UserServiceSnapshot:
        return await self._action(name, "start")

    async def stop(self, name: str) -> UserServiceSnapshot:
        return await self._action(name, "stop")

    async def restart(self, name: str) -> UserServiceSnapshot:
        return await self._action(name, "restart")

    async def _action(self, name: str, action: str) -> UserServiceSnapshot:
        spec = self._specs[name]
        result = await self._run(("systemctl", "--user", action, spec.unit))
        if result.returncode != 0:
            message = result.output.strip() or f"systemctl {action} exited {result.returncode}"
            raise RuntimeError(message)
        return await self.refresh(name)

    async def _run(self, command: tuple[str, ...]) -> _CommandResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self.env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.command_timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return _CommandResult(124, f"command timed out: {' '.join(command)}")
        return _CommandResult(process.returncode or 0, stdout.decode(errors="replace"))

    def _unavailable_snapshot(
        self, spec: UserServiceSpec, status: str, error: str | None
    ) -> UserServiceSnapshot:
        return UserServiceSnapshot(
            name=spec.name,
            label=spec.label,
            unit=spec.unit,
            status=status,
            substate="",
            pid=None,
            uptime_seconds=None,
            exit_code=None,
            restart_count=0,
            latest_log=self._logs[spec.name][-1] if self._logs[spec.name] else "",
            error=error,
        )


def _parse_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _service_status(active_state: str) -> str:
    return {
        "active": "RUNNING",
        "activating": "STARTING",
        "deactivating": "STOPPING",
        "failed": "FAILED",
        "inactive": "STOPPED",
    }.get(active_state, "UNKNOWN")


def _positive_int(value: str | None) -> int | None:
    parsed = _nonnegative_int(value)
    return parsed if parsed and parsed > 0 else None


def _nonnegative_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
