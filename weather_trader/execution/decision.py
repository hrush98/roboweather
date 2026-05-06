from __future__ import annotations

from dataclasses import dataclass

from weather_trader.execution.contracts import BookSnapshot, Decision, MarketSnapshot, Signal, StrategyBucket, TradeAction, utc_now_iso


@dataclass(frozen=True)
class DecisionConfig:
    high_conviction_min_probability: float = 0.75
    high_conviction_min_edge: float = 0.10
    tail_min_probability: float = 0.08
    tail_max_probability: float = 0.25
    tail_max_ask: float = 0.10
    tail_min_relative_value: float = 2.0
    tail_min_edge: float = 0.05
    max_spread: float = 0.08
    high_conviction_bankroll_pct: float = 0.005
    tail_bankroll_pct: float = 0.001
    hrrr_veto_enabled: bool = True


class DecisionEngine:
    def __init__(self, config: DecisionConfig | None = None) -> None:
        self.config = config or DecisionConfig()

    def decide(
        self,
        market: MarketSnapshot,
        signal: Signal,
        yes_book: BookSnapshot | None,
        no_book: BookSnapshot | None,
        bankroll_usd: float,
    ) -> Decision:
        blocked_reasons = [code for code in signal.reason_codes if code.endswith("_BLOCKED")]
        if blocked_reasons:
            return Decision(
                timestamp=utc_now_iso(),
                market_id=market.market_id,
                token_id=None,
                action=TradeAction.SKIP,
                strategy_bucket=StrategyBucket.NONE,
                max_price=None,
                target_usd=0.0,
                expected_value=None,
                skip_reasons=blocked_reasons,
                reason_codes=signal.reason_codes,
            )
        if self.config.hrrr_veto_enabled and "HRRR_MISSING_LOG" not in signal.reason_codes:
            veto_reasons = []
            yes_veto = _hrrr_veto_reasons(TradeAction.BUY_YES, signal)
            no_veto = _hrrr_veto_reasons(TradeAction.BUY_NO, signal)
        else:
            yes_veto = []
            no_veto = []
        candidates = [
            self._candidate(
                action=TradeAction.BUY_YES,
                token_id=market.yes_token_id,
                fair=signal.fair_yes,
                edge=signal.edge_yes,
                book=yes_book,
                bankroll_usd=bankroll_usd,
                veto_reasons=yes_veto,
            ),
            self._candidate(
                action=TradeAction.BUY_NO,
                token_id=market.no_token_id,
                fair=signal.fair_no,
                edge=signal.edge_no,
                book=no_book,
                bankroll_usd=bankroll_usd,
                veto_reasons=no_veto,
            ),
        ]
        candidates = [candidate for candidate in candidates if candidate.action != TradeAction.SKIP]
        if not candidates:
            return Decision(
                timestamp=utc_now_iso(),
                market_id=market.market_id,
                token_id=None,
                action=TradeAction.SKIP,
                strategy_bucket=StrategyBucket.NONE,
                max_price=None,
                target_usd=0.0,
                expected_value=None,
                skip_reasons=["NO_DECISION_RULE_MATCH"],
                reason_codes=signal.reason_codes,
            )
        return max(candidates, key=lambda candidate: candidate.expected_value or 0.0)

    def _candidate(
        self,
        action: TradeAction,
        token_id: str | None,
        fair: float,
        edge: float | None,
        book: BookSnapshot | None,
        bankroll_usd: float,
        veto_reasons: list[str] | None = None,
    ) -> Decision:
        skip_reasons: list[str] = []
        skip_reasons.extend(veto_reasons or [])
        if not token_id:
            skip_reasons.append("MISSING_TOKEN")
        if book is None:
            skip_reasons.append("MISSING_BOOK")
            ask = None
            spread = None
        else:
            ask = book.best_ask
            spread = book.spread
        if ask is None:
            skip_reasons.append("MISSING_ASK")
        if spread is None:
            skip_reasons.append("MISSING_SPREAD")
        elif spread > self.config.max_spread:
            skip_reasons.append("SPREAD_TOO_WIDE")
        if edge is None:
            skip_reasons.append("MISSING_EDGE")
        if skip_reasons:
            return self._skip(skip_reasons)

        assert ask is not None
        assert edge is not None
        strategy_bucket = StrategyBucket.NONE
        target_pct = 0.0
        if fair >= self.config.high_conviction_min_probability and edge >= self.config.high_conviction_min_edge:
            strategy_bucket = StrategyBucket.HIGH_CONVICTION
            target_pct = self.config.high_conviction_bankroll_pct
        elif (
            self.config.tail_min_probability <= fair <= self.config.tail_max_probability
            and ask <= self.config.tail_max_ask
            and fair / ask >= self.config.tail_min_relative_value
            and edge >= self.config.tail_min_edge
        ):
            strategy_bucket = StrategyBucket.TAIL
            target_pct = self.config.tail_bankroll_pct

        if strategy_bucket == StrategyBucket.NONE:
            return self._skip(["NO_DECISION_RULE_MATCH"])

        return Decision(
            timestamp=utc_now_iso(),
            market_id="",
            token_id=token_id,
            action=action,
            strategy_bucket=strategy_bucket,
            max_price=ask,
            target_usd=bankroll_usd * target_pct,
            expected_value=edge,
            skip_reasons=[],
            reason_codes=[],
        )

    @staticmethod
    def _skip(skip_reasons: list[str]) -> Decision:
        return Decision(
            timestamp=utc_now_iso(),
            market_id="",
            token_id=None,
            action=TradeAction.SKIP,
            strategy_bucket=StrategyBucket.NONE,
            max_price=None,
            target_usd=0.0,
            expected_value=None,
            skip_reasons=skip_reasons,
            reason_codes=[],
        )


def finalize_decision(decision: Decision, market_id: str, reason_codes: list[str]) -> Decision:
    return Decision(
        timestamp=decision.timestamp,
        market_id=market_id,
        token_id=decision.token_id,
        action=decision.action,
        strategy_bucket=decision.strategy_bucket,
        max_price=decision.max_price,
        target_usd=decision.target_usd,
        expected_value=decision.expected_value,
        skip_reasons=decision.skip_reasons,
        reason_codes=reason_codes,
    )


def _hrrr_veto_reasons(action: TradeAction, signal: Signal) -> list[str]:
    reasons: list[str] = []
    high_so_far = signal.high_so_far
    hrrr_max = signal.hrrr_remaining_max
    if hrrr_max is None:
        return reasons
    lower = signal.lower_f
    upper = signal.upper_f

    if action == TradeAction.BUY_YES:
        if lower is not None and high_so_far < lower and hrrr_max < lower:
            reasons.append("HRRR_VETO_BELOW_LOWER_BLOCKED")
        if lower is not None and upper is not None and high_so_far < lower and hrrr_max > upper:
            reasons.append("HRRR_VETO_ABOVE_BUCKET_BLOCKED")
        return reasons

    if action == TradeAction.BUY_NO:
        if lower is not None and upper is None and (high_so_far >= lower or hrrr_max >= lower):
            reasons.append("HRRR_VETO_OR_HIGHER_SUPPORTED_BLOCKED")
        if lower is not None and upper is not None and lower <= high_so_far <= upper and hrrr_max <= upper:
            reasons.append("HRRR_VETO_CURRENT_BUCKET_SUPPORTED_BLOCKED")
        if lower is None and upper is not None and hrrr_max <= upper:
            reasons.append("HRRR_VETO_OR_BELOW_SUPPORTED_BLOCKED")
    return reasons
