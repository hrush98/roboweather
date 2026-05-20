from __future__ import annotations

import asyncio
import sys

from weather_trader.ui.process_supervisor import ProcessSpec, ProcessSupervisor


def test_process_supervisor_captures_output_and_exit_code(tmp_path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec(
                    "short",
                    "Short Process",
                    (sys.executable, "-u", "-c", "print('hello supervisor')"),
                )
            ],
            cwd=tmp_path,
        )

        snapshot = await supervisor.start("short")
        assert snapshot.status == "RUNNING"
        for _ in range(50):
            snapshot = supervisor.snapshot("short")
            if snapshot.status == "EXITED":
                break
            await asyncio.sleep(0.02)

        assert snapshot.status == "EXITED"
        assert snapshot.exit_code == 0
        assert any("hello supervisor" in line for line in supervisor.logs("short"))

    asyncio.run(scenario())


def test_process_supervisor_stops_running_process(tmp_path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec(
                    "long",
                    "Long Process",
                    (sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"),
                )
            ],
            cwd=tmp_path,
            stop_timeout_seconds=1.0,
        )

        await supervisor.start("long")
        assert supervisor.snapshot("long").status == "RUNNING"
        snapshot = await supervisor.stop("long")

        assert snapshot.status in {"EXITED", "FAILED"}
        assert snapshot.exit_code is not None
        assert any("stopping" in line for line in supervisor.logs("long"))

    asyncio.run(scenario())
