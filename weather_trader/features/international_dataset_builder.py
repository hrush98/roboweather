from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from weather_trader.features.build_low_temp_features import build_low_temp_threshold_examples
from weather_trader.features.build_same_day_features import build_synthetic_threshold_examples
from weather_trader.stations.hko_client import HKOClimateClient
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import Station


INTERNATIONAL_STATION_MAP = Path(__file__).parents[1] / "stations" / "international_station_map.csv"


@dataclass(frozen=True)
class InternationalStation:
    city: str
    station: str
    display_name: str
    timezone: str
    latitude: float
    longitude: float
    resolution_station: str
    resolution_source: str

    def as_station(self) -> Station:
        return Station(
            city=self.city,
            station=self.station,
            display_name=self.display_name,
            timezone=self.timezone,
            latitude=self.latitude,
            longitude=self.longitude,
        )


@dataclass
class InternationalDatasetBuilder:
    observations_client: IEMASOSClient
    hko_client: HKOClimateClient

    def build_for_station(self, station: InternationalStation, start: date, end: date, metric: str = "high") -> pd.DataFrame:
        observations = self.observations_client.fetch_observations(
            station=station.station,
            start=start,
            end=end + timedelta(days=2),
        )
        observations = _fahrenheit_observations_to_celsius(observations)
        base_station = station.as_station()
        if metric == "high":
            frame = build_synthetic_threshold_examples(observations=observations, station=base_station)
            final_column = "final_high_tmpf"
        elif metric == "low":
            frame = build_low_temp_threshold_examples(observations=observations, station=base_station)
            final_column = "final_low_tmpf"
        else:
            raise ValueError("metric must be 'high' or 'low'")

        if frame.empty:
            return frame
        frame = frame.loc[(pd.to_datetime(frame["local_date"]).dt.date >= start) & (pd.to_datetime(frame["local_date"]).dt.date <= end)].copy()
        frame["temperature_unit"] = "C"
        frame["observation_source"] = "IEM ASOS/METAR"
        frame["resolution_station"] = station.resolution_station
        frame["resolution_source"] = station.resolution_source
        frame["target_source"] = station.resolution_source
        if station.resolution_station == "HKO":
            frame = self._apply_hko_target(frame, metric=metric, final_column=final_column)
        if final_column in frame:
            frame[final_column.replace("_tmpf", "_tmpc")] = frame[final_column]
        return frame.reset_index(drop=True)

    def build_for_stations(
        self,
        stations: list[InternationalStation],
        start: date,
        end: date,
        metric: str = "high",
    ) -> pd.DataFrame:
        frames = []
        for station in stations:
            frame = self.build_for_station(station=station, start=start, end=end, metric=metric)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _apply_hko_target(self, frame: pd.DataFrame, metric: str, final_column: str) -> pd.DataFrame:
        daily = self.hko_client.fetch_daily_temperature_series(metric, station="HKO")
        keep_columns = ["local_date", final_column]
        daily = daily[keep_columns].dropna().copy()
        frame = frame.drop(columns=[final_column], errors="ignore").merge(daily, on="local_date", how="left")
        frame = frame.loc[frame[final_column].notna()].copy()
        if metric == "high":
            frame["target"] = (frame[final_column].astype(float) >= frame["threshold"].astype(float)).astype(int)
        else:
            frame["target"] = (frame[final_column].astype(float) <= frame["threshold"].astype(float)).astype(int)
        frame["target_source"] = "HKO"
        return frame


def load_international_station_table(path: Path = INTERNATIONAL_STATION_MAP) -> pd.DataFrame:
    return pd.read_csv(path)


def list_international_stations(station_ids: list[str] | None = None) -> list[InternationalStation]:
    table = load_international_station_table()
    if station_ids is not None:
        selected = {station_id.upper() for station_id in station_ids}
        table = table.loc[table["station"].astype(str).str.upper().isin(selected)]
    return [InternationalStation(**row) for row in table.to_dict(orient="records")]


def build_international_dataset(
    start: date,
    end: date,
    metric: str = "high",
    station_ids: list[str] | None = None,
) -> pd.DataFrame:
    builder = InternationalDatasetBuilder(
        observations_client=IEMASOSClient(max_retries=4, retry_backoff_seconds=10.0),
        hko_client=HKOClimateClient(),
    )
    return builder.build_for_stations(
        stations=list_international_stations(station_ids=station_ids),
        start=start,
        end=end,
        metric=metric,
    )


def _fahrenheit_observations_to_celsius(observations: pd.DataFrame) -> pd.DataFrame:
    frame = observations.copy()
    for column in ("tmpf", "dwpf"):
        if column in frame.columns:
            frame[column] = ((pd.to_numeric(frame[column], errors="coerce") - 32.0) * 5.0 / 9.0).round(1)
    return frame
