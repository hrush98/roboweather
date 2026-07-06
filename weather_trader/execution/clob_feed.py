from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.contracts import utc_now_iso


def record_clob_message(
    store: ExecutionStore,
    *,
    channel: str,
    message: dict[str, Any] | list[Any],
    received_at: str | None = None,
    live_candidate_id: str | None = None,
    live_position_id: int | None = None,
) -> list[int]:
    recorder = ClobFeedRecorder(store, channel=channel)
    return recorder.record_message(
        message,
        received_at=received_at,
        live_candidate_id=live_candidate_id,
        live_position_id=live_position_id,
    )


class ClobFeedRecorder:
    def __init__(self, store: ExecutionStore, *, channel: str) -> None:
        self.store = store
        self.channel = channel.lower()

    def record_message(
        self,
        message: dict[str, Any] | list[Any],
        *,
        received_at: str | None = None,
        live_candidate_id: str | None = None,
        live_position_id: int | None = None,
    ) -> list[int]:
        received = received_at or utc_now_iso()
        if isinstance(message, list):
            ids: list[int] = []
            for item in message:
                if isinstance(item, dict):
                    ids.extend(
                        self.record_message(
                            item,
                            received_at=received,
                            live_candidate_id=live_candidate_id,
                            live_position_id=live_position_id,
                        )
                    )
            return ids
        if not isinstance(message, dict):
            return []

        event_type = str(message.get("event_type") or message.get("type") or "unknown")
        if event_type == "price_change" and isinstance(message.get("price_changes"), list):
            ids = []
            for change in message["price_changes"]:
                if not isinstance(change, dict):
                    continue
                payload = {"parent": message, "price_change": change}
                ids.append(
                    self._insert(
                        payload,
                        event_type=event_type,
                        received_at=received,
                        market_id=_str_or_none(message.get("market")),
                        token_id=_str_or_none(change.get("asset_id") or change.get("token_id")),
                        side=_str_or_none(change.get("side")),
                        price=_float_or_none(change.get("price")),
                        size=_float_or_none(change.get("size")),
                        best_bid=_float_or_none(change.get("best_bid")),
                        best_ask=_float_or_none(change.get("best_ask")),
                        feed_timestamp=_str_or_none(message.get("timestamp")),
                        live_candidate_id=live_candidate_id,
                        live_position_id=live_position_id,
                    )
                )
            return ids

        return [
            self._insert(
                message,
                event_type=event_type,
                received_at=received,
                market_id=_str_or_none(message.get("market") or message.get("condition_id")),
                token_id=_str_or_none(message.get("asset_id") or message.get("token_id")),
                side=_str_or_none(message.get("side")),
                price=_float_or_none(message.get("price")),
                size=_float_or_none(message.get("size")),
                best_bid=_float_or_none(message.get("best_bid")),
                best_ask=_float_or_none(message.get("best_ask")),
                feed_timestamp=_str_or_none(message.get("timestamp")),
                live_candidate_id=live_candidate_id,
                live_position_id=live_position_id,
                external_order_id=_str_or_none(message.get("order_id") or message.get("orderID") or message.get("id")),
            )
        ]

    def _insert(
        self,
        raw_payload: dict[str, Any],
        *,
        event_type: str,
        received_at: str,
        market_id: str | None,
        token_id: str | None,
        side: str | None,
        price: float | None,
        size: float | None,
        best_bid: float | None,
        best_ask: float | None,
        feed_timestamp: str | None,
        live_candidate_id: str | None,
        live_position_id: int | None,
        external_order_id: str | None = None,
    ) -> int:
        return self.store.insert_clob_feed_event(
            channel=self.channel,
            event_type=event_type,
            market_id=market_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            best_bid=best_bid,
            best_ask=best_ask,
            feed_timestamp=feed_timestamp,
            feed_timestamp_ms=_timestamp_ms(feed_timestamp),
            received_at=received_at,
            live_candidate_id=live_candidate_id,
            live_position_id=live_position_id,
            external_order_id=external_order_id,
            raw_payload=raw_payload,
        )


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _timestamp_ms(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        numeric = int(float(value))
    except (TypeError, ValueError):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    if numeric < 10_000_000_000:
        numeric *= 1000
    return numeric
