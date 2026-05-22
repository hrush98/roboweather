from __future__ import annotations

import asyncio
import os
import signal
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    label: str
    command: tuple[str, ...]


@dataclass
class ProcessSnapshot:
    name: str
    label: str
    status: str
    pid: int | None
    started_at: datetime | None
    stopped_at: datetime | None
    exit_code: int | None
    restart_count: int
    latest_log: str
    command: str

    @property
    def uptime_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.stopped_at or datetime.now(timezone.utc)
        return max(0.0, (end - self.started_at).total_seconds())


@dataclass
class _ManagedProcess:
    spec: ProcessSpec
    process: asyncio.subprocess.Process | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    exit_code: int | None = None
    restart_count: int = 0
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    reader_task: asyncio.Task | None = None
    waiter_task: asyncio.Task | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.returncode is None


class ProcessSupervisor:
    def __init__(
        self,
        specs: list[ProcessSpec],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        log_limit: int = 500,
        stop_timeout_seconds: float = 5.0,
    ) -> None:
        self.cwd = cwd
        self.env = dict(env or os.environ)
        self.log_limit = log_limit
        self.stop_timeout_seconds = stop_timeout_seconds
        self._processes = {
            spec.name: _ManagedProcess(spec=spec, logs=deque(maxlen=log_limit))
            for spec in specs
        }

    def names(self) -> list[str]:
        return list(self._processes)

    def snapshot(self, name: str) -> ProcessSnapshot:
        managed = self._processes[name]
        status = "STOPPED"
        if managed.is_running():
            status = "RUNNING"
        elif managed.exit_code is not None:
            status = "EXITED" if managed.exit_code == 0 else "FAILED"
        latest_log = managed.logs[-1] if managed.logs else ""
        return ProcessSnapshot(
            name=managed.spec.name,
            label=managed.spec.label,
            status=status,
            pid=managed.process.pid if managed.process is not None else None,
            started_at=managed.started_at,
            stopped_at=managed.stopped_at,
            exit_code=managed.exit_code,
            restart_count=managed.restart_count,
            latest_log=latest_log,
            command=" ".join(managed.spec.command),
        )

    def snapshots(self) -> list[ProcessSnapshot]:
        return [self.snapshot(name) for name in self.names()]

    def logs(self, name: str) -> list[str]:
        return list(self._processes[name].logs)

    async def start(
        self,
        name: str,
        *,
        extra_env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessSnapshot:
        managed = self._processes[name]
        if managed.is_running():
            return self.snapshot(name)
        child_env = dict(self.env)
        if extra_env:
            child_env.update(extra_env)
        managed.restart_count += 1
        managed.started_at = datetime.now(timezone.utc)
        managed.stopped_at = None
        managed.exit_code = None
        managed.logs.clear()
        managed.logs.append(f"starting: {' '.join(managed.spec.command)}")
        process = await asyncio.create_subprocess_exec(
            *managed.spec.command,
            cwd=str(self.cwd),
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        managed.process = process
        managed.logs.append(f"started pid={process.pid}")
        managed.reader_task = asyncio.create_task(self._read_output(managed))
        managed.waiter_task = asyncio.create_task(self._wait_for_exit(managed))
        return self.snapshot(name)

    async def stop(self, name: str) -> ProcessSnapshot:
        managed = self._processes[name]
        process = managed.process
        if process is None or process.returncode is not None:
            return self.snapshot(name)
        managed.logs.append("stopping")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=self.stop_timeout_seconds)
        except asyncio.TimeoutError:
            managed.logs.append("stop timeout; killing")
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        await self._join_tasks(managed)
        return self.snapshot(name)

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(name) for name in self.names()))

    async def _read_output(self, managed: _ManagedProcess) -> None:
        process = managed.process
        if process is None or process.stdout is None:
            return
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            managed.logs.append(line.decode(errors="replace").rstrip())

    async def _wait_for_exit(self, managed: _ManagedProcess) -> None:
        process = managed.process
        if process is None:
            return
        managed.exit_code = await process.wait()
        managed.stopped_at = datetime.now(timezone.utc)
        managed.logs.append(f"exited code={managed.exit_code}")

    async def _join_tasks(self, managed: _ManagedProcess) -> None:
        tasks = [task for task in (managed.reader_task, managed.waiter_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
