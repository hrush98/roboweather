from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from weather_trader.live.settings import LiveSettings, load_live_settings

_IMPORT_ERROR: Exception | None = None
try:  # pragma: no cover - optional live dependency
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import (
        ApiCreds,
        AssetType,
        BalanceAllowanceParams,
        MarketOrderArgs,
        OrderType,
        PartialCreateOrderOptions,
        PostOrdersArgs,
    )
    from py_clob_client.exceptions import PolyApiException
    from py_clob_client.order_builder.constants import BUY, SELL
except Exception as exc:  # pragma: no cover
    ClobClient = None
    ApiCreds = None
    AssetType = None
    BalanceAllowanceParams = None
    MarketOrderArgs = None
    OrderType = None
    PartialCreateOrderOptions = None
    PostOrdersArgs = None
    PolyApiException = None
    BUY = None
    SELL = None
    _IMPORT_ERROR = exc


@dataclass(frozen=True)
class AllowanceCheck:
    ok: bool
    balance: float | None
    allowance: float | None
    reason: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrderSubmission:
    success: bool
    order_id: str | None
    status: str | None
    error_msg: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class CancelSubmission:
    success: bool
    canceled: list[str]
    not_canceled: dict[str, Any] | None
    error_msg: str | None
    raw: dict[str, Any]


class ClobExecutor:
    def __init__(
        self,
        *,
        private_key: str,
        funder: str | None = None,
        signature_type: int | None = None,
        chain_id: int | None = None,
        clob_url: str | None = None,
        kill_switch_path: str | None = None,
        settings: LiveSettings | None = None,
    ) -> None:
        if ClobClient is None:
            raise RuntimeError("py-clob-client is required for live trading.") from _IMPORT_ERROR
        self.settings = settings or load_live_settings()
        self._kill_switch_path = kill_switch_path or self.settings.live_kill_switch_path
        self._client = ClobClient(
            host=clob_url or self.settings.polymarket_clob_url,
            key=private_key,
            chain_id=chain_id if chain_id is not None else self.settings.polymarket_chain_id,
            signature_type=signature_type if signature_type is not None else self.settings.polymarket_signature_type,
            funder=funder if funder is not None else self.settings.polymarket_funder_address,
        )
        creds = self._client.create_or_derive_api_creds()
        if not isinstance(creds, ApiCreds):
            raise RuntimeError("Failed to derive API credentials.")
        self._creds = creds
        self._client.set_api_creds(creds)

    @property
    def api_creds(self) -> dict[str, str]:
        return {
            "apiKey": self._creds.api_key,
            "secret": self._creds.api_secret,
            "passphrase": self._creds.api_passphrase,
        }

    def check_kill_switch(self) -> bool:
        return Path(str(self._kill_switch_path)).expanduser().exists()

    def check_allowance_buy(self, required_usdc: float) -> AllowanceCheck:
        raw = self._client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        balance, allowance = _parse_balance_allowance(raw, decimals=self.settings.polymarket_token_decimals)
        if balance is None or allowance is None:
            return AllowanceCheck(False, balance, allowance, "missing_balance", _raw_dict(raw))
        if balance < required_usdc:
            return AllowanceCheck(False, balance, allowance, "insufficient_balance", _raw_dict(raw))
        if allowance < required_usdc:
            return AllowanceCheck(False, balance, allowance, "insufficient_allowance", _raw_dict(raw))
        return AllowanceCheck(True, balance, allowance, raw=_raw_dict(raw))

    def place_fak_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        return self._place_order(token_id=token_id, side=side, price=price, amount=amount, tick_size=tick_size, order_type=OrderType.FAK)

    def place_fok_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        return self._place_order(token_id=token_id, side=side, price=price, amount=amount, tick_size=tick_size, order_type=OrderType.FOK)

    def place_gtc_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None = None) -> OrderSubmission:
        return self._place_order(token_id=token_id, side=side, price=price, amount=amount, tick_size=tick_size, order_type=OrderType.GTC)

    def place_fok_batch(self, orders: list[dict[str, Any]]) -> list[OrderSubmission]:
        if self.check_kill_switch():
            return [_blocked_submission("kill_switch")]
        prepared: list[PostOrdersArgs] = []
        for order in orders:
            normalized_tick = _normalize_tick_size(order.get("tick_size"))
            side = str(order.get("side") or "BUY").upper()
            amount = _quantize_usdc_amount(float(order.get("amount") or 0.0)) if side == "BUY" else _quantize_market_sell_amount(float(order.get("amount") or 0.0))
            price = _quantize_price(float(order.get("price") or 0.0), normalized_tick)
            token_id = str(order.get("token_id") or "")
            if not token_id or side not in {"BUY", "SELL"} or price <= 0 or amount <= 0:
                return [OrderSubmission(False, None, "error", "invalid_order_payload", {"order": order})]
            signed = self._client.create_market_order(
                MarketOrderArgs(token_id=token_id, side=BUY if side == "BUY" else SELL, amount=amount, price=price, order_type=OrderType.FOK),
                PartialCreateOrderOptions(tick_size=normalized_tick) if normalized_tick else None,
            )
            prepared.append(PostOrdersArgs(order=signed, orderType=OrderType.FOK, postOnly=bool(order.get("post_only", False))))
        try:
            response = self._client.post_orders(prepared)
        except Exception as exc:  # pragma: no cover
            return [_exception_submission(exc)]
        return [_submission_from_payload(item) for item in response] if isinstance(response, list) else [_submission_from_payload(response)]

    def get_order(self, order_id: str) -> dict[str, Any]:
        return _raw_dict(self._client.get_order(order_id))

    def cancel_order(self, order_id: str) -> CancelSubmission:
        try:
            response = self._client.cancel(order_id=order_id)
        except Exception as exc:  # pragma: no cover
            return CancelSubmission(False, [], {order_id: str(exc)}, str(exc), {"exception_type": type(exc).__name__, "exception": str(exc)})
        raw = _raw_dict(response)
        canceled = raw.get("canceled") if isinstance(raw.get("canceled"), list) else []
        not_canceled = raw.get("not_canceled") if isinstance(raw.get("not_canceled"), dict) else None
        success = order_id in canceled or bool(canceled)
        return CancelSubmission(success, canceled, not_canceled, None if success else "cancel_failed", raw)

    def _place_order(self, *, token_id: str, side: str, price: float, amount: float, tick_size: float | None, order_type) -> OrderSubmission:
        if self.check_kill_switch():
            return _blocked_submission("kill_switch")
        normalized_tick = _normalize_tick_size(tick_size)
        order_side = str(side).upper()
        price = _quantize_price(price, normalized_tick)
        amount = _quantize_usdc_amount(amount) if order_side == "BUY" else _quantize_market_sell_amount(amount)
        if not token_id or order_side not in {"BUY", "SELL"} or price <= 0 or amount <= 0:
            return _blocked_submission("order_size_zero_after_round")
        try:
            signed = self._client.create_market_order(
                MarketOrderArgs(token_id=token_id, side=BUY if order_side == "BUY" else SELL, amount=amount, price=price, order_type=order_type),
                PartialCreateOrderOptions(tick_size=normalized_tick) if normalized_tick else None,
            )
            response = self._client.post_order(signed, order_type)
        except Exception as exc:  # pragma: no cover
            return _exception_submission(exc)
        return _submission_from_payload(response)


def _submission_from_payload(payload: Any) -> OrderSubmission:
    raw = _raw_dict(payload)
    return OrderSubmission(bool(raw.get("success", True)), raw.get("orderId") or raw.get("orderID"), raw.get("status"), raw.get("errorMsg"), raw)


def _blocked_submission(reason: str) -> OrderSubmission:
    return OrderSubmission(False, None, "blocked", reason, {"blocked": True, "reason": reason})


def _exception_submission(exc: Exception) -> OrderSubmission:
    raw: dict[str, Any] = {"exception_type": type(exc).__name__, "exception": str(exc)}
    if PolyApiException is not None and isinstance(exc, PolyApiException):
        raw["status_code"] = getattr(exc, "status_code", None)
        raw["error_message"] = getattr(exc, "error_message", None)
    return OrderSubmission(False, None, "error", str(raw.get("error_message") or raw["exception"]), raw)


def _raw_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"raw": value}


def _quantize_price(value: float, tick_size: str | None = None) -> float:
    if value <= 0:
        return 0.0
    step = Decimal(str(tick_size or "0.01"))
    rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(rounded)


def _quantize_usdc_amount(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _quantize_market_sell_amount(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _normalize_tick_size(value: float | str | None) -> str | None:
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    for option in (0.1, 0.01, 0.001, 0.0001):
        if abs(as_float - option) < 1e-9:
            return f"{option:.4f}".rstrip("0").rstrip(".")
    return None


def _parse_balance_allowance(raw: Any, *, decimals: int = 0) -> tuple[float | None, float | None]:
    if not isinstance(raw, dict):
        return None, None
    balance = raw.get("balance") or raw.get("amount") or raw.get("available")
    allowance = raw.get("allowance") or raw.get("approved")
    if allowance is None and isinstance(raw.get("allowances"), dict):
        # Some CLOB responses return allowances by exchange contract address.
        # Use the max value as the usable allowance for the current collateral.
        try:
            allowance = max(float(value) for value in raw["allowances"].values())
        except (TypeError, ValueError):
            allowance = None
    try:
        balance_val = float(balance) if balance is not None else None
    except (TypeError, ValueError):
        balance_val = None
    try:
        allowance_val = float(allowance) if allowance is not None else None
    except (TypeError, ValueError):
        allowance_val = None
    if decimals > 0:
        scale = 10 ** decimals
        balance_val = balance_val / scale if balance_val is not None else None
        allowance_val = allowance_val / scale if allowance_val is not None else None
    return balance_val, allowance_val
