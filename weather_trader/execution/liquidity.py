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
DEFAULT_SWEEP_TARGETS_USD: tuple[float, ...] = (25.0, 50.0, 100.0)
DEFAULT_SIGNAL_MIN_EDGE = 0.25
DEFAULT_POST_FILL_MIN_EDGE = 0.15
DEFAULT_SWEEP_MAX_SLIPPAGE = 0.05
DEFAULT_BID_LADDER_ORDER_NOTIONAL_USD = 50.0
DEFAULT_BID_LADDER_TOTAL_NOTIONAL_USD = 500.0
DEFAULT_BID_LADDER_STEP_CENTS = 0.01
DEFAULT_BID_LADDER_RANGE_CENTS = 0.10
DEFAULT_BID_LADDER_TTL_SECONDS = 180


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


def selected_side_execution_modes(
    book: BookSnapshot | None,
    *,
    selected_side: str,
    fair: float | None,
    entry_edge: float | None = None,
    signal_min_edge: float = DEFAULT_SIGNAL_MIN_EDGE,
    post_fill_min_edge: float = DEFAULT_POST_FILL_MIN_EDGE,
    sweep_max_slippage: float = DEFAULT_SWEEP_MAX_SLIPPAGE,
    sweep_targets_usd: tuple[float, ...] = DEFAULT_SWEEP_TARGETS_USD,
    bid_ladder_order_notional_usd: float = DEFAULT_BID_LADDER_ORDER_NOTIONAL_USD,
    bid_ladder_total_notional_usd: float = DEFAULT_BID_LADDER_TOTAL_NOTIONAL_USD,
    bid_ladder_step_cents: float = DEFAULT_BID_LADDER_STEP_CENTS,
    bid_ladder_range_cents: float = DEFAULT_BID_LADDER_RANGE_CENTS,
    bid_ladder_ttl_seconds: int = DEFAULT_BID_LADDER_TTL_SECONDS,
) -> dict[str, Any]:
    reason = _execution_ineligible_reason(
        book=book,
        selected_side=selected_side,
        fair=fair,
        entry_edge=entry_edge,
        signal_min_edge=signal_min_edge,
    )
    if reason is not None:
        return {
            "ask_sweep": _ineligible_ask_sweep(reason, fair, signal_min_edge, post_fill_min_edge, sweep_max_slippage),
            "bid_ladder": _ineligible_bid_ladder(reason, fair, signal_min_edge, post_fill_min_edge),
        }

    assert book is not None
    assert fair is not None
    assert book.best_ask is not None
    edge = float(entry_edge) if entry_edge is not None else fair - book.best_ask
    ask_sweep = build_ask_sweep(
        book=book,
        fair=fair,
        entry_edge=edge,
        signal_min_edge=signal_min_edge,
        post_fill_min_edge=post_fill_min_edge,
        sweep_max_slippage=sweep_max_slippage,
        sweep_targets_usd=sweep_targets_usd,
    )
    bid_ladder = build_bid_ladder(
        book=book,
        fair=fair,
        entry_edge=edge,
        signal_min_edge=signal_min_edge,
        post_fill_min_edge=post_fill_min_edge,
        order_notional_usd=bid_ladder_order_notional_usd,
        total_notional_usd=bid_ladder_total_notional_usd,
        step_cents=bid_ladder_step_cents,
        range_cents=bid_ladder_range_cents,
        ttl_seconds=bid_ladder_ttl_seconds,
    )
    return {"ask_sweep": ask_sweep, "bid_ladder": bid_ladder}


def build_ask_sweep(
    *,
    book: BookSnapshot,
    fair: float,
    entry_edge: float,
    signal_min_edge: float = DEFAULT_SIGNAL_MIN_EDGE,
    post_fill_min_edge: float = DEFAULT_POST_FILL_MIN_EDGE,
    sweep_max_slippage: float = DEFAULT_SWEEP_MAX_SLIPPAGE,
    sweep_targets_usd: tuple[float, ...] = DEFAULT_SWEEP_TARGETS_USD,
) -> dict[str, Any]:
    best_ask = book.best_ask
    if best_ask is None:
        return _ineligible_ask_sweep("MISSING_ASK", fair, signal_min_edge, post_fill_min_edge, sweep_max_slippage)
    price_cap = quantize_price(min(best_ask + sweep_max_slippage, fair - post_fill_min_edge))
    depth_to_cap = _depth_at_cap(book, price_cap)
    targets: dict[str, dict[str, Any]] = {}
    for target in sweep_targets_usd:
        walk = walk_ask_ladder(
            book=book,
            limit_price=best_ask,
            target_notional_usd=target,
            execution_price_cap=price_cap,
        )
        targets[f"{int(target)}"] = {
            "fillable_notional_usd": walk.cost_usd,
            "filled_shares": walk.filled_shares,
            "vwap": walk.avg_price,
            "fully_fillable": walk.cost_usd + 1e-9 >= quantize_usdc(target),
            "levels_consumed": walk.levels_consumed,
        }
    return {
        "version": 1,
        "mode": "ask_sweep",
        "eligible": True,
        "reason": None,
        "fair": quantize_price(fair),
        "entry_ask": quantize_price(best_ask),
        "entry_edge": round(entry_edge, 4),
        "signal_min_edge": signal_min_edge,
        "post_fill_min_edge": post_fill_min_edge,
        "sweep_max_slippage": sweep_max_slippage,
        "price_cap": price_cap,
        "price_cap_formula": "min(ask+0.05,fair-0.15)",
        "depth_to_cap": depth_to_cap,
        "targets": targets,
    }


def build_bid_ladder(
    *,
    book: BookSnapshot,
    fair: float,
    entry_edge: float,
    signal_min_edge: float = DEFAULT_SIGNAL_MIN_EDGE,
    post_fill_min_edge: float = DEFAULT_POST_FILL_MIN_EDGE,
    order_notional_usd: float = DEFAULT_BID_LADDER_ORDER_NOTIONAL_USD,
    total_notional_usd: float = DEFAULT_BID_LADDER_TOTAL_NOTIONAL_USD,
    step_cents: float = DEFAULT_BID_LADDER_STEP_CENTS,
    range_cents: float = DEFAULT_BID_LADDER_RANGE_CENTS,
    ttl_seconds: int = DEFAULT_BID_LADDER_TTL_SECONDS,
) -> dict[str, Any]:
    best_ask = book.best_ask
    if best_ask is None:
        return _ineligible_bid_ladder("MISSING_ASK", fair, signal_min_edge, post_fill_min_edge)
    edge_max_bid = quantize_price(fair - post_fill_min_edge)
    post_only_top_bid = quantize_price(min(edge_max_bid, best_ask - 0.01))
    if post_only_top_bid <= 0:
        return _ineligible_bid_ladder("NO_POST_ONLY_PRICE", fair, signal_min_edge, post_fill_min_edge)
    low_bid = quantize_price(post_only_top_bid - range_cents)
    step = quantize_price(step_cents)
    levels: list[dict[str, Any]] = []
    total = 0.0
    price = post_only_top_bid
    best_bid = book.best_bid
    while price >= low_bid and price > 0 and total < total_notional_usd:
        notional = min(order_notional_usd, quantize_usdc(total_notional_usd - total))
        levels.append(
            {
                "price": price,
                "notional_usd": quantize_usdc(notional),
                "edge_after_fill": round(fair - price, 4),
                "distance_from_ask": round(best_ask - price, 4),
                "distance_from_best_bid": None if best_bid is None else round(price - best_bid, 4),
                "would_improve_best_bid": best_bid is None or price > best_bid,
                "would_be_best_bid": best_bid is None or price > best_bid,
            }
        )
        total = quantize_usdc(total + notional)
        price = quantize_price(price - step)
    edges = [float(level["edge_after_fill"]) for level in levels]
    return {
        "version": 1,
        "mode": "post_only_bid_ladder",
        "eligible": True,
        "reason": None,
        "fair": quantize_price(fair),
        "entry_ask": quantize_price(best_ask),
        "entry_edge": round(entry_edge, 4),
        "signal_min_edge": signal_min_edge,
        "post_fill_min_edge": post_fill_min_edge,
        "edge_max_bid": edge_max_bid,
        "post_only_top_bid": post_only_top_bid,
        "low_bid": low_bid,
        "step": step,
        "ttl_seconds": ttl_seconds,
        "order_notional_usd": quantize_usdc(order_notional_usd),
        "total_notional_usd": quantize_usdc(total),
        "target_total_notional_usd": quantize_usdc(total_notional_usd),
        "levels": levels,
        "level_count": len(levels),
        "top_distance_from_ask": None if not levels else levels[0]["distance_from_ask"],
        "top_improvement_over_best_bid": None if not levels else levels[0]["distance_from_best_bid"],
        "min_edge": min(edges) if edges else None,
        "max_edge": max(edges) if edges else None,
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


def _execution_ineligible_reason(
    *,
    book: BookSnapshot | None,
    selected_side: str,
    fair: float | None,
    entry_edge: float | None,
    signal_min_edge: float,
) -> str | None:
    if selected_side not in {"BUY_YES", "BUY_NO"}:
        return "SKIP"
    if fair is None:
        return "MISSING_FAIR"
    if book is None:
        return "MISSING_BOOK"
    if book.best_ask is None:
        return "MISSING_ASK"
    edge = entry_edge if entry_edge is not None else fair - book.best_ask
    if edge < signal_min_edge:
        return "EDGE_BELOW_SIGNAL_GATE"
    return None


def _ineligible_ask_sweep(
    reason: str,
    fair: float | None,
    signal_min_edge: float,
    post_fill_min_edge: float,
    sweep_max_slippage: float,
) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "ask_sweep",
        "eligible": False,
        "reason": reason,
        "fair": None if fair is None else quantize_price(fair),
        "signal_min_edge": signal_min_edge,
        "post_fill_min_edge": post_fill_min_edge,
        "sweep_max_slippage": sweep_max_slippage,
        "price_cap": None,
        "depth_to_cap": 0.0,
        "targets": {},
    }


def _ineligible_bid_ladder(
    reason: str,
    fair: float | None,
    signal_min_edge: float,
    post_fill_min_edge: float,
) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "post_only_bid_ladder",
        "eligible": False,
        "reason": reason,
        "fair": None if fair is None else quantize_price(fair),
        "signal_min_edge": signal_min_edge,
        "post_fill_min_edge": post_fill_min_edge,
        "edge_max_bid": None,
        "post_only_top_bid": None,
        "low_bid": None,
        "levels": [],
        "level_count": 0,
        "total_notional_usd": 0.0,
    }
