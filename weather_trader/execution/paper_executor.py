from __future__ import annotations

import uuid

from weather_trader.execution.contracts import BookSnapshot, Decision, OrderState, PaperOrder, TradeAction, utc_now_iso


class PaperOrderExecutor:
    def __init__(self, strict_fok: bool = True, min_fill_usd: float = 1.0) -> None:
        self.strict_fok = strict_fok
        self.min_fill_usd = min_fill_usd

    def submit(self, decision: Decision, book: BookSnapshot | None) -> PaperOrder:
        order_id = f"paper-{uuid.uuid4().hex[:12]}"
        if decision.action == TradeAction.SKIP or not decision.token_id or decision.max_price is None:
            return self._rejected(order_id, decision, "SKIP_DECISION")
        if book is None:
            return self._rejected(order_id, decision, "MISSING_BOOK")

        target_shares = decision.target_usd / decision.max_price
        remaining = target_shares
        filled_shares = 0.0
        cost = 0.0
        consumed: list[dict[str, float]] = []
        for level in book.asks:
            if level.price > decision.max_price:
                break
            take = min(remaining, level.size)
            if take <= 0:
                break
            filled_shares += take
            cost += take * level.price
            consumed.append({"price": level.price, "shares": take, "cost": take * level.price})
            remaining -= take
            if remaining <= 1e-9:
                break

        if filled_shares <= 0 or cost < self.min_fill_usd:
            return self._rejected(order_id, decision, "INSUFFICIENT_DEPTH")
        if self.strict_fok and remaining > 1e-9:
            return self._rejected(order_id, decision, "FOK_NOT_FILLED")

        avg_price = cost / filled_shares
        state = OrderState.FILLED if remaining <= 1e-9 else OrderState.PARTIAL
        return PaperOrder(
            timestamp=utc_now_iso(),
            order_id=order_id,
            market_id=decision.market_id,
            token_id=decision.token_id,
            action=decision.action,
            state=state,
            max_price=decision.max_price,
            target_usd=decision.target_usd,
            filled_shares=filled_shares,
            avg_price=avg_price,
            cost=cost,
            levels_consumed=consumed,
            reject_reason=None,
        )

    @staticmethod
    def _rejected(order_id: str, decision: Decision, reason: str) -> PaperOrder:
        return PaperOrder(
            timestamp=utc_now_iso(),
            order_id=order_id,
            market_id=decision.market_id,
            token_id=decision.token_id or "",
            action=decision.action,
            state=OrderState.REJECTED,
            max_price=decision.max_price or 0.0,
            target_usd=decision.target_usd,
            filled_shares=0.0,
            avg_price=None,
            cost=0.0,
            levels_consumed=[],
            reject_reason=reason,
        )
