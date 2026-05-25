#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import importlib.metadata
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_trader.execution.clob_executor import OrderSubmission
from weather_trader.live.settings import decrypt_age_keyfile_with_passphrase, load_live_settings


DEFAULT_LIVE_DB = "~/.local/state/roboweather/live_trading.sqlite"
DEFAULT_LOG_DIR = "data/logs"


@dataclass(frozen=True)
class ProbeTarget:
    source: str
    token_id: str
    side: str
    amount: float
    price: float
    live_position_id: int | None = None
    market_id: str | None = None
    strategy_name: str | None = None
    station: str | None = None
    market_date: str | None = None
    selected_bucket: str | None = None
    entry_price: float | None = None
    entry_edge: float | None = None


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        print(json.dumps(record, sort_keys=True, default=str))


def main() -> int:
    args = parse_args()
    log_path = resolve_log_path(args.log_file)
    logger = JsonlLogger(log_path)
    logger.write(
        "probe_start",
        {
            "submit": args.submit,
            "log_file": str(log_path),
            "live_db": str(Path(args.live_db).expanduser()),
        },
    )

    settings = load_live_settings()
    logger.write(
        "settings",
        {
            "clob_url": settings.polymarket_clob_url,
            "chain_id": settings.polymarket_chain_id,
            "signature_type": settings.polymarket_signature_type,
            "has_funder_address": bool(settings.polymarket_funder_address),
            "keyfile_path": str(Path(settings.polymarket_keyfile_path).expanduser()),
            "require_allowance_check": settings.live_require_allowance_check,
            "py_clob_client_v2_version": package_version("py_clob_client_v2"),
        },
    )

    target = resolve_target(args)
    logger.write("target", asdict(target))

    if not args.submit:
        log_public_client_metadata(settings, target, logger)
        logger.write("not_submitted", {"reason": "pass --submit to place the live FAK order"})
        return 0

    private_key = unlock_private_key(settings.polymarket_keyfile_path, args.passphrase_env)
    try:
        response = place_fak_order_v2(settings, private_key, target, args.tick_size, logger)
    finally:
        private_key = ""
    logger.write("submit_response", redact_submission(response))
    return 0 if response.success else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe live Polymarket V2 order submission. By default it uses the latest "
            "live_policy_positions row from the live DB."
        )
    )
    parser.add_argument("--live-db", default=os.getenv("ROBOWEATHER_LIVE_DB", DEFAULT_LIVE_DB))
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--passphrase-env", default="POLYMARKET_KEYFILE_PASSPHRASE")
    parser.add_argument("--amount", type=float, default=3.0)
    parser.add_argument("--price", type=float, default=None, help="Worst-price limit. Defaults to the latest attempt/position cap.")
    parser.add_argument("--token-id", default=None)
    parser.add_argument("--side", choices=("BUY", "SELL"), default="BUY")
    parser.add_argument("--tick-size", default=None, help="Optional explicit tick size, e.g. 0.01.")
    parser.add_argument("--submit", action="store_true", help="Actually place the live FAK order.")
    return parser.parse_args()


def resolve_log_path(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(DEFAULT_LOG_DIR) / f"polymarket_live_order_probe_{stamp}.jsonl"


def resolve_target(args: argparse.Namespace) -> ProbeTarget:
    if args.token_id:
        if args.price is None:
            raise SystemExit("--price is required when --token-id is supplied")
        return ProbeTarget("cli", args.token_id, args.side, args.amount, args.price)
    return latest_live_position_target(Path(args.live_db).expanduser(), args.amount, args.price)


def latest_live_position_target(db_path: Path, amount: float, price_override: float | None) -> ProbeTarget:
    if not db_path.exists():
        raise SystemExit(f"live DB not found: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        select
            lpp.id, lpp.strategy_name, lpp.station, lpp.market_date, lpp.selected_market_id,
            lpp.selected_token_id, lpp.selected_side, lpp.selected_bucket, lpp.entry_price,
            lpp.entry_edge, lpp.raw_json,
            (
                select loa.limit_price
                from live_order_attempts loa
                where loa.live_position_id = lpp.id
                order by loa.timestamp desc, loa.id desc
                limit 1
            ) as last_attempt_limit_price
        from live_policy_positions lpp
        order by lpp.timestamp desc, lpp.id desc
        limit 1
        """
    ).fetchone()
    if row is None:
        raise SystemExit(f"no live_policy_positions rows in {db_path}")

    raw = json.loads(row["raw_json"] or "{}")
    nested = raw.get("raw_json") if isinstance(raw.get("raw_json"), dict) else {}
    candidate = nested.get("candidate") if isinstance(nested.get("candidate"), dict) else {}
    price = first_float(
        price_override,
        row["last_attempt_limit_price"],
        nested.get("limit_price"),
        candidate.get("selected_sweep_price_cap"),
        row["entry_price"],
    )
    if price is None:
        raise SystemExit("unable to infer a price; pass --price")

    return ProbeTarget(
        source="latest_live_policy_position",
        token_id=str(row["selected_token_id"]),
        side="BUY",
        amount=amount,
        price=price,
        live_position_id=int(row["id"]),
        market_id=str(row["selected_market_id"]),
        strategy_name=str(row["strategy_name"]),
        station=str(row["station"]),
        market_date=str(row["market_date"]),
        selected_bucket=row["selected_bucket"],
        entry_price=float(row["entry_price"]) if row["entry_price"] is not None else None,
        entry_edge=float(row["entry_edge"]) if row["entry_edge"] is not None else None,
    )


def first_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def unlock_private_key(keyfile_path: str, passphrase_env: str) -> str:
    passphrase = os.getenv(passphrase_env)
    if passphrase is None:
        passphrase = getpass.getpass(f"Passphrase for {Path(keyfile_path).expanduser()}: ")
    return decrypt_age_keyfile_with_passphrase(keyfile_path, passphrase)


def log_public_client_metadata(settings: Any, target: ProbeTarget, logger: JsonlLogger) -> None:
    try:
        from py_clob_client_v2 import ClobClient

        client = ClobClient(host=settings.polymarket_clob_url, chain_id=settings.polymarket_chain_id)
    except Exception as exc:
        logger.write("public_client_metadata", {"error_type": type(exc).__name__, "error": str(exc)})
        return
    metadata: dict[str, Any] = {}
    for name in ("get_tick_size", "get_neg_risk", "get_fee_rate_bps"):
        try:
            metadata[name] = getattr(client, name)(target.token_id)
        except Exception as exc:
            metadata[name] = {"error_type": type(exc).__name__, "error": str(exc)}
    logger.write("public_client_metadata", metadata)


def place_fak_order_v2(settings: Any, private_key: str, target: ProbeTarget, tick_size: str | None, logger: JsonlLogger) -> OrderSubmission:
    try:
        from py_clob_client_v2 import (
            ApiCreds,
            AssetType,
            BalanceAllowanceParams,
            ClobClient,
            MarketOrderArgs,
            OrderType,
            PartialCreateOrderOptions,
            Side,
        )
    except Exception as exc:
        return OrderSubmission(False, None, "error", str(exc), {"exception_type": type(exc).__name__, "exception": str(exc)})

    client = ClobClient(
        host=settings.polymarket_clob_url,
        chain_id=settings.polymarket_chain_id,
        key=private_key,
        signature_type=settings.polymarket_signature_type,
        funder=settings.polymarket_funder_address,
    )
    try:
        creds = client.create_or_derive_api_key()
        if not isinstance(creds, ApiCreds):
            return OrderSubmission(False, None, "error", "failed_to_derive_v2_api_creds", {"creds_type": type(creds).__name__})
        client.set_api_creds(creds)
        logger.write("v2_api_creds", {"derived": True, "api_key_prefix": str(creds.api_key)[:8]})

        metadata: dict[str, Any] = {
            "version": safe_call(client.get_version),
            "tick_size": safe_call(client.get_tick_size, target.token_id),
            "neg_risk": safe_call(client.get_neg_risk, target.token_id),
            "fee_rate_bps": safe_call(client.get_fee_rate_bps, target.token_id),
        }
        try:
            raw_allowance = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            metadata["allowance"] = raw_allowance
        except Exception as exc:
            metadata["allowance"] = {"error_type": type(exc).__name__, "error": str(exc)}
        logger.write("v2_client_metadata", redact(metadata))

        resolved_tick_size = str(tick_size or client.get_tick_size(target.token_id))
        neg_risk = bool(client.get_neg_risk(target.token_id))
        side = Side.BUY if target.side == "BUY" else Side.SELL
        logger.write(
            "v2_submit_begin",
            {
                "token_id": target.token_id,
                "side": target.side,
                "amount": target.amount,
                "price": target.price,
                "tick_size": resolved_tick_size,
                "neg_risk": neg_risk,
            },
        )
        response = client.create_and_post_market_order(
            order_args=MarketOrderArgs(
                token_id=target.token_id,
                side=side,
                amount=target.amount,
                price=target.price,
                order_type=OrderType.FAK,
            ),
            options=PartialCreateOrderOptions(tick_size=resolved_tick_size, neg_risk=neg_risk),
            order_type=OrderType.FAK,
        )
    except Exception as exc:
        return OrderSubmission(False, None, "error", str(exc), {"exception_type": type(exc).__name__, "exception": str(exc)})
    return _submission_from_v2_payload(response)


def _submission_from_v2_payload(payload: Any) -> OrderSubmission:
    raw = _raw_dict(payload)
    success_value = raw.get("success", True)
    if isinstance(success_value, str):
        success = success_value.strip().lower() != "false"
    else:
        success = bool(success_value)
    return OrderSubmission(
        success,
        raw.get("orderID") or raw.get("orderId") or raw.get("id"),
        raw.get("status"),
        raw.get("errorMsg") or raw.get("error"),
        raw,
    )


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"raw": value}


def safe_call(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:
        return {"error_type": type(exc).__name__, "error": str(exc)}


def redact_submission(response: OrderSubmission) -> dict[str, Any]:
    payload = {
        "success": response.success,
        "order_id": response.order_id,
        "status": response.status,
        "error_msg": response.error_msg,
        "raw": response.raw,
    }
    return redact(payload)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in ("secret", "passphrase", "private", "signature")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    sys.exit(main())
