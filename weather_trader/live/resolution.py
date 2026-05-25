from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import requests

from weather_trader.execution.contracts import LiveTradeEvent, LiveTradeEventType, TradeAction, utc_now_iso
from weather_trader.execution.store import ExecutionStore
from weather_trader.markets.polymarket_reader import CLOB_URL, GAMMA_URL


@dataclass(frozen=True)
class PolymarketResolution:
    resolved: bool
    closed: bool
    winning_token_id: str | None
    winning_outcome: str | None
    resolved_at: str | None
    source: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class LiveResolutionSummary:
    candidates: int
    resolved: int
    pending: int
    skipped: int
    errors: list[str]
    dry_run: bool = False


class LiveResolutionClient(Protocol):
    def fetch_resolution(
        self,
        *,
        market_id: str,
        condition_id: str | None,
        yes_token_id: str | None,
        no_token_id: str | None,
    ) -> PolymarketResolution:
        ...


class PolymarketResolutionClient:
    def __init__(self, timeout_seconds: int = 30, max_retries: int = 3, retry_backoff_seconds: float = 1.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds

    def fetch_resolution(
        self,
        *,
        market_id: str,
        condition_id: str | None,
        yes_token_id: str | None,
        no_token_id: str | None,
    ) -> PolymarketResolution:
        payloads: list[dict[str, Any]] = []
        gamma_payload = self._get_json(f"{GAMMA_URL}/markets/{market_id}")
        if isinstance(gamma_payload, dict):
            payloads.append(gamma_payload)
        if condition_id:
            for path in (f"{CLOB_URL}/markets/{condition_id}", f"{CLOB_URL}/clob-markets/{condition_id}"):
                try:
                    clob_payload = self._get_json(path)
                except requests.HTTPError as exc:
                    response = exc.response
                    if response is not None and response.status_code == 404:
                        continue
                    raise
                if isinstance(clob_payload, dict):
                    payloads.append(clob_payload)

        merged = _merge_payloads(payloads)
        return normalize_polymarket_resolution(
            merged,
            market_id=market_id,
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )

    def _get_json(self, url: str) -> Any:
        last_error: requests.RequestException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries or not _retryable_request_error(exc):
                    raise
                time.sleep(self.retry_backoff_seconds * attempt)
        raise last_error or RuntimeError(f"request failed: {url}")


class LiveResolutionService:
    def __init__(self, store: ExecutionStore, client: LiveResolutionClient | None = None) -> None:
        self.store = store
        self.client = client or PolymarketResolutionClient()

    def resolve_due(
        self,
        *,
        as_of_utc: datetime | None = None,
        min_market_age_days: int = 1,
        limit: int = 1000,
        dry_run: bool = False,
    ) -> LiveResolutionSummary:
        now = as_of_utc or datetime.now(timezone.utc)
        max_market_date = (now - timedelta(days=min_market_age_days)).date()
        rows = self.store.live_unsettled_positions(max_market_date=max_market_date, limit=limit)
        resolved = pending = skipped = 0
        errors: list[str] = []
        for row in rows:
            position_id = int(row["id"])
            market_id = str(row["selected_market_id"] or "")
            if not market_id:
                skipped += 1
                errors.append(f"position:{position_id}: missing selected_market_id")
                continue
            try:
                resolution = self.client.fetch_resolution(
                    market_id=market_id,
                    condition_id=row.get("condition_id"),
                    yes_token_id=row.get("yes_token_id"),
                    no_token_id=row.get("no_token_id"),
                )
            except Exception as exc:
                errors.append(f"position:{position_id}: {exc}")
                continue
            if not resolution.resolved or not resolution.winning_token_id:
                pending += 1
                continue
            settlement = _settlement_for_position(row, resolution)
            resolved += 1
            if dry_run:
                continue
            self._write_settlement(row, resolution, settlement)
        return LiveResolutionSummary(len(rows), resolved, pending, skipped, errors, dry_run=dry_run)

    def _write_settlement(self, row: dict[str, Any], resolution: PolymarketResolution, settlement: dict[str, Any]) -> None:
        position_id = int(row["id"])
        winning_side = _winning_side_for_token(resolution.winning_token_id, row.get("yes_token_id"), row.get("no_token_id"))
        resolved_at = resolution.resolved_at or utc_now_iso()
        self.store.update_live_policy_position_settlement(
            position_id,
            resolved_at=resolved_at,
            resolution_source=resolution.source,
            winning_token_id=resolution.winning_token_id,
            winning_side=str(winning_side) if winning_side is not None else None,
            settlement_value_usd=float(settlement["settlement_value_usd"]),
            realized_pnl=float(settlement["realized_pnl"]),
            realized_rr=settlement["realized_rr"],
            raw_patch={"polymarket_resolution": resolution.raw},
        )
        if not self.store.has_live_trade_event(position_id, str(LiveTradeEventType.RESOLVED)):
            self.store.insert_live_trade_event(
                LiveTradeEvent(
                    utc_now_iso(),
                    position_id,
                    row.get("strategy_name"),
                    LiveTradeEventType.RESOLVED,
                    "official Polymarket resolution",
                    {"resolution": resolution.raw, "settlement": settlement},
                )
            )


def normalize_polymarket_resolution(
    payload: dict[str, Any],
    *,
    market_id: str,
    condition_id: str | None,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> PolymarketResolution:
    tokens = _extract_tokens(payload)
    closed = _boolish(payload.get("closed")) or _boolish(payload.get("archived"))
    winning = next((token for token in tokens if _boolish(token.get("winner"))), None)
    winning_token_id = _token_id(winning) if winning is not None else None
    winning_outcome = str(winning.get("outcome") or winning.get("o") or "") if winning is not None else None

    if winning_token_id is None and closed:
        winning_token_id, winning_outcome = _winner_from_outcome_prices(payload, yes_token_id=yes_token_id, no_token_id=no_token_id)

    resolved = bool(closed and winning_token_id)
    raw = dict(payload)
    raw.setdefault("market_id", market_id)
    raw.setdefault("condition_id", condition_id)
    return PolymarketResolution(
        resolved=resolved,
        closed=bool(closed),
        winning_token_id=winning_token_id,
        winning_outcome=winning_outcome,
        resolved_at=_resolved_at(payload),
        source="POLYMARKET",
        raw=raw,
    )


def _settlement_for_position(row: dict[str, Any], resolution: PolymarketResolution) -> dict[str, Any]:
    selected_token_id = str(row.get("selected_token_id") or "")
    filled_shares = float(row.get("filled_shares") or 0.0)
    cost_usd = float(row.get("cost_usd") or 0.0)
    settlement_value = filled_shares if selected_token_id and selected_token_id == str(resolution.winning_token_id) else 0.0
    realized_pnl = settlement_value - cost_usd
    realized_rr = realized_pnl / cost_usd if cost_usd > 0 else None
    return {
        "settlement_value_usd": settlement_value,
        "realized_pnl": realized_pnl,
        "realized_rr": realized_rr,
        "cost_usd": cost_usd,
        "filled_shares": filled_shares,
        "selected_token_id": selected_token_id,
        "winning_token_id": resolution.winning_token_id,
    }


def _winner_from_outcome_prices(
    payload: dict[str, Any],
    *,
    yes_token_id: str | None,
    no_token_id: str | None,
) -> tuple[str | None, str | None]:
    prices = _parse_json_list(payload.get("outcomePrices") or payload.get("outcome_prices"))
    outcomes = _parse_json_list(payload.get("outcomes"))
    token_ids = _parse_json_list(payload.get("clobTokenIds") or payload.get("clob_token_ids"))
    if len(token_ids) < 2:
        token_ids = [yes_token_id, no_token_id]
    if len(prices) < 2 or len(token_ids) < 2:
        return None, None
    try:
        numeric = [float(prices[0]), float(prices[1])]
    except (TypeError, ValueError):
        return None, None
    if max(numeric) < 0.99:
        return None, None
    idx = 0 if numeric[0] >= numeric[1] else 1
    token_id = token_ids[idx]
    outcome = outcomes[idx] if len(outcomes) > idx else ("Yes" if idx == 0 else "No")
    return (str(token_id) if token_id else None), str(outcome)


def _winning_side_for_token(token_id: str | None, yes_token_id: str | None, no_token_id: str | None) -> TradeAction | None:
    if token_id is None:
        return None
    if yes_token_id is not None and str(token_id) == str(yes_token_id):
        return TradeAction.BUY_YES
    if no_token_id is not None and str(token_id) == str(no_token_id):
        return TradeAction.BUY_NO
    return None


def _extract_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [payload.get("tokens"), payload.get("t")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("tokens"), data.get("t")])
    for candidate in candidates:
        if isinstance(candidate, str):
            candidate = _parse_json_list(candidate)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _merge_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for payload in payloads:
        for key, value in payload.items():
            if value not in (None, "", []):
                merged[key] = value
    merged["raw_payloads"] = payloads
    return merged


def _token_id(token: dict[str, Any] | None) -> str | None:
    if token is None:
        return None
    value = token.get("token_id") or token.get("asset_id") or token.get("t") or token.get("id")
    return str(value) if value not in (None, "") else None


def _parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _resolved_at(payload: dict[str, Any]) -> str | None:
    for key in ("resolvedAt", "resolved_at", "closedTime", "closed_time", "updatedAt", "updated_at"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _retryable_request_error(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        return False
    return response.status_code == 429 or 500 <= response.status_code < 600
