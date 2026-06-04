from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from weather_trader.stations.metadata import Station


NOMADS_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"


@dataclass
class HRRRClient:
    timeout_seconds: int = 120
    max_forecast_hour: int = 18
    neighborhood_degrees: float = 0.6
    cycle_lag_hours: int = 1

    def fetch_remaining_day_features(
        self,
        station: Station,
        as_of_utc: datetime,
        cycle_utc: datetime | None = None,
    ) -> dict[str, float]:
        if as_of_utc.tzinfo is None:
            raise ValueError("as_of_utc must be timezone-aware")
        cycle = (cycle_utc or (as_of_utc - timedelta(hours=self.cycle_lag_hours))).replace(minute=0, second=0, microsecond=0)
        local_now = as_of_utc.astimezone(ZoneInfo(station.timezone))
        local_midnight = datetime.combine(local_now.date() + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo(station.timezone))
        remaining_hours = max(0, int(np.ceil((local_midnight.astimezone(ZoneInfo("UTC")) - as_of_utc).total_seconds() / 3600)))
        horizon = min(self.max_forecast_hour, remaining_hours)
        rows = []
        for forecast_hour in range(0, horizon + 1):
            valid_time = cycle + timedelta(hours=forecast_hour)
            if valid_time < as_of_utc:
                continue
            try:
                rows.append(self.fetch_point_forecast(station=station, cycle_utc=cycle, forecast_hour=forecast_hour))
            except requests.HTTPError:
                continue
        names = (
            "hrrr_current_temp",
            "hrrr_remaining_max",
            "hrrr_remaining_min",
            "hrrr_temp_next_3h_max",
            "hrrr_temp_next_3h_mean",
            "hrrr_temp_trend_next_3h",
            "hrrr_dewpoint_current",
            "hrrr_dewpoint_next_3h_mean",
            "hrrr_dewpoint_remaining_mean",
            "hrrr_rh_current",
            "hrrr_rh_next_3h_mean",
            "hrrr_rh_remaining_mean",
            "hrrr_wind_speed_current",
            "hrrr_wind_speed_next_3h_mean",
            "hrrr_wind_speed_remaining_max",
            "hrrr_gust_remaining_max",
            "hrrr_cloud_cover_current",
            "hrrr_cloud_cover_next_3h_mean",
            "hrrr_cloud_cover_remaining_mean",
            "hrrr_cloud_cover_remaining_max",
            "hrrr_shortwave_next_3h_mean",
            "hrrr_shortwave_remaining_max",
            "hrrr_forecast_hours_count",
        )
        if not rows:
            return {name: np.nan for name in names}
        frame = pd.DataFrame(rows).sort_values("forecast_hour")
        next_3h = frame.head(4)
        temp = _numeric_series(frame, "tmpf")
        next_temp = _numeric_series(next_3h, "tmpf")
        return {
            "hrrr_current_temp": _safe_float(temp.iloc[0]) if len(temp) else np.nan,
            "hrrr_remaining_max": _safe_float(temp.max()),
            "hrrr_remaining_min": _safe_float(temp.min()),
            "hrrr_temp_next_3h_max": _safe_float(next_temp.max()),
            "hrrr_temp_next_3h_mean": _safe_float(next_temp.mean()),
            "hrrr_temp_trend_next_3h": _safe_float(next_temp.dropna().iloc[-1] - next_temp.dropna().iloc[0]) if len(next_temp.dropna()) >= 2 else np.nan,
            "hrrr_dewpoint_current": _series_current(frame, "dwpf"),
            "hrrr_dewpoint_next_3h_mean": _series_next_3h_mean(next_3h, "dwpf"),
            "hrrr_dewpoint_remaining_mean": _series_mean(frame, "dwpf"),
            "hrrr_rh_current": _series_current(frame, "rh"),
            "hrrr_rh_next_3h_mean": _series_next_3h_mean(next_3h, "rh"),
            "hrrr_rh_remaining_mean": _series_mean(frame, "rh"),
            "hrrr_wind_speed_current": _series_current(frame, "wind_speed_mph"),
            "hrrr_wind_speed_next_3h_mean": _series_next_3h_mean(next_3h, "wind_speed_mph"),
            "hrrr_wind_speed_remaining_max": _series_max(frame, "wind_speed_mph"),
            "hrrr_gust_remaining_max": _series_max(frame, "gust_mph"),
            "hrrr_cloud_cover_current": _series_current(frame, "tcdc"),
            "hrrr_cloud_cover_next_3h_mean": _series_next_3h_mean(next_3h, "tcdc"),
            "hrrr_cloud_cover_remaining_mean": _series_mean(frame, "tcdc"),
            "hrrr_cloud_cover_remaining_max": _series_max(frame, "tcdc"),
            "hrrr_shortwave_next_3h_mean": _series_next_3h_mean(next_3h, "dswrf"),
            "hrrr_shortwave_remaining_max": _series_max(frame, "dswrf"),
            "hrrr_forecast_hours_count": float(len(frame)),
        }

    def fetch_point_forecast(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> dict[str, float]:
        payload = self._download_subset(station=station, cycle_utc=cycle_utc, forecast_hour=forecast_hour)
        import pygrib

        with NamedTemporaryFile(suffix=".grib2") as handle:
            handle.write(payload)
            handle.flush()
            messages = list(pygrib.open(handle.name))
        parsed = {"forecast_hour": forecast_hour}
        for message in messages:
            data, lats, lons = message.data()
            lat_idx, lon_idx = _nearest_index(lats, lons, station.latitude, station.longitude)
            value = float(data[lat_idx, lon_idx])
            name = message.shortName.lower()
            level = int(message.level)
            if name == "2t" or (name == "tmp" and level == 2):
                parsed["tmpf"] = _kelvin_to_f(value)
            elif name in {"2d", "dpt", "dewpoint"} or (name == "dpt" and level == 2):
                parsed["dwpf"] = _kelvin_to_f(value)
            elif name in {"2r", "r", "rh"}:
                parsed["rh"] = value
            elif name == "10u":
                parsed["u10"] = value
            elif name == "10v":
                parsed["v10"] = value
            elif name in {"gust", "gusts"}:
                parsed["gust_mph"] = value * 2.23694
            elif name in {"tcc", "tcdc"}:
                parsed["tcdc"] = value
            elif name in {"dswrf", "sdswrf"}:
                parsed["dswrf"] = value
        parsed["wind_speed_mph"] = float(np.hypot(parsed.get("u10", np.nan), parsed.get("v10", np.nan)) * 2.23694)
        return parsed

    def _download_subset(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> bytes:
        params = {
            "dir": f"/hrrr.{cycle_utc:%Y%m%d}/conus",
            "file": f"hrrr.t{cycle_utc:%H}z.wrfsfcf{forecast_hour:02d}.grib2",
            "var_TMP": "on",
            "var_DPT": "on",
            "var_RH": "on",
            "var_UGRD": "on",
            "var_VGRD": "on",
            "var_GUST": "on",
            "var_TCDC": "on",
            "var_DSWRF": "on",
            "lev_2_m_above_ground": "on",
            "lev_10_m_above_ground": "on",
            "lev_surface": "on",
            "lev_entire_atmosphere": "on",
            "subregion": "",
            "leftlon": station.longitude - self.neighborhood_degrees,
            "rightlon": station.longitude + self.neighborhood_degrees,
            "toplat": station.latitude + self.neighborhood_degrees,
            "bottomlat": station.latitude - self.neighborhood_degrees,
        }
        response = requests.get(NOMADS_URL, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.content


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _series_current(frame: pd.DataFrame, column: str) -> float:
    values = _numeric_series(frame, column)
    if values.empty:
        return np.nan
    return _safe_float(values.iloc[0])


def _series_mean(frame: pd.DataFrame, column: str) -> float:
    values = _numeric_series(frame, column)
    if values.empty:
        return np.nan
    return _safe_float(values.mean())


def _series_next_3h_mean(frame: pd.DataFrame, column: str) -> float:
    return _series_mean(frame, column)


def _series_max(frame: pd.DataFrame, column: str) -> float:
    values = _numeric_series(frame, column)
    if values.empty:
        return np.nan
    return _safe_float(values.max())


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _nearest_index(lats, lons, target_lat: float, target_lon: float) -> tuple[int, int]:
    distance = (lats - target_lat) ** 2 + (lons - target_lon) ** 2
    flat_idx = int(distance.argmin())
    return np.unravel_index(flat_idx, distance.shape)


def _kelvin_to_f(kelvin: float) -> float:
    return (kelvin - 273.15) * 9.0 / 5.0 + 32.0
