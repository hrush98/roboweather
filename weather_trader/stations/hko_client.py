from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import time

import pandas as pd
import requests


BASE_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"


@dataclass
class HKOClimateClient:
    timeout_seconds: int = 60
    max_retries: int = 3
    retry_backoff_seconds: float = 5.0

    def fetch_daily_temperatures(self, station: str = "HKO") -> pd.DataFrame:
        maximum = self.fetch_daily_temperature_series("high", station=station)
        minimum = self.fetch_daily_temperature_series("low", station=station)
        return maximum.merge(minimum, on="local_date", how="outer").sort_values("local_date").reset_index(drop=True)

    def fetch_daily_temperature_series(self, metric: str, station: str = "HKO") -> pd.DataFrame:
        if metric == "high":
            return self._fetch_temperature_series("CLMMAXT", "final_high_tmpf", station=station)
        if metric == "low":
            return self._fetch_temperature_series("CLMMINT", "final_low_tmpf", station=station)
        raise ValueError("metric must be 'high' or 'low'")

    def _fetch_temperature_series(self, data_type: str, value_column: str, station: str) -> pd.DataFrame:
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(
                    BASE_URL,
                    params={"dataType": data_type, "rformat": "csv", "station": station},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt >= self.max_retries:
                    raise
                time.sleep(self.retry_backoff_seconds * (attempt + 1))
        assert response is not None
        response.raise_for_status()
        frame = pd.read_csv(StringIO(response.text), skiprows=2)
        frame.columns = ["year", "month", "day", value_column, "data_completeness"]
        for column in ("year", "month", "day", value_column):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.loc[frame[value_column].notna()].copy()
        frame["local_date"] = pd.to_datetime(
            {
                "year": frame["year"].astype(int),
                "month": frame["month"].astype(int),
                "day": frame["day"].astype(int),
            }
        ).dt.date
        return frame[["local_date", value_column, "data_completeness"]].rename(
            columns={"data_completeness": f"{value_column}_completeness"}
        )
