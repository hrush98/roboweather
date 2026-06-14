from __future__ import annotations

import pytest

from scripts.live_policy_promotion_report import (
    PolicySpec,
    PromotionThresholds,
    build_report,
    candidate_policy_specs,
    select_policy_rows,
)


def _row(*, ident: str, source: str = "model", date: str = "2026-06-07", obs: str = "10m", bucket: str = "80-81F", pnl: float = 0.6, entry: float = 0.4, side: str = "BUY_NO") -> dict:
    return {
        "id": len(date) + len(bucket),
        "timestamp": f"{date}T17:00:00+00:00",
        "station": "KATL",
        "market_date": date,
        "market_family": "HIGH_TEMP",
        "decision_time_local": f"{date}T13:00:00-04:00",
        "obs_delay_bucket": obs,
        "strategy_bucket": "HIGH_CONVICTION",
        "selected_side": side,
        "selected_bucket": bucket,
        "selected_market_id": f"m-{bucket}",
        "selected_edge": 0.5,
        "selected_no_ask": entry if side == "BUY_NO" else None,
        "selected_yes_ask": entry if side == "BUY_YES" else None,
        "selected_sweep_fillable_50_usd": 50.0,
        "selected_sweep_vwap_50": entry,
        "paper_pnl": pnl,
        "model_name": ident if source == "model" else "unused",
        "source": ident if source == "model" else f"consensus:{ident}",
    }


def test_select_policy_rows_deduplicates_by_live_opportunity_scope() -> None:
    spec = PolicySpec("p", "model", "model-a", "Model A", entry_price_min=0.0)
    rows = [
        _row(ident="model-a", date="2026-06-07", obs="10m", pnl=0.6),
        {**_row(ident="model-a", date="2026-06-07", obs="10m", pnl=-0.4), "timestamp": "2026-06-07T17:05:00+00:00", "id": 99},
        _row(ident="model-a", date="2026-06-07", obs="15m", pnl=0.6),
    ]

    selected = select_policy_rows(rows, spec)

    assert len(selected) == 2
    assert [row["obs_delay_bucket"] for row in selected] == ["10m", "15m"]
    assert selected[0]["paper_pnl"] == pytest.approx(0.6)


def test_build_report_deactivates_live_policy_with_bad_recent_replay() -> None:
    spec = PolicySpec("live_bad", "model", "model-a", "Live Bad", live_status="live_current", entry_price_min=0.0)
    rows = [_row(ident="model-a", date=f"2026-06-{day:02d}", bucket=f"{day}-{day+1}F", pnl=-0.4, entry=0.4) for day in range(1, 8)]

    report = build_report(rows, [spec], thresholds=PromotionThresholds(canary_min_resolved_30=3), last30_start="2026-05-09", last7_start="2026-06-01")

    assert report[0].decision == "DEACTIVATE"


def test_build_report_promotes_strong_candidate() -> None:
    spec = PolicySpec("candidate", "consensus", "group-a", "Candidate", live_status="candidate", entry_price_min=0.0)
    rows = [_row(ident="group-a", source="consensus", date=f"2026-06-{(day % 7) + 1:02d}", bucket=f"{day}-{day+1}F", pnl=0.8, entry=0.2) for day in range(30)]

    report = build_report(rows, [spec], last30_start="2026-05-09", last7_start="2026-06-01")

    assert report[0].decision == "PROMOTE"
    assert report[0].last30.resolved == 30


def test_candidate_policy_specs_include_current_live_stack() -> None:
    names = {spec.name for spec in candidate_policy_specs()}

    assert "live_old_dynamic_core" in names
    assert "live_consensus_no_tiny" in names
    assert "live_core_consensus_15m" in names
    assert "live_ngboost_buy_yes" in names
