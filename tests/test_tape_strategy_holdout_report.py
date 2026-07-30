from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts.tape_strategy_holdout_report import (
    CoverageState,
    apply_event,
    freeze_portfolio,
    summarize,
    sweep_asks,
)


@dataclass
class Event:
    event_type: str
    raw_payload: dict
    coverage_state: CoverageState = CoverageState.VALID


def test_sweep_asks_respects_price_cap_and_target_cost() -> None:
    cost, shares, vwap = sweep_asks(
        {0.20: 50.0, 0.40: 100.0, 0.60: 100.0},
        price_cap=0.50,
        target_cost=25.0,
    )

    assert cost == pytest.approx(25.0)
    assert shares == pytest.approx(87.5)
    assert vwap == pytest.approx(25.0 / 87.5)


def test_apply_event_replaces_book_and_applies_absolute_delta() -> None:
    bids: dict[float, float] = {0.1: 1.0}
    asks: dict[float, float] = {0.9: 1.0}

    assert apply_event(
        bids,
        asks,
        Event(
            "book",
            {
                "bids": [{"price": "0.30", "size": "5"}],
                "asks": [{"price": "0.40", "size": "6"}],
            },
            CoverageState.RESYNCING,
        ),
    )
    assert bids == {0.3: 5.0}
    assert asks == {0.4: 6.0}

    assert apply_event(
        bids,
        asks,
        Event(
            "price_change",
            {"price_change": {"side": "SELL", "price": "0.40", "size": "0"}},
        ),
    )
    assert asks == {}


def test_apply_event_fails_closed_on_nonvalid_delta() -> None:
    assert not apply_event(
        {},
        {},
        Event(
            "price_change",
            {"side": "BUY", "price": "0.30", "size": "5"},
            CoverageState.GAPPED,
        ),
    )


def test_freeze_portfolio_deduplicates_in_sleeve_priority(monkeypatch) -> None:
    first = {"station": "KATL", "market_date": "2026-07-23", "id": 1}
    overlap = {"station": "KATL", "market_date": "2026-07-23", "id": 2}
    additive = {"station": "KDAL", "market_date": "2026-07-23", "id": 3}

    def fake_select(rows, spec):
        del rows
        return [first] if spec.name == "first" else [overlap, additive]

    monkeypatch.setattr(
        "scripts.tape_strategy_holdout_report.select_policy_rows", fake_select
    )

    @dataclass
    class Spec:
        name: str

    frozen, counts = freeze_portfolio([], [Spec("first"), Spec("second")])

    assert counts == {"first": 1, "second": 2}
    assert [(sleeve, row["id"]) for sleeve, row in frozen] == [
        ("first", 1),
        ("second", 3),
    ]


def test_summarize_reports_binary_payout_pnl() -> None:
    stats = summarize(
        [
            {
                "cost": 20.0,
                "pnl": 30.0,
                "won": True,
                "vwap": 0.4,
                "checkpoint_age_s": 2.0,
            },
            {
                "cost": 25.0,
                "pnl": -25.0,
                "won": False,
                "vwap": 0.5,
                "checkpoint_age_s": 4.0,
            },
        ]
    )

    assert stats == {
        "executions": 2,
        "cost": 45.0,
        "pnl": 5.0,
        "rr": 0.111,
        "wins": 1,
        "avg_vwap": 0.45,
        "avg_checkpoint_age_s": 3.0,
    }
