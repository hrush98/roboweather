from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

import weather_trader.live.settings as live_settings


def _reload_settings(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "POLYMARKET_KEYFILE_PATH",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_PRIVATE_KEY_FD",
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


def test_private_key_fd_is_preferred_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"0xfdkey")
    os.close(write_fd)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY_FD", str(read_fd))
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xenvkey")

    private_key = module.private_key_from_env_or_keyfile(module.LiveSettings())

    assert private_key == "0xfdkey"
    with pytest.raises(OSError):
        os.fstat(read_fd)


def test_private_key_fd_invalid_key_reports_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"not-a-key")
    os.close(write_fd)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY_FD", str(read_fd))

    with pytest.raises(ValueError, match="FD private key does not look"):
        module.private_key_from_env_or_keyfile(module.LiveSettings())


def test_decrypt_age_keyfile_with_passphrase_uses_pty_without_leaking_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    decryptor = tmp_path / "fake-age"
    decryptor.write_text(
        "#!/usr/bin/env python3\n"
        "import getpass, sys\n"
        "pw = getpass.getpass('Enter passphrase: ')\n"
        "if pw != 'correct horse':\n"
        "    print('bad passphrase', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "print('0x' + 'a' * 64)\n",
        encoding="utf-8",
    )
    decryptor.chmod(0o700)
    keyfile = tmp_path / "polymarket.key.age"
    keyfile.write_text("encrypted", encoding="utf-8")
    monkeypatch.setenv("AGE_BINARY", str(decryptor))

    assert module.decrypt_age_keyfile_with_passphrase(str(keyfile), "correct horse") == "0x" + "a" * 64

    with pytest.raises(RuntimeError) as exc_info:
        module.decrypt_age_keyfile_with_passphrase(str(keyfile), "wrong secret")
    message = str(exc_info.value)
    assert "wrong secret" not in message
    assert "passphrase" in message


def test_decrypt_output_extracts_private_key_from_env_style_text(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload_settings(monkeypatch)
    private_key = "0x" + "1" * 64

    assert module._extract_private_key_from_decrypt_output(f"POLYMARKET_PRIVATE_KEY={private_key}\n") == private_key
    assert module._extract_private_key_from_decrypt_output(f"Enter passphrase: \r\n{private_key}\r\n") == private_key
