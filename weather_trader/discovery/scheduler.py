from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Protocol, Sequence

from weather_trader.discovery.registry import DiscoveryRegistry, RegistryWriterLocked
from weather_trader.pricing.contracts import stable_hash


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_CONTRACT_VERSION = "phase3d_bounded_scheduler_v1"


@dataclass(frozen=True)
class SchedulerConfig:
    research_db: Path
    tape_catalog: Path
    registry: Path
    source_start_date: str
    python_executable: Path = Path(sys.executable)
    interval_seconds: int = 6 * 60 * 60
    discovery_cadence_seconds: int = 7 * 24 * 60 * 60
    activation_lead_seconds: int = 5 * 60
    task_timeout_seconds: int = 15 * 60
    maximum_cycle_runtime_seconds: int = 30 * 60
    maximum_registry_bytes: int = 1024 * 1024 * 1024
    maximum_task_output_chars: int = 65_536

    def __post_init__(self) -> None:
        datetime.fromisoformat(self.source_start_date)
        positive = (
            self.interval_seconds,
            self.discovery_cadence_seconds,
            self.activation_lead_seconds,
            self.task_timeout_seconds,
            self.maximum_cycle_runtime_seconds,
            self.maximum_registry_bytes,
            self.maximum_task_output_chars,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("scheduler budgets and intervals must be positive")

    @property
    def config_hash(self) -> str:
        payload = {
            **asdict(self),
            "research_db": str(self.research_db.expanduser()),
            "tape_catalog": str(self.tape_catalog.expanduser()),
            "registry": str(self.registry.expanduser()),
            "python_executable": str(self.python_executable.expanduser()),
            "contract_version": SCHEDULER_CONTRACT_VERSION,
        }
        return stable_hash(payload)


class TaskRunner(Protocol):
    def run(
        self, name: str, command: Sequence[str], *, timeout_seconds: int
    ) -> dict[str, Any]: ...


class SubprocessTaskRunner:
    def __init__(self, *, cwd: Path = REPO_ROOT, maximum_output_chars: int = 65_536) -> None:
        self.cwd = cwd
        self.maximum_output_chars = maximum_output_chars

    def run(
        self, name: str, command: Sequence[str], *, timeout_seconds: int
    ) -> dict[str, Any]:
        try:
            result = subprocess.run(
                list(command),
                cwd=self.cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{name} timed out after {timeout_seconds}s") from exc
        stdout = result.stdout[-self.maximum_output_chars :]
        stderr = result.stderr[-self.maximum_output_chars :]
        if result.returncode != 0:
            detail = (stderr or stdout or f"exit {result.returncode}").strip()
            raise RuntimeError(f"{name} failed: {detail}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{name} returned invalid JSON: {stdout[-500:]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{name} returned a non-object JSON result")
        return payload


class SchedulerProcessLock:
    def __init__(self, registry_path: Path) -> None:
        self.path = Path(f"{registry_path.expanduser()}.scheduler.lock")
        self._handle: Any | None = None

    def __enter__(self) -> SchedulerProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RegistryWriterLocked(f"another discovery scheduler owns {self.path}") from exc
        self._handle = handle
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class BoundedDiscoveryScheduler:
    """Run idempotent C4/C5/C3 tasks under explicit time/storage budgets."""

    def __init__(
        self,
        config: SchedulerConfig,
        *,
        task_runner: TaskRunner | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self.task_runner = task_runner or SubprocessTaskRunner(
            maximum_output_chars=config.maximum_task_output_chars
        )
        self.monotonic_clock = monotonic_clock

    def run_cycle(self, *, now_utc: datetime) -> dict[str, Any]:
        now = _utc_datetime(now_utc)
        scheduled = _scheduled_boundary(now, self.config.interval_seconds)
        cycle_id = "p3d_cycle_" + stable_hash({
            "scheduled_for_utc": scheduled.isoformat(),
            "config_hash": self.config.config_hash,
        })[:24]
        discovery_due = self._discovery_due(now)
        tasks = ["continuous_evaluation", "role_transitions"]
        if discovery_due:
            tasks.append("recurring_discovery")

        with DiscoveryRegistry(self.config.registry) as registry:
            prior = registry.scheduler_cycle_events(cycle_id)
            terminal = next(
                (event for event in reversed(prior) if event["event_type"] in {"COMPLETED", "FAILED", "SKIPPED"}),
                None,
            )
            if terminal is not None:
                return {
                    "status": "NOOP_TERMINAL_CYCLE",
                    "cycle_id": cycle_id,
                    "terminal_event": terminal,
                    "funded_authorization": False,
                }
            if not any(event["event_type"] == "STARTED" for event in prior):
                registry.append_scheduler_event(
                    cycle_id=cycle_id,
                    event_type="STARTED",
                    scheduled_for_utc=scheduled.isoformat(),
                    occurred_at_utc=now.isoformat(),
                    config_hash=self.config.config_hash,
                    tasks=tasks,
                    details={
                        "contract_version": SCHEDULER_CONTRACT_VERSION,
                        "registry_bytes": _size(self.config.registry),
                        "funded_authorization": False,
                    },
                )

        started = self.monotonic_clock()
        results: dict[str, Any] = {}
        current_task = "startup"
        try:
            self._check_storage_budget()
            for current_task, command in self._commands(now, discovery_due):
                elapsed = self.monotonic_clock() - started
                remaining = self.config.maximum_cycle_runtime_seconds - elapsed
                if remaining <= 0:
                    raise RuntimeError("scheduler cycle runtime budget exhausted")
                results[current_task] = self.task_runner.run(
                    current_task,
                    command,
                    timeout_seconds=max(1, min(self.config.task_timeout_seconds, int(remaining))),
                )
                self._check_storage_budget()
            duration = round(self.monotonic_clock() - started, 6)
            with DiscoveryRegistry(self.config.registry) as registry:
                event_id = registry.append_scheduler_event(
                    cycle_id=cycle_id,
                    event_type="COMPLETED",
                    scheduled_for_utc=scheduled.isoformat(),
                    occurred_at_utc=now.isoformat(),
                    config_hash=self.config.config_hash,
                    tasks=tasks,
                    details={
                        "results": results,
                        "duration_seconds": duration,
                        "registry_bytes": _size(self.config.registry),
                        "funded_authorization": False,
                    },
                )
            return {
                "status": "COMPLETED",
                "cycle_id": cycle_id,
                "event_id": event_id,
                "tasks": tasks,
                "results": results,
                "duration_seconds": duration,
                "funded_authorization": False,
            }
        except Exception as exc:
            duration = round(self.monotonic_clock() - started, 6)
            error = str(exc)[-self.config.maximum_task_output_chars :]
            with DiscoveryRegistry(self.config.registry) as registry:
                registry.append_scheduler_event(
                    cycle_id=cycle_id,
                    event_type="FAILED",
                    scheduled_for_utc=scheduled.isoformat(),
                    occurred_at_utc=now.isoformat(),
                    config_hash=self.config.config_hash,
                    tasks=tasks,
                    details={
                        "failed_task": current_task,
                        "error": error,
                        "completed_results": results,
                        "duration_seconds": duration,
                        "registry_bytes": _size(self.config.registry),
                        "funded_authorization": False,
                    },
                )
            raise

    def _commands(
        self, now: datetime, discovery_due: bool
    ) -> list[tuple[str, list[str]]]:
        python = str(self.config.python_executable.expanduser())
        registry = str(self.config.registry.expanduser())
        end_date = now.date().isoformat()
        common = [
            "--research-db", str(self.config.research_db.expanduser()),
            "--tape-catalog", str(self.config.tape_catalog.expanduser()),
            "--registry", registry,
        ]
        commands = [
            ("continuous_evaluation", [
                python, str(REPO_ROOT / "scripts/phase3d_continuous_evaluation.py"),
                *common,
                "--end-date-exclusive", end_date,
                "--as-of-timestamp", now.isoformat(),
            ]),
            ("role_transitions", [
                python, str(REPO_ROOT / "scripts/phase3d_apply_transitions.py"),
                "--registry", registry,
                "--effective-at-timestamp", now.isoformat(),
            ]),
        ]
        if discovery_due:
            activation = now + timedelta(seconds=self.config.activation_lead_seconds)
            commands.append(("recurring_discovery", [
                python, str(REPO_ROOT / "scripts/phase3d_continuous_discovery.py"),
                *common,
                "--source-start-date", self.config.source_start_date,
                "--discovery-cutoff-exclusive", end_date,
                "--activation-timestamp", activation.isoformat(),
            ]))
        return commands

    def _discovery_due(self, now: datetime) -> bool:
        if not self.config.registry.expanduser().exists():
            return True
        with DiscoveryRegistry(self.config.registry) as registry:
            row = registry.connection.execute(
                "select max(completed_at_utc) from discovery_run_outcomes"
            ).fetchone()
        if row is None or row[0] is None:
            return True
        latest = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return now - latest.astimezone(timezone.utc) >= timedelta(
            seconds=self.config.discovery_cadence_seconds
        )

    def _check_storage_budget(self) -> None:
        size = _size(self.config.registry)
        if size > self.config.maximum_registry_bytes:
            raise RuntimeError(
                f"registry storage budget exceeded: {size}>{self.config.maximum_registry_bytes}"
            )


def _scheduled_boundary(now: datetime, interval_seconds: int) -> datetime:
    timestamp = int(now.timestamp())
    return datetime.fromtimestamp(
        timestamp - timestamp % interval_seconds, tz=timezone.utc
    )


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("scheduler timestamps must include timezone")
    return value.astimezone(timezone.utc)


def _size(path: Path) -> int:
    expanded = path.expanduser()
    return expanded.stat().st_size if expanded.exists() else 0
