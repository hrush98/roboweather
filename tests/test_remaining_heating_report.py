from copy import deepcopy

from scripts.forecast_remaining_heating_report import build_acceptance_checks


def _comparison(log_loss: float, rps: float, upper: float = -0.01) -> dict:
    return {
        "candidate_minus_reference": {"log_loss": log_loss, "rps": rps},
        "weather_date_clustered_95pct_ci": {
            "log_loss": [-0.2, upper],
            "rps": [-0.3, upper],
        },
    }


def _comparisons() -> dict:
    return {
        "historical_remaining_minus_hrrr": _comparison(-0.1, -0.2),
        "weather_stack_minus_hrrr_holdout": _comparison(-0.1, -0.2),
        "weather_stack_minus_hrrr_recent": _comparison(-0.05, -0.06),
        "market_stack_minus_market_holdout": _comparison(-0.02, -0.03, 0.04),
        "market_stack_minus_market_recent": _comparison(-0.01, -0.02, 0.05),
    }


def test_acceptance_requires_every_f3_gate() -> None:
    checks = build_acceptance_checks(
        _comparisons(),
        weather_weight=0.5,
        market_relative_weight=0.7,
        holdout_weather_dates=22,
        post_cutoff_rows=0,
        coherence_violations=0,
    )
    assert checks
    assert all(checks.values())


def test_acceptance_fails_when_weather_holdout_is_not_cluster_robust() -> None:
    comparisons = deepcopy(_comparisons())
    comparisons["weather_stack_minus_hrrr_holdout"][
        "weather_date_clustered_95pct_ci"
    ]["log_loss"][1] = 0.01
    checks = build_acceptance_checks(
        comparisons,
        weather_weight=0.5,
        market_relative_weight=0.7,
        holdout_weather_dates=22,
        post_cutoff_rows=0,
        coherence_violations=0,
    )
    assert not checks["weather_stack_log_loss_ci_below_zero"]
