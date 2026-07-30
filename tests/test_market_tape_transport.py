from __future__ import annotations

import asyncio
import json

import pytest

from weather_trader.tape.collector import PolymarketTapeTransport


class RecordingWebSocket:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        self.payloads.append(json.loads(raw))


def test_initial_subscription_is_batched_and_seeds_every_token() -> None:
    websocket = RecordingWebSocket()
    transport = PolymarketTapeTransport(subscription_batch_size=500)
    token_ids = tuple(f"token-{index:04d}" for index in range(1364))

    asyncio.run(transport._send_initial_subscription(websocket, token_ids))

    assert [len(payload["assets_ids"]) for payload in websocket.payloads] == [
        500,
        500,
        364,
    ]
    assert websocket.payloads[0]["type"] == "market"
    assert "operation" not in websocket.payloads[0]
    assert [payload["operation"] for payload in websocket.payloads[1:]] == [
        "subscribe",
        "subscribe",
    ]
    sent = tuple(
        token_id
        for payload in websocket.payloads
        for token_id in payload["assets_ids"]
    )
    assert sent == token_ids


def test_dynamic_subscribe_and_unsubscribe_requests_are_batched() -> None:
    websocket = RecordingWebSocket()
    transport = PolymarketTapeTransport(subscription_batch_size=500)
    added = tuple(f"added-{index:04d}" for index in range(1201))
    removed = tuple(f"removed-{index:04d}" for index in range(501))

    async def send_updates() -> None:
        await transport._send_subscription_operation(
            websocket,
            added,
            operation="subscribe",
        )
        await transport._send_subscription_operation(
            websocket,
            removed,
            operation="unsubscribe",
        )

    asyncio.run(send_updates())

    assert [
        (payload["operation"], len(payload["assets_ids"]))
        for payload in websocket.payloads
    ] == [
        ("subscribe", 500),
        ("subscribe", 500),
        ("subscribe", 201),
        ("unsubscribe", 500),
        ("unsubscribe", 1),
    ]
    assert [
        token_id
        for payload in websocket.payloads[:3]
        for token_id in payload["assets_ids"]
    ] == list(added)
    assert [
        token_id
        for payload in websocket.payloads[3:]
        for token_id in payload["assets_ids"]
    ] == list(removed)


def test_subscription_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="subscription_batch_size"):
        PolymarketTapeTransport(subscription_batch_size=0)
