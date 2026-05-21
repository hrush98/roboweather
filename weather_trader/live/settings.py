from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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
    for name in ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "LIVE_PRIVATE_KEY"):
        value = os.getenv(name)
        if value:
            return value.strip()
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
    if not plaintext.startswith("0x"):
        raise ValueError("Decrypted key does not look like a hex private key.")
    return plaintext


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
