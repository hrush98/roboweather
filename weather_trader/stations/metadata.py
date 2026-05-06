from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Station:
    city: str
    station: str
    display_name: str
    timezone: str
    latitude: float
    longitude: float


@lru_cache(maxsize=1)
def load_station_table() -> pd.DataFrame:
    path = Path(__file__).with_name("station_map.csv")
    return pd.read_csv(path)


@lru_cache(maxsize=None)
def get_station(station_id: str) -> Station:
    table = load_station_table()
    row = table.loc[table["station"] == station_id.upper()]
    if row.empty:
        raise KeyError(f"Unknown station: {station_id}")
    record = row.iloc[0].to_dict()
    return Station(**record)


def list_stations(initial_only: bool = True) -> list[Station]:
    table = load_station_table()
    if initial_only:
        table = table.iloc[:5]
    return [Station(**row) for row in table.to_dict(orient="records")]
