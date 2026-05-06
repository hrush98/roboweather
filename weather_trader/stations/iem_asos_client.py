from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO
import time

import pandas as pd
import requests


BASE_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DEFAULT_FIELDS = [
    "tmpf",
    "dwpf",
    "sknt",
    "drct",
    "relh",
    "mslp",
    "skyc1",
    "skyc2",
    "skyc3",
]


@dataclass
class IEMASOSClient:
    timeout_seconds: int = 60
    min_request_interval_seconds: float = 1.0
    max_retries: int = 2
    retry_backoff_seconds: float = 3.0
    _last_request_at: float = field(default=0.0, init=False)

    def fetch_observations(
        self,
        station: str,
        start: date | datetime,
        end: date | datetime,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        params = self._build_params(station=station, start=start, end=end, fields=fields or DEFAULT_FIELDS)
        response = None
        for attempt in range(self.max_retries + 1):
            self._pace_requests()
            response = requests.get(BASE_URL, params=params, timeout=self.timeout_seconds)
            self._last_request_at = time.monotonic()
            if response.status_code != 429 or attempt >= self.max_retries:
                break
            time.sleep(self.retry_backoff_seconds * (attempt + 1))
        assert response is not None
        response.raise_for_status()
        return self._parse_csv(response.text)

    def _pace_requests(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval_seconds:
            time.sleep(self.min_request_interval_seconds - elapsed)

    @staticmethod
    def _build_params(
        station: str,
        start: date | datetime,
        end: date | datetime,
        fields: list[str],
    ) -> list[tuple[str, str | int]]:
        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        params: list[tuple[str, str | int]] = [
            ("station", station.upper().removeprefix("K")),
            ("year1", start_dt.year),
            ("month1", start_dt.month),
            ("day1", start_dt.day),
            ("year2", end_dt.year),
            ("month2", end_dt.month),
            ("day2", end_dt.day),
            ("tz", "UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
            ("report_type", "1"),
            ("report_type", "2"),
        ]
        for field in fields:
            params.append(("data", field))
        return params

    @staticmethod
    def _parse_csv(csv_text: str) -> pd.DataFrame:
        frame = pd.read_csv(StringIO(csv_text), na_values=["M"])
        frame.columns = [column.strip().lower() for column in frame.columns]
        if "valid" not in frame.columns:
            raise ValueError("IEM response missing 'valid' column")
        frame["valid"] = pd.to_datetime(frame["valid"], utc=True)
        for numeric_column in ("tmpf", "dwpf", "sknt", "drct", "relh", "mslp"):
            if numeric_column in frame.columns:
                frame[numeric_column] = pd.to_numeric(frame[numeric_column], errors="coerce")
        return frame.sort_values("valid").reset_index(drop=True)
