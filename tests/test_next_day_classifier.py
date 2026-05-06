from __future__ import annotations

import pandas as pd

from weather_trader.models.next_day_classifier import build_next_day_threshold_dataset


def test_next_day_dataset_uses_next_calendar_day_and_prior_station_history() -> None:
    same_day = pd.DataFrame(
        [
            _row("KAAA", "2024-01-01", 50, 45),
            _row("KAAA", "2024-01-02", 52, 46),
            _row("KAAA", "2024-01-03", 54, 47),
            _row("KBBB", "2024-01-01", 80, 75),
            _row("KBBB", "2024-01-02", 82, 76),
        ]
    )
    dataset = build_next_day_threshold_dataset(same_day, threshold_offsets=[0])

    aaa_jan2 = dataset.loc[(dataset["station"] == "KAAA") & (dataset["local_date"] == pd.Timestamp("2024-01-02").date())].iloc[0]
    bbb_jan2 = dataset.loc[(dataset["station"] == "KBBB") & (dataset["local_date"] == pd.Timestamp("2024-01-02").date())].iloc[0]

    assert aaa_jan2["prediction_date"] == pd.Timestamp("2024-01-01").date()
    assert aaa_jan2["target_final_high_tmpf"] == 52
    assert pd.isna(aaa_jan2["prior_day_high"])
    assert bbb_jan2["target_final_high_tmpf"] == 82
    assert pd.isna(bbb_jan2["prior_day_high"])


def _row(station: str, local_date: str, final_high: float, current_temp: float) -> dict[str, object]:
    return {
        "station": station,
        "city": station,
        "timezone": "America/New_York",
        "local_date": local_date,
        "snapshot_time_local": f"{local_date} 19:51:00+00:00",
        "hour_local": 14,
        "day_of_year": pd.Timestamp(local_date).dayofyear,
        "current_temp": current_temp,
        "max_temp_so_far": current_temp,
        "threshold": final_high,
        "threshold_minus_current_temp": final_high - current_temp,
        "threshold_minus_max_so_far": final_high - current_temp,
        "temp_change_1h": 1.0,
        "temp_change_3h": 2.0,
        "dewpoint": current_temp - 10,
        "wind_speed": 5.0,
        "wind_dir_sin": 0.0,
        "wind_dir_cos": 1.0,
        "cloud_cover_code": 0,
        "final_high_tmpf": final_high,
        "target": 1,
    }
