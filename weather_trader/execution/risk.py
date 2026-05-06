from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weather_trader.execution.contracts import Decision, Position, RiskState, TradeAction, utc_now_iso


@dataclass(frozen=True)
class RiskConfig:
    bankroll_usd: float = 1000.0
    max_usd_per_order: float = 10.0
    max_open_positions: int = 20
    max_station_date_pct: float = 0.02
    max_portfolio_pct: float = 0.05
    min_seconds_between_orders: float = 3.0
    kill_switch_path: str = "~/.roboweather/STOP_TRADING"


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._last_order_ts: float | None = None

    def current_state(self, positions: list[Position]) -> RiskState:
        exposures = self.station_date_exposure(positions)
        return RiskState(
            timestamp=utc_now_iso(),
            bankroll_usd=self.config.bankroll_usd,
            open_positions=len([position for position in positions if position.state == "OPEN"]),
            station_date_exposure_usd=exposures,
            portfolio_exposure_usd=sum(exposures.values()),
            kill_switch_active=self.kill_switch_active(),
        )

    def apply(self, decision: Decision, market_station: str, market_date, positions: list[Position], now_ts: float) -> Decision:
        if decision.action == TradeAction.SKIP:
            return decision
        skip_reasons: list[str] = []
        if self.kill_switch_active():
            skip_reasons.append("KILL_SWITCH_ACTIVE")
        if self._last_order_ts is not None and now_ts - self._last_order_ts < self.config.min_seconds_between_orders:
            skip_reasons.append("ORDER_RATE_LIMIT")
        open_positions = [position for position in positions if position.state == "OPEN"]
        if len(open_positions) >= self.config.max_open_positions:
            skip_reasons.append("MAX_OPEN_POSITIONS")
        if any(position.token_id == decision.token_id for position in open_positions):
            skip_reasons.append("DUPLICATE_TOKEN_POSITION")

        station_key = f"{market_station}:{market_date}"
        station_exposure = self.station_date_exposure(open_positions).get(station_key, 0.0)
        max_station_exposure = self.config.bankroll_usd * self.config.max_station_date_pct
        max_portfolio_exposure = self.config.bankroll_usd * self.config.max_portfolio_pct
        portfolio_exposure = sum(position.cost for position in open_positions)

        target_usd = min(decision.target_usd, self.config.max_usd_per_order)
        remaining_station = max(0.0, max_station_exposure - station_exposure)
        remaining_portfolio = max(0.0, max_portfolio_exposure - portfolio_exposure)
        target_usd = min(target_usd, remaining_station, remaining_portfolio)
        if target_usd <= 0:
            skip_reasons.append("EXPOSURE_CAP_REACHED")

        if skip_reasons:
            return Decision(
                timestamp=decision.timestamp,
                market_id=decision.market_id,
                token_id=decision.token_id,
                action=TradeAction.SKIP,
                strategy_bucket=decision.strategy_bucket,
                max_price=decision.max_price,
                target_usd=0.0,
                expected_value=decision.expected_value,
                skip_reasons=[*decision.skip_reasons, *skip_reasons],
                reason_codes=decision.reason_codes,
            )

        return Decision(
            timestamp=decision.timestamp,
            market_id=decision.market_id,
            token_id=decision.token_id,
            action=decision.action,
            strategy_bucket=decision.strategy_bucket,
            max_price=decision.max_price,
            target_usd=target_usd,
            expected_value=decision.expected_value,
            skip_reasons=decision.skip_reasons,
            reason_codes=decision.reason_codes,
        )

    def mark_order_submitted(self, now_ts: float) -> None:
        self._last_order_ts = now_ts

    def kill_switch_active(self) -> bool:
        return Path(self.config.kill_switch_path).expanduser().exists()

    @staticmethod
    def station_date_exposure(positions: list[Position]) -> dict[str, float]:
        exposure: dict[str, float] = {}
        for position in positions:
            if position.state != "OPEN":
                continue
            key = f"{position.station}:{position.market_date}"
            exposure[key] = exposure.get(key, 0.0) + position.cost
        return exposure
