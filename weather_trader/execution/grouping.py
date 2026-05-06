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

    def select_all_strategies(
        self,
        contexts: list[GroupMarketContext],
        bankroll_usd: float,
    ) -> list[GroupSelection]:
        return [
            self.select_strategy(contexts, bankroll_usd, strategy)
            for strategy in (
                StrategyBucket.BEST_BUCKET,
                StrategyBucket.HIGH_CONVICTION,
                StrategyBucket.TAIL,
                StrategyBucket.MAX_SO_FAR,
            )
        ]

    def select_strategy(
        self,
        contexts: list[GroupMarketContext],
        bankroll_usd: float,
        strategy_bucket: StrategyBucket,
    ) -> GroupSelection:
        if not contexts:
            raise ValueError("Cannot select station/date decision from empty context list")

        contexts_sorted = sorted(contexts, key=_context_sort_key)
        distribution = [_distribution_row(context.market, context.signal) for context in contexts_sorted]
        decisions: dict[str, Decision] = {}
        candidates: list[dict[str, object]] = []

        selected_context = self._strategy_context(contexts_sorted, strategy_bucket)
        for context in contexts_sorted:
            market = context.market
            decision = _skip_decision(market.market_id, strategy_bucket, "STRATEGY_NOT_SELECTED")
            fair = context.signal.fair_yes
            edge = context.signal.edge_yes
            action = TradeAction.BUY_YES
            book = context.yes_book
            apply_hrrr_veto = True

            if selected_context is not None and market.market_id == selected_context.market.market_id:
                if strategy_bucket == StrategyBucket.HIGH_CONVICTION:
                    action, fair, edge, book = _best_side(context, StrategyBucket.HIGH_CONVICTION)
                elif strategy_bucket == StrategyBucket.TAIL:
                    action, fair, edge, book = _best_side(context, StrategyBucket.TAIL)
                elif strategy_bucket == StrategyBucket.MAX_SO_FAR:
                    fair = 1.0
                    edge = fair - context.signal.yes_ask if context.signal.yes_ask is not None else None
                    apply_hrrr_veto = False

                decision = self.decision_engine.candidate_for_strategy(
                    market=market,
                    signal=context.signal,
                    action=action,
                    fair=fair,
                    edge=edge,
                    book=book,
                    bankroll_usd=bankroll_usd,
                    strategy_bucket=strategy_bucket,
                    apply_hrrr_veto=apply_hrrr_veto,
                )
                if decision.action == TradeAction.SKIP:
                    decision = Decision(
                        timestamp=decision.timestamp,
                        market_id=decision.market_id,
                        token_id=decision.token_id,
                        action=decision.action,
                        strategy_bucket=strategy_bucket,
                        max_price=decision.max_price,
                        target_usd=decision.target_usd,
                        expected_value=decision.expected_value,
                        skip_reasons=decision.skip_reasons,
                        reason_codes=decision.reason_codes,
                    )

            decisions[market.market_id] = decision
            is_max_so_far_selected = (
                strategy_bucket == StrategyBucket.MAX_SO_FAR
                and selected_context is not None
                and market.market_id == selected_context.market.market_id
            )
            candidates.append(
                _candidate_row(
                    market,
                    context.signal,
                    decision,
                    selected=decision.action != TradeAction.SKIP,
                    fair_yes=fair if is_max_so_far_selected else None,
                    edge_yes=edge if is_max_so_far_selected else None,
                )
            )

        selected = next((decision for decision in decisions.values() if decision.action != TradeAction.SKIP), None)
        first = contexts[0].market
        trace = StationDateDecisionTrace(
            timestamp=utc_now_iso(),
            station=first.station,
            market_date=first.market_date,
            candidate_count=1 if selected_context is not None else 0,
            selected_market_id=selected.market_id if selected else None,
            selected_action=selected.action if selected else TradeAction.SKIP,
            selected_strategy_bucket=strategy_bucket,
            selected_edge=selected.expected_value if selected else None,
            selected_score=selected.expected_value if selected else None,
            skip_reason=None if selected else "NO_GROUP_CANDIDATE",
            distribution=distribution,
            candidates=candidates,
        )
        return GroupSelection(decisions=decisions, selected_decision=selected, trace=trace)

    def _strategy_context(
        self,
        contexts: list[GroupMarketContext],
        strategy_bucket: StrategyBucket,
    ) -> GroupMarketContext | None:
        if strategy_bucket == StrategyBucket.BEST_BUCKET:
            return max(contexts, key=lambda context: context.signal.fair_yes)
        if strategy_bucket == StrategyBucket.MAX_SO_FAR:
            return next((context for context in contexts if _contains(context.signal.high_so_far, context.market.lower_f, context.market.upper_f)), None)

        candidates: list[tuple[float, GroupMarketContext]] = []
        for context in contexts:
            action, fair, edge, _ = _best_side(context, strategy_bucket)
            ask = context.signal.yes_ask if action == TradeAction.BUY_YES else context.signal.no_ask
            if self.decision_engine.strategy_matches(strategy_bucket, fair, edge, ask):
                candidates.append((edge or 0.0, context))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]


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
    fair_yes: float | None = None,
    edge_yes: float | None = None,
) -> dict[str, object]:
    row_fair_yes = signal.fair_yes if fair_yes is None else fair_yes
    row_edge_yes = signal.edge_yes if edge_yes is None else edge_yes
    return {
        "market_id": market.market_id,
        "bucket": _bucket_label(market.lower_f, market.upper_f),
        "action": str(decision.action),
        "strategy_bucket": str(decision.strategy_bucket),
        "fair_yes": row_fair_yes,
        "fair_no": 1.0 - row_fair_yes if fair_yes is not None else signal.fair_no,
        "yes_ask": signal.yes_ask,
        "no_ask": signal.no_ask,
        "edge_yes": row_edge_yes,
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


def _best_side(
    context: GroupMarketContext,
    strategy_bucket: StrategyBucket,
) -> tuple[TradeAction, float, float | None, BookSnapshot | None]:
    yes_edge = context.signal.edge_yes if context.signal.edge_yes is not None else float("-inf")
    no_edge = context.signal.edge_no if context.signal.edge_no is not None else float("-inf")
    if strategy_bucket == StrategyBucket.TAIL:
        yes_match = context.signal.edge_yes is not None and context.signal.yes_ask is not None and context.signal.yes_ask <= 0.10
        no_match = context.signal.edge_no is not None and context.signal.no_ask is not None and context.signal.no_ask <= 0.10
        if no_match and (not yes_match or no_edge > yes_edge):
            return TradeAction.BUY_NO, context.signal.fair_no, context.signal.edge_no, context.no_book
    if no_edge > yes_edge:
        return TradeAction.BUY_NO, context.signal.fair_no, context.signal.edge_no, context.no_book
    return TradeAction.BUY_YES, context.signal.fair_yes, context.signal.edge_yes, context.yes_book


def _contains(value: float, lower_f: float | None, upper_f: float | None) -> bool:
    if lower_f is not None and upper_f is not None:
        return lower_f <= value <= upper_f
    if lower_f is not None:
        return value >= lower_f
    if upper_f is not None:
        return value <= upper_f
    return False


def _skip_decision(market_id: str, strategy_bucket: StrategyBucket, reason: str) -> Decision:
    return Decision(
        timestamp=utc_now_iso(),
        market_id=market_id,
        token_id=None,
        action=TradeAction.SKIP,
        strategy_bucket=strategy_bucket,
        max_price=None,
        target_usd=0.0,
        expected_value=None,
        skip_reasons=[reason],
        reason_codes=[],
    )
