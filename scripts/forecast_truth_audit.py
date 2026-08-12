#!/usr/bin/env python3
"""Reproduce the F0 US high-temperature settlement/sensor truth audit."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, timedelta
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_trader.forecasting.truth import (  # noqa: E402
    MarketBucket,
    TemperatureTruth,
    TRUTH_CONTRACT_VERSION,
    VenueSettlement,
    daily_maximum,
    pairwise_mismatch_rows,
    parse_gamma_winner,
    parse_wunderground_daily_high,
    truth_matrix_rows,
    utc_now_iso,
)
from weather_trader.stations.iem_asos_client import IEMASOSClient  # noqa: E402
from weather_trader.stations.metadata import get_station  # noqa: E402


GAMMA_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
IEM_CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"
IEM_ONE_MINUTE_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
SOURCE_ORDER = ("NWS_CLI", "WUNDERGROUND_DISPLAY", "IEM_ROUTINE_SPECIAL_METAR", "NCEI_ASOS_1MIN")
PROXY_CANDIDATE_ORDER = ("IEM_ROUTINE_SPECIAL_METAR", "NWS_CLI", "NCEI_ASOS_1MIN")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Research SQLite database with markets")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--station", action="append", dest="stations")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument(
        "--no-online",
        action="store_true",
        help="Write a schema/coverage report without fetching online sources.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.end_date < args.start_date:
        raise SystemExit("--end-date precedes --start-date")
    markets = load_market_cohort(args.db, args.start_date, args.end_date, args.stations)
    if not markets:
        raise SystemExit("no US HIGH_TEMP markets in requested cohort")
    captured_at = utc_now_iso()
    session = requests.Session()
    session.headers.update({"User-Agent": "RoboWeather research truth audit/1.0"})

    truths: list[TemperatureTruth] = []
    venue: list[VenueSettlement] = []
    if args.no_online:
        truths.extend(missing_truth_rows(markets, "online collection disabled", captured_at))
        venue.extend(missing_venue_rows(markets, "online collection disabled", captured_at))
    else:
        truths.extend(fetch_iem_routine(markets, captured_at))
        truths.extend(fetch_iem_one_minute(markets, session, args.timeout, captured_at))
        truths.extend(fetch_cli(markets, session, args.timeout, captured_at))
        for group in markets.values():
            truths.append(fetch_wunderground(group, session, args.timeout, captured_at))
            venue.append(fetch_venue(group, session, args.timeout, captured_at))
            if args.request_delay:
                time.sleep(args.request_delay)

    write_report(args, markets, truths, venue, captured_at)
    return 0


def load_market_cohort(
    database: Path,
    start_date: date,
    end_date: date,
    stations: list[str] | None,
) -> dict[tuple[str, date], dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        params: list[Any] = [start_date.isoformat(), end_date.isoformat()]
        station_sql = ""
        if stations:
            normalized = [item.upper() for item in stations]
            station_sql = f" and station in ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
        rows = connection.execute(
            f"""
            select market_id, station, market_date, slug, lower_f, upper_f,
                   resolution_source
            from markets
            where market_family = 'HIGH_TEMP'
              and station like 'K%'
              and market_date between ? and ?
              {station_sql}
            order by station, market_date, coalesce(lower_f, upper_f), market_id
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    groups: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        day = date.fromisoformat(str(row["market_date"]))
        key = (str(row["station"]), day)
        group = groups.setdefault(
            key,
            {
                "station": key[0],
                "market_date": day,
                "resolution_source": str(row["resolution_source"] or ""),
                "event_slug": event_slug_from_market_slug(str(row["slug"])),
                "buckets": [],
            },
        )
        group["buckets"].append(
            MarketBucket(
                market_id=str(row["market_id"]),
                lower_f=float(row["lower_f"]) if row["lower_f"] is not None else None,
                upper_f=float(row["upper_f"]) if row["upper_f"] is not None else None,
                label=bucket_label(row["lower_f"], row["upper_f"]),
            )
        )
    return groups


def event_slug_from_market_slug(slug: str) -> str:
    value = re.sub(
        r"-(?:-?\d+(?:\.\d+)?forbelow|-?\d+(?:\.\d+)?forhigher|-?\d+(?:\.\d+)?--?-?\d+(?:\.\d+)?f)$",
        "",
        slug,
    )
    if value == slug:
        raise ValueError(f"cannot derive HIGH_TEMP event slug from {slug}")
    return value


def bucket_label(lower: Any, upper: Any) -> str:
    if lower is None:
        return f"<={float(upper):g}F"
    if upper is None:
        return f">={float(lower):g}F"
    return f"{float(lower):g}-{float(upper):g}F"


def cohort_dates(markets: dict[tuple[str, date], dict[str, Any]], station: str) -> list[date]:
    return sorted(day for candidate, day in markets if candidate == station)


def fetch_iem_routine(
    markets: dict[tuple[str, date], dict[str, Any]], captured_at: str
) -> list[TemperatureTruth]:
    client = IEMASOSClient(max_retries=3, retry_backoff_seconds=1.0)
    output: list[TemperatureTruth] = []
    for station_id in sorted({key[0] for key in markets}):
        days = cohort_dates(markets, station_id)
        metadata = get_station(station_id)
        error = None
        frame = pd.DataFrame()
        for attempt in range(3):
            try:
                frame = client.fetch_observations(
                    station_id, days[0] - timedelta(days=1), days[-1] + timedelta(days=2)
                )
                error = None
                break
            except Exception as exc:  # report source failures instead of substituting
                error = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    time.sleep(attempt + 1)
        for day in days:
            value = daily_maximum(
                frame,
                market_date=day,
                timezone_name=metadata.timezone,
            )
            output.append(
                TemperatureTruth(
                    station_id,
                    day,
                    "IEM_ROUTINE_SPECIAL_METAR",
                    value,
                    captured_at,
                    source_url="https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
                    source_value=value,
                    source_unit="F",
                    day_semantics="00:00-24:00 civil station-local time (current research behavior)",
                    exactness="ROUTINE_SPECIAL_REPORT_MAXIMUM_PROXY",
                    error=error or (None if value is not None else "NO_OBSERVATIONS"),
                )
            )
    return output


def fetch_iem_one_minute(
    markets: dict[tuple[str, date], dict[str, Any]],
    session: requests.Session,
    timeout: float,
    captured_at: str,
) -> list[TemperatureTruth]:
    output: list[TemperatureTruth] = []
    for station_id in sorted({key[0] for key in markets}):
        days = cohort_dates(markets, station_id)
        metadata = get_station(station_id)
        start = days[0] - timedelta(days=1)
        end = days[-1] + timedelta(days=2)
        params = {
            "station": station_id.removeprefix("K"),
            "vars": "tmpf",
            "sts": f"{start.isoformat()}T00:00Z",
            "ets": f"{end.isoformat()}T00:00Z",
            "sample": "1min",
            "what": "download",
            "tz": "UTC",
            "delim": "comma",
            "gis": "no",
        }
        try:
            response = session.get(IEM_ONE_MINUTE_URL, params=params, timeout=timeout)
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text), na_values=["M"])
            valid_column = next(item for item in frame if item.lower().startswith("valid"))
            frame = frame.rename(columns={valid_column: "valid"})
            error = None
            source_url = response.url
        except Exception as exc:
            frame = pd.DataFrame()
            error = f"{type(exc).__name__}: {exc}"
            source_url = IEM_ONE_MINUTE_URL
        for day in days:
            value = daily_maximum(
                frame,
                market_date=day,
                timezone_name=metadata.timezone,
            )
            output.append(
                TemperatureTruth(
                    station_id,
                    day,
                    "NCEI_ASOS_1MIN",
                    value,
                    captured_at,
                    source_url=source_url,
                    source_value=value,
                    source_unit="F",
                    day_semantics="00:00-24:00 civil station-local time",
                    exactness="NCEI_ASOS_ONE_MINUTE_ARCHIVE_MAXIMUM",
                    error=error or (None if value is not None else "NO_OBSERVATIONS_OR_ARCHIVE_LAG"),
                )
            )
    return output


def fetch_cli(
    markets: dict[tuple[str, date], dict[str, Any]],
    session: requests.Session,
    timeout: float,
    captured_at: str,
) -> list[TemperatureTruth]:
    output: list[TemperatureTruth] = []
    for station_id in sorted({key[0] for key in markets}):
        days = cohort_dates(markets, station_id)
        by_date: dict[date, dict[str, Any]] = {}
        errors: list[str] = []
        source_urls: list[str] = []
        for year in sorted({item.year for item in days}):
            try:
                response = session.get(
                    IEM_CLI_URL, params={"station": station_id, "year": year}, timeout=timeout
                )
                response.raise_for_status()
                source_urls.append(response.url)
                for row in response.json().get("results") or []:
                    by_date[date.fromisoformat(str(row["valid"]))] = row
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        for day in days:
            row = by_date.get(day)
            raw = row.get("high") if row else None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
            output.append(
                TemperatureTruth(
                    station_id,
                    day,
                    "NWS_CLI",
                    value,
                    captured_at,
                    source_url=str(row.get("link")) if row and row.get("link") else (source_urls[0] if source_urls else IEM_CLI_URL),
                    source_value=value,
                    source_unit="F",
                    day_semantics="00:00-24:00 local standard time",
                    exactness="PARSED_OFFICIAL_NWS_CLI",
                    revision_status="LATEST_IEM_PARSED_CLI",
                    error="; ".join(errors) or (None if value is not None else "CLI_NOT_AVAILABLE_FOR_STATION_DATE"),
                )
            )
    return output


def fetch_wunderground(
    group: dict[str, Any],
    session: requests.Session,
    timeout: float,
    captured_at: str,
) -> TemperatureTruth:
    station = str(group["station"])
    day: date = group["market_date"]
    base_url = str(group["resolution_source"] or "").rstrip("/")
    url = f"{base_url}/date/{day.year}-{day.month}-{day.day}" if base_url else ""
    try:
        if not url:
            raise ValueError("market has no Weather Underground resolution URL")
        response = session.get(url, timeout=timeout, headers={"Accept-Language": "en-US,en;q=0.9"})
        response.raise_for_status()
        value_f, source_value, source_unit, exactness, value_lower_f, value_upper_f = parse_wunderground_daily_high(response.text)
        error = None
        source_url = response.url
    except Exception as exc:
        value_f = source_value = None
        value_lower_f = value_upper_f = None
        source_unit = None
        exactness = "UNAVAILABLE"
        error = f"{type(exc).__name__}: {exc}"
        source_url = url or None
    return TemperatureTruth(
        station,
        day,
        "WUNDERGROUND_DISPLAY",
        value_f,
        captured_at,
        source_url=source_url,
        source_value=source_value,
        source_unit=source_unit,
        value_lower_f=value_lower_f,
        value_upper_f=value_upper_f,
        day_semantics="Weather Underground rendered daily-history day",
        exactness=exactness,
        revision_status="CAPTURED_PAGE_LATEST_NOT_IMMUTABLE",
        error=error,
    )


def fetch_venue(
    group: dict[str, Any],
    session: requests.Session,
    timeout: float,
    captured_at: str,
) -> VenueSettlement:
    station = str(group["station"])
    day: date = group["market_date"]
    slug = str(group["event_slug"])
    try:
        response = session.get(GAMMA_EVENT_URL.format(slug=slug), timeout=timeout)
        response.raise_for_status()
        return parse_gamma_winner(
            response.json(),
            station=station,
            market_date=day,
            event_slug=slug,
            buckets=group["buckets"],
            captured_at_utc=captured_at,
        )
    except Exception as exc:
        return VenueSettlement(
            station,
            day,
            slug,
            None,
            None,
            str(group["resolution_source"] or "") or None,
            captured_at,
            f"{type(exc).__name__}: {exc}",
        )


def missing_truth_rows(
    markets: dict[tuple[str, date], dict[str, Any]], error: str, captured_at: str
) -> list[TemperatureTruth]:
    return [
        TemperatureTruth(station, day, source, None, captured_at, error=error, exactness="UNAVAILABLE")
        for station, day in markets
        for source in SOURCE_ORDER
    ]


def missing_venue_rows(
    markets: dict[tuple[str, date], dict[str, Any]], error: str, captured_at: str
) -> list[VenueSettlement]:
    return [
        VenueSettlement(
            group["station"], group["market_date"], group["event_slug"], None, None,
            group["resolution_source"] or None, captured_at, error,
        )
        for group in markets.values()
    ]


def write_report(
    args: argparse.Namespace,
    markets: dict[tuple[str, date], dict[str, Any]],
    truths: list[TemperatureTruth],
    venue: list[VenueSettlement],
    captured_at: str,
) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    truth_payload = [item.to_dict() for item in sorted(truths, key=lambda x: (x.station, x.market_date, x.source))]
    venue_payload = [item.to_dict() for item in sorted(venue, key=lambda x: (x.station, x.market_date))]
    mismatch_rows = pairwise_mismatch_rows(truths, venue=venue)
    matrix_rows = truth_matrix_rows(truths, venue)
    write_csv(args.out / "truth_observations.csv", truth_payload)
    write_csv(args.out / "venue_settlements.csv", venue_payload)
    write_csv(args.out / "station_mismatches.csv", mismatch_rows)
    write_csv(args.out / "station_date_matrix.csv", matrix_rows)

    coverage = {
        source: {
            "available": sum(item.source == source and item.value_f is not None and item.error is None for item in truths),
            "requested": sum(item.source == source for item in truths),
            "errors": Counter(item.error for item in truths if item.source == source and item.error),
        }
        for source in SOURCE_ORDER
    }
    venue_available = sum(item.winning_bucket is not None and item.error is None for item in venue)
    bucket_mismatches = {
        source: sum(
            row.get(f"{source}_matches_venue_bucket") is False for row in matrix_rows
        )
        for source in SOURCE_ORDER
    }
    comparable = {
        source: sum(
            row.get(f"{source}_matches_venue_bucket") is not None for row in matrix_rows
        )
        for source in SOURCE_ORDER
    }
    best_source = select_numeric_proxy(bucket_mismatches, comparable)
    proxy_passes = bool(best_source and bucket_mismatches[best_source] == 0)
    result = {
        "schema_version": 1,
        "contract_version": TRUTH_CONTRACT_VERSION,
        "captured_at_utc": captured_at,
        "database": str(args.db),
        "cohort": {
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "station_dates": len(markets),
            "stations": sorted({item[0] for item in markets}),
        },
        "coverage": coverage,
        "venue": {"available": venue_available, "requested": len(venue)},
        "venue_bucket_mismatches": bucket_mismatches,
        "venue_bucket_comparable": comparable,
        "decision": {
            "historical_training_target": (
                f"VENUE_BUCKET_AUTHORITATIVE_WITH_{best_source}_NUMERIC_PROXY_{TRUTH_CONTRACT_VERSION}"
                if proxy_passes
                else "NO_NUMERIC_PROXY_ACCEPTED"
            ),
            "weather_underground_role": "CAPTURED_RESOLUTION_SOURCE_AND_VENUE_MAPPING_EVIDENCE_NOT_AN_IMMUTABLE_BULK_ARCHIVE",
            "live_high_so_far": (
                "IEM_ROUTINE_SPECIAL_REPORT_MAX_SO_FAR_WITH_SOURCE_AVAILABILITY_AND_REVISION_FLAGS"
                if best_source == "IEM_ROUTINE_SPECIAL_METAR" and proxy_passes
                else "PUBLIC_REPORT_INTERVAL_NOT_EXACT_RESOLUTION_COMPATIBLE_HIGH"
            ),
            "fallback": "FAIL_CLOSED_NO_SILENT_SUBSTITUTION",
            "backfill": (
                "PROVENANCE_AND_VENUE_BUCKET_BACKFILL_REQUIRED; NO_NUMERIC_RELABEL_FOR_MATCHED_COHORT"
                if best_source == "IEM_ROUTINE_SPECIAL_METAR" and proxy_passes
                else "NUMERIC_AND_PROVENANCE_BACKFILL_REQUIRED_BEFORE_RETRAINING_OR_PROMOTION"
            ),
        },
        "artifacts": {
            "truth_observations": "truth_observations.csv",
            "venue_settlements": "venue_settlements.csv",
            "station_mismatches": "station_mismatches.csv",
            "station_date_matrix": "station_date_matrix.csv",
        },
    }
    (args.out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=list) + "\n")
    (args.out / "report.md").write_text(render_markdown(result, mismatch_rows))


def select_numeric_proxy(
    bucket_mismatches: dict[str, int], comparable: dict[str, int]
) -> str | None:
    """Select only an eligible immutable numeric source with enough evidence."""

    return min(
        (source for source in PROXY_CANDIDATE_ORDER if comparable.get(source, 0) >= 30),
        key=lambda source: (
            bucket_mismatches[source] / comparable[source],
            PROXY_CANDIDATE_ORDER.index(source),
        ),
        default=None,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    fields = list(rows[0])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(result: dict[str, Any], mismatch_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# US High-Temperature Truth Audit",
        "",
        f"Contract: `{result['contract_version']}`",
        f"Cohort: {result['cohort']['start_date']} through {result['cohort']['end_date']} "
        f"({result['cohort']['station_dates']} station-dates)",
        "",
        "## Coverage",
        "",
        "| source | available | requested | venue-bucket mismatches |",
        "| --- | ---: | ---: | ---: |",
    ]
    for source in SOURCE_ORDER:
        coverage = result["coverage"][source]
        lines.append(
            f"| {source} | {coverage['available']} | {coverage['requested']} | "
            f"{result['venue_bucket_mismatches'][source]} |"
        )
    lines.extend(
        [
            f"| POLYMARKET_VENUE_BUCKET | {result['venue']['available']} | {result['venue']['requested']} | — |",
            "",
            "## Canonical Decision",
            "",
            f"- Historical training target: `{result['decision']['historical_training_target']}`. Venue settlement is authoritative; the accepted numeric proxy is cohort-bounded and separately versioned.",
            "- Weather Underground: preserve the rendered source value, capture time, unit, and page URL. It is resolution-source evidence, but the public page is mutable/localized and is not an immutable bulk training archive.",
            f"- Live high-so-far: `{result['decision']['live_high_so_far']}`. It is a report-stream maximum, not the latent physical ASOS maximum.",
            f"- Backfill: `{result['decision']['backfill']}`. Preserve source identity; never overwrite IEM rows as if they were CLI or exact venue temperatures.",
            "- Missing authoritative evidence fails closed. No silent source substitution is allowed.",
            "",
            "## Pairwise Mismatch Summary",
            "",
            "Detailed station/date and pairwise tables are in the CSV artifacts. The table below is sorted by station and source pair.",
            "",
            "| station | left | right | n | midpoint equal | >=1F | >=2F | venue pair-class disagreements |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in mismatch_rows:
        lines.append(
            f"| {row['station']} | {row['source_left']} | {row['source_right']} | "
            f"{row['dates_compared']} | {row['exact_matches']} | {row['one_degree_or_more']} | "
            f"{row['two_degrees_or_more']} | {row['pair_bucket_classification_disagreements']} |"
        )
    lines.extend(
        [
            "",
            "## Semantics",
            "",
            "NWS CLI is the official calendar-day product and uses local standard time. The current RoboWeather IEM resolver instead takes the maximum routine/special report over a civil local day. NCEI one-minute ASOS is a delayed historical archive. Weather Underground values are parsed from the rendered `Day High` field and localized Celsius captures are explicitly marked as rounded conversions. Polymarket provides the authoritative winning bucket, not an exact temperature.",
            "",
            "This audit resolves the training-target contract only. It does not establish forecast information edge or authorize funded trading.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
