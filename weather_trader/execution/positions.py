from __future__ import annotations

from dataclasses import replace

from weather_trader.execution.contracts import (
    BookSnapshot,
    EffectiveStatus,
    MarketSnapshot,
    OrderState,
    PaperOrder,
    Position,
    PositionMark,
    TradeAction,
    utc_now_iso,
)


class PositionTracker:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions = positions or []

    def apply_order(self, order: PaperOrder, market: MarketSnapshot, current_bid: float | None = None) -> Position | None:
        if order.state not in {OrderState.FILLED, OrderState.PARTIAL}:
            return None
        existing = next((position for position in self.positions if position.token_id == order.token_id and position.state == "OPEN"), None)
        if existing is None:
            position = Position(
                position_id=f"{market.market_id}:{order.token_id}",
                market_id=market.market_id,
                token_id=order.token_id,
                side=order.action,
                station=market.station,
                market_date=market.market_date,
                lower_f=market.lower_f,
                upper_f=market.upper_f,
                shares=order.filled_shares,
                avg_entry_price=order.avg_price or 0.0,
                cost=order.cost,
                current_bid=current_bid,
                mark_value=(current_bid or 0.0) * order.filled_shares,
                unrealized_pnl=((current_bid or 0.0) * order.filled_shares) - order.cost,
                state="OPEN",
            )
            self.positions.append(position)
            return position

        total_cost = existing.cost + order.cost
        total_shares = existing.shares + order.filled_shares
        avg_price = total_cost / total_shares if total_shares else 0.0
        updated = replace(
            existing,
            shares=total_shares,
            avg_entry_price=avg_price,
            cost=total_cost,
            current_bid=current_bid,
            mark_value=(current_bid or 0.0) * total_shares,
            unrealized_pnl=((current_bid or 0.0) * total_shares) - total_cost,
        )
        self.positions = [updated if position.position_id == existing.position_id else position for position in self.positions]
        return updated

    def open_positions(self) -> list[Position]:
        return [position for position in self.positions if position.state == "OPEN"]

    def update_mark(self, mark: PositionMark) -> Position | None:
        existing = next((position for position in self.positions if position.position_id == mark.position_id), None)
        if existing is None or mark.mark_value is None or mark.unrealized_pnl is None:
            return existing
        updated = replace(
            existing,
            current_bid=mark.current_bid,
            mark_value=mark.mark_value,
            unrealized_pnl=mark.unrealized_pnl,
        )
        self.positions = [updated if position.position_id == existing.position_id else position for position in self.positions]
        return updated


def mark_position(position: Position, book: BookSnapshot | None, high_so_far: float | None) -> PositionMark:
    current_bid = book.best_bid if book else None
    if current_bid is None:
        mark_value = None
        unrealized_pnl = None
        unrealized_pnl_pct = None
        mark_reason = "MISSING_BID" if book else "MISSING_BOOK"
    else:
        mark_value = position.shares * current_bid
        unrealized_pnl = mark_value - position.cost
        unrealized_pnl_pct = unrealized_pnl / position.cost if position.cost else None
        mark_reason = "MARKED_TO_BID"

    effective_status = effective_status_for_position(
        side=position.side,
        lower_f=position.lower_f,
        upper_f=position.upper_f,
        high_so_far=high_so_far,
    )
    reason = mark_reason if effective_status == EffectiveStatus.LIVE else f"{mark_reason},{effective_status.value}"
    return PositionMark(
        timestamp=utc_now_iso(),
        position_id=position.position_id,
        market_id=position.market_id,
        token_id=position.token_id,
        side=position.side,
        station=position.station,
        market_date=position.market_date,
        lower_f=position.lower_f,
        upper_f=position.upper_f,
        shares=position.shares,
        cost=position.cost,
        avg_entry_price=position.avg_entry_price,
        current_bid=current_bid,
        mark_value=mark_value,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
        high_so_far=high_so_far,
        effective_status=effective_status,
        reason=reason,
    )


def effective_status_for_position(
    side: TradeAction,
    lower_f: float | None,
    upper_f: float | None,
    high_so_far: float | None,
) -> EffectiveStatus:
    if high_so_far is None:
        return EffectiveStatus.UNKNOWN

    yes_outcome = _definitive_yes_outcome(high_so_far=high_so_far, lower_f=lower_f, upper_f=upper_f)
    if yes_outcome is None:
        return EffectiveStatus.LIVE
    if side == TradeAction.BUY_YES:
        return EffectiveStatus.EFFECTIVELY_WON if yes_outcome else EffectiveStatus.EFFECTIVELY_LOST
    if side == TradeAction.BUY_NO:
        return EffectiveStatus.EFFECTIVELY_LOST if yes_outcome else EffectiveStatus.EFFECTIVELY_WON
    return EffectiveStatus.UNKNOWN


def _definitive_yes_outcome(high_so_far: float, lower_f: float | None, upper_f: float | None) -> bool | None:
    if lower_f is not None and upper_f is not None:
        if high_so_far > upper_f:
            return False
        return None
    if lower_f is not None:
        if high_so_far >= lower_f:
            return True
        return None
    if upper_f is not None:
        if high_so_far > upper_f:
            return False
        return None
    return None


def winning_side_for_bucket(final_high: float, lower_f: float | None, upper_f: float | None) -> TradeAction:
    if lower_f is not None and upper_f is not None:
        return TradeAction.BUY_YES if lower_f <= final_high <= upper_f else TradeAction.BUY_NO
    if lower_f is not None:
        return TradeAction.BUY_YES if final_high >= lower_f else TradeAction.BUY_NO
    if upper_f is not None:
        return TradeAction.BUY_YES if final_high <= upper_f else TradeAction.BUY_NO
    return TradeAction.BUY_NO
