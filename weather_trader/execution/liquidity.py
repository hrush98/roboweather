from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from weather_trader.execution.contracts import BookSnapshot


DEFAULT_LIQUIDITY_TARGETS_USD: tuple[float, ...] = (25.0, 50.0, 100.0, 250.0, 500.0)
DEFAULT_LIQUIDITY_CAP_OFFSETS: tuple[tuple[str, float], ...] = (
    ("ask", 0.0),
    ("ask_plus_0_01", 0.01),
    ("ask_plus_0_03", 0.03),
    ("ask_plus_0_05", 0.05),
)


@dataclass(frozen=True)
class LadderWalk:
    filled_shares: float
    cost_usd: float
    avg_price: float | None
    levels_consumed: list[dict[str, float]]
    remaining_shares: float

    @property
    def fully_filled(self) -> bool:
        return self.remaining_shares <= 0


def quantize_price(value: float) -> float:
    return round(max(0.0, min(1.0, value)) + 1e-12, 4)


def quantize_usdc(value: float) -> float:
    return round(max(0.0, value) + 1e-12, 2)


def quantize_shares(value: float) -> float:
    return round(max(0.0, value) + 1e-12, 6)


def walk_ask_ladder(
    *,
    book: BookSnapshot,
    limit_price: float,
    target_notional_usd: float,
    execution_price_cap: float | None = None,
    force_half_target: bool = False,
) -> LadderWalk:
    limit_price = quantize_price(limit_price)
    execution_price_cap = quantize_price(execution_price_cap if execution_price_cap is not None else limit_price)
    target_notional_usd = quantize_usdc(target_notional_usd)
    if not book.asks or limit_price <= 0:
        return LadderWalk(0.0, 0.0, None, [], 0.0)

    target_shares = quantize_shares(target_notional_usd / limit_price)
    if force_half_target:
        target_shares = quantize_shares(target_shares / 2.0)
    remaining = target_shares
    shares = 0.0
    cost = 0.0
    consumed: list[dict[str, float]] = []
    for level in book.asks:
        price = quantize_price(level.price)
        if price > 1.0:
            break
        take = min(remaining, quantize_shares(level.size))
        budget_remaining = quantize_usdc(target_notional_usd - cost)
        if budget_remaining <= 0:
            break
        if price <= 0:
            continue
        take = min(take, quantize_shares(budget_remaining / price))
        if take <= 0:
            continue
        projected_cost = cost + take * price
        projected_shares = shares + take
        projected_vwap = projected_cost / projected_shares if projected_shares > 0 else float("inf")
        if projected_vwap > execution_price_cap:
            if price <= execution_price_cap or shares <= 0:
                break
            max_take = ((execution_price_cap * shares) - cost) / (price - execution_price_cap)
            take = min(take, quantize_shares(max_take))
            if take <= 0:
                break
        level_cost = quantize_usdc(take * price)
        shares = quantize_shares(shares + take)
        cost = quantize_usdc(cost + level_cost)
        consumed.append({"price": price, "shares": take, "cost": level_cost})
        remaining = quantize_shares(remaining - take)
        if remaining <= 0:
            break

    avg = quantize_price(cost / shares) if shares > 0 else None
    return LadderWalk(shares, cost, avg, consumed, remaining)


def book_age_seconds(book: BookSnapshot, as_of_utc: datetime | None = None) -> float | None:
    as_of = as_of_utc or datetime.now(timezone.utc)
    try:
        timestamp = datetime.fromisoformat(book.timestamp)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (as_of.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds())


def selected_side_liquidity(
    book: BookSnapshot | None,
    *,
    as_of_utc: datetime | None = None,
    targets_usd: tuple[float, ...] = DEFAULT_LIQUIDITY_TARGETS_USD,
    cap_offsets: tuple[tuple[str, float], ...] = DEFAULT_LIQUIDITY_CAP_OFFSETS,
) -> dict[str, Any]:
    if book is None:
        return _empty_liquidity()
    best_ask = book.best_ask
    summary = _ladder_summary(book, best_ask, targets_usd=targets_usd, cap_offsets=cap_offsets)
    depths = summary["depth_notional_by_cap"]
    return {
        "best_bid": book.best_bid,
        "best_ask": best_ask,
        "spread": book.spread,
        "depth_at_ask": depths["ask"],
        "depth_ask_plus_0_01": depths["ask_plus_0_01"],
        "depth_ask_plus_0_03": depths["ask_plus_0_03"],
        "depth_ask_plus_0_05": depths["ask_plus_0_05"],
        "book_timestamp": book.timestamp,
        "book_age_seconds": book_age_seconds(book, as_of_utc),
        "summary": summary,
    }


def _empty_liquidity() -> dict[str, Any]:
    return {
        "best_bid": None,
        "best_ask": None,
        "spread": None,
        "depth_at_ask": 0.0,
        "depth_ask_plus_0_01": 0.0,
        "depth_ask_plus_0_03": 0.0,
        "depth_ask_plus_0_05": 0.0,
        "book_timestamp": None,
        "book_age_seconds": None,
        "summary": _ladder_summary(None, None),
    }


def _ladder_summary(
    book: BookSnapshot | None,
    best_ask: float | None,
    *,
    targets_usd: tuple[float, ...] = DEFAULT_LIQUIDITY_TARGETS_USD,
    cap_offsets: tuple[tuple[str, float], ...] = DEFAULT_LIQUIDITY_CAP_OFFSETS,
) -> dict[str, Any]:
    depth_by_cap: dict[str, float] = {}
    targets: dict[str, dict[str, dict[str, Any]]] = {}
    for cap_name, offset in cap_offsets:
        cap = quantize_price((best_ask or 0.0) + offset) if best_ask is not None else None
        depth_by_cap[cap_name] = _depth_at_cap(book, cap)
        cap_rows: dict[str, dict[str, Any]] = {}
        for target in targets_usd:
            if book is None or best_ask is None or cap is None:
                walk = LadderWalk(0.0, 0.0, None, [], 0.0)
                fully_fillable = False
            else:
                walk = walk_ask_ladder(
                    book=book,
                    limit_price=best_ask,
                    target_notional_usd=target,
                    execution_price_cap=cap,
                )
                fully_fillable = walk.cost_usd + 1e-9 >= target
            cap_rows[f"{int(target)}"] = {
                "fillable_notional_usd": walk.cost_usd,
                "filled_shares": walk.filled_shares,
                "vwap": walk.avg_price,
                "fully_fillable": fully_fillable,
                "levels_consumed": walk.levels_consumed,
            }
        targets[cap_name] = cap_rows
    return {
        "targets_usd": [int(value) if float(value).is_integer() else value for value in targets_usd],
        "price_caps": {
            name: (quantize_price((best_ask or 0.0) + offset) if best_ask is not None else None)
            for name, offset in cap_offsets
        },
        "depth_notional_by_cap": depth_by_cap,
        "targets": targets,
    }


def _depth_at_cap(book: BookSnapshot | None, cap: float | None) -> float:
    if book is None or cap is None:
        return 0.0
    return quantize_usdc(sum(level.price * level.size for level in book.asks if quantize_price(level.price) <= cap))
