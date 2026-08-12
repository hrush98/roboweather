"""Settlement and sensor truth primitives for US high-temperature markets.

The important invariant in this module is that sources stay separate.  A
Weather Underground display value, an NWS CLI value, a METAR maximum, a
one-minute ASOS maximum, and a venue-settled bucket are related evidence, not
interchangeable spellings of ``final_high_tmpf``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import html
import re
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import pandas as pd


TRUTH_CONTRACT_VERSION = "us_high_temperature_truth_v1"


@dataclass(frozen=True)
class TemperatureTruth:
    station: str
    market_date: date
    source: str
    value_f: float | None
    captured_at_utc: str
    source_url: str | None = None
    source_value: float | None = None
    source_unit: str | None = None
    value_lower_f: float | None = None
    value_upper_f: float | None = None
    day_semantics: str | None = None
    exactness: str = "EXACT_SOURCE_VALUE"
    revision_status: str = "LATEST_OBSERVED"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market_date"] = self.market_date.isoformat()
        return payload


@dataclass(frozen=True)
class MarketBucket:
    market_id: str
    lower_f: float | None
    upper_f: float | None
    label: str


@dataclass(frozen=True)
class VenueSettlement:
    station: str
    market_date: date
    event_slug: str
    winning_market_id: str | None
    winning_bucket: MarketBucket | None
    resolution_source: str | None
    captured_at_utc: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "station": self.station,
            "market_date": self.market_date.isoformat(),
            "event_slug": self.event_slug,
            "winning_market_id": self.winning_market_id,
            "winning_bucket": self.winning_bucket.label if self.winning_bucket else None,
            "winning_lower_f": self.winning_bucket.lower_f if self.winning_bucket else None,
            "winning_upper_f": self.winning_bucket.upper_f if self.winning_bucket else None,
            "resolution_source": self.resolution_source,
            "captured_at_utc": self.captured_at_utc,
            "error": self.error,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def local_day_bounds_utc(market_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Return civil-midnight boundaries, including DST transitions."""

    zone = ZoneInfo(timezone_name)
    start = datetime.combine(market_date, time.min, tzinfo=zone)
    end = datetime.combine(market_date + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_standard_day_bounds_utc(market_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Return the midnight-LST climate day used by NWS CLI products.

    NWS CLI days stay on local *standard* time.  During daylight saving time
    this is normally 01:00-to-01:00 on the local wall clock.
    """

    zone = ZoneInfo(timezone_name)
    january = datetime(market_date.year, 1, 15, 12, tzinfo=zone)
    july = datetime(market_date.year, 7, 15, 12, tzinfo=zone)
    standard_offset = min(january.utcoffset(), july.utcoffset())
    if standard_offset is None:
        raise ValueError(f"timezone has no UTC offset: {timezone_name}")
    start = datetime.combine(market_date, time.min).replace(
        tzinfo=timezone(standard_offset)
    )
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def daily_maximum(
    frame: pd.DataFrame,
    *,
    market_date: date,
    timezone_name: str,
    timestamp_column: str = "valid",
    value_column: str = "tmpf",
    standard_day: bool = False,
) -> float | None:
    if frame.empty or timestamp_column not in frame or value_column not in frame:
        return None
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce")
    values = pd.to_numeric(frame[value_column], errors="coerce")
    bounds = local_standard_day_bounds_utc if standard_day else local_day_bounds_utc
    start, end = bounds(market_date, timezone_name)
    selected = values.loc[(timestamps >= start) & (timestamps < end)].dropna()
    return float(selected.max()) if not selected.empty else None


_WU_HIGH_RE = re.compile(
    r'<div\s+class="high-low-item\s+high">.*?'
    r'<div\s+class="value">\s*([^<]+?)\s*</div>',
    re.IGNORECASE | re.DOTALL,
)
_TEMPERATURE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*[°Â]*\s*([CF])", re.IGNORECASE)


def parse_wunderground_daily_high(page_html: str) -> tuple[float, float, str, str, float, float]:
    """Parse the source's rendered Day High value.

    Returns ``(normalized_f, source_value, source_unit, exactness, low_f,
    high_f)``. A page localized to Celsius has already rounded away some
    Fahrenheit information, so the converted interval is preserved.
    """

    match = _WU_HIGH_RE.search(page_html)
    if not match:
        raise ValueError("Weather Underground page missing rendered Day High")
    text = html.unescape(match.group(1)).strip()
    try:
        text = text.encode("latin1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    parsed = _TEMPERATURE_RE.search(text)
    if not parsed:
        raise ValueError(f"unrecognized Weather Underground Day High: {text!r}")
    source_value = float(parsed.group(1))
    unit = parsed.group(2).upper()
    if unit == "F":
        normalized = float(round_half_up(source_value))
        return normalized, source_value, unit, "EXACT_DISPLAY_F", normalized, normalized
    converted = source_value * 9.0 / 5.0 + 32.0
    lower = (source_value - 0.5) * 9.0 / 5.0 + 32.0
    upper = (source_value + 0.5) * 9.0 / 5.0 + 32.0
    return (
        float(round_half_up(converted)),
        source_value,
        unit,
        "LOCALIZED_C_ROUNDED_TO_F_INTERVAL",
        lower,
        upper,
    )


def temperature_in_bucket(value_f: float, bucket: MarketBucket) -> bool:
    value = float(round_half_up(value_f))
    if bucket.lower_f is not None and value < bucket.lower_f:
        return False
    if bucket.upper_f is not None and value > bucket.upper_f:
        return False
    return True


def truth_compatible_with_bucket(item: TemperatureTruth, bucket: MarketBucket) -> bool:
    """Whether any reportable integer in a truth interval lands in a bucket."""

    if item.value_f is None:
        return False
    if item.value_lower_f is None or item.value_upper_f is None:
        return temperature_in_bucket(item.value_f, bucket)
    lowest = round_half_up(item.value_lower_f)
    highest = round_half_up(item.value_upper_f)
    return any(temperature_in_bucket(value, bucket) for value in range(lowest, highest + 1))


def bucket_for_temperature(value_f: float, buckets: Sequence[MarketBucket]) -> MarketBucket | None:
    matches = [bucket for bucket in buckets if temperature_in_bucket(value_f, bucket)]
    if len(matches) > 1:
        raise ValueError(f"overlapping bucket ladder at {value_f}: {[item.label for item in matches]}")
    return matches[0] if matches else None


def parse_gamma_winner(
    payload: dict[str, Any],
    *,
    station: str,
    market_date: date,
    event_slug: str,
    buckets: Sequence[MarketBucket],
    captured_at_utc: str,
) -> VenueSettlement:
    gamma_markets = {str(item.get("id")): item for item in payload.get("markets") or []}
    winners: list[MarketBucket] = []
    unresolved: list[str] = []
    for bucket in buckets:
        item = gamma_markets.get(bucket.market_id)
        if item is None:
            unresolved.append(f"missing:{bucket.market_id}")
            continue
        prices = item.get("outcomePrices")
        if isinstance(prices, str):
            import json

            try:
                prices = json.loads(prices)
            except ValueError:
                prices = []
        try:
            yes, no = (Decimal(str(prices[0])), Decimal(str(prices[1])))
        except (IndexError, TypeError, ValueError):
            unresolved.append(f"unresolved:{bucket.market_id}")
            continue
        if yes == Decimal("1") and no == Decimal("0"):
            winners.append(bucket)
        elif not (yes == Decimal("0") and no == Decimal("1")):
            unresolved.append(f"unresolved:{bucket.market_id}")
    error = None
    winner = winners[0] if len(winners) == 1 and not unresolved else None
    if len(winners) != 1 or unresolved:
        error = f"expected one fully resolved winner; winners={len(winners)} issues={','.join(unresolved)}"
    return VenueSettlement(
        station=station,
        market_date=market_date,
        event_slug=event_slug,
        winning_market_id=winner.market_id if winner else None,
        winning_bucket=winner,
        resolution_source=str(payload.get("resolutionSource") or "") or None,
        captured_at_utc=captured_at_utc,
        error=error,
    )


def pairwise_mismatch_rows(
    truths: Iterable[TemperatureTruth],
    *,
    venue: Iterable[VenueSettlement] = (),
) -> list[dict[str, Any]]:
    records = [item for item in truths if item.value_f is not None and item.error is None]
    venue_map = {(item.station, item.market_date): item for item in venue}
    sources = sorted({item.source for item in records})
    rows: list[dict[str, Any]] = []
    for station in sorted({item.station for item in records}):
        station_records = [item for item in records if item.station == station]
        source_maps = {
            source: {item.market_date: item for item in station_records if item.source == source}
            for source in sources
        }
        for index, left in enumerate(sources):
            for right in sources[index + 1 :]:
                common = sorted(set(source_maps[left]) & set(source_maps[right]))
                if not common:
                    continue
                differences = [
                    float(source_maps[left][day].value_f) - float(source_maps[right][day].value_f)
                    for day in common
                ]
                venue_days = 0
                left_flips = right_flips = disagreement_flips = 0
                for day in common:
                    settlement = venue_map.get((station, day))
                    if settlement is None or settlement.winning_bucket is None:
                        continue
                    venue_days += 1
                    left_matches = truth_compatible_with_bucket(source_maps[left][day], settlement.winning_bucket)
                    right_matches = truth_compatible_with_bucket(source_maps[right][day], settlement.winning_bucket)
                    left_flips += int(not left_matches)
                    right_flips += int(not right_matches)
                    disagreement_flips += int(left_matches != right_matches)
                rows.append(
                    {
                        "station": station,
                        "source_left": left,
                        "source_right": right,
                        "dates_compared": len(common),
                        "exact_matches": sum(diff == 0 for diff in differences),
                        "one_degree_or_more": sum(abs(diff) >= 1 for diff in differences),
                        "two_degrees_or_more": sum(abs(diff) >= 2 for diff in differences),
                        "mean_signed_difference_f": sum(differences) / len(differences),
                        "mean_absolute_difference_f": sum(abs(diff) for diff in differences) / len(differences),
                        "max_absolute_difference_f": max(abs(diff) for diff in differences),
                        "venue_dates_compared": venue_days,
                        "left_venue_bucket_mismatches": left_flips,
                        "right_venue_bucket_mismatches": right_flips,
                        "pair_bucket_classification_disagreements": disagreement_flips,
                    }
                )
    return rows


def truth_matrix_rows(
    truths: Iterable[TemperatureTruth], venue: Iterable[VenueSettlement]
) -> list[dict[str, Any]]:
    truth_map = {(item.station, item.market_date, item.source): item for item in truths}
    venue_map = {(item.station, item.market_date): item for item in venue}
    keys = sorted({(item.station, item.market_date) for item in truths} | set(venue_map))
    sources = sorted({item.source for item in truths})
    rows: list[dict[str, Any]] = []
    for station, day in keys:
        settlement = venue_map.get((station, day))
        row: dict[str, Any] = {
            "station": station,
            "market_date": day.isoformat(),
            "venue_winning_bucket": settlement.winning_bucket.label if settlement and settlement.winning_bucket else None,
            "venue_error": settlement.error if settlement else "MISSING",
        }
        for source in sources:
            item = truth_map.get((station, day, source))
            row[f"{source}_f"] = item.value_f if item else None
            row[f"{source}_exactness"] = item.exactness if item else None
            row[f"{source}_error"] = item.error if item else "MISSING"
            if item and item.value_f is not None and settlement and settlement.winning_bucket:
                row[f"{source}_matches_venue_bucket"] = truth_compatible_with_bucket(
                    item, settlement.winning_bucket
                )
            else:
                row[f"{source}_matches_venue_bucket"] = None
        rows.append(row)
    return rows
