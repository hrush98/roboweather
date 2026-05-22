from __future__ import annotations

import asyncio
import os
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


def test_process_supervisor_extra_env_applies_only_to_started_child(tmp_path) -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor(
            [
                ProcessSpec(
                    "env",
                    "Env Process",
                    (sys.executable, "-u", "-c", "import os; print(os.getenv('ONE_SHOT_SECRET', 'missing'))"),
                )
            ],
            cwd=tmp_path,
        )

        await supervisor.start("env", extra_env={"ONE_SHOT_SECRET": "available"})
        for _ in range(50):
            if supervisor.snapshot("env").status == "EXITED":
                break
            await asyncio.sleep(0.02)
        assert any(line == "available" for line in supervisor.logs("env"))
        assert "ONE_SHOT_SECRET" not in supervisor.env

        await supervisor.start("env")
        for _ in range(50):
            if supervisor.snapshot("env").status == "EXITED":
                break
            await asyncio.sleep(0.02)
        assert any(line == "missing" for line in supervisor.logs("env"))

    asyncio.run(scenario())


def test_process_supervisor_pass_fds_survive_shell_wrapper(tmp_path) -> None:
    async def scenario() -> None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"0xpipekey")
        os.close(write_fd)
        code = "import os; fd=int(os.environ['POLYMARKET_PRIVATE_KEY_FD']); print(os.fdopen(fd).read())"
        supervisor = ProcessSupervisor(
            [
                ProcessSpec(
                    "fd",
                    "FD Process",
                    ("/bin/sh", "-c", f"{sys.executable} -u -c \"{code}\""),
                )
            ],
            cwd=tmp_path,
        )

        try:
            snapshot = await supervisor.start("fd", extra_env={"POLYMARKET_PRIVATE_KEY_FD": str(read_fd)}, pass_fds=(read_fd,))
            assert "0xpipekey" not in snapshot.command
            for _ in range(50):
                snapshot = supervisor.snapshot("fd")
                if snapshot.status == "EXITED":
                    break
                await asyncio.sleep(0.02)
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

        assert snapshot.status == "EXITED"
        assert any(line == "0xpipekey" for line in supervisor.logs("fd"))
        assert not any("0xpipekey" in line for line in supervisor.logs("fd") if line.startswith("starting:"))

    asyncio.run(scenario())
