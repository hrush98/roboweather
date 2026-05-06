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
        if not rows:
            return {
                "hrrr_current_temp": np.nan,
                "hrrr_remaining_max": np.nan,
                "hrrr_cloud_cover_next_3h": np.nan,
                "hrrr_wind_speed_next_3h": np.nan,
            }
        frame = pd.DataFrame(rows).sort_values("forecast_hour")
        return {
            "hrrr_current_temp": float(frame["tmpf"].iloc[0]),
            "hrrr_remaining_max": float(frame["tmpf"].max()),
            "hrrr_cloud_cover_next_3h": float(frame.head(4)["tcdc"].mean()),
            "hrrr_wind_speed_next_3h": float(frame.head(4)["wind_speed_mph"].mean()),
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
            elif name == "10u":
                parsed["u10"] = value
            elif name == "10v":
                parsed["v10"] = value
            elif name == "tcc":
                parsed["tcdc"] = value
            elif name == "tcdc":
                parsed["tcdc"] = value
        parsed["wind_speed_mph"] = float(np.hypot(parsed.get("u10", np.nan), parsed.get("v10", np.nan)) * 2.23694)
        return parsed

    def _download_subset(self, station: Station, cycle_utc: datetime, forecast_hour: int) -> bytes:
        params = {
            "dir": f"/hrrr.{cycle_utc:%Y%m%d}/conus",
            "file": f"hrrr.t{cycle_utc:%H}z.wrfsfcf{forecast_hour:02d}.grib2",
            "var_TMP": "on",
            "var_UGRD": "on",
            "var_VGRD": "on",
            "var_TCDC": "on",
            "lev_2_m_above_ground": "on",
            "lev_10_m_above_ground": "on",
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


def _nearest_index(lats, lons, target_lat: float, target_lon: float) -> tuple[int, int]:
    distance = (lats - target_lat) ** 2 + (lons - target_lon) ** 2
    flat_idx = int(distance.argmin())
    return np.unravel_index(flat_idx, distance.shape)


def _kelvin_to_f(kelvin: float) -> float:
    return (kelvin - 273.15) * 9.0 / 5.0 + 32.0
