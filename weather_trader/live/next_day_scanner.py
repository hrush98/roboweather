from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from weather_trader.execution.books import RestBookClient
from weather_trader.features.build_same_day_features import build_daily_station_table, prepare_station_observations
from weather_trader.markets.polymarket_reader import PolymarketReader, WeatherMarket
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station


TRAINED_NEXT_DAY_STATIONS = {"KLGA", "KBOS", "KDCA", "KORD", "KATL"}


@dataclass(frozen=True)
class NextDayStationState:
    station: str
    prediction_date: date
    target_date: date
    latest_obs_time: str
    current_temp: float
    today_high_so_far: float
    prediction_hour_local: int
    prediction_day_of_year: int
    target_day_of_year: int
    temp_change_1h: float
    temp_change_3h: float
    dewpoint: float
    wind_speed: float
    wind_dir_sin: float
    wind_dir_cos: float
    cloud_cover_code: float
    prior_day_high: float
    prior_3day_high_mean: float
    prior_7day_high_mean: float


class NextDayScanner:
    def __init__(self, model_path: Path) -> None:
        bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.feature_columns = list(bundle["feature_columns"])
        self.model_name = model_path.stem
        self.market_reader = PolymarketReader()
        self.book_client = RestBookClient()
        self.obs_client = IEMASOSClient()

    def scan(self, target_date: date, as_of_utc: datetime | None = None, limit: int = 50000) -> pd.DataFrame:
        now = as_of_utc or datetime.now(timezone.utc)
        markets = [
            market
            for item in self.market_reader._fetch_gamma_markets(limit=limit)
            for market in [self.market_reader._parse_weather_market(item)]
            if market is not None and market.market_date == target_date
        ]
        token_ids = sorted(
            {
                token_id
                for market in markets
                for token_id in (market.yes_token_id, market.no_token_id)
                if token_id
            }
        )
        books = self.book_client.fetch_books(token_ids)
        station_states: dict[str, NextDayStationState] = {}
        rows: list[dict[str, object]] = []
        for market in markets:
            try:
                if market.station not in station_states:
                    station_states[market.station] = self._build_station_state(market.station, target_date, now)
                state = station_states[market.station]
            except Exception as exc:
                rows.append({"market_id": market.market_id, "station": market.station, "question": market.question, "error": str(exc)})
                continue
            fair_yes = self._bucket_probability(market, state)
            fair_no = 1.0 - fair_yes
            yes_book = books.get(market.yes_token_id or "")
            no_book = books.get(market.no_token_id or "")
            yes_ask = yes_book.best_ask if yes_book else None
            no_ask = no_book.best_ask if no_book else None
            edge_yes = fair_yes - yes_ask if yes_ask is not None else np.nan
            edge_no = fair_no - no_ask if no_ask is not None else np.nan
            rows.append(
                {
                    "market_id": market.market_id,
                    "station": market.station,
                    "trained_station": market.station in TRAINED_NEXT_DAY_STATIONS,
                    "bucket": _bucket_label(market),
                    "current_temp": state.current_temp,
                    "today_high_so_far": state.today_high_so_far,
                    "prior_day_high": state.prior_day_high,
                    "prior_7day_high_mean": state.prior_7day_high_mean,
                    "yes_bid": yes_book.best_bid if yes_book else np.nan,
                    "yes_ask": yes_ask,
                    "no_bid": no_book.best_bid if no_book else np.nan,
                    "no_ask": no_ask,
                    "fair_yes": fair_yes,
                    "fair_no": fair_no,
                    "edge_yes": edge_yes,
                    "edge_no": edge_no,
                    "best_side": "BUY_YES" if edge_yes >= edge_no else "BUY_NO",
                    "best_edge": max(edge_yes, edge_no),
                    "latest_obs_time": state.latest_obs_time,
                    "question": market.question,
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty or "best_edge" not in frame:
            return frame
        return frame.sort_values("best_edge", ascending=False)

    def _build_station_state(self, station_id: str, target_date: date, as_of_utc: datetime) -> NextDayStationState:
        station = get_station(station_id)
        prediction_date = as_of_utc.astimezone(ZoneInfo(station.timezone)).date()
        observations = self.obs_client.fetch_observations(
            station=station.station,
            start=prediction_date - timedelta(days=10),
            end=prediction_date + timedelta(days=1),
        )
        prepared = prepare_station_observations(observations, station)
        valid_temp = prepared.loc[prepared["tmpf"].notna()]
        today = valid_temp.loc[valid_temp["local_date"] == prediction_date]
        if today.empty:
            raise ValueError(f"No observations for {station.station} on {prediction_date}")
        latest = today.iloc[-1]
        daily = build_daily_station_table(prepared)
        prior_daily = daily.loc[daily["local_date"] < prediction_date].sort_values("local_date")
        if prior_daily.empty:
            raise ValueError(f"No prior daily highs for {station.station}")
        prior_highs = prior_daily["final_high_tmpf"].astype(float)
        target_timestamp = pd.Timestamp(target_date)
        return NextDayStationState(
            station=station.station,
            prediction_date=prediction_date,
            target_date=target_date,
            latest_obs_time=latest["valid"].isoformat(),
            current_temp=float(latest["tmpf"]),
            today_high_so_far=float(today["tmpf"].max()),
            prediction_hour_local=int(latest["hour_local"]),
            prediction_day_of_year=int(latest["doy"]),
            target_day_of_year=int(target_timestamp.dayofyear),
            temp_change_1h=_float_or_nan(latest.get("temp_change_1h", np.nan)),
            temp_change_3h=_float_or_nan(latest.get("temp_change_3h", np.nan)),
            dewpoint=_float_or_nan(latest.get("dwpf", np.nan)),
            wind_speed=_float_or_nan(latest.get("sknt", np.nan)),
            wind_dir_sin=_float_or_nan(latest.get("wind_dir_sin", np.nan)),
            wind_dir_cos=_float_or_nan(latest.get("wind_dir_cos", np.nan)),
            cloud_cover_code=_float_or_nan(latest.get("cloud_cover_code", np.nan)),
            prior_day_high=float(prior_highs.iloc[-1]),
            prior_3day_high_mean=float(prior_highs.tail(3).mean()),
            prior_7day_high_mean=float(prior_highs.tail(7).mean()),
        )

    def _bucket_probability(self, market: WeatherMarket, state: NextDayStationState) -> float:
        if market.lower_f is not None and market.upper_f is not None:
            return max(0.0, self._probability_at_threshold(state, market.lower_f) - self._probability_at_threshold(state, market.upper_f + 1.0))
        if market.lower_f is not None:
            return self._probability_at_threshold(state, market.lower_f)
        if market.upper_f is not None:
            return 1.0 - self._probability_at_threshold(state, market.upper_f + 1.0)
        return 0.0

    def _probability_at_threshold(self, state: NextDayStationState, threshold: float) -> float:
        features = {
            "station": state.station,
            "prediction_hour_local": state.prediction_hour_local,
            "prediction_day_of_year": state.prediction_day_of_year,
            "target_day_of_year": state.target_day_of_year,
            "current_temp": state.current_temp,
            "today_high_so_far": state.today_high_so_far,
            "threshold": threshold,
            "threshold_minus_current_temp": threshold - state.current_temp,
            "threshold_minus_today_high_so_far": threshold - state.today_high_so_far,
            "temp_change_1h": state.temp_change_1h,
            "temp_change_3h": state.temp_change_3h,
            "dewpoint": state.dewpoint,
            "wind_speed": state.wind_speed,
            "wind_dir_sin": state.wind_dir_sin,
            "wind_dir_cos": state.wind_dir_cos,
            "cloud_cover_code": state.cloud_cover_code,
            "prior_day_high": state.prior_day_high,
            "prior_3day_high_mean": state.prior_3day_high_mean,
            "prior_7day_high_mean": state.prior_7day_high_mean,
            "prior_day_high_minus_threshold": state.prior_day_high - threshold,
            "prior_7day_high_mean_minus_threshold": state.prior_7day_high_mean - threshold,
        }
        frame = pd.DataFrame([{column: features.get(column, np.nan) for column in self.feature_columns}])
        return float(self.model.predict_proba(frame)[:, 1][0])


def _bucket_label(market: WeatherMarket) -> str:
    if market.lower_f is not None and market.upper_f is not None:
        return f"{market.lower_f:g}-{market.upper_f:g}F"
    if market.lower_f is not None:
        return f">={market.lower_f:g}F"
    if market.upper_f is not None:
        return f"<={market.upper_f:g}F"
    return "unknown"


def _float_or_nan(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
