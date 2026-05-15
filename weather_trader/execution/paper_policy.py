from __future__ import annotations

import random
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

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
from weather_trader.execution.liquidity import quantize_price, quantize_usdc, walk_ask_ladder
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
    order_mode: PaperPolicyOrderMode = PaperPolicyOrderMode.FAK
    max_book_age_seconds: float = 10.0
    max_slippage_cents: float = 0.05
    min_post_slippage_edge: float = 0.05
    min_fill_usd: float = 1.0
    entry_intent_ttl_seconds: float = 180.0
    retry_cooldown_seconds: float = 30.0
    max_attempts: int = 6
    unknown_retry_grace_seconds: float = 30.0
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
        retry_attempts, retry_filled, retry_rejected, retry_delayed, retry_unknown = self.retry_pending_entries()
        if market_date is None:
            market_date = self.store.latest_research_market_date()
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
            elif state in {PaperPolicyFinalState.RESERVED, PaperPolicyFinalState.SUBMITTED}:
                pass
            else:
                rejected += 1
        marked = self.mark_open_positions()
        settled = self.settle_resolved_positions()
        self._record_risk_snapshot()
        return PaperPolicyCycleResult(
            candidates=len(candidates),
            reserved=reserved,
            attempts=attempts + retry_attempts,
            filled=filled + retry_filled,
            rejected=rejected + retry_rejected,
            delayed=delayed + retry_delayed,
            unknown=unknown + retry_unknown,
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

    def retry_pending_entries(self) -> tuple[int, int, int, int, int]:
        attempts = filled = rejected = delayed = unknown = 0
        now = datetime.now(timezone.utc)
        for row in self.store.paper_policy_retryable_positions():
            if float(row["filled_shares"] or 0.0) > 0:
                continue
            raw_json = _load_raw(row)
            last_reason = str(raw_json.get("last_attempt_reason") or "")
            state = str(row["state"])
            if state in {str(PaperPolicyFinalState.RESERVED), str(PaperPolicyFinalState.SUBMITTED)} and last_reason != "INSUFFICIENT_DEPTH":
                continue
            attempt_seq = self.store.latest_paper_policy_attempt_seq(int(row["id"]))
            age = _age_seconds(str(row["timestamp"]), now)
            last_attempt_age = _age_seconds(str(raw_json.get("last_attempt_timestamp") or row["timestamp"]), now)
            if not self._can_retry_entry(age_seconds=age, attempt_seq=attempt_seq):
                self._expire_no_liquidity(row, age_seconds=age, attempt_seq=attempt_seq)
                rejected += 1
                continue
            if last_attempt_age < self.config.retry_cooldown_seconds:
                continue
            research_row = _research_row_from_position_raw(raw_json)
            if not research_row:
                continue
            position = self._position_from_row(row)
            self._event(
                int(row["id"]),
                int(row["research_policy_position_id"]),
                PaperPolicyEventType.ENTRY_RETRY,
                "retrying pending entry",
                {"attempt_seq": attempt_seq + 1, "age_seconds": age},
            )
            attempt = self._execute_attempt(
                int(row["id"]),
                research_row,
                position,
                attempt_seq=attempt_seq + 1,
            )
            applied_state = self._apply_attempt(int(row["id"]), int(row["research_policy_position_id"]), attempt, age_seconds=age)
            attempts += 1
            if applied_state in {PaperPolicyFinalState.FILLED, PaperPolicyFinalState.PARTIAL}:
                filled += 1
            elif applied_state == PaperPolicyFinalState.DELAYED:
                delayed += 1
            elif applied_state == PaperPolicyFinalState.UNKNOWN:
                unknown += 1
            elif self._is_terminal(applied_state, attempt.final_reason):
                rejected += 1
        return attempts, filled, rejected, delayed, unknown

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
            policy_name=str(row["policy_name"]),
            station=str(row["station"]),
            market_date=str(row["market_date"]),
            selected_bucket=row.get("selected_bucket"),
            selected_side=str(row["selected_side"]),
        ):
            self._event(None, int(row["id"]), PaperPolicyEventType.ENTRY_REJECTED, "duplicate exposure blocked", row)
            return None

        exposure = self.store.paper_policy_exposure_summary(policy_name=str(row["policy_name"]))
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

        attempt = self._execute_attempt(paper_position_id, row, position, attempt_seq=1)
        return self._apply_attempt(paper_position_id, int(row["id"]), attempt, age_seconds=0.0)

    def _execute_attempt(
        self,
        paper_position_id: int,
        row: dict,
        position: PaperPolicyPosition,
        *,
        attempt_seq: int,
    ) -> PaperPolicyOrderAttempt:
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
                attempt_seq=attempt_seq,
            )
        if self._roll(self.config.stale_book_probability) or _book_age_seconds(book) > self.config.max_book_age_seconds:
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.STALE_BOOK,
                "STALE_BOOK",
                raw_payload={"book_timestamp": book.timestamp, "max_book_age_seconds": self.config.max_book_age_seconds},
                attempt_seq=attempt_seq,
            )
        if self._roll(self.config.delayed_probability):
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.DELAYED,
                "DELAYED",
                external_status="DELAYED",
                attempt_seq=attempt_seq,
            )
        if self._roll(self.config.unknown_probability):
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.UNKNOWN,
                "UNKNOWN_ORDER_ID",
                external_status="UNKNOWN",
                external_order_id=None,
                attempt_seq=attempt_seq,
            )
        if self.config.order_mode == PaperPolicyOrderMode.FOK and self._roll(self.config.fok_miss_probability):
            return self._attempt(
                paper_position_id,
                row,
                position,
                PaperPolicyFinalState.FOK_NOT_FILLED,
                "FOK_MISS_AFTER_BOOK",
                attempt_seq=attempt_seq,
            )

        execution_price_cap = execution_price_cap_for_row(
            row,
            max_slippage_cents=self.config.max_slippage_cents,
            min_post_slippage_edge=self.config.min_post_slippage_edge,
        )
        fill = simulate_ladder_fill(
            book=book,
            limit_price=position.entry_limit_price,
            execution_price_cap=execution_price_cap,
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
            raw_payload={
                "book_timestamp": book.timestamp,
                "best_ask": book.best_ask,
                "best_bid": book.best_bid,
                "vwap_price": fill.avg_price,
                "execution_price_cap": execution_price_cap,
                "post_slippage_edge": _post_slippage_edge(row, fill.avg_price),
                "fillable_notional_usd": fill.cost_usd,
                "levels_consumed": fill.levels_consumed,
            },
            attempt_seq=attempt_seq,
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
        attempt_seq: int = 1,
    ) -> PaperPolicyOrderAttempt:
        return PaperPolicyOrderAttempt(
            timestamp=utc_now_iso(),
            paper_position_id=paper_position_id,
            research_policy_position_id=int(row["id"]),
            attempt_seq=attempt_seq,
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

    def _apply_attempt(
        self,
        paper_position_id: int,
        research_policy_position_id: int,
        attempt: PaperPolicyOrderAttempt,
        *,
        age_seconds: float,
    ) -> PaperPolicyFinalState:
        self.store.insert_paper_policy_order_attempt(attempt)
        state = attempt.final_state
        if attempt.final_reason == "INSUFFICIENT_DEPTH" and attempt.final_state == PaperPolicyFinalState.REJECTED:
            if self._can_retry_entry(age_seconds=age_seconds, attempt_seq=attempt.attempt_seq):
                state = PaperPolicyFinalState.RESERVED
            else:
                state = PaperPolicyFinalState.EXPIRED_NO_LIQUIDITY
        self.store.update_paper_policy_position_execution(
            paper_position_id,
            state=state,
            filled_shares=attempt.filled_shares,
            avg_entry_price=attempt.avg_price,
            cost_usd=attempt.cost_usd,
            raw_patch={
                "last_attempt_state": str(attempt.final_state),
                "last_attempt_reason": attempt.final_reason,
                "last_attempt_timestamp": attempt.timestamp,
                "last_attempt_seq": attempt.attempt_seq,
                "expired_age_seconds": age_seconds if state == PaperPolicyFinalState.EXPIRED_NO_LIQUIDITY else None,
            },
        )
        if attempt.final_state in {PaperPolicyFinalState.FILLED, PaperPolicyFinalState.PARTIAL}:
            event_type = PaperPolicyEventType.ENTRY_CONFIRMED
        elif state == PaperPolicyFinalState.RESERVED:
            event_type = PaperPolicyEventType.ENTRY_RETRY
        else:
            event_type = PaperPolicyEventType.ENTRY_REJECTED
        self._event(paper_position_id, research_policy_position_id, event_type, attempt.final_reason, attempt.raw_payload)
        return state

    def _can_retry_entry(self, *, age_seconds: float, attempt_seq: int) -> bool:
        return age_seconds < self.config.entry_intent_ttl_seconds and attempt_seq < self.config.max_attempts

    def _expire_no_liquidity(self, row: dict[str, Any], *, age_seconds: float, attempt_seq: int) -> None:
        paper_position_id = int(row["id"])
        self.store.update_paper_policy_position_execution(
            paper_position_id,
            state=PaperPolicyFinalState.EXPIRED_NO_LIQUIDITY,
            filled_shares=0.0,
            avg_entry_price=None,
            cost_usd=0.0,
            raw_patch={
                "last_attempt_state": str(PaperPolicyFinalState.EXPIRED_NO_LIQUIDITY),
                "last_attempt_reason": "EXPIRED_NO_LIQUIDITY",
                "expired_age_seconds": age_seconds,
                "expired_attempt_seq": attempt_seq,
            },
        )
        self._event(
            paper_position_id,
            int(row["research_policy_position_id"]),
            PaperPolicyEventType.ENTRY_REJECTED,
            "EXPIRED_NO_LIQUIDITY",
            {"age_seconds": age_seconds, "attempt_seq": attempt_seq},
        )

    def _position_from_row(self, row: dict[str, Any]) -> PaperPolicyPosition:
        return PaperPolicyPosition(
            timestamp=str(row["timestamp"]),
            research_policy_position_id=int(row["research_policy_position_id"]),
            policy_name=str(row["policy_name"]),
            station=str(row["station"]),
            market_date=datetime.fromisoformat(str(row["market_date"])).date(),
            selected_market_id=str(row["selected_market_id"]),
            selected_token_id=str(row["selected_token_id"]),
            selected_side=TradeAction(str(row["selected_side"])),
            selected_bucket=row.get("selected_bucket"),
            entry_limit_price=float(row["entry_limit_price"]),
            target_notional_usd=float(row["target_notional_usd"]),
            filled_shares=float(row["filled_shares"] or 0.0),
            avg_entry_price=row["avg_entry_price"],
            cost_usd=float(row["cost_usd"] or 0.0),
            state=PaperPolicyFinalState(str(row["state"])),
            raw_json=_load_raw(row),
        )

    def _is_terminal(self, final_state: PaperPolicyFinalState, final_reason: str) -> bool:
        if final_state == PaperPolicyFinalState.EXPIRED_NO_LIQUIDITY:
            return True
        return final_state not in {
            PaperPolicyFinalState.FILLED,
            PaperPolicyFinalState.PARTIAL,
            PaperPolicyFinalState.DELAYED,
            PaperPolicyFinalState.UNKNOWN,
        } and final_reason != "INSUFFICIENT_DEPTH"

    def _record_risk_snapshot(self) -> None:
        exposure = self.store.paper_policy_exposure_summary()
        by_policy = {
            policy_name: self.store.paper_policy_exposure_summary(policy_name=policy_name)
            for policy_name in self.config.promoted_policies
        }
        self.store.insert_paper_policy_risk_snapshot(
            PaperPolicyRiskSnapshot(
                timestamp=utc_now_iso(),
                bankroll_usd=self.config.risk.bankroll_usd,
                open_positions=int(exposure["open_positions"]),
                open_risk_usd=float(exposure["open_risk_usd"]),
                station_date_exposure_usd=dict(exposure["station_date_exposure_usd"]),
                raw_payload={"risk_config": self.config.risk.__dict__, "by_policy": by_policy},
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
    execution_price_cap: float
    fillable_notional_usd: float


def simulate_ladder_fill(
    *,
    book: BookSnapshot,
    limit_price: float,
    target_notional_usd: float,
    order_mode: PaperPolicyOrderMode,
    min_fill_usd: float,
    execution_price_cap: float | None = None,
    force_partial: bool = False,
) -> LadderFill:
    limit_price = quantize_price(limit_price)
    execution_price_cap = quantize_price(execution_price_cap if execution_price_cap is not None else limit_price)
    target_notional_usd = quantize_usdc(target_notional_usd)
    if not book.asks:
        return LadderFill(PaperPolicyFinalState.REJECTED, "MISSING_ASKS", 0.0, None, 0.0, [], execution_price_cap, 0.0)
    walk = walk_ask_ladder(
        book=book,
        limit_price=limit_price,
        target_notional_usd=target_notional_usd,
        execution_price_cap=execution_price_cap,
        force_half_target=force_partial,
    )

    if walk.filled_shares <= 0 or walk.cost_usd < min_fill_usd:
        return LadderFill(
            PaperPolicyFinalState.REJECTED,
            "INSUFFICIENT_DEPTH",
            0.0,
            None,
            0.0,
            walk.levels_consumed,
            execution_price_cap,
            walk.cost_usd,
        )
    if order_mode == PaperPolicyOrderMode.FOK and walk.remaining_shares > 0:
        return LadderFill(
            PaperPolicyFinalState.FOK_NOT_FILLED,
            "FOK_NOT_FILLED",
            0.0,
            None,
            0.0,
            walk.levels_consumed,
            execution_price_cap,
            walk.cost_usd,
        )
    state = PaperPolicyFinalState.FILLED if walk.remaining_shares <= 0 else PaperPolicyFinalState.PARTIAL
    return LadderFill(
        state,
        str(state),
        walk.filled_shares,
        walk.avg_price,
        walk.cost_usd,
        walk.levels_consumed,
        execution_price_cap,
        walk.cost_usd,
    )


def execution_price_cap_for_row(
    row: dict[str, Any],
    *,
    max_slippage_cents: float,
    min_post_slippage_edge: float,
) -> float:
    entry_price = quantize_price(float(row["entry_price"]))
    slippage_price_cap = entry_price + max_slippage_cents
    fair = row.get("entry_fair")
    if fair is None:
        return quantize_price(min(slippage_price_cap, 1.0))
    edge_price_cap = float(fair) - min_post_slippage_edge
    return quantize_price(min(edge_price_cap, slippage_price_cap, 1.0))


def _post_slippage_edge(row: dict[str, Any], avg_price: float | None) -> float | None:
    if avg_price is None or row.get("entry_fair") is None:
        return None
    return round(float(row["entry_fair"]) - avg_price + 1e-12, 4)


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


def _load_raw(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("raw_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _research_row_from_position_raw(raw_json: dict[str, Any]) -> dict[str, Any]:
    direct = raw_json.get("research_policy_position")
    if isinstance(direct, dict):
        return dict(direct)
    nested = raw_json.get("raw_json")
    if isinstance(nested, dict) and isinstance(nested.get("research_policy_position"), dict):
        return dict(nested["research_policy_position"])
    return {}
