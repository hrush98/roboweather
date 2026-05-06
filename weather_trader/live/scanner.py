from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from weather_trader.markets.polymarket_reader import PolymarketReader
from weather_trader.markets.polymarket_reader import WeatherMarket
from weather_trader.models.train_classifier import load_artifacts
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station
from weather_trader.features.build_same_day_features import prepare_station_observations
from weather_trader.forecasts.hrrr_client import HRRRClient


@dataclass(frozen=True)
class ScanRow:
    market: str
    station: str
    current_temp: float
    max_temp_so_far: float
    hrrr_remaining_max: float
    market_bid_yes: float
    market_ask_yes: float
    fair_yes: float
    edge_yes: float
    edge_no: float
    signal: str


class LiveScanner:
    def __init__(self, model_path: Path) -> None:
        bundle = load_artifacts(model_path)
        self.model = bundle["model"]
        self.feature_columns = bundle["feature_columns"]
        self.market_reader = PolymarketReader()
        self.obs_client = IEMASOSClient()
        self.hrrr_client = HRRRClient()

    def scan(self, as_of_utc: datetime | None = None) -> pd.DataFrame:
        now = as_of_utc or datetime.now(timezone.utc)
        markets = self.market_reader.fetch_weather_markets()
        rows = []
        station_context: dict[str, tuple[object, pd.Series, float, dict[str, float]]] = {}
        for market in markets:
            try:
                station = get_station(market.station)
            except KeyError:
                continue
            local_date = now.astimezone(ZoneInfo(station.timezone)).date()
            if market.market_date is not None and market.market_date != local_date:
                continue
            if station.station not in station_context:
                try:
                    observations = self.obs_client.fetch_observations(
                        station=station.station,
                        start=(now - timedelta(days=1)).date(),
                        end=(now + timedelta(days=1)).date(),
                    )
                except Exception:
                    continue
                prepared = prepare_station_observations(observations, station)
                current = prepared.loc[prepared["tmpf"].notna()].iloc[-1]
                today = prepared.loc[prepared["local_date"] == current["local_date"]]
                max_temp_so_far = float(today["tmpf"].max())
                hrrr = self.hrrr_client.fetch_remaining_day_features(station=station, as_of_utc=now)
                station_context[station.station] = (station, current, max_temp_so_far, hrrr)
            station, current, max_temp_so_far, hrrr = station_context[station.station]
            features = {
                "station": station.station,
                "hour_local": int(current["hour_local"]),
                "day_of_year": int(current["doy"]),
                "current_temp": float(current["tmpf"]),
                "max_temp_so_far": max_temp_so_far,
                "threshold": market.threshold_f,
                "threshold_minus_current_temp": market.threshold_f - float(current["tmpf"]),
                "threshold_minus_max_so_far": market.threshold_f - max_temp_so_far,
                "temp_change_1h": current.get("temp_change_1h", np.nan),
                "temp_change_3h": current.get("temp_change_3h", np.nan),
                "dewpoint": current.get("dwpf", np.nan),
                "wind_speed": current.get("sknt", np.nan),
                "wind_dir_sin": current.get("wind_dir_sin", np.nan),
                "wind_dir_cos": current.get("wind_dir_cos", np.nan),
                "cloud_cover_code": current.get("cloud_cover_code", np.nan),
                "hrrr_current_temp": hrrr.get("hrrr_current_temp", np.nan),
                "hrrr_remaining_max": hrrr.get("hrrr_remaining_max", np.nan),
                "hrrr_remaining_max_minus_threshold": hrrr.get("hrrr_remaining_max", np.nan) - market.threshold_f,
                "hrrr_current_temp_minus_current_temp": hrrr.get("hrrr_current_temp", np.nan) - float(current["tmpf"]),
            }
            fair_yes = self._bucket_probability(market, features)
            edge_yes = fair_yes - market.best_ask_yes
            no_ask = market.best_ask_no if market.best_ask_no is not None else 1.0 - market.best_bid_yes
            fair_no = 1.0 - fair_yes
            edge_no = fair_no - no_ask
            signal = "HOLD"
            if edge_yes >= 0.10:
                signal = "BUY_YES"
            elif edge_no >= 0.10:
                signal = "BUY_NO"
            rows.append(
                {
                    "market": market.question,
                    "station": station.station,
                    "current_temp": float(current["tmpf"]),
                    "max_temp_so_far": max_temp_so_far,
                    "hrrr_remaining_max": hrrr.get("hrrr_remaining_max", np.nan),
                    "market_bid_yes": market.best_bid_yes,
                    "market_ask_yes": market.best_ask_yes,
                    "market_ask_no": no_ask,
                    "fair_yes": fair_yes,
                    "fair_no": fair_no,
                    "edge_yes": edge_yes,
                    "edge_no": edge_no,
                    "signal": signal,
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        return frame.sort_values(["signal", "edge_yes", "edge_no"], ascending=[True, False, False])

    def _bucket_probability(self, market: WeatherMarket, base_features: dict[str, object]) -> float:
        max_so_far = float(base_features["max_temp_so_far"])
        if market.lower_f is not None and market.upper_f is not None:
            if max_so_far > market.upper_f:
                return 0.0
            return max(0.0, self._probability_at_threshold(base_features, market.lower_f) - self._probability_at_threshold(base_features, market.upper_f + 1.0))
        if market.lower_f is not None:
            if max_so_far >= market.lower_f:
                return 1.0
            return self._probability_at_threshold(base_features, market.lower_f)
        if market.upper_f is not None:
            if max_so_far > market.upper_f:
                return 0.0
            return 1.0 - self._probability_at_threshold(base_features, market.upper_f + 1.0)
        return self._probability_at_threshold(base_features, market.threshold_f)

    def _probability_at_threshold(self, base_features: dict[str, object], threshold: float) -> float:
        features = dict(base_features)
        current_temp = float(features["current_temp"])
        max_temp_so_far = float(features["max_temp_so_far"])
        features["threshold"] = threshold
        features["threshold_minus_current_temp"] = threshold - current_temp
        features["threshold_minus_max_so_far"] = threshold - max_temp_so_far
        features["hrrr_remaining_max_minus_threshold"] = features.get("hrrr_remaining_max", np.nan) - threshold
        feature_frame = pd.DataFrame([{column: features.get(column, np.nan) for column in self.feature_columns}])
        return float(self.model.predict_proba(feature_frame)[:, 1][0])
