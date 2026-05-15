from __future__ import annotations

from datetime import datetime, timezone

from weather_trader.execution.contracts import BookLevel, BookSnapshot
from weather_trader.execution.liquidity import selected_side_liquidity


def test_liquidity_thin_book_partially_fills_100() -> None:
    liquidity = selected_side_liquidity(
        _book(asks=[(0.50, 50)], bids=[(0.45, 10)]),
        as_of_utc=datetime(2026, 5, 15, 12, 0, 30, tzinfo=timezone.utc),
    )

    target = liquidity["summary"]["targets"]["ask"]["100"]
    assert target["fillable_notional_usd"] == 25
    assert target["filled_shares"] == 50
    assert target["vwap"] == 0.5
    assert target["fully_fillable"] is False
    assert liquidity["book_age_seconds"] == 30


def test_liquidity_deep_book_fully_fills_core_targets() -> None:
    liquidity = selected_side_liquidity(_book(asks=[(0.50, 300)]))

    ask_targets = liquidity["summary"]["targets"]["ask"]
    assert ask_targets["25"]["fully_fillable"] is True
    assert ask_targets["50"]["fully_fillable"] is True
    assert ask_targets["100"]["fully_fillable"] is True
    assert ask_targets["100"]["fillable_notional_usd"] == 100


def test_liquidity_cap_changes_fillable_notional() -> None:
    liquidity = selected_side_liquidity(_book(asks=[(0.50, 20), (0.55, 200)]))

    assert liquidity["summary"]["targets"]["ask"]["100"]["fillable_notional_usd"] == 10
    assert liquidity["summary"]["targets"]["ask_plus_0_05"]["100"]["fillable_notional_usd"] == 100
    assert liquidity["depth_at_ask"] == 10
    assert liquidity["depth_ask_plus_0_05"] == 120


def test_liquidity_missing_asks_produces_zero_fillable() -> None:
    liquidity = selected_side_liquidity(_book(asks=[]))

    assert liquidity["best_ask"] is None
    assert liquidity["depth_at_ask"] == 0
    assert liquidity["summary"]["targets"]["ask"]["100"]["fillable_notional_usd"] == 0
    assert liquidity["summary"]["targets"]["ask"]["100"]["fully_fillable"] is False


def _book(
    *,
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]] | None = None,
) -> BookSnapshot:
    return BookSnapshot(
        token_id="token",
        bids=[BookLevel(price, size) for price, size in (bids or [])],
        asks=[BookLevel(price, size) for price, size in asks],
        timestamp="2026-05-15T12:00:00+00:00",
    )
