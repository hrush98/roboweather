from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from weather_trader.execution.contracts import MarketSnapshot, Resolution, utc_now_iso
from weather_trader.execution.positions import winning_side_for_bucket
from weather_trader.features.build_same_day_features import prepare_station_observations
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station


class ResolutionTracker:
    def __init__(self, obs_client: IEMASOSClient | None = None) -> None:
        self.obs_client = obs_client or IEMASOSClient()

    def resolve_with_iem(self, market: MarketSnapshot) -> Resolution:
        if market.market_date is None:
            raise ValueError(f"Market {market.market_id} has no market_date")
        station = get_station(market.station)
        start = market.market_date
        observations = self.obs_client.fetch_observations(
            station=station.station,
            start=start,
            end=start + timedelta(days=1),
        )
        prepared = prepare_station_observations(observations, station)
        day = prepared.loc[prepared["local_date"] == market.market_date]
        if day.empty:
            raise ValueError(f"No IEM observations for {station.station} on {market.market_date}")
        final_high = float(day["tmpf"].max())
        return Resolution(
            market_id=market.market_id,
            station=market.station,
            market_date=market.market_date,
            final_high=final_high,
            winning_side=winning_side_for_bucket(final_high, market.lower_f, market.upper_f),
            source="IEM_ASOS",
            resolved_at=utc_now_iso(),
            discrepancy_flag=False,
        )
