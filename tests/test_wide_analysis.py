from weather_trader.discovery.wide_analysis import WideRow, WideSearchConfig, enumerate_behaviors, behavior_from_rule


def _row(index: int, *, side: str = "BUY_YES", ask: float = .2) -> WideRow:
    summaries = {f"cap={cap:.8f}|target={target:.8f}": {"cost_usd": target, "shares": target / ask, "vwap": ask} for cap in (.35, .5) for target in (25., 50., 100.)}
    return WideRow(f"m{index}", index, f"2026-01-{index:02d}T12:00:00Z", "KATL", f"2026-01-{index:02d}", "model", "HIGH_TEMP", side, "HIGH_CONVICTION", True, "10m", "D0_LATE", "13:00", "80-81F", 4., ask, .01, .2, 100., 1, summaries)


def test_behavior_normalization_is_deterministic_and_replayable():
    rows = [_row(i) for i in range(1, 24)]
    config = WideSearchConfig("2026-01-01", "2026-02-01", minimum_discovery_trades=20)
    first, diagnostics = enumerate_behaviors(rows, config=config)
    second, _ = enumerate_behaviors(list(rows), config=config)
    assert diagnostics["theoretical_rules"] > diagnostics["unique_behaviors"]
    assert [(x.mask, x.rule) for x in first] == [(x.mask, x.rule) for x in second]
    for behavior in first:
        replayed = behavior_from_rule(behavior.rule, rows)
        assert replayed.mask == behavior.mask


def test_behavior_grid_includes_non_high_conviction_and_both_sides():
    rows = [_row(i, side="BUY_NO" if i % 2 else "BUY_YES") for i in range(1, 45)]
    rows = [row if i % 3 else WideRow(**{**row.__dict__, "high_conviction": False, "strategy_bucket": "BEST_BUCKET"}) for i, row in enumerate(rows)]
    config = WideSearchConfig("2026-01-01", "2026-03-01", minimum_discovery_trades=10)
    behaviors, _ = enumerate_behaviors(rows, config=config)
    assert any(x.rule["conviction"] == "LOW" for x in behaviors)
    assert any(x.rule["selected_side"] == "BUY_NO" for x in behaviors)


def test_holdout_labels_do_not_change_frozen_representatives():
    from weather_trader.discovery.wide_analysis import run_wide_search

    discovery = [_row(i) for i in range(1, 7)]
    winning = [_row(7), _row(8)]
    losing = [WideRow(**{**row.__dict__, "label": 0}) for row in winning]
    config = WideSearchConfig(
        "2026-01-01", "2026-01-09", holdout_dates=2, fold_count=3,
        minimum_discovery_dates=3, minimum_discovery_trades=3,
        minimum_holdout_dates=2, minimum_holdout_trades=2,
        bootstrap_repetitions=20, workers=1,
    )
    diagnostics = {"pending_decisions": 0, "eligible_rows": 8, "row_set_hash": "test"}
    won = run_wide_search(discovery + winning, config=config, cache_diagnostics=diagnostics, sealed_manifest={})
    lost = run_wide_search(discovery + losing, config=config, cache_diagnostics=diagnostics, sealed_manifest={})
    assert won["grid"]["representative_freeze_hash"] == lost["grid"]["representative_freeze_hash"]
    assert [x["rule_id"] for x in won["family_representatives"]] == [x["rule_id"] for x in lost["family_representatives"]]
    assert won["status"] == "COMPLETED_WITH_EMERGED_STRATEGIES"
    assert lost["status"] == "COMPLETED_NO_EMERGED_STRATEGIES"
