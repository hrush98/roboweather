from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re
import time

import requests

from weather_trader.execution.contracts import MarketFamily
from weather_trader.stations.metadata import load_international_station_table, load_station_table


GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


@dataclass(frozen=True)
class WeatherMarket:
    market_id: str
    question: str
    slug: str
    city: str
    station: str
    threshold_f: float
    lower_f: float | None
    upper_f: float | None
    best_bid_yes: float
    best_ask_yes: float
    end_date: str
    resolution_source: str
    best_bid_no: float | None = None
    best_ask_no: float | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    market_date: date | None = None
    market_family: MarketFamily = MarketFamily.HIGH_TEMP


@dataclass(frozen=True)
class WeatherEventTarget:
    city_slug: str
    market_date: date
    market_family: MarketFamily = MarketFamily.HIGH_TEMP

    @property
    def event_slug(self) -> str:
        month = _MONTH_NAMES[self.market_date.month]
        prefix = "lowest-temperature" if self.market_family == MarketFamily.LOW_TEMP else "highest-temperature"
        return f"{prefix}-in-{self.city_slug}-on-{month}-{self.market_date.day}-{self.market_date.year}"


class PolymarketReader:
    def __init__(self, timeout_seconds: int = 30, max_retries: int = 3, retry_backoff_seconds: float = 1.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds
        table = load_station_table()
        self.city_map = {row["city"].lower(): row["station"] for row in table.to_dict(orient="records")}
        self.city_map["nyc"] = "KLGA"
        self.display_city = {row["station"]: row["city"] for row in table.to_dict(orient="records")}
        international_table = load_international_station_table()
        self.international_city_map = {
            _city_slug(row["city"]): row["station"] for row in international_table.to_dict(orient="records")
        }
        self.international_display_city = {
            row["station"]: row["city"] for row in international_table.to_dict(orient="records")
        }
        for city_slug, station in self.international_city_map.items():
            self.city_map.setdefault(city_slug, station)
            self.display_city.setdefault(station, self.international_display_city[station])
        for row in international_table.to_dict(orient="records"):
            self.city_map.setdefault(str(row["city"]).lower(), row["station"])

    def fetch_weather_markets(self, limit: int = 50000) -> list[WeatherMarket]:
        markets = []
        for item in self._fetch_gamma_markets(limit=limit):
            skeleton = self._parse_weather_market(item)
            if skeleton is None:
                continue
            markets.append(self._with_clob_prices(skeleton))
        return markets

    def _fetch_gamma_markets(self, limit: int) -> list[dict]:
        all_markets: list[dict] = []
        offset = 0
        page_size = min(limit, 100)
        while len(all_markets) < limit:
            try:
                response = self._get_gamma_markets_page(page_size=page_size, offset=offset)
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code == 422 and offset > 0:
                    break
                raise
            payload = response.json()
            batch = payload if isinstance(payload, list) else payload.get("data") or payload.get("markets") or []
            if not batch:
                break
            all_markets.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return all_markets[:limit]

    def _get_gamma_markets_page(self, page_size: int, offset: int) -> requests.Response:
        last_error: requests.RequestException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(
                    f"{GAMMA_URL}/markets",
                    params={"limit": page_size, "offset": offset, "active": "true", "closed": "false"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries or not _retryable_request_error(exc):
                    raise
                time.sleep(self.retry_backoff_seconds * attempt)
        raise last_error or RuntimeError("gamma market request failed")

    def _fetch_weather_event_markets(self, targets: list[WeatherEventTarget]) -> tuple[list[dict], list[str]]:
        items: list[dict] = []
        missing: list[str] = []
        for target in targets:
            try:
                event = self._fetch_gamma_event_by_slug(target.event_slug)
            except requests.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code == 404:
                    missing.append(target.event_slug)
                    continue
                raise
            markets = event.get("markets") or []
            if not markets:
                missing.append(target.event_slug)
                continue
            items.extend(item for item in markets if not item.get("closed"))
        return items, missing

    def _fetch_gamma_event_by_slug(self, slug: str) -> dict:
        last_error: requests.RequestException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.get(
                    f"{GAMMA_URL}/events/slug/{slug}",
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries or not _retryable_request_error(exc):
                    raise
                time.sleep(self.retry_backoff_seconds * attempt)
        raise last_error or RuntimeError("gamma event request failed")

    def _parse_weather_market(self, item: dict) -> WeatherMarket | None:
        question = item.get("question") or ""
        question_lower = question.lower()
        city = next((city for city in self.city_map if city in question_lower), None)
        if city is None:
            return None
        if "lowest temperature" in question_lower:
            market_family = MarketFamily.LOW_TEMP
        elif "highest temperature" in question_lower:
            market_family = MarketFamily.HIGH_TEMP
        else:
            return None
        lower_f, upper_f = _parse_temperature_bucket(question_lower)
        if lower_f is None and upper_f is None:
            return None
        threshold_f = lower_f if lower_f is not None else upper_f
        if threshold_f is None:
            return None
        token_ids = _parse_list_field(item.get("clobTokenIds"))
        station_id = _parse_resolution_station(item) or self.city_map[city]
        best_bid = _to_float(item.get("bestBid"))
        best_ask = _to_float(item.get("bestAsk"))
        if best_ask is None:
            return None
        return WeatherMarket(
            market_id=str(item.get("id")),
            question=question,
            slug=item.get("slug") or "",
            city=self.display_city.get(station_id, city.title()),
            station=station_id,
            threshold_f=float(threshold_f),
            lower_f=lower_f,
            upper_f=upper_f,
            best_bid_yes=best_bid or 0.0,
            best_ask_yes=best_ask,
            best_bid_no=None,
            best_ask_no=None,
            end_date=item.get("endDate") or "",
            resolution_source=item.get("resolutionSource") or "",
            yes_token_id=token_ids[0] if len(token_ids) >= 1 else None,
            no_token_id=token_ids[1] if len(token_ids) >= 2 else None,
            market_date=_parse_market_date(item),
            market_family=market_family,
        )

    def _with_clob_prices(self, market: WeatherMarket) -> WeatherMarket:
        if not market.yes_token_id:
            return market
        try:
            yes_book = self._fetch_book(market.yes_token_id)
            no_book = self._fetch_book(market.no_token_id) if market.no_token_id else None
        except requests.RequestException:
            return market
        best_bid = _best_bid(yes_book)
        best_ask = _best_ask(yes_book)
        if best_bid is None or best_ask is None:
            return market
        best_bid_no = _best_bid(no_book) if no_book else None
        best_ask_no = _best_ask(no_book) if no_book else None
        return WeatherMarket(
            market_id=market.market_id,
            question=market.question,
            slug=market.slug,
            city=market.city,
            station=market.station,
            threshold_f=market.threshold_f,
            lower_f=market.lower_f,
            upper_f=market.upper_f,
            best_bid_yes=best_bid,
            best_ask_yes=best_ask,
            best_bid_no=best_bid_no,
            best_ask_no=best_ask_no,
            end_date=market.end_date,
            resolution_source=market.resolution_source,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            market_date=market.market_date,
            market_family=market.market_family,
        )

    def _fetch_book(self, token_id: str) -> dict:
        response = requests.get(
            f"{CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


def _to_float(value) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _retryable_request_error(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    if response is None:
        return False
    return response.status_code == 429 or 500 <= response.status_code < 600


def _parse_temperature_bucket(question_lower: str) -> tuple[float | None, float | None]:
    between_c = re.search(r"between\s+(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°?\s*c", question_lower)
    if between_c:
        return float(between_c.group(1)), float(between_c.group(2))
    or_higher_c = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*c\s+or\s+higher", question_lower)
    if or_higher_c:
        return float(or_higher_c.group(1)), None
    or_below_c = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*c\s+or\s+below", question_lower)
    if or_below_c:
        return None, float(or_below_c.group(1))
    exact_c = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°?\s*c(?:\s+on|\?|$)", question_lower)
    if exact_c:
        value = float(exact_c.group(1))
        return value, value
    between = re.search(r"between\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*f", question_lower)
    if between:
        return float(between.group(1)), float(between.group(2))
    or_higher = re.search(r"(\d+(?:\.\d+)?)\s*°?\s*f\s+or\s+higher", question_lower)
    if or_higher:
        return float(or_higher.group(1)), None
    or_below = re.search(r"(\d+(?:\.\d+)?)\s*°?\s*f\s+or\s+below", question_lower)
    if or_below:
        return None, float(or_below.group(1))
    return None, None


def _parse_market_date(item: dict) -> date | None:
    text = " ".join(str(item.get(key) or "") for key in ("question", "slug")).lower()
    match = re.search(
        r"(?:on-?|on\s+)(january|february|march|april|may|june|july|august|september|october|november|december)[-\s]+(\d{1,2})(?:[-,\s]+(\d{4}))?",
        text,
    )
    if not match:
        return None
    month = _MONTHS[match.group(1)]
    day = int(match.group(2))
    year = int(match.group(3) or 2026)
    return date(year, month, day)


def _parse_resolution_station(item: dict) -> str | None:
    text = " ".join(str(item.get(key) or "") for key in ("resolutionSource", "description"))
    match = re.search(r"/(K[A-Z0-9]{3})(?:[./?#]|$)", text)
    if match:
        return match.group(1)
    match = re.search(r"/([A-Z]{4})(?:[./?#]|$)", text)
    if match:
        return match.group(1)
    return None


def _city_slug(city: str) -> str:
    return city.strip().lower().replace(" ", "-")


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


_MONTH_NAMES = {value: key for key, value in _MONTHS.items()}


def _best_bid(book: dict) -> float | None:
    bids = book.get("bids") or []
    prices = [_to_float(level.get("price")) for level in bids if isinstance(level, dict)]
    prices = [price for price in prices if price is not None]
    return max(prices) if prices else None


def _best_ask(book: dict) -> float | None:
    asks = book.get("asks") or []
    prices = [_to_float(level.get("price")) for level in asks if isinstance(level, dict)]
    prices = [price for price in prices if price is not None]
    return min(prices) if prices else None
