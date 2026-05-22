from __future__ import annotations

import os
import select
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_ENV_LOADED = False
_ENV_NAMES = (".env", ".env.local", ".env.dev")


@dataclass(frozen=True)
class LiveSettings:
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_chain_id: int = 137
    polymarket_signature_type: int = 0
    polymarket_funder_address: str | None = None
    polymarket_keyfile_path: str = "~/.sleeperservice/keys/polymarket.key.age"
    polymarket_token_decimals: int = 6
    live_kill_switch_path: str = "~/.sleeperservice/keys/STOP_TRADING"
    live_share_step: float = 0.0001
    live_require_allowance_check: bool = True


def load_live_settings() -> LiveSettings:
    load_local_env()
    return LiveSettings(
        polymarket_clob_url=os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com"),
        polymarket_chain_id=_int_env("POLYMARKET_CHAIN_ID", 137),
        polymarket_signature_type=_int_env("POLYMARKET_SIGNATURE_TYPE", 0),
        polymarket_funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS") or None,
        polymarket_keyfile_path=os.getenv("POLYMARKET_KEYFILE_PATH", "~/.sleeperservice/keys/polymarket.key.age"),
        polymarket_token_decimals=_int_env("POLYMARKET_TOKEN_DECIMALS", 6),
        live_kill_switch_path=os.getenv("LIVE_KILL_SWITCH_PATH", "~/.sleeperservice/keys/STOP_TRADING"),
        live_share_step=_float_env("LIVE_SHARE_STEP", 0.0001),
        live_require_allowance_check=_bool_env("LIVE_REQUIRE_ALLOWANCE_CHECK", True),
    )


def private_key_from_env_or_keyfile(settings: LiveSettings) -> str:
    load_local_env()
    fd_value = os.getenv("POLYMARKET_PRIVATE_KEY_FD")
    if fd_value:
        return _read_private_key_fd(fd_value)
    for name in ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "LIVE_PRIVATE_KEY"):
        value = os.getenv(name)
        if value:
            return _validate_private_key_text(value.strip(), "Environment private key")
    return decrypt_age_keyfile(settings.polymarket_keyfile_path)


def decrypt_age_keyfile(keyfile_path: str) -> str:
    resolved = Path(os.path.expanduser(keyfile_path)).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Keyfile not found: {resolved}")
    decryptor = _age_binary()
    try:
        result = subprocess.run(
            [decryptor, "--decrypt", "-o", "-", str(resolved)],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(stderr or f"{Path(decryptor).name} decryption failed") from exc
    plaintext = result.stdout.strip()
    return _validate_private_key_text(plaintext, "Decrypted key")


def decrypt_age_keyfile_with_passphrase(keyfile_path: str, passphrase: str) -> str:
    resolved = Path(os.path.expanduser(keyfile_path)).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Keyfile not found: {resolved}")
    decryptor = _age_binary()
    pid, master_fd = os.forkpty()
    if pid == 0:  # pragma: no cover - child process is covered through parent behavior
        try:
            os.execv(decryptor, [decryptor, "--decrypt", "-o", "-", str(resolved)])
        except Exception:
            os._exit(127)

    output = bytearray()
    sent_passphrase = False
    deadline = time.monotonic() + 30.0
    status = 0
    try:
        while True:
            if time.monotonic() > deadline:
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
                raise RuntimeError("age decryption timed out")
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)
                    if not sent_passphrase and b"passphrase" in output.lower():
                        os.write(master_fd, (passphrase + "\n").encode())
                        sent_passphrase = True
                else:
                    break
            if not sent_passphrase and time.monotonic() > deadline - 29.0:
                os.write(master_fd, (passphrase + "\n").encode())
                sent_passphrase = True
            finished_pid, status = os.waitpid(pid, os.WNOHANG)
            if finished_pid:
                break
        if not status:
            _, status = os.waitpid(pid, 0)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    decoded = output.decode(errors="replace")
    if status != 0:
        raise RuntimeError("age decryption failed; passphrase was rejected or keyfile is invalid")
    plaintext = _extract_private_key_from_decrypt_output(decoded)
    return _validate_private_key_text(plaintext, "Decrypted key")


def _read_private_key_fd(fd_value: str) -> str:
    try:
        fd = int(fd_value)
    except ValueError as exc:
        raise ValueError("POLYMARKET_PRIVATE_KEY_FD must be an integer file descriptor") from exc
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            plaintext = handle.read().strip()
    except OSError as exc:
        raise RuntimeError("Unable to read POLYMARKET_PRIVATE_KEY_FD") from exc
    return _validate_private_key_text(plaintext, "FD private key")


def _extract_private_key_from_decrypt_output(output: str) -> str:
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("0x"):
            return text
    return output.strip()


def _validate_private_key_text(value: str, label: str) -> str:
    if not value.startswith("0x"):
        raise ValueError(f"{label} does not look like a hex private key.")
    return value


def load_local_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for env_path in _find_env_files():
        _load_env_file(env_path)


def _find_env_files() -> tuple[Path, ...]:
    current_root = Path.cwd().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    search_roots = (current_root,) if current_root == repo_root else (current_root, repo_root)
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in search_roots:
        for env_name in _ENV_NAMES:
            env_path = (root / env_name).resolve()
            if not env_path.exists() or env_path in seen:
                continue
            seen.add(env_path)
            candidates.append(env_path)
    return tuple(candidates)


def _load_env_file(env_path: Path) -> None:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = _parse_env_value(value)


def _parse_env_value(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    try:
        parts = shlex.split(stripped, comments=True, posix=True)
    except ValueError:
        return stripped
    return parts[0] if parts else ""


def _age_binary() -> str:
    configured = os.getenv("AGE_BINARY")
    if configured:
        expanded = os.path.expanduser(configured)
        if shutil.which(expanded) or Path(expanded).exists():
            return expanded
        raise FileNotFoundError(f"AGE_BINARY is not executable or on PATH: {configured}")
    for candidate in ("age", "rage"):
        path = shutil.which(candidate)
        if path:
            return path
    raise FileNotFoundError("age/rage decryptor not found on PATH. Install age or set AGE_BINARY.")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}
