from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from weather_trader.execution.contracts import (
    BookSnapshot,
    Decision,
    MarketSnapshot,
    Signal,
    StationDateDecisionTrace,
    StrategyBucket,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.decision import DecisionEngine, finalize_decision


@dataclass(frozen=True)
class GroupMarketContext:
    market: MarketSnapshot
    signal: Signal
    yes_book: BookSnapshot | None
    no_book: BookSnapshot | None


@dataclass(frozen=True)
class GroupSelection:
    decisions: dict[str, Decision]
    selected_decision: Decision | None
    trace: StationDateDecisionTrace


def group_key(market: MarketSnapshot) -> tuple[str, date | None]:
    return (market.station, market.market_date)


class StationDateDecisionEngine:
    def __init__(self, decision_engine: DecisionEngine | None = None) -> None:
        self.decision_engine = decision_engine or DecisionEngine()

    def select(
        self,
        contexts: list[GroupMarketContext],
        bankroll_usd: float,
    ) -> GroupSelection:
        if not contexts:
            raise ValueError("Cannot select station/date decision from empty context list")

        decisions: dict[str, Decision] = {}
        candidates: list[dict[str, object]] = []
        distribution: list[dict[str, object]] = []
        tradable: list[Decision] = []

        for context in sorted(contexts, key=_context_sort_key):
            market = context.market
            signal = context.signal
            distribution.append(_distribution_row(market, signal))
            decision = self.decision_engine.decide(
                market=market,
                signal=signal,
                yes_book=context.yes_book,
                no_book=context.no_book,
                bankroll_usd=bankroll_usd,
            )
            decision = finalize_decision(decision, market.market_id, signal.reason_codes)
            decisions[market.market_id] = decision
            candidates.append(_candidate_row(market, signal, decision, selected=False))
            if decision.action != TradeAction.SKIP:
                tradable.append(decision)

        selected = max(tradable, key=lambda item: item.expected_value or 0.0) if tradable else None
        final_decisions: dict[str, Decision] = {}
        for market_id, decision in decisions.items():
            if selected is not None and market_id == selected.market_id:
                final_decisions[market_id] = selected
                continue
            if decision.action == TradeAction.SKIP:
                final_decisions[market_id] = decision
                continue
            final_decisions[market_id] = Decision(
                timestamp=decision.timestamp,
                market_id=decision.market_id,
                token_id=decision.token_id,
                action=TradeAction.SKIP,
                strategy_bucket=decision.strategy_bucket,
                max_price=decision.max_price,
                target_usd=0.0,
                expected_value=decision.expected_value,
                skip_reasons=[*decision.skip_reasons, "GROUP_NOT_SELECTED"],
                reason_codes=decision.reason_codes,
            )

        selected_market_id = selected.market_id if selected is not None else None
        trace_candidates = [
            {**candidate, "selected": candidate["market_id"] == selected_market_id}
            for candidate in candidates
        ]
        first = contexts[0].market
        trace = StationDateDecisionTrace(
            timestamp=utc_now_iso(),
            station=first.station,
            market_date=first.market_date,
            candidate_count=len(tradable),
            selected_market_id=selected_market_id,
            selected_action=selected.action if selected else TradeAction.SKIP,
            selected_strategy_bucket=selected.strategy_bucket if selected else StrategyBucket.NONE,
            selected_edge=selected.expected_value if selected else None,
            selected_score=selected.expected_value if selected else None,
            skip_reason=None if selected else "NO_GROUP_CANDIDATE",
            distribution=distribution,
            candidates=trace_candidates,
        )
        return GroupSelection(decisions=final_decisions, selected_decision=selected, trace=trace)


def _context_sort_key(context: GroupMarketContext) -> tuple[float, float, str]:
    lower = context.market.lower_f if context.market.lower_f is not None else float("-inf")
    upper = context.market.upper_f if context.market.upper_f is not None else float("inf")
    return (lower, upper, context.market.market_id)


def _distribution_row(market: MarketSnapshot, signal: Signal) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "bucket": _bucket_label(market.lower_f, market.upper_f),
        "lower_f": market.lower_f,
        "upper_f": market.upper_f,
        "fair_yes": signal.fair_yes,
        "fair_no": signal.fair_no,
        "yes_ask": signal.yes_ask,
        "no_ask": signal.no_ask,
        "edge_yes": signal.edge_yes,
        "edge_no": signal.edge_no,
    }


def _candidate_row(
    market: MarketSnapshot,
    signal: Signal,
    decision: Decision,
    selected: bool,
) -> dict[str, object]:
    return {
        "market_id": market.market_id,
        "bucket": _bucket_label(market.lower_f, market.upper_f),
        "action": str(decision.action),
        "strategy_bucket": str(decision.strategy_bucket),
        "fair_yes": signal.fair_yes,
        "fair_no": signal.fair_no,
        "yes_ask": signal.yes_ask,
        "no_ask": signal.no_ask,
        "edge_yes": signal.edge_yes,
        "edge_no": signal.edge_no,
        "expected_value": decision.expected_value,
        "target_usd": decision.target_usd,
        "max_price": decision.max_price,
        "skip_reasons": decision.skip_reasons,
        "selected": selected,
    }


def _bucket_label(lower_f: float | None, upper_f: float | None) -> str:
    if lower_f is not None and upper_f is not None:
        return f"{lower_f:g}-{upper_f:g}F"
    if lower_f is not None:
        return f">={lower_f:g}F"
    if upper_f is not None:
        return f"<={upper_f:g}F"
    return "unknown"
