from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from scripts.trading_retrospective_report import Thresholds, build_report, default_period, promotion_status


def _create_live_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table live_policy_positions (
            id integer primary key autoincrement,
            timestamp text not null,
            strategy_name text not null,
            station text not null,
            market_date text not null,
            market_family text not null,
            scope_key text not null,
            selected_market_id text not null,
            selected_token_id text not null,
            selected_side text not null,
            selected_bucket text,
            obs_delay_bucket text not null,
            entry_price real not null,
            entry_fair real,
            entry_edge real,
            target_notional_usd real not null,
            target_shares real not null,
            filled_shares real not null default 0,
            avg_entry_price real,
            cost_usd real not null default 0,
            state text not null,
            source_prediction_snapshot_ids text not null,
            raw_json text not null,
            realized_pnl real
        );
        create table live_order_attempts (
            id integer primary key autoincrement,
            timestamp text not null,
            live_position_id integer not null,
            attempt_seq integer not null,
            token_id text not null,
            side text not null,
            order_mode text not null,
            limit_price real not null,
            target_notional_usd real not null,
            target_shares real not null,
            final_state text not null,
            final_reason text not null,
            filled_shares real not null,
            avg_price real,
            cost_usd real not null,
            raw_payload text not null
        );
        create table live_trade_events (
            id integer primary key autoincrement,
            timestamp text not null,
            live_position_id integer,
            strategy_name text,
            event_type text not null,
            message text not null,
            raw_payload text not null
        );
        """
    )
    conn.execute(
        """
        insert into live_policy_positions (
            timestamp, strategy_name, station, market_date, market_family, scope_key,
            selected_market_id, selected_token_id, selected_side, selected_bucket,
            obs_delay_bucket, entry_price, entry_fair, entry_edge, target_notional_usd,
            target_shares, filled_shares, avg_entry_price, cost_usd, state,
            source_prediction_snapshot_ids, raw_json, realized_pnl
        ) values (?, ?, 'KATL', '2026-06-08', 'HIGH_TEMP', 'scope',
                  'm1', 't1', 'BUY_NO', '90-91F', '10m', 0.25, 0.35, 0.10,
                  100.0, 400.0, 200.0, 0.25, 50.0, 'RESOLVED', '[]', '{}', 75.0)
        """,
        ("2026-06-08T12:00:00+00:00", "policy_a"),
    )
    conn.execute(
        """
        insert into live_policy_positions (
            timestamp, strategy_name, station, market_date, market_family, scope_key,
            selected_market_id, selected_token_id, selected_side, selected_bucket,
            obs_delay_bucket, entry_price, entry_fair, entry_edge, target_notional_usd,
            target_shares, filled_shares, avg_entry_price, cost_usd, state,
            source_prediction_snapshot_ids, raw_json, realized_pnl
        ) values (?, ?, 'KDAL', '2026-06-08', 'HIGH_TEMP', 'scope2',
                  'm2', 't2', 'BUY_NO', '91-92F', '10m', 0.50, 0.60, 0.10,
                  50.0, 100.0, 0.0, null, 0.0, 'REJECTED', '[]', '{}', null)
        """,
        ("2026-06-08T13:00:00+00:00", "policy_a"),
    )
    attempts = [
        ("2026-06-08T12:00:01+00:00", 1, 1, "FAK", 0.25, 100.0, "REJECTED", "no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.", 0.0),
        ("2026-06-08T12:00:02+00:00", 1, 2, "GTC", 0.24, 50.0, "CANCELLED", "RESTING_TTL_EXPIRED", 0.0),
        ("2026-06-08T12:00:03+00:00", 1, 3, "GTC", 0.23, 50.0, "SUBMITTED", "partial", 25.0),
        ("2026-06-08T13:00:01+00:00", 2, 1, "FAK", 0.31, 50.0, "REJECTED", "price 0.3100002356001791 breaks minimum tick size rule 0.01", 0.0),
    ]
    for ts, position_id, seq, mode, price, notional, state, reason, cost in attempts:
        conn.execute(
            """
            insert into live_order_attempts (
                timestamp, live_position_id, attempt_seq, token_id, side, order_mode,
                limit_price, target_notional_usd, target_shares, final_state,
                final_reason, filled_shares, avg_price, cost_usd, raw_payload
            ) values (?, ?, ?, 't1', 'BUY', ?, ?, ?, 100.0, ?, ?, 0.0, null, ?, '{}')
            """,
            (ts, position_id, seq, mode, price, notional, state, reason, cost),
        )
    conn.execute(
        """
        insert into live_trade_events (
            timestamp, live_position_id, strategy_name, event_type, message, raw_payload
        ) values (?, 1, 'policy_a', 'ORDER_REJECTED', 'rejected', '{}')
        """,
        ("2026-06-08T12:00:02+00:00",),
    )
    conn.commit()
    conn.close()


def test_default_period_uses_previous_seven_days_ending_yesterday() -> None:
    assert default_period(date(2026, 6, 15)) == ("2026-06-08", "2026-06-14")


def test_live_retrospective_aggregates_ev_execution_categories_and_flags(tmp_path) -> None:
    db = tmp_path / "live.sqlite"
    _create_live_db(db)

    report = build_report(
        db,
        research_db=None,
        start_date="2026-06-08",
        end_date="2026-06-08",
        thresholds=Thresholds(min_resolved_positions=1),
    )

    assert report["totals"]["positions"] == 2
    assert report["totals"]["intended_notional_usd"] == pytest.approx(150.0)
    assert report["totals"]["filled_notional_usd"] == pytest.approx(50.0)
    assert report["totals"]["live_expected_ev_usd"] == pytest.approx(50.0)
    assert report["totals"]["live_filled_expected_ev_usd"] == pytest.approx(20.0)
    assert report["totals"]["missed_expected_ev_usd"] == pytest.approx(30.0)
    assert report["totals"]["live_realized_pnl_usd"] == pytest.approx(75.0)
    assert report["totals"]["reject_rate"] == pytest.approx(0.5)
    assert report["totals"]["terminal_reject_rate"] == pytest.approx(0.0)
    assert report["totals"]["child_fak_miss_then_resting_attempts"] == 1
    assert report["totals"]["resting_ttl_expired_attempts"] == 1
    assert report["totals"]["order_construction_error_attempts"] == 1
    assert report["totals"]["partial_fill_attempts"] == 1
    categories = {row["category"] for row in report["execution_categories"]}
    assert {"child_fak_miss_then_resting", "resting_ttl_expired", "partial_fill", "order_construction_error"}.issubset(categories)
    assert report["policies"][0]["flags"] == ["REVIEW_FILL_RATE", "REVIEW_ORDER_CONSTRUCTION_ERRORS"]


def test_timestamp_filter_limits_live_ledger_rows(tmp_path) -> None:
    db = tmp_path / "live.sqlite"
    _create_live_db(db)

    report = build_report(
        db,
        research_db=None,
        start_date="2026-06-08",
        end_date="2026-06-08",
        thresholds=Thresholds(min_resolved_positions=1),
        start_timestamp="2026-06-08T12:30:00+00:00",
    )

    assert report["totals"]["positions"] == 1
    assert report["totals"]["intended_notional_usd"] == pytest.approx(50.0)
    assert report["totals"]["live_expected_ev_usd"] == pytest.approx(10.0)
    assert report["totals"]["order_construction_error_attempts"] == 1


def test_promotion_status_keeps_positive_low_sample_candidates_on_watch() -> None:
    thresholds = Thresholds(promotion_min_fills=20, promotion_min_rr=0.25)

    assert promotion_status("candidate", 10, 158.09, 0.674, thresholds)[0] == "WATCH_LOW_SAMPLE"
    assert promotion_status("candidate", 25, 200.0, 0.50, thresholds)[0] == "PROMOTE_REVIEW"
    assert promotion_status("candidate", 25, -10.0, -0.05, thresholds)[0] == "REJECT_REVIEW"
    assert promotion_status("live_current", 25, 200.0, 0.75, thresholds)[0] == "SIZE_UP_REVIEW"
