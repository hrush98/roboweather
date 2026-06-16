from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import re
import time as time_module
from zoneinfo import ZoneInfo

import requests

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


GAMMA_URL = "https://gamma-api.polymarket.com"
POLYMARKET_LOW_TEMP_STATIONS = frozenset({"RJTT", "RKSI", "VHHH", "ZSPD"})


@dataclass(frozen=True)
class ResolverConfig:
    resolve_after_local_hour: int = 6
    source: str = "IEM_ASOS"
    polymarket_timeout_seconds: int = 30
    polymarket_max_retries: int = 3
    polymarket_retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class ResolveSummary:
    groups_checked: int
    groups_resolved: int
    results_written: int
    errors: list[str]


@dataclass(frozen=True)
class PolymarketLowTempResolution:
    final_low_tmpf: float
    event_slug: str
    winning_market_id: str
    winning_slug: str
    resolution_source: str


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
                snapshots = self.store.prediction_snapshots_for_group(station_id, str(market_date))
                market_families = _snapshot_market_families(snapshots)
                outcome = self._resolve_station_date(station_id, market_date, market_families=market_families)
                self.store.upsert_station_date_outcome(outcome)
                resolved += 1
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

    def _resolve_station_date(
        self,
        station_id: str,
        market_date: date,
        market_families: set[MarketFamily] | None = None,
    ) -> StationDateOutcome:
        if _is_international_station(station_id):
            return self._resolve_global_station_date(station_id, market_date, market_families=market_families)
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

    def _resolve_global_station_date(
        self,
        station_id: str,
        market_date: date,
        market_families: set[MarketFamily] | None = None,
    ) -> StationDateOutcome:
        station = get_international_station(station_id)
        resolved_at = utc_now_iso()
        if station.station == "VHHH":
            high = self.hko_client.fetch_daily_temperature_series("high", station="HKO")
            low = self.hko_client.fetch_daily_temperature_series("low", station="HKO")
            daily = high.merge(low, on="local_date", how="outer")
            row = daily.loc[daily["local_date"] == market_date]
            if not row.empty:
                outcome = StationDateOutcome(
                    timestamp=resolved_at,
                    station=station.station,
                    market_date=market_date,
                    final_high_tmpf=float(row.iloc[0]["final_high_tmpf"]),
                    final_low_tmpf=float(row.iloc[0]["final_low_tmpf"]),
                    source="HKO_CLMMAXT_CLMMINT_C",
                    resolved_at=resolved_at,
                )
                return self._with_polymarket_low_temp_if_needed(outcome, market_families)
            outcome = self._resolve_global_metar_station_date(station_id, market_date, resolved_at)
            return self._with_polymarket_low_temp_if_needed(outcome, market_families)
        outcome = self._resolve_global_metar_station_date(station_id, market_date, resolved_at)
        return self._with_polymarket_low_temp_if_needed(outcome, market_families)

    def _with_polymarket_low_temp_if_needed(
        self,
        outcome: StationDateOutcome,
        market_families: set[MarketFamily] | None,
    ) -> StationDateOutcome:
        if outcome.station not in POLYMARKET_LOW_TEMP_STATIONS:
            return outcome
        if market_families is None or MarketFamily.LOW_TEMP not in market_families:
            return outcome
        resolution = self._resolve_polymarket_low_temp(outcome.station, outcome.market_date)
        return StationDateOutcome(
            timestamp=outcome.timestamp,
            station=outcome.station,
            market_date=outcome.market_date,
            final_high_tmpf=outcome.final_high_tmpf,
            final_low_tmpf=resolution.final_low_tmpf,
            source=f"POLYMARKET_GAMMA_LOW_TEMP:{outcome.source}",
            resolved_at=outcome.resolved_at,
        )

    def _resolve_polymarket_low_temp(self, station_id: str, market_date: date) -> PolymarketLowTempResolution:
        local_markets = self._local_low_temp_markets(station_id, market_date)
        if not local_markets:
            raise ValueError(f"No local LOW_TEMP markets for {station_id} on {market_date}")
        event_slugs = {_event_slug_from_market_slug(str(row["slug"])) for row in local_markets}
        if len(event_slugs) != 1:
            raise ValueError(f"Expected one LOW_TEMP event for {station_id} on {market_date}, found {sorted(event_slugs)}")
        event_slug = next(iter(event_slugs))
        event = self._fetch_gamma_event_by_slug(event_slug)
        gamma_markets = {str(item.get("id")): item for item in event.get("markets") or []}
        winners: list[dict] = []
        for row in local_markets:
            gamma_market = gamma_markets.get(str(row["market_id"]))
            if gamma_market is None:
                raise ValueError(f"Polymarket event {event_slug} missing local market {row['market_id']}")
            if not gamma_market.get("closed"):
                raise ValueError(f"Polymarket event {event_slug} is not fully closed")
            winning_yes = _gamma_yes_won(gamma_market.get("outcomePrices"))
            if winning_yes is None:
                raise ValueError(
                    f"Polymarket market {row['market_id']} is closed without binary settlement prices: "
                    f"{gamma_market.get('outcomePrices')}"
                )
            if winning_yes:
                winners.append(row)
        if len(winners) != 1:
            raise ValueError(f"Expected one winning LOW_TEMP market in {event_slug}, found {len(winners)}")
        winner = winners[0]
        final_low = _representative_bucket_value(winner["lower_f"], winner["upper_f"])
        return PolymarketLowTempResolution(
            final_low_tmpf=final_low,
            event_slug=event_slug,
            winning_market_id=str(winner["market_id"]),
            winning_slug=str(winner["slug"]),
            resolution_source=str(event.get("resolutionSource") or ""),
        )

    def _local_low_temp_markets(self, station_id: str, market_date: date) -> list[dict]:
        rows = self.store.connection.execute(
            """
            select market_id, slug, lower_f, upper_f
            from markets
            where station = ?
              and market_date = ?
              and market_family = ?
            order by coalesce(lower_f, upper_f), coalesce(upper_f, lower_f), market_id
            """,
            (station_id, str(market_date), str(MarketFamily.LOW_TEMP)),
        ).fetchall()
        return [dict(row) for row in rows]

    def _fetch_gamma_event_by_slug(self, slug: str) -> dict:
        last_error: requests.RequestException | None = None
        for attempt in range(1, self.config.polymarket_max_retries + 1):
            try:
                response = requests.get(
                    f"{GAMMA_URL}/events/slug/{slug}",
                    timeout=self.config.polymarket_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.polymarket_max_retries or not _retryable_request_error(exc):
                    raise
                time_module.sleep(self.config.polymarket_retry_backoff_seconds * attempt)
        raise last_error or RuntimeError("gamma event request failed")

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


def _snapshot_market_families(snapshots: list[dict]) -> set[MarketFamily]:
    families: set[MarketFamily] = set()
    for snapshot in snapshots:
        try:
            families.add(MarketFamily(str(snapshot.get("market_family") or MarketFamily.HIGH_TEMP)))
        except ValueError:
            continue
    return families


def _event_slug_from_market_slug(slug: str) -> str:
    event_slug = re.sub(r"-(?:-?\d+(?:\.\d+)?corbelow|-?\d+(?:\.\d+)?corhigher|-?\d+(?:\.\d+)?c)$", "", slug)
    if event_slug == slug:
        raise ValueError(f"Could not derive LOW_TEMP event slug from market slug: {slug}")
    return event_slug


def _gamma_yes_won(outcome_prices: object) -> bool | None:
    prices = _parse_gamma_list(outcome_prices)
    if len(prices) < 2:
        return None
    try:
        yes_price = Decimal(str(prices[0]))
        no_price = Decimal(str(prices[1]))
    except Exception:
        return None
    if yes_price == Decimal("1") and no_price == Decimal("0"):
        return True
    if yes_price == Decimal("0") and no_price == Decimal("1"):
        return False
    return None


def _parse_gamma_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _representative_bucket_value(lower: object, upper: object) -> float:
    lower_float = _float_or_none(lower)
    upper_float = _float_or_none(upper)
    if lower_float is None and upper_float is not None:
        return upper_float
    if lower_float is not None and upper_float is None:
        return lower_float
    if lower_float is not None and upper_float is not None:
        return lower_float
    raise ValueError(f"Winning bucket has no numeric bounds: lower={lower!r}, upper={upper!r}")


def _retryable_request_error(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        return False
    return response.status_code == 429 or 500 <= response.status_code < 600


def _is_international_station(station_id: str) -> bool:
    try:
        get_international_station(station_id)
    except KeyError:
        return False
    return True
