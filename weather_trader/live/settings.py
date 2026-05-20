from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    for name in ("POLYMARKET_PRIVATE_KEY", "PRIVATE_KEY", "LIVE_PRIVATE_KEY"):
        value = os.getenv(name)
        if value:
            return value.strip()
    return decrypt_age_keyfile(settings.polymarket_keyfile_path)


def decrypt_age_keyfile(keyfile_path: str) -> str:
    import subprocess

    resolved = Path(os.path.expanduser(keyfile_path)).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Keyfile not found: {resolved}")
    result = subprocess.run(
        ["age", "--decrypt", "-o", "-", str(resolved)],
        check=True,
        text=True,
        capture_output=True,
    )
    plaintext = result.stdout.strip()
    if not plaintext.startswith("0x"):
        raise ValueError("Decrypted key does not look like a hex private key.")
    return plaintext


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
