from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from weather_trader.features.build_same_day_features import prepare_station_observations
from weather_trader.stations.metadata import Station


LOW_SNAPSHOT_HOURS = [2, 3, 4, 5, 6, 7, 8, 9]
LOW_THRESHOLD_OFFSETS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]


@dataclass(frozen=True)
class LowSnapshotConfig:
    hours_local: tuple[int, ...] = tuple(LOW_SNAPSHOT_HOURS)
    threshold_offsets: tuple[int, ...] = tuple(LOW_THRESHOLD_OFFSETS)


def build_daily_low_station_table(observations: pd.DataFrame) -> pd.DataFrame:
    grouped = observations.groupby(["station", "local_date"], dropna=False)
    daily = grouped.agg(
        final_low_tmpf=("tmpf", "min"),
        first_valid=("valid", "min"),
        last_valid=("valid", "max"),
        obs_count=("valid", "count"),
    )
    return daily.reset_index()


def build_low_temp_threshold_examples(
    observations: pd.DataFrame,
    station: Station,
    config: LowSnapshotConfig | None = None,
) -> pd.DataFrame:
    config = config or LowSnapshotConfig()
    prepared = prepare_station_observations(observations=observations, station=station)
    daily = build_daily_low_station_table(prepared)
    daily_map = {
        (row.station, row.local_date): row.final_low_tmpf
        for row in daily.itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    for local_date, day_frame in prepared.groupby("local_date", sort=True):
        final_low = daily_map.get((station.station, local_date))
        if pd.isna(final_low):
            continue
        for hour in config.hours_local:
            snapshot = _select_snapshot(day_frame, hour)
            if snapshot is None or pd.isna(snapshot.tmpf):
                continue
            min_so_far = day_frame.loc[day_frame["valid_local"] <= snapshot.valid_local, "tmpf"].min()
            for offset in config.threshold_offsets:
                threshold = round(float(final_low) + offset)
                rows.append(
                    _make_low_feature_row(
                        station=station,
                        snapshot=snapshot,
                        local_date=local_date,
                        threshold=threshold,
                        min_so_far=min_so_far,
                        final_low=final_low,
                    )
                )
    return pd.DataFrame(rows)


def _select_snapshot(day_frame: pd.DataFrame, hour_local: int):
    first_local = day_frame["valid_local"].iloc[0]
    cutoff_time = pd.Timestamp(day_frame["local_date"].iloc[0]).tz_localize(first_local.tz) + pd.Timedelta(hours=hour_local)
    cutoff = day_frame.loc[(day_frame["valid_local"] <= cutoff_time) & day_frame["tmpf"].notna()]
    if cutoff.empty:
        return None
    return cutoff.iloc[-1]


def _make_low_feature_row(
    station: Station,
    snapshot: pd.Series,
    local_date,
    threshold: float,
    min_so_far: float,
    final_low: float,
) -> dict[str, object]:
    current_temp = float(snapshot.tmpf)
    dewpoint = float(snapshot.dwpf) if pd.notna(snapshot.dwpf) else np.nan
    return {
        "station": station.station,
        "city": station.city,
        "timezone": station.timezone,
        "local_date": local_date,
        "snapshot_time_local": snapshot.valid_local,
        "hour_local": int(snapshot.hour_local),
        "day_of_year": int(snapshot.doy),
        "current_temp": current_temp,
        "min_temp_so_far": float(min_so_far),
        "threshold": float(threshold),
        "threshold_minus_current_temp": float(threshold - current_temp),
        "threshold_minus_min_so_far": float(threshold - min_so_far),
        "current_temp_minus_threshold": float(current_temp - threshold),
        "min_so_far_minus_threshold": float(min_so_far - threshold),
        "temp_change_1h": snapshot.temp_change_1h,
        "temp_change_3h": snapshot.temp_change_3h,
        "dewpoint": dewpoint,
        "wind_speed": snapshot.sknt,
        "wind_dir_sin": snapshot.wind_dir_sin,
        "wind_dir_cos": snapshot.wind_dir_cos,
        "cloud_cover_code": snapshot.cloud_cover_code,
        "final_low_tmpf": float(final_low),
        "target": int(final_low <= threshold),
    }
