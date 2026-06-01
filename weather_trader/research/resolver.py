from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from weather_trader.execution.contracts import (
    PredictionResult,
    StationDateOutcome,
    MarketFamily,
    TradeAction,
    utc_now_iso,
)
from weather_trader.execution.positions import winning_side_for_bucket
from weather_trader.execution.store import ExecutionStore
from weather_trader.features.build_same_day_features import prepare_station_observations
from weather_trader.features.international_dataset_builder import _fahrenheit_observations_to_celsius
from weather_trader.stations.hko_client import HKOClimateClient
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_international_station, get_station, get_station_any


@dataclass(frozen=True)
class ResolverConfig:
    resolve_after_local_hour: int = 6
    source: str = "IEM_ASOS"


@dataclass(frozen=True)
class ResolveSummary:
    groups_checked: int
    groups_resolved: int
    results_written: int
    errors: list[str]


class ResearchResolver:
    def __init__(
        self,
        store: ExecutionStore,
        config: ResolverConfig | None = None,
        obs_client: IEMASOSClient | None = None,
        hko_client: HKOClimateClient | None = None,
        market_scope: str = "us",
    ) -> None:
        self.store = store
        self.config = config or ResolverConfig()
        self.obs_client = obs_client or IEMASOSClient()
        self.hko_client = hko_client or HKOClimateClient()
        self.market_scope = market_scope

    def resolve_due(self, as_of_utc: datetime | None = None) -> ResolveSummary:
        now = as_of_utc or datetime.now(timezone.utc)
        groups = self.store.unresolved_snapshot_groups()
        resolved = 0
        results_written = 0
        errors: list[str] = []
        for group in groups:
            station_id = str(group["station"])
            market_date = date.fromisoformat(str(group["market_date"]))
            if not self._is_due(station_id, market_date, now):
                continue
            try:
                outcome = self._resolve_station_date(station_id, market_date)
                self.store.upsert_station_date_outcome(outcome)
                resolved += 1
                snapshots = self.store.prediction_snapshots_for_group(station_id, str(market_date))
                for snapshot in snapshots:
                    self.store.upsert_prediction_result(score_snapshot(snapshot, outcome))
                    results_written += 1
            except Exception as exc:
                errors.append(f"{station_id}:{market_date}: {exc}")
        return ResolveSummary(
            groups_checked=len(groups),
            groups_resolved=resolved,
            results_written=results_written,
            errors=errors,
        )

    def _is_due(self, station_id: str, market_date: date, as_of_utc: datetime) -> bool:
        station = get_station_any(station_id)
        zone = ZoneInfo(station.timezone)
        resolve_at_local = datetime.combine(
            market_date + timedelta(days=1),
            time(self.config.resolve_after_local_hour, 0),
            tzinfo=zone,
        )
        return as_of_utc >= resolve_at_local.astimezone(timezone.utc)

    def _resolve_station_date(self, station_id: str, market_date: date) -> StationDateOutcome:
        if _is_international_station(station_id):
            return self._resolve_global_station_date(station_id, market_date)
        station = get_station(station_id)
        observations = self.obs_client.fetch_observations(
            station=station.station,
            start=market_date,
            end=market_date + timedelta(days=1),
        )
        prepared = prepare_station_observations(observations, station)
        day = prepared.loc[prepared["local_date"] == market_date]
        if day.empty:
            raise ValueError(f"No IEM observations for {station.station} on {market_date}")
        final_high = float(day["tmpf"].max())
        final_low = float(day["tmpf"].min())
        resolved_at = utc_now_iso()
        return StationDateOutcome(
            timestamp=resolved_at,
            station=station.station,
            market_date=market_date,
            final_high_tmpf=final_high,
            source=self.config.source,
            resolved_at=resolved_at,
            final_low_tmpf=final_low,
        )

    def _resolve_global_station_date(self, station_id: str, market_date: date) -> StationDateOutcome:
        station = get_international_station(station_id)
        resolved_at = utc_now_iso()
        if station.station == "VHHH":
            high = self.hko_client.fetch_daily_temperature_series("high", station="HKO")
            low = self.hko_client.fetch_daily_temperature_series("low", station="HKO")
            daily = high.merge(low, on="local_date", how="outer")
            row = daily.loc[daily["local_date"] == market_date]
            if not row.empty:
                return StationDateOutcome(
                    timestamp=resolved_at,
                    station=station.station,
                    market_date=market_date,
                    final_high_tmpf=float(row.iloc[0]["final_high_tmpf"]),
                    final_low_tmpf=float(row.iloc[0]["final_low_tmpf"]),
                    source="HKO_CLMMAXT_CLMMINT_C",
                    resolved_at=resolved_at,
                )
            return self._resolve_global_metar_station_date(station_id, market_date, resolved_at)
        return self._resolve_global_metar_station_date(station_id, market_date, resolved_at)

    def _resolve_global_metar_station_date(
        self,
        station_id: str,
        market_date: date,
        resolved_at: str,
    ) -> StationDateOutcome:
        station = get_international_station(station_id)
        observations = self.obs_client.fetch_observations(
            station=station.station,
            start=market_date,
            end=market_date + timedelta(days=1),
        )
        prepared = prepare_station_observations(_fahrenheit_observations_to_celsius(observations), station)
        day = prepared.loc[prepared["local_date"] == market_date]
        if day.empty:
            raise ValueError(f"No IEM observations for {station.station} on {market_date}")
        return StationDateOutcome(
            timestamp=resolved_at,
            station=station.station,
            market_date=market_date,
            final_high_tmpf=round(float(day["tmpf"].max()), 1),
            final_low_tmpf=round(float(day["tmpf"].min()), 1),
            source="IEM_ASOS_METAR_C",
            resolved_at=resolved_at,
        )


def score_snapshot(snapshot: dict, outcome: StationDateOutcome) -> PredictionResult:
    selected_side = TradeAction(str(snapshot.get("selected_side") or TradeAction.SKIP))
    market_family = MarketFamily(str(snapshot.get("market_family") or MarketFamily.HIGH_TEMP))
    lower, upper = _parse_bucket(snapshot.get("selected_bucket"))
    winning_side = None
    correct = None
    entry_price = _entry_price(snapshot, selected_side)
    paper_pnl = None
    final_temp = outcome.final_low_tmpf if market_family == MarketFamily.LOW_TEMP else outcome.final_high_tmpf
    if selected_side != TradeAction.SKIP and (lower is not None or upper is not None):
        if final_temp is None:
            winning_side = None
            correct = None
        else:
            winning_side = winning_side_for_bucket(final_temp, lower, upper, market_family=market_family)
            correct = selected_side == winning_side
        if entry_price is not None and correct is not None:
            paper_pnl = (1.0 - entry_price) if correct else -entry_price
    return PredictionResult(
        timestamp=utc_now_iso(),
        prediction_snapshot_id=int(snapshot["id"]),
        station=outcome.station,
        market_date=outcome.market_date,
        obs_delay_bucket=str(snapshot.get("obs_delay_bucket", "")),
        selected_market_id=snapshot.get("selected_market_id"),
        selected_bucket=snapshot.get("selected_bucket"),
        selected_side=selected_side,
        final_high_tmpf=outcome.final_high_tmpf,
        winning_side=winning_side,
        correct=correct,
        entry_price=entry_price,
        paper_pnl=paper_pnl,
        edge=_float_or_none(snapshot.get("selected_edge")),
        decision_time_local=str(snapshot.get("decision_time_local", "")),
        obs_age_minutes=float(snapshot.get("obs_age_minutes") or 0.0),
        resolved_at=outcome.resolved_at,
        market_family=market_family,
        final_low_tmpf=outcome.final_low_tmpf,
    )


def _entry_price(snapshot: dict, side: TradeAction) -> float | None:
    if side == TradeAction.BUY_YES:
        return _float_or_none(snapshot.get("selected_yes_ask"))
    if side == TradeAction.BUY_NO:
        return _float_or_none(snapshot.get("selected_no_ask"))
    return None


def _parse_bucket(bucket: object) -> tuple[float | None, float | None]:
    if not bucket:
        return None, None
    text = str(bucket).removesuffix("F").removesuffix("C")
    if text.startswith(">="):
        return _float_or_none(text.removeprefix(">=")), None
    if text.startswith("<="):
        return None, _float_or_none(text.removeprefix("<="))
    if "-" in text:
        lower, upper = text.split("-", 1)
        return _float_or_none(lower), _float_or_none(upper)
    return None, None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_international_station(station_id: str) -> bool:
    try:
        get_international_station(station_id)
    except KeyError:
        return False
    return True
