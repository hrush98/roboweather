from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from weather_trader.stations.metadata import Station


SNAPSHOT_HOURS = [10, 11, 12, 13, 14, 15]
THRESHOLD_OFFSETS = [-4, -3, -2, -1, 0, 1, 2, 3, 4]


@dataclass(frozen=True)
class SnapshotConfig:
    hours_local: tuple[int, ...] = tuple(SNAPSHOT_HOURS)
    threshold_offsets: tuple[int, ...] = tuple(THRESHOLD_OFFSETS)


def prepare_station_observations(observations: pd.DataFrame, station: Station) -> pd.DataFrame:
    frame = observations.copy()
    zone = ZoneInfo(station.timezone)
    frame["station"] = station.station
    frame["valid_local"] = frame["valid"].dt.tz_convert(zone)
    frame["local_date"] = frame["valid_local"].dt.date
    frame["hour_local"] = frame["valid_local"].dt.hour
    frame["minute_local"] = frame["valid_local"].dt.minute
    frame["doy"] = frame["valid_local"].dt.dayofyear
    frame["temp_change_1h"] = frame["tmpf"] - frame["tmpf"].shift(12)
    frame["temp_change_3h"] = frame["tmpf"] - frame["tmpf"].shift(36)
    frame["wind_dir_sin"] = np.sin(np.deg2rad(frame["drct"]))
    frame["wind_dir_cos"] = np.cos(np.deg2rad(frame["drct"]))
    frame["cloud_cover_code"] = frame.apply(_cloud_cover_code, axis=1)
    return frame


def build_daily_station_table(observations: pd.DataFrame) -> pd.DataFrame:
    grouped = observations.groupby(["station", "local_date"], dropna=False)
    daily = grouped.agg(
        final_high_tmpf=("tmpf", "max"),
        first_valid=("valid", "min"),
        last_valid=("valid", "max"),
        obs_count=("valid", "count"),
    )
    return daily.reset_index()


def build_synthetic_threshold_examples(
    observations: pd.DataFrame,
    station: Station,
    config: SnapshotConfig | None = None,
) -> pd.DataFrame:
    config = config or SnapshotConfig()
    prepared = prepare_station_observations(observations=observations, station=station)
    daily = build_daily_station_table(prepared)
    daily_map = {
        (row.station, row.local_date): row.final_high_tmpf
        for row in daily.itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    for local_date, day_frame in prepared.groupby("local_date", sort=True):
        final_high = daily_map.get((station.station, local_date))
        if pd.isna(final_high):
            continue
        for hour in config.hours_local:
            snapshot = _select_snapshot(day_frame, hour)
            if snapshot is None or pd.isna(snapshot.tmpf):
                continue
            max_so_far = day_frame.loc[day_frame["valid_local"] <= snapshot.valid_local, "tmpf"].max()
            for offset in config.threshold_offsets:
                threshold = round(float(final_high) + offset)
                rows.append(
                    _make_feature_row(
                        station=station,
                        snapshot=snapshot,
                        local_date=local_date,
                        threshold=threshold,
                        max_so_far=max_so_far,
                        final_high=final_high,
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


def _make_feature_row(
    station: Station,
    snapshot: pd.Series,
    local_date,
    threshold: float,
    max_so_far: float,
    final_high: float,
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
        "max_temp_so_far": float(max_so_far),
        "threshold": float(threshold),
        "threshold_minus_current_temp": float(threshold - current_temp),
        "threshold_minus_max_so_far": float(threshold - max_so_far),
        "temp_change_1h": snapshot.temp_change_1h,
        "temp_change_3h": snapshot.temp_change_3h,
        "dewpoint": dewpoint,
        "wind_speed": snapshot.sknt,
        "wind_dir_sin": snapshot.wind_dir_sin,
        "wind_dir_cos": snapshot.wind_dir_cos,
        "cloud_cover_code": snapshot.cloud_cover_code,
        "final_high_tmpf": float(final_high),
        "target": int(final_high >= threshold),
    }


def _cloud_cover_code(row: pd.Series) -> int:
    codes = [row.get("skyc1"), row.get("skyc2"), row.get("skyc3")]
    cleaned = [str(code).strip().upper() for code in codes if pd.notna(code)]
    if not cleaned:
        return -1
    if any(code in {"OVC", "VV"} for code in cleaned):
        return 3
    if any(code == "BKN" for code in cleaned):
        return 2
    if any(code in {"SCT", "FEW"} for code in cleaned):
        return 1
    return 0
