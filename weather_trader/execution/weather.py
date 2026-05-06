from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from importlib.util import find_spec
from zoneinfo import ZoneInfo

import numpy as np

from weather_trader.features.build_same_day_features import prepare_station_observations
from weather_trader.forecasts.hrrr_client import HRRRClient
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station


@dataclass(frozen=True)
class StationWeatherState:
    station: str
    local_date: object
    latest_obs_time: str
    latest_obs_age_minutes: float
    current_temp: float
    high_so_far: float
    hour_local: int
    day_of_year: int
    temp_change_1h: float
    temp_change_3h: float
    dewpoint: float
    wind_speed: float
    wind_dir_sin: float
    wind_dir_cos: float
    cloud_cover_code: float
    hrrr_current_temp: float | None
    hrrr_remaining_max: float | None
    stale: bool


class WeatherFeatureService:
    def __init__(
        self,
        obs_client: IEMASOSClient | None = None,
        hrrr_client: HRRRClient | None = None,
        max_obs_age_minutes: int = 25,
    ) -> None:
        self.obs_client = obs_client or IEMASOSClient()
        self.hrrr_client = hrrr_client or HRRRClient()
        self.hrrr_available = find_spec("pygrib") is not None
        self.max_obs_age_minutes = max_obs_age_minutes
        self._cache: dict[tuple[str, object], StationWeatherState] = {}
        self._cache_fetched_at: dict[tuple[str, object], datetime] = {}

    def get_state(self, station_id: str, as_of_utc: datetime) -> StationWeatherState:
        station = get_station(station_id)
        zone = ZoneInfo(station.timezone)
        local_date = as_of_utc.astimezone(zone).date()
        key = (station.station, local_date)
        cached = self._cache.get(key)
        fetched_at = self._cache_fetched_at.get(key)
        if cached and fetched_at and (as_of_utc - fetched_at).total_seconds() < 60:
            return cached

        local_midnight = datetime.combine(local_date, time.min, tzinfo=zone)
        start_utc = local_midnight.astimezone(timezone.utc)
        try:
            observations = self.obs_client.fetch_observations(
                station=station.station,
                start=start_utc.date(),
                end=(as_of_utc + timedelta(hours=1)).date(),
            )
        except Exception:
            if cached is not None:
                latest_obs_time = datetime.fromisoformat(cached.latest_obs_time)
                age_minutes = max(0.0, (as_of_utc - latest_obs_time.astimezone(timezone.utc)).total_seconds() / 60.0)
                return replace(cached, latest_obs_age_minutes=age_minutes, stale=True)
            raise
        prepared = prepare_station_observations(observations, station)
        valid_temp = prepared.loc[
            (prepared["tmpf"].notna())
            & (prepared["valid"] <= as_of_utc)
            & (prepared["local_date"] == local_date)
        ]
        if valid_temp.empty:
            raise ValueError(f"No non-null temperature observations for {station.station} on {local_date} as of {as_of_utc.isoformat()}")
        latest = valid_temp.iloc[-1]
        today = valid_temp
        high_so_far = float(today["tmpf"].max())
        latest_obs_time = latest["valid"].to_pydatetime()
        if latest_obs_time.tzinfo is None:
            latest_obs_time = latest_obs_time.replace(tzinfo=timezone.utc)
        age_minutes = max(0.0, (as_of_utc - latest_obs_time.astimezone(timezone.utc)).total_seconds() / 60.0)

        hrrr_current = np.nan
        hrrr_remaining = np.nan
        if self.hrrr_available:
            try:
                hrrr = self.hrrr_client.fetch_remaining_day_features(station=station, as_of_utc=as_of_utc)
                hrrr_current = hrrr.get("hrrr_current_temp", np.nan)
                hrrr_remaining = hrrr.get("hrrr_remaining_max", np.nan)
            except Exception:
                pass

        state = StationWeatherState(
            station=station.station,
            local_date=latest["local_date"],
            latest_obs_time=latest_obs_time.astimezone(timezone.utc).isoformat(),
            latest_obs_age_minutes=age_minutes,
            current_temp=float(latest["tmpf"]),
            high_so_far=high_so_far,
            hour_local=int(latest["hour_local"]),
            day_of_year=int(latest["doy"]),
            temp_change_1h=_float_or_nan(latest.get("temp_change_1h", np.nan)),
            temp_change_3h=_float_or_nan(latest.get("temp_change_3h", np.nan)),
            dewpoint=_float_or_nan(latest.get("dwpf", np.nan)),
            wind_speed=_float_or_nan(latest.get("sknt", np.nan)),
            wind_dir_sin=_float_or_nan(latest.get("wind_dir_sin", np.nan)),
            wind_dir_cos=_float_or_nan(latest.get("wind_dir_cos", np.nan)),
            cloud_cover_code=_float_or_nan(latest.get("cloud_cover_code", np.nan)),
            hrrr_current_temp=_none_if_nan(hrrr_current),
            hrrr_remaining_max=_none_if_nan(hrrr_remaining),
            stale=age_minutes > self.max_obs_age_minutes,
        )
        self._cache[key] = state
        self._cache_fetched_at[key] = as_of_utc
        return state


def _float_or_nan(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _none_if_nan(value) -> float | None:
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(as_float):
        return None
    return as_float
