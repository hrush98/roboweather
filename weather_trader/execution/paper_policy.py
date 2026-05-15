from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from weather_trader.execution.books import RestBookClient
from weather_trader.execution.contracts import (
    BookSnapshot,
    PaperPolicyEventType,
    PaperPolicyFinalState,
    PaperPolicyOrderAttempt,
    PaperPolicyOrderMode,
    PaperPolicyPosition,
    PaperPolicyRiskSnapshot,
    PaperPolicySizingDecision,
    PaperPolicyTradeEvent,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.positions import winning_side_for_bucket
from weather_trader.execution.store import ExecutionStore


DEFAULT_PROMOTED_POLICIES: tuple[str, ...] = (
    "pm_us12_consensus_hc_15m_entry_50_75_first",
    "pm_us12_consensus_hc_late_entry_50_75_first",
    "pm_us12_consensus_hc_15m_entry_25_75_first",
)


@dataclass(frozen=True)
class PaperPolicyRiskConfig:
    bankroll_usd: float = 1000.0
    fixed_fraction: float = 0.02
    max_usd_per_order: float = 25.0
    max_exposure_per_station_date: float = 50.0
    max_total_open_risk: float = 150.0
    allow_duplicate_bucket_side: bool = False


@dataclass(frozen=True)
class PaperPolicyExecutionConfig:
    promoted_policies: tuple[str, ...] = DEFAULT_PROMOTED_POLICIES
    risk: PaperPolicyRiskConfig = PaperPolicyRiskConfig()
    order_mode: PaperPolicyOrderMode = PaperPolicyOrderMode.FOK
    max_book_age_seconds: float = 10.0
    min_fill_usd: float = 1.0
    max_attempts: int = 3
    unknown_retry_grace_seconds: float = 30.0
    retry_cooldown_seconds: float = 30.0
    fok_miss_probability: float = 0.0
    stale_book_probability: float = 0.0
    delayed_probability: float = 0.0
    unknown_probability: float = 0.0
    partial_fill_probability: float = 0.0
    random_seed: int | None = None


@dataclass(frozen=True)
class PaperPolicyCycleResult:
    candidates: int
    reserved: int
    attempts: int
    filled: int
    rejected: int
    delayed: int
    unknown: int
    marked: int
    settled: int


class SizingModel(Protocol):
    def size(self, row: dict, exposure_summary: dict) -> PaperPolicySizingDecision:
        ...


class FixedFractionSizingModel:
    def __init__(self, config: PaperPolicyRiskConfig) -> None:
        self.config = config

    def size(self, row: dict, exposure_summary: dict) -> PaperPolicySizingDecision:
        station_key = f"{row['station']}:{row['market_date']}"
        station_exposure = float(exposure_summary["station_date_exposure_usd"].get(station_key, 0.0))
        total_exposure = float(exposure_summary["open_risk_usd"])
        base = self.config.bankroll_usd * self.config.fixed_fraction
        caps = {
            "fixed_fraction": base,
            "max_usd_per_order": self.config.max_usd_per_order,
            "station_date_remaining": max(0.0, self.config.max_exposure_per_station_date - station_exposure),
            "total_open_remaining": max(0.0, self.config.max_total_open_risk - total_exposure),
        }
        target = min(caps.values())
        cap_reason = min(caps, key=caps.get)
        return PaperPolicySizingDecision(
            target_notional_usd=quantize_usdc(target),
            cap_reason=cap_reason,
            raw_inputs={
                "fair_probability": row.get("entry_fair"),
                "entry_price": row.get("entry_price"),
                "calibration_haircut": None,
                "liquidity_cap": None,
                "station_confidence": None,
                "policy_confidence": None,
                "caps": caps,
            },
        )


class PaperPolicyTrader:
    def __init__(
        self,
        store: ExecutionStore,
        config: PaperPolicyExecutionConfig | None = None,
        book_client: RestBookClient | None = None,
        sizing_model: SizingModel | None = None,
    ) -> None:
        self.store = store
        self.config = config or PaperPolicyExecutionConfig()
        self.book_client = book_client or RestBookClient()
        self.sizing_model = sizing_model or FixedFractionSizingModel(self.config.risk)
        self.random = random.Random(self.config.random_seed)

    def run_once(self, market_date: str | None = None) -> PaperPolicyCycleResult:
        self.reconcile_open_positions()
        candidates = self.store.promotable_research_policy_positions(
            set(self.config.promoted_policies),
            market_date=market_date,
        )
        reserved = attempts = filled = rejected = delayed = unknown = 0
        for row in candidates:
            state = self._promote(row)
            if state is None:
                continue
            reserved += 1
            attempts += 1
            if state == PaperPolicyFinalState.FILLED:
                filled += 1
            elif state == PaperPolicyFinalState.PARTIAL:
                filled += 1
            elif state == PaperPolicyFinalState.DELAYED:
                delayed += 1
            elif state == PaperPolicyFinalState.UNKNOWN:
                unknown += 1
            else:
                rejected += 1
        marked = self.mark_open_positions()
        settled = self.settle_resolved_positions()
        self._record_risk_snapshot()
        return PaperPolicyCycleResult(
            candidates=len(candidates),
            reserved=reserved,
            attempts=attempts,
            filled=filled,
            rejected=rejected,
            delayed=delayed,
            unknown=unknown,
            marked=marked,
            settled=settled,
        )

    def reconcile_open_positions(self) -> int:
        reconciled = 0
        now = datetime.now(timezone.utc)
        for row in self.store.paper_policy_open_positions():
            state = str(row["state"])
            if state not in {str(PaperPolicyFinalState.DELAYED), str(PaperPolicyFinalState.UNKNOWN)}:
                continue
            age = _age_seconds(str(row["timestamp"]), now)
            if age < self.config.unknown_retry_grace_seconds:
                continue
            self._event(
                int(row["id"]),
                int(row["research_policy_position_id"]),
                PaperPolicyEventType.ENTRY_RETRY,
                f"reconcile {state}",
                {"age_seconds": age, "retry_cooldown_seconds": self.config.retry_cooldown_seconds},
            )
            reconciled += 1
        return reconciled

    def mark_open_positions(self) -> int:
        marked = 0
        for row in self.store.paper_policy_open_positions():
            if float(row["filled_shares"] or 0.0) <= 0:
                continue
            try:
                book = self.book_client.fetch_book(str(row["selected_token_id"]))
            except Exception as exc:
                self._event(
                    int(row["id"]),
                    int(row["research_policy_position_id"]),
                    PaperPolicyEventType.MARK,
                    "missing mark book",
                    {"error": str(exc)},
                )
                continue
            current_bid = book.best_bid
            mark_value = None if current_bid is None else quantize_usdc(float(row["filled_shares"]) * current_bid)
            unrealized = None if mark_value is None else quantize_usdc(mark_value - float(row["cost_usd"] or 0.0))
            self.store.update_paper_policy_position_mark(
                int(row["id"]),
                mark_value=mark_value,
                unrealized_pnl=unrealized,
                raw_patch={"mark_book_timestamp": book.timestamp, "current_bid": current_bid},
            )
            self._event(
                int(row["id"]),
                int(row["research_policy_position_id"]),
                PaperPolicyEventType.MARK,
                "marked to bid",
                {"current_bid": current_bid, "mark_value": mark_value, "unrealized_pnl": unrealized},
            )
            marked += 1
        return marked

    def settle_resolved_positions(self) -> int:
        rows = self.store.connection.execute(
            """
            select p.*, o.final_high_tmpf
            from paper_policy_positions p
            join station_date_outcomes o
                on o.station = p.station
                and o.market_date = p.market_date
            where p.state in ('FILLED', 'PARTIAL')
            """
        ).fetchall()
        settled = 0
        for row in rows:
            lower = row["lower_f"] if "lower_f" in row.keys() else None
            upper = row["upper_f"] if "upper_f" in row.keys() else None
            if lower is None or upper is None:
                market = self.store.connection.execute(
                    "select lower_f, upper_f from markets where market_id = ?",
                    (row["selected_market_id"],),
                ).fetchone()
                lower = None if market is None else market["lower_f"]
                upper = None if market is None else market["upper_f"]
            winning = winning_side_for_bucket(float(row["final_high_tmpf"]), lower, upper)
            shares = float(row["filled_shares"] or 0.0)
            cost = float(row["cost_usd"] or 0.0)
            payout = shares if TradeAction(str(row["selected_side"])) == winning else 0.0
            pnl = quantize_usdc(payout - cost)
            rr = None if cost <= 0 else pnl / cost
            self.store.update_paper_policy_position_settlement(
                int(row["id"]),
                state=PaperPolicyFinalState.SETTLED,
                realized_pnl=pnl,
                realized_rr=rr,
                raw_patch={"final_high_tmpf": row["final_high_tmpf"], "winning_side": str(winning), "payout": payout},
            )
            self._event(
                int(row["id"]),
                int(row["research_policy_position_id"]),
                PaperPolicyEventType.RESOLVED,
                "settled from station high",
                {"realized_pnl": pnl, "realized_rr": rr, "winning_side": str(winning)},
            )
            settled += 1
        return settled

    def _promote(self, row: dict) -> PaperPolicyFinalState | None:
        token_id = str(row.get("selected_token_id") or "")
        if not token_id:
            self._event(None, int(row["id"]), PaperPolicyEventType.ENTRY_REJECTED, "missing selected token", row)
            return None
        if not self.config.risk.allow_duplicate_bucket_side and self.store.has_open_paper_policy_exposure(
            station=str(row["station"]),
            market_date=str(row["market_date"]),
            selected_bucket=row.get("selected_bucket"),
            selected_side=str(row["selected_side"]),
        ):
            self._event(None, int(row["id"]), PaperPolicyEventType.ENTRY_REJECTED, "duplicate exposure blocked", row)
            return None

        exposure = self.store.paper_policy_exposure_summary()
        sizing = self.sizing_model.size(row, exposure)
        if sizing.target_notional_usd < self.config.min_fill_usd:
            self._event(
                None,
                int(row["id"]),
                PaperPolicyEventType.ENTRY_REJECTED,
                "sizing below minimum",
                {"sizing": sizing.raw_inputs, "target_notional_usd": sizing.target_notional_usd},
            )
            return None

        position = PaperPolicyPosition(
            timestamp=utc_now_iso(),
            research_policy_position_id=int(row["id"]),
            policy_name=str(row["policy_name"]),
            station=str(row["station"]),
            market_date=datetime.fromisoformat(str(row["market_date"])).date(),
            selected_market_id=str(row["selected_market_id"]),
            selected_token_id=token_id,
            selected_side=TradeAction(str(row["selected_side"])),
            selected_bucket=row.get("selected_bucket"),
            entry_limit_price=quantize_price(float(row["entry_price"])),
            target_notional_usd=sizing.target_notional_usd,
            filled_shares=0.0,
            avg_entry_price=None,
            cost_usd=0.0,
            state=PaperPolicyFinalState.RESERVED,
            raw_json={"research_policy_position": _jsonish(row), "sizing": sizing.raw_inputs, "cap_reason": sizing.cap_reason},
        )
        paper_position_id = self.store.insert_paper_policy_position(position)
        if paper_position_id is None:
            return None
        self._event(paper_position_id, int(row["id"]), PaperPolicyEventType.ENTRY_RESERVED, "reserved paper exposure", {})

        attempt = self._execute_attempt(paper_position_id, row, position)
        self.store.insert_paper_policy_order_attempt(attempt)
        self.store.update_paper_policy_position_execution(
            paper_position_id,
            state=attempt.final_state,
            filled_shares=attempt.filled_shares,
            avg_entry_price=attempt.avg_price,
            cost_usd=attempt.cost_usd,
            raw_patch={"last_attempt_state": str(attempt.final_state), "last_attempt_reason": attempt.final_reason},
        )
        event_type = (
            PaperPolicyEventType.ENTRY_CONFIRMED
            if attempt.final_state in {PaperPolicyFinalState.FILLED, PaperPolicyFinalState.PARTIAL}
            else PaperPolicyEventType.ENTRY_REJECTED
        )
        self._event(paper_position_id, int(row["id"]), event_type, attempt.final_reason, attempt.raw_payload)
        return attempt.final_state

    def _execute_attempt(self, paper_position_id: int, row: dict, position: PaperPolicyPosition) -> PaperPolicyOrderAttempt:
        self._event(paper_position_id, int(row["id"]), PaperPolicyEventType.ENTRY_SUBMIT, "refetching live book", {})
        book: BookSnapshot | None
        try:
            book = self.book_client.fetch_book(position.selected_token_id)
        except Exception as exc:
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.REJECTED,
                "MISSING_BOOK",
                raw_payload={"error": str(exc)},
            )
        if self._roll(self.config.stale_book_probability) or _book_age_seconds(book) > self.config.max_book_age_seconds:
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.STALE_BOOK,
                "STALE_BOOK",
                raw_payload={"book_timestamp": book.timestamp, "max_book_age_seconds": self.config.max_book_age_seconds},
            )
        if self._roll(self.config.delayed_probability):
            return self._attempt(paper_position_id, row, position, PaperPolicyFinalState.DELAYED, "DELAYED", external_status="DELAYED")
        if self._roll(self.config.unknown_probability):
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.UNKNOWN,
                "UNKNOWN_ORDER_ID",
                external_status="UNKNOWN",
                external_order_id=None,
            )
        if self.config.order_mode == PaperPolicyOrderMode.FOK and self._roll(self.config.fok_miss_probability):
            return self._attempt(paper_position_id, row, position, PaperPolicyFinalState.FOK_NOT_FILLED, "FOK_MISS_AFTER_BOOK")

        fill = simulate_ladder_fill(
            book=book,
            limit_price=position.entry_limit_price,
            target_notional_usd=position.target_notional_usd,
            order_mode=self.config.order_mode,
            min_fill_usd=self.config.min_fill_usd,
            force_partial=self.config.order_mode == PaperPolicyOrderMode.FAK and self._roll(self.config.partial_fill_probability),
        )
        return self._attempt(
            paper_position_id,
            row,
            position,
            fill.final_state,
            fill.reason,
            filled_shares=fill.filled_shares,
            avg_price=fill.avg_price,
            cost_usd=fill.cost_usd,
            levels_consumed=fill.levels_consumed,
            external_order_id=f"paper-{uuid.uuid4().hex[:12]}",
            external_status=str(fill.final_state),
            raw_payload={"book_timestamp": book.timestamp, "best_ask": book.best_ask, "best_bid": book.best_bid},
        )

    def _attempt(
        self,
        paper_position_id: int,
        row: dict,
        position: PaperPolicyPosition,
        final_state: PaperPolicyFinalState,
        final_reason: str,
        *,
        filled_shares: float = 0.0,
        avg_price: float | None = None,
        cost_usd: float = 0.0,
        levels_consumed: list[dict[str, float]] | None = None,
        external_order_id: str | None = None,
        external_status: str | None = None,
        raw_payload: dict | None = None,
    ) -> PaperPolicyOrderAttempt:
        return PaperPolicyOrderAttempt(
            timestamp=utc_now_iso(),
            paper_position_id=paper_position_id,
            research_policy_position_id=int(row["id"]),
            attempt_seq=1,
            token_id=position.selected_token_id,
            side=position.selected_side,
            order_mode=self.config.order_mode,
            limit_price=position.entry_limit_price,
            target_notional_usd=position.target_notional_usd,
            external_order_id=external_order_id,
            external_status=external_status,
            not_found_count=1 if final_state == PaperPolicyFinalState.UNKNOWN else 0,
            final_state=final_state,
            final_reason=final_reason,
            filled_shares=filled_shares,
            avg_price=avg_price,
            cost_usd=cost_usd,
            levels_consumed=levels_consumed or [],
            raw_payload=raw_payload or {},
        )

    def _record_risk_snapshot(self) -> None:
        exposure = self.store.paper_policy_exposure_summary()
        self.store.insert_paper_policy_risk_snapshot(
            PaperPolicyRiskSnapshot(
                timestamp=utc_now_iso(),
                bankroll_usd=self.config.risk.bankroll_usd,
                open_positions=int(exposure["open_positions"]),
                open_risk_usd=float(exposure["open_risk_usd"]),
                station_date_exposure_usd=dict(exposure["station_date_exposure_usd"]),
                raw_payload={"risk_config": self.config.risk.__dict__},
            )
        )

    def _event(
        self,
        paper_position_id: int | None,
        research_policy_position_id: int | None,
        event_type: PaperPolicyEventType,
        message: str,
        payload: dict,
    ) -> None:
        self.store.insert_paper_policy_trade_event(
            PaperPolicyTradeEvent(
                timestamp=utc_now_iso(),
                paper_position_id=paper_position_id,
                research_policy_position_id=research_policy_position_id,
                event_type=event_type,
                message=message,
                raw_payload=payload,
            )
        )

    def _roll(self, probability: float) -> bool:
        return probability > 0 and self.random.random() < probability


@dataclass(frozen=True)
class LadderFill:
    final_state: PaperPolicyFinalState
    reason: str
    filled_shares: float
    avg_price: float | None
    cost_usd: float
    levels_consumed: list[dict[str, float]]


def simulate_ladder_fill(
    *,
    book: BookSnapshot,
    limit_price: float,
    target_notional_usd: float,
    order_mode: PaperPolicyOrderMode,
    min_fill_usd: float,
    force_partial: bool = False,
) -> LadderFill:
    limit_price = quantize_price(limit_price)
    target_notional_usd = quantize_usdc(target_notional_usd)
    if not book.asks:
        return LadderFill(PaperPolicyFinalState.REJECTED, "MISSING_ASKS", 0.0, None, 0.0, [])
    target_shares = quantize_shares(target_notional_usd / limit_price)
    if force_partial:
        target_shares = quantize_shares(target_shares / 2.0)
    remaining = target_shares
    shares = 0.0
    cost = 0.0
    consumed: list[dict[str, float]] = []
    for level in book.asks:
        price = quantize_price(level.price)
        if price > limit_price:
            break
        take = min(remaining, quantize_shares(level.size))
        if take <= 0:
            continue
        level_cost = quantize_usdc(take * price)
        shares = quantize_shares(shares + take)
        cost = quantize_usdc(cost + level_cost)
        consumed.append({"price": price, "shares": take, "cost": level_cost})
        remaining = quantize_shares(remaining - take)
        if remaining <= 0:
            break

    if shares <= 0 or cost < min_fill_usd:
        return LadderFill(PaperPolicyFinalState.REJECTED, "INSUFFICIENT_DEPTH", 0.0, None, 0.0, consumed)
    if order_mode == PaperPolicyOrderMode.FOK and remaining > 0:
        return LadderFill(PaperPolicyFinalState.FOK_NOT_FILLED, "FOK_NOT_FILLED", 0.0, None, 0.0, consumed)
    avg = quantize_price(cost / shares)
    state = PaperPolicyFinalState.FILLED if remaining <= 0 else PaperPolicyFinalState.PARTIAL
    return LadderFill(state, str(state), shares, avg, cost, consumed)


def quantize_price(value: float) -> float:
    return round(max(0.0, min(1.0, value)) + 1e-12, 4)


def quantize_usdc(value: float) -> float:
    return round(max(0.0, value) + 1e-12, 2)


def quantize_shares(value: float) -> float:
    return round(max(0.0, value) + 1e-12, 6)


def adversity_profile(name: str) -> PaperPolicyExecutionConfig:
    if name == "off":
        return PaperPolicyExecutionConfig()
    if name == "mild":
        return PaperPolicyExecutionConfig(
            fok_miss_probability=0.03,
            stale_book_probability=0.01,
            delayed_probability=0.01,
            unknown_probability=0.005,
        )
    if name == "stress":
        return PaperPolicyExecutionConfig(
            fok_miss_probability=0.15,
            stale_book_probability=0.05,
            delayed_probability=0.05,
            unknown_probability=0.03,
            partial_fill_probability=0.15,
        )
    raise ValueError(f"Unknown adversity profile: {name}")


def _book_age_seconds(book: BookSnapshot) -> float:
    return _age_seconds(book.timestamp, datetime.now(timezone.utc))


def _age_seconds(timestamp: str, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def _jsonish(row: dict) -> dict:
    return {str(key): value for key, value in row.items()}
