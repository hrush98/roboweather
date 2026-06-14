from __future__ import annotations

import pytest

from scripts.portfolio_promotion_report import (
    ReplaySpec,
    RiskCaps,
    build_portfolio_report,
    default_replay_specs,
    select_spec_rows,
)


def _row(
    row_id: int,
    *,
    source: str = "consensus:obs_bucket_consensus",
    model: str = "unused",
    station: str = "KATL",
    date: str = "2026-06-07",
    family: str = "HIGH_TEMP",
    bucket: str = "82-83F",
    side: str = "BUY_NO",
    entry: float = 0.25,
    pnl: float = 0.75,
    edge: float = 0.40,
    strategy: str = "HIGH_CONVICTION",
    local: str = "2026-06-07T13:00:00-04:00",
    obs: str = "10m",
    fill25: float = 25.0,
    fill50: float = 50.0,
    fill100: float = 100.0,
) -> dict:
    return {
        "id": row_id,
        "timestamp": f"{date}T17:{row_id:02d}:00+00:00",
        "station": station,
        "market_date": date,
        "market_family": family,
        "decision_time_local": local,
        "obs_delay_bucket": obs,
        "strategy_bucket": strategy,
        "selected_side": side,
        "selected_bucket": bucket,
        "selected_market_id": f"m-{station}-{date}-{bucket}",
        "selected_edge": edge,
        "selected_fair_no": entry + edge if side == "BUY_NO" else 1.0 - entry - edge,
        "selected_fair_yes": entry + edge if side == "BUY_YES" else 1.0 - entry - edge,
        "selected_no_ask": entry if side == "BUY_NO" else 1.0 - entry,
        "selected_yes_ask": entry if side == "BUY_YES" else 1.0 - entry,
        "selected_sweep_fillable_25_usd": fill25,
        "selected_sweep_fillable_50_usd": fill50,
        "selected_sweep_fillable_100_usd": fill100,
        "paper_pnl": pnl,
        "source": source,
        "model_name": model,
    }


def test_portfolio_replay_applies_exact_bucket_side_cap_incrementally() -> None:
    core = ReplaySpec(
        "core",
        "Core",
        "consensus",
        "obs_bucket_consensus",
        target_notional_usd=100.0,
        entry_price_max=0.50,
    )
    addon = ReplaySpec(
        "addon",
        "Add-on",
        "model",
        "model-a",
        target_notional_usd=50.0,
        entry_price_max=0.50,
    )
    rows = [
        _row(1, source="consensus:obs_bucket_consensus", entry=0.25, pnl=0.75),
        _row(2, source="model-a", model="model-a", entry=0.25, pnl=0.75),
    ]

    report = build_portfolio_report(rows, [core, addon], caps=RiskCaps(exact_bucket_side_usd=100.0), use_depth=True)

    assert report[0].stats.risk_usd == pytest.approx(100.0)
    assert report[0].stats.pnl_usd == pytest.approx(300.0)
    assert report[1].stats.risk_usd == pytest.approx(0.0)
    assert report[1].stats.skipped_capacity_rows == 1


def test_portfolio_replay_scales_pnl_by_filled_notional_and_depth() -> None:
    spec = ReplaySpec("thin", "Thin", "model", "model-a", target_notional_usd=100.0, entry_price_max=0.50)
    rows = [_row(1, source="model-a", model="model-a", entry=0.25, pnl=0.75, fill100=30.0)]

    report = build_portfolio_report(rows, [spec], caps=RiskCaps(), use_depth=True)

    assert report[0].stats.risk_usd == pytest.approx(30.0)
    assert report[0].stats.pnl_usd == pytest.approx(90.0)
    assert report[0].stats.rr == pytest.approx(3.0)


def test_hrrr_disagreement_candidate_requires_weak_or_absent_obs_core() -> None:
    hrrr_spec = [spec for spec in default_replay_specs() if spec.name == "hrrr_dynamic_tuned_inland_late_disagreement"][0]
    strong_obs = _row(
        1,
        source="consensus:obs_bucket_consensus",
        station="KATL",
        edge=0.20,
        entry=0.25,
        pnl=0.75,
    )
    hrrr_same = _row(
        2,
        source="dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025",
        model="dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025",
        station="KATL",
        edge=0.45,
        entry=0.25,
        pnl=0.75,
    )
    weak_obs = {**strong_obs, "selected_edge": 0.02, "selected_fair_no": 0.35}

    assert select_spec_rows([strong_obs, hrrr_same], hrrr_spec, {"obs_by_key": {}})

    selected_with_strong_obs = build_portfolio_report([strong_obs, hrrr_same], [hrrr_spec], use_depth=False)[0].stats
    assert selected_with_strong_obs.candidate_rows == 0

    selected_with_weak_obs = build_portfolio_report([weak_obs, hrrr_same], [hrrr_spec], use_depth=False)[0].stats
    assert selected_with_weak_obs.candidate_rows == 1
