from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import whole_chain_truth_report as report_mod
from scripts.whole_chain_truth_report import ChainStats, build_report, render_markdown


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
            winning_side text,
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
        """
    )
    strategy = "pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first"
    raw_block = json.dumps({"calibration": {"decision": "BLOCK", "reason": "bucket_match"}})
    conn.execute(
        """
        insert into live_policy_positions (
            timestamp, strategy_name, station, market_date, market_family, scope_key,
            selected_market_id, selected_token_id, selected_side, selected_bucket,
            obs_delay_bucket, entry_price, entry_fair, entry_edge, target_notional_usd,
            target_shares, filled_shares, avg_entry_price, cost_usd, state,
            source_prediction_snapshot_ids, raw_json, winning_side, realized_pnl
        ) values (
            '2026-06-08T12:00:00+00:00', ?, 'KATL', '2026-06-08', 'HIGH_TEMP', 'scope1',
            'm1', 't1', 'BUY_NO', '90-91F', '10m', 0.25, 0.75, 0.50, 100.0,
            400.0, 166.6667, 0.30, 50.0, 'SETTLED', '[1,2]', ?, 'BUY_NO', 70.0
        )
        """,
        (strategy, raw_block),
    )
    conn.execute(
        """
        insert into live_policy_positions (
            timestamp, strategy_name, station, market_date, market_family, scope_key,
            selected_market_id, selected_token_id, selected_side, selected_bucket,
            obs_delay_bucket, entry_price, entry_fair, entry_edge, target_notional_usd,
            target_shares, filled_shares, avg_entry_price, cost_usd, state,
            source_prediction_snapshot_ids, raw_json, winning_side, realized_pnl
        ) values (
            '2026-06-08T12:05:00+00:00', ?, 'KDAL', '2026-06-08', 'HIGH_TEMP', 'scope2',
            'm2', 't2', 'BUY_NO', '90-91F', '10m', 0.50, 0.60, 0.10, 40.0,
            80.0, 0.0, null, 0.0, 'REJECTED', '[]', '{}', null, null
        )
        """,
        (strategy,),
    )
    attempts = [
        (1, "CANCELLED", "RESTING_TTL_EXPIRED", 10.0, 0.0),
        (1, "REJECTED", "no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.", 20.0, 0.0),
        (2, "REJECTED", "INSUFFICIENT_DEPTH", 40.0, 0.0),
    ]
    for seq, (position_id, state, reason, target, cost) in enumerate(attempts, start=1):
        conn.execute(
            """
            insert into live_order_attempts (
                timestamp, live_position_id, attempt_seq, token_id, side, order_mode, limit_price,
                target_notional_usd, target_shares, final_state, final_reason, filled_shares,
                avg_price, cost_usd, raw_payload
            ) values ('2026-06-08T12:10:00+00:00', ?, ?, 't1', 'BUY', 'FAK', 0.25, ?, 100.0, ?, ?, 0.0, null, ?, '{}')
            """,
            (position_id, seq, target, state, reason, cost),
        )
    conn.commit()
    conn.close()


def _create_research_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table station_date_outcomes (
            station text not null,
            market_date text not null,
            timestamp text not null,
            final_high_tmpf real,
            source text not null,
            resolved_at text not null,
            raw_json text not null,
            final_low_tmpf real,
            primary key (station, market_date)
        );
        insert into station_date_outcomes values ('KATL', '2026-06-08', 'ts', 92.0, 'test', 'ts', '{}', null);
        insert into station_date_outcomes values ('KDAL', '2026-06-08', 'ts', 91.0, 'test', 'ts', '{}', null);
        """
    )
    conn.commit()
    conn.close()


def test_whole_chain_report_reconciles_selected_filled_slippage_and_capacity(tmp_path, monkeypatch) -> None:
    live_db = tmp_path / "live.sqlite"
    research_db = tmp_path / "research.sqlite"
    _create_live_db(live_db)
    _create_research_db(research_db)

    def fake_raw_replay(*args, **kwargs):
        return {"live_consensus_no_tiny": ChainStats(candidates=2, resolved=2, wins=1, risk_usd=100.0, pnl_usd=20.0)}, {"source": "test"}

    monkeypatch.setattr(report_mod, "build_raw_replay", fake_raw_replay)

    report = build_report(live_db, research_db, start_date="2026-06-08", end_date="2026-06-08")
    sleeve = next(row for row in report["sleeves"] if row["name"] == "live_consensus_no_tiny")

    assert sleeve["raw_snapshot_replay"]["pnl_usd"] == pytest.approx(20.0)
    assert sleeve["live_selected_replay"]["risk_usd"] == pytest.approx(140.0)
    assert sleeve["live_selected_replay"]["pnl_usd"] == pytest.approx(260.0)
    assert sleeve["filled_entry_replay"]["pnl_usd"] == pytest.approx(150.0)
    assert sleeve["filled_actual_price_replay"]["pnl_usd"] == pytest.approx(116.6666667)
    assert sleeve["actual_live_pnl"]["pnl_usd"] == pytest.approx(70.0)
    assert sleeve["unfilled_selected_replay"]["pnl_usd"] == pytest.approx(-40.0)
    assert sleeve["slippage"]["avg_cents"] == pytest.approx(5.0)
    assert sleeve["capacity_loss"]["insufficient_depth_usd"] == pytest.approx(40.0)
    assert sleeve["capacity_loss"]["fak_miss_usd"] == pytest.approx(20.0)
    assert sleeve["capacity_loss"]["ttl_expired_usd"] == pytest.approx(10.0)
    assert sleeve["calibration"]["would_block"] == 1
    assert "Whole-Chain Truth Report" in render_markdown(report)
