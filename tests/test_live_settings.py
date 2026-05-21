from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import weather_trader.live.settings as live_settings


def _reload_settings(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "POLYMARKET_KEYFILE_PATH",
        "POLYMARKET_SIGNATURE_TYPE",
        "POLYMARKET_FUNDER_ADDRESS",
        "AGE_BINARY",
    ):
        monkeypatch.delenv(name, raising=False)
    module = importlib.reload(live_settings)
    return module


def test_load_live_settings_reads_local_env_without_overriding_shell_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "POLYMARKET_KEYFILE_PATH=.poly_key_enc/polymarket.key.age\n"
        "POLYMARKET_SIGNATURE_TYPE=2\n"
        "POLYMARKET_FUNDER_ADDRESS=0xabc123\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLYMARKET_SIGNATURE_TYPE", "1")

    settings = module.load_live_settings()

    assert settings.polymarket_keyfile_path == ".poly_key_enc/polymarket.key.age"
    assert settings.polymarket_signature_type == 1
    assert settings.polymarket_funder_address == "0xabc123"


def test_age_binary_uses_explicit_configured_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    decryptor = tmp_path / "age"
    decryptor.write_text("#!/bin/sh\n", encoding="utf-8")
    decryptor.chmod(0o700)
    monkeypatch.setenv("AGE_BINARY", str(decryptor))

    assert module._age_binary() == str(decryptor)


def test_age_binary_reports_missing_decryptor(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(FileNotFoundError, match="age/rage decryptor not found"):
        module._age_binary()
