from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from scripts.forecast_truth_audit import select_numeric_proxy

from weather_trader.forecasting.truth import (
    MarketBucket,
    TemperatureTruth,
    bucket_for_temperature,
    daily_maximum,
    local_day_bounds_utc,
    local_standard_day_bounds_utc,
    pairwise_mismatch_rows,
    parse_gamma_winner,
    parse_wunderground_daily_high,
    round_half_up,
    truth_compatible_with_bucket,
)


def test_rounding_and_bucket_mapping_use_reported_integer_fahrenheit() -> None:
    assert round_half_up(80.5) == 81
    assert round_half_up(-0.5) == -1
    buckets = [
        MarketBucket("low", None, 79.0, "<=79F"),
        MarketBucket("mid", 80.0, 81.0, "80-81F"),
        MarketBucket("high", 82.0, None, ">=82F"),
    ]
    assert bucket_for_temperature(80.49, buckets).market_id == "mid"
    assert bucket_for_temperature(81.5, buckets).market_id == "high"


def test_civil_and_cli_standard_days_diverge_during_dst() -> None:
    day = date(2026, 7, 4)
    civil_start, civil_end = local_day_bounds_utc(day, "America/New_York")
    standard_start, standard_end = local_standard_day_bounds_utc(day, "America/New_York")
    assert civil_start == datetime(2026, 7, 4, 4, tzinfo=timezone.utc)
    assert standard_start == datetime(2026, 7, 4, 5, tzinfo=timezone.utc)
    assert civil_end == datetime(2026, 7, 5, 4, tzinfo=timezone.utc)
    assert standard_end == datetime(2026, 7, 5, 5, tzinfo=timezone.utc)

    frame = pd.DataFrame(
        {
            "valid": ["2026-07-04T04:30:00Z", "2026-07-04T05:30:00Z"],
            "tmpf": [99, 80],
        }
    )
    assert daily_maximum(frame, market_date=day, timezone_name="America/New_York") == 99
    assert daily_maximum(
        frame,
        market_date=day,
        timezone_name="America/New_York",
        standard_day=True,
    ) == 80


def test_parse_wunderground_high_preserves_unit_and_marks_celsius_loss() -> None:
    fahrenheit = '<div class="high-low-item high"><div class="label">High</div><div class="value">90 °F</div></div>'
    assert parse_wunderground_daily_high(fahrenheit) == (
        90.0, 90.0, "F", "EXACT_DISPLAY_F", 90.0, 90.0
    )

    celsius = '<div class="high-low-item high"><div class="value">32Â°C</div></div>'
    normalized, source_value, unit, exactness, lower, upper = parse_wunderground_daily_high(celsius)
    assert (normalized, source_value, unit, exactness) == (
        90.0, 32.0, "C", "LOCALIZED_C_ROUNDED_TO_F_INTERVAL"
    )
    assert lower == 88.7
    assert upper == 90.5
    interval_truth = TemperatureTruth(
        "KATL", date(2026, 7, 4), "WU", normalized, "now",
        value_lower_f=lower, value_upper_f=upper,
    )
    assert truth_compatible_with_bucket(
        interval_truth, MarketBucket("winner", 88, 89, "88-89F")
    )


def test_gamma_winner_requires_one_fully_resolved_market() -> None:
    buckets = [
        MarketBucket("a", None, 79, "<=79F"),
        MarketBucket("b", 80, None, ">=80F"),
    ]
    payload = {
        "resolutionSource": "Weather Underground",
        "markets": [
            {"id": "a", "outcomePrices": '["0", "1"]'},
            {"id": "b", "outcomePrices": '["1", "0"]'},
        ],
    }
    result = parse_gamma_winner(
        payload,
        station="KATL",
        market_date=date(2026, 7, 4),
        event_slug="event",
        buckets=buckets,
        captured_at_utc="now",
    )
    assert result.error is None
    assert result.winning_market_id == "b"

    payload["markets"][0]["outcomePrices"] = '["0.5", "0.5"]'
    failed = parse_gamma_winner(
        payload,
        station="KATL",
        market_date=date(2026, 7, 4),
        event_slug="event",
        buckets=buckets,
        captured_at_utc="now",
    )
    assert failed.winning_bucket is None
    assert "fully resolved" in failed.error


def test_pairwise_report_counts_temperature_and_venue_bucket_disagreements() -> None:
    day1, day2 = date(2026, 7, 4), date(2026, 7, 5)
    truths = [
        TemperatureTruth("KATL", day1, "CLI", 80, "now"),
        TemperatureTruth("KATL", day1, "METAR", 81, "now"),
        TemperatureTruth("KATL", day2, "CLI", 82, "now"),
        TemperatureTruth("KATL", day2, "METAR", 82, "now"),
    ]
    bucket = MarketBucket("winner", 80, 81, "80-81F")
    venue = [
        type("Settlement", (), {"station": "KATL", "market_date": day1, "winning_bucket": bucket})(),
        type("Settlement", (), {"station": "KATL", "market_date": day2, "winning_bucket": bucket})(),
    ]
    row = pairwise_mismatch_rows(truths, venue=venue)[0]
    assert row["dates_compared"] == 2
    assert row["exact_matches"] == 1
    assert row["one_degree_or_more"] == 1
    assert row["pair_bucket_classification_disagreements"] == 0
    assert row["left_venue_bucket_mismatches"] == 1
    assert row["right_venue_bucket_mismatches"] == 1


def test_numeric_proxy_selection_excludes_mutable_display_source() -> None:
    mismatches = {
        "IEM_ROUTINE_SPECIAL_METAR": 0,
        "NWS_CLI": 4,
        "NCEI_ASOS_1MIN": 8,
        "WUNDERGROUND_DISPLAY": 0,
    }
    comparable = {source: 100 for source in mismatches}
    assert select_numeric_proxy(mismatches, comparable) == "IEM_ROUTINE_SPECIAL_METAR"
