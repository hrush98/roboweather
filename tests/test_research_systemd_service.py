from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = REPO_ROOT / "deploy/systemd/roboweather-research.service"
RUNNER_PATH = REPO_ROOT / "scripts/run_research.sh"


def test_research_service_is_durable_bounded_and_external_to_tui() -> None:
    unit = SERVICE_PATH.read_text()

    assert "ExecStart=/usr/bin/env bash scripts/run_research.sh loop" in unit
    assert "Restart=always" in unit
    assert "MemoryMax=4G" in unit
    assert "KillSignal=SIGINT" in unit
    assert "ReadWritePaths=%h/.local/state/roboweather %h/General/roboweather/data/logs" in unit
    assert "WantedBy=default.target" in unit


def test_research_runner_rejects_duplicate_database_writer(tmp_path: Path) -> None:
    model = tmp_path / "model.joblib"
    threshold = tmp_path / "threshold.joblib"
    model.touch()
    threshold.touch()
    db = tmp_path / "research.sqlite"
    lock_path = Path(f"{db}.research-loop.lock")
    lock_path.touch()
    env = {
        **os.environ,
        "MODEL": str(model),
        "THRESHOLD_MODEL": str(threshold),
        "EXTRA_MODELS": "",
        "DB": str(db),
        "LIVE_DB": str(tmp_path / "live.sqlite"),
        "PYTHON": "/bin/true",
    }

    with lock_path.open("r+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ("bash", str(RUNNER_PATH), "loop"),
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 73
    assert "Research loop already owns database lock" in result.stderr

    released = subprocess.run(
        ("bash", str(RUNNER_PATH), "loop"),
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert released.returncode == 0
