from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import Station, list_stations
from weather_trader.features.build_same_day_features import build_synthetic_threshold_examples


@dataclass
class DatasetBuilder:
    client: IEMASOSClient

    def build_for_station(self, station: Station, start: date, end: date) -> pd.DataFrame:
        observations = self.client.fetch_observations(station=station.station, start=start, end=end)
        return build_synthetic_threshold_examples(observations=observations, station=station)

    def build_for_stations(self, stations: list[Station], start: date, end: date) -> pd.DataFrame:
        frames = []
        for station in stations:
            frame = self.build_for_station(station=station, start=start, end=end)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)


def build_default_dataset(start: date, end: date, initial_only: bool = True) -> pd.DataFrame:
    builder = DatasetBuilder(client=IEMASOSClient())
    stations = list_stations(initial_only=initial_only)
    return builder.build_for_stations(stations=stations, start=start, end=end)
