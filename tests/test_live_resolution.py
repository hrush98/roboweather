from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from weather_trader.execution.contracts import LivePolicyPosition, LivePositionState, MarketFamily, MarketSnapshot, TradeAction, utc_now_iso
from weather_trader.execution.store import ExecutionStore
from weather_trader.live.execution import LIVE_POLICY_NAME
from weather_trader.live.resolution import LiveResolutionService, PolymarketResolution, normalize_polymarket_resolution


class FakeResolutionClient:
    def __init__(self, resolutions: dict[str, PolymarketResolution]) -> None:
        self.resolutions = resolutions
        self.calls: list[str] = []

    def fetch_resolution(self, *, market_id: str, condition_id: str | None, yes_token_id: str | None, no_token_id: str | None) -> PolymarketResolution:
        self.calls.append(market_id)
        return self.resolutions[market_id]


def test_live_resolution_settles_winning_token(tmp_path: Path) -> None:
    store = _store_with_filled_position(tmp_path, selected_token_id="no-token")
    client = FakeResolutionClient({"market-1": _resolved("no-token", "No")})

    summary = LiveResolutionService(store, client).resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc))

    assert summary.candidates == 1
    assert summary.resolved == 1
    row = store.connection.execute("select * from live_policy_positions").fetchone()
    assert row["state"] == "SETTLED"
    assert row["winning_token_id"] == "no-token"
    assert row["winning_side"] == "BUY_NO"
    assert row["settlement_value_usd"] == pytest.approx(7.5)
    assert row["realized_pnl"] == pytest.approx(4.5)
    assert row["realized_rr"] == pytest.approx(1.5)
    event = store.connection.execute("select event_type from live_trade_events where live_position_id = 1").fetchone()
    assert event["event_type"] == "RESOLVED"


def test_live_resolution_settles_losing_token(tmp_path: Path) -> None:
    store = _store_with_filled_position(tmp_path, selected_token_id="no-token")
    client = FakeResolutionClient({"market-1": _resolved("yes-token", "Yes")})

    LiveResolutionService(store, client).resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc))

    row = store.connection.execute("select * from live_policy_positions").fetchone()
    assert row["state"] == "SETTLED"
    assert row["settlement_value_usd"] == pytest.approx(0.0)
    assert row["realized_pnl"] == pytest.approx(-3.0)
    assert row["realized_rr"] == pytest.approx(-1.0)


def test_live_resolution_leaves_pending_market_unchanged(tmp_path: Path) -> None:
    store = _store_with_filled_position(tmp_path, selected_token_id="no-token")
    client = FakeResolutionClient({"market-1": PolymarketResolution(False, False, None, None, None, "POLYMARKET", {"closed": False})})

    summary = LiveResolutionService(store, client).resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc))

    assert summary.pending == 1
    row = store.connection.execute("select state, resolved_at from live_policy_positions").fetchone()
    assert row["state"] == "FILLED"
    assert row["resolved_at"] is None
    assert store.connection.execute("select count(*) c from live_trade_events").fetchone()["c"] == 0


def test_live_resolution_dry_run_does_not_mutate(tmp_path: Path) -> None:
    store = _store_with_filled_position(tmp_path, selected_token_id="no-token")
    client = FakeResolutionClient({"market-1": _resolved("no-token", "No")})

    summary = LiveResolutionService(store, client).resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc), dry_run=True)

    assert summary.dry_run is True
    assert summary.resolved == 1
    row = store.connection.execute("select state, resolved_at from live_policy_positions").fetchone()
    assert row["state"] == "FILLED"
    assert row["resolved_at"] is None


def test_live_resolution_is_idempotent(tmp_path: Path) -> None:
    store = _store_with_filled_position(tmp_path, selected_token_id="no-token")
    client = FakeResolutionClient({"market-1": _resolved("no-token", "No")})
    service = LiveResolutionService(store, client)

    first = service.resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc))
    second = service.resolve_due(as_of_utc=datetime(2026, 5, 22, tzinfo=timezone.utc))

    assert first.resolved == 1
    assert second.candidates == 0
    assert store.connection.execute("select count(*) c from live_trade_events where event_type = \"RESOLVED\"").fetchone()["c"] == 1


def test_normalize_resolution_uses_outcome_prices_when_closed() -> None:
    resolution = normalize_polymarket_resolution(
        {
            "closed": True,
            "outcomes": "[\"Yes\", \"No\"]",
            "outcomePrices": "[\"0\", \"1\"]",
            "clobTokenIds": "[\"yes-token\", \"no-token\"]",
            "closedTime": "2026-05-21T18:00:00Z",
        },
        market_id="market-1",
        condition_id="condition-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
    )

    assert resolution.resolved is True
    assert resolution.winning_token_id == "no-token"
    assert resolution.winning_outcome == "No"
    assert resolution.resolved_at == "2026-05-21T18:00:00Z"


def _resolved(token_id: str, outcome: str) -> PolymarketResolution:
    return PolymarketResolution(True, True, token_id, outcome, "2026-05-21T18:00:00Z", "POLYMARKET", {"closed": True, "tokens": [{"token_id": token_id, "winner": True}]})


def _store_with_filled_position(tmp_path: Path, *, selected_token_id: str) -> ExecutionStore:
    store = ExecutionStore(tmp_path / "live.sqlite")
    store.upsert_market(MarketSnapshot("market-1", "condition-1", "Highest temperature in Atlanta on May 20, 2026?", "highest-temperature-in-atlanta-on-may-20-2026", "Atlanta", "KATL", date(2026, 5, 20), 72.0, 73.0, "yes-token", "no-token", "2026-05-21T00:00:00Z", "Polymarket", utc_now_iso(), True, MarketFamily.HIGH_TEMP))
    position_id = store.insert_live_policy_position(
        LivePolicyPosition(
            timestamp=utc_now_iso(),
            strategy_name=LIVE_POLICY_NAME,
            station="KATL",
            market_date=date(2026, 5, 20),
            market_family=MarketFamily.HIGH_TEMP,
            scope_key="station_date_bucket_side_obs_delay:72-73F:BUY_NO:15m",
            selected_market_id="market-1",
            selected_token_id=selected_token_id,
            selected_side=TradeAction.BUY_NO,
            selected_bucket="72-73F",
            obs_delay_bucket="15m",
            entry_price=0.4,
            entry_fair=0.9,
            entry_edge=0.5,
            target_notional_usd=3.0,
            target_shares=7.5,
            state=LivePositionState.RESERVED,
            source_prediction_snapshot_ids=[1],
            raw_json={"limit_price": 0.4},
        )
    )
    assert position_id is not None
    store.update_live_policy_position_execution(position_id, state=str(LivePositionState.FILLED), filled_shares=7.5, avg_entry_price=0.4, cost_usd=3.0)
    return store
