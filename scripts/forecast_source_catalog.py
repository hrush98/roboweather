#!/usr/bin/env python3
"""Collect and report F1 causal forecast-source vintages."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_trader.config import DEFAULT_STATE_DIR
from weather_trader.forecasting.source_catalog import (
    ArtifactRequest,
    BoundedCollector,
    ForecastSourceCatalog,
    MarketTarget,
    SOURCE_CONTRACTS,
    SOURCE_VINTAGE_CONTRACT_VERSION,
    canonical_json,
    iso_utc,
    manifest_requests,
)
from weather_trader.stations.metadata import get_station


DEFAULT_ROOT = DEFAULT_STATE_DIR / "forecast_sources"
DEFAULT_CATALOG = DEFAULT_ROOT / "catalog.sqlite"
DEFAULT_RAW = DEFAULT_ROOT / "raw"
NBM_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_blend.pl"
HRRR_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
IEM_ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--research-db", type=Path)
    parser.add_argument("--manifest", type=Path, action="append", default=[])
    parser.add_argument(
        "--source",
        action="append",
        choices=[contract.source_id for contract in SOURCE_CONTRACTS],
        help="Repeat to restrict collection; defaults to NBM, HRRR, and IEM.",
    )
    parser.add_argument("--as-of", type=parse_timestamp)
    parser.add_argument("--target-days", type=int, default=2)
    parser.add_argument("--lead-stride-hours", type=int, default=3)
    parser.add_argument("--max-artifacts", type=int, default=120)
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = args.as_of or datetime.now(UTC)
    sources = set(args.source or ["nbm_v5", "hrrr_v4", "iem_metar"])
    targets: list[MarketTarget] = []
    if args.research_db:
        targets = load_targets(
            args.research_db,
            as_of_utc=as_of,
            target_days=args.target_days,
        )
    requests_to_fetch: list[ArtifactRequest] = []
    if targets:
        requests_to_fetch.extend(
            plan_operational_requests(
                targets,
                sources=sources,
                as_of_utc=as_of,
                lead_stride_hours=args.lead_stride_hours,
            )
        )
    for manifest in args.manifest:
        requests_to_fetch.extend(manifest_requests(manifest, allowed_sources=sources))
    requests_to_fetch = sorted(
        requests_to_fetch,
        key=lambda item: (
            item.source_id,
            item.station or "",
            item.market_date or "",
            item.cycle_at_utc or "",
            item.source_key,
        ),
    )

    if args.plan_only:
        print(
            json.dumps(
                {
                    "contract_version": SOURCE_VINTAGE_CONTRACT_VERSION,
                    "as_of_utc": as_of.isoformat(),
                    "targets": len(targets),
                    "requests": len(requests_to_fetch),
                    "by_source": count_by_source(requests_to_fetch),
                    "sample": [request_to_dict(item) for item in requests_to_fetch[:10]],
                },
                indent=2,
            )
        )
        return 0

    with ForecastSourceCatalog(args.catalog, args.raw_dir) as catalog:
        catalog.upsert_targets(targets)
        collection_summary = None
        if not args.report_only:
            if not requests_to_fetch:
                raise SystemExit("no requests planned; supply --research-db and/or --manifest")
            collection_summary = BoundedCollector(
                catalog,
                timeout_seconds=args.timeout,
            ).collect(
                requests_to_fetch,
                max_artifacts=args.max_artifacts,
                max_bytes=args.max_bytes,
            )
        coverage = catalog.coverage()

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "as_of_utc": as_of.isoformat(),
        "catalog": str(args.catalog),
        "raw_dir": str(args.raw_dir),
        "targets_loaded": len(targets),
        "planned_requests": len(requests_to_fetch),
        "planned_by_source": count_by_source(requests_to_fetch),
        "collection": collection_summary,
        "coverage": coverage,
        "limitations": source_limitations(coverage),
    }
    if args.report_out:
        write_report(args.report_out, report)
    print(json.dumps(report, indent=2))
    return 0 if collection_summary is None or collection_summary["status"] != "FAILED" else 2


def load_targets(
    database: Path,
    *,
    as_of_utc: datetime,
    target_days: int,
) -> list[MarketTarget]:
    if target_days < 0:
        raise ValueError("target_days must be nonnegative")
    start = as_of_utc.date() - timedelta(days=1)
    end = as_of_utc.date() + timedelta(days=target_days)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """select station, market_date, min(discovered_at) first_listing
               from markets
               where market_family='HIGH_TEMP'
                 and station like 'K%'
                 and market_date between ? and ?
               group by station, market_date
               order by market_date, station""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        connection.close()
    targets: list[MarketTarget] = []
    for row in rows:
        station = get_station(str(row["station"]))
        listing = parse_timestamp(str(row["first_listing"]))
        if listing > as_of_utc:
            continue
        targets.append(
            MarketTarget(
                station=station.station,
                market_date=date.fromisoformat(str(row["market_date"])),
                first_supported_listing_at_utc=listing.isoformat(),
                listing_source="research_markets.discovered_at",
                latitude=station.latitude,
                longitude=station.longitude,
            )
        )
    return targets


def plan_operational_requests(
    targets: Iterable[MarketTarget],
    *,
    sources: set[str],
    as_of_utc: datetime,
    lead_stride_hours: int,
) -> list[ArtifactRequest]:
    if lead_stride_hours <= 0:
        raise ValueError("lead_stride_hours must be positive")
    output: list[ArtifactRequest] = []
    nbm_cycle = latest_cycle(as_of_utc, cycle_hours=1, release_lag=timedelta(hours=1, minutes=45))
    hrrr_cycle = latest_cycle(as_of_utc, cycle_hours=6, release_lag=timedelta(hours=2))
    for target in targets:
        listing = parse_timestamp(target.first_supported_listing_at_utc)
        if listing > as_of_utc:
            continue
        if "nbm_v5" in sources:
            output.extend(
                nbm_requests(target, nbm_cycle, stride_hours=lead_stride_hours)
            )
        if "hrrr_v4" in sources:
            output.extend(
                hrrr_requests(target, hrrr_cycle, stride_hours=lead_stride_hours)
            )
        if "iem_metar" in sources:
            request = iem_request(target, as_of_utc)
            if request is not None:
                output.append(request)
    return output


def latest_cycle(as_of_utc: datetime, *, cycle_hours: int, release_lag: timedelta) -> datetime:
    eligible = as_of_utc.astimezone(UTC) - release_lag
    hour = eligible.hour - (eligible.hour % cycle_hours)
    return eligible.replace(hour=hour, minute=0, second=0, microsecond=0)


def target_valid_times(target: MarketTarget, stride_hours: int) -> list[datetime]:
    station = get_station(target.station)
    zone = ZoneInfo(station.timezone)
    start = datetime.combine(target.market_date, time.min, tzinfo=zone)
    end = datetime.combine(target.market_date + timedelta(days=1), time.min, tzinfo=zone)
    output: list[datetime] = []
    cursor = start
    while cursor < end:
        output.append(cursor.astimezone(UTC))
        cursor += timedelta(hours=stride_hours)
    return output


def nbm_requests(
    target: MarketTarget,
    cycle: datetime,
    *,
    stride_hours: int,
) -> list[ArtifactRequest]:
    output: list[ArtifactRequest] = []
    for valid in target_valid_times(target, stride_hours):
        lead = int((valid - cycle).total_seconds() // 3600)
        if lead < 1 or lead > 264:
            continue
        source_key = f"nbm_v5/{cycle:%Y%m%d%H}/{target.station}/f{lead:03d}"
        params = {
            "dir": f"/blend.{cycle:%Y%m%d}/{cycle:%H}/core",
            "file": f"blend.t{cycle:%H}z.core.f{lead:03d}.co.grib2",
            "var_TMP": "on",
            "var_TMAX": "on",
            "lev_2_m_above_ground": "on",
            "subregion": "",
            **bbox_params(target, 0.08),
        }
        output.append(
            ArtifactRequest(
                source_id="nbm_v5",
                source_key=source_key,
                url=NBM_FILTER_URL,
                station=target.station,
                market_date=target.market_date.isoformat(),
                cycle_at_utc=cycle.isoformat(),
                valid_start_at_utc=valid.isoformat(),
                valid_end_at_utc=(valid + timedelta(hours=stride_hours)).isoformat(),
                params=params,
                metadata={
                    "fields": ["TMP:2m:mean", "TMP:2m:ensemble_stddev", "TMAX:2m"],
                    "first_supported_listing_at_utc": target.first_supported_listing_at_utc,
                    "listing_source": target.listing_source,
                },
            )
        )
    return output


def hrrr_requests(
    target: MarketTarget,
    cycle: datetime,
    *,
    stride_hours: int,
) -> list[ArtifactRequest]:
    output: list[ArtifactRequest] = []
    for valid in target_valid_times(target, stride_hours):
        lead = int((valid - cycle).total_seconds() // 3600)
        if lead < 0 or lead > 48:
            continue
        source_key = f"hrrr_v4/{cycle:%Y%m%d%H}/{target.station}/f{lead:02d}"
        params = {
            "dir": f"/hrrr.{cycle:%Y%m%d}/conus",
            "file": f"hrrr.t{cycle:%H}z.wrfsfcf{lead:02d}.grib2",
            "var_TMP": "on",
            "var_DPT": "on",
            "var_RH": "on",
            "var_UGRD": "on",
            "var_VGRD": "on",
            "var_GUST": "on",
            "var_TCDC": "on",
            "var_DSWRF": "on",
            "lev_2_m_above_ground": "on",
            "lev_10_m_above_ground": "on",
            "lev_surface": "on",
            "lev_entire_atmosphere": "on",
            "subregion": "",
            **bbox_params(target, 0.08),
        }
        output.append(
            ArtifactRequest(
                source_id="hrrr_v4",
                source_key=source_key,
                url=HRRR_FILTER_URL,
                station=target.station,
                market_date=target.market_date.isoformat(),
                cycle_at_utc=cycle.isoformat(),
                valid_start_at_utc=valid.isoformat(),
                valid_end_at_utc=valid.isoformat(),
                params=params,
                metadata={
                    "fields": ["TMP", "DPT", "RH", "UGRD", "VGRD", "GUST", "TCDC", "DSWRF"],
                    "first_supported_listing_at_utc": target.first_supported_listing_at_utc,
                    "listing_source": target.listing_source,
                },
            )
        )
    return output


def iem_request(target: MarketTarget, as_of_utc: datetime) -> ArtifactRequest | None:
    station = get_station(target.station)
    zone = ZoneInfo(station.timezone)
    start = datetime.combine(target.market_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(
        target.market_date + timedelta(days=1), time.min, tzinfo=zone
    ).astimezone(UTC)
    query_end = min(end, as_of_utc.astimezone(UTC))
    if query_end <= start:
        return None
    params: dict[str, Any] = {
        "station": target.station.removeprefix("K"),
        "year1": str(start.year),
        "month1": str(start.month),
        "day1": str(start.day),
        "hour1": str(start.hour),
        "minute1": str(start.minute),
        "year2": str(query_end.year),
        "month2": str(query_end.month),
        "day2": str(query_end.day),
        "hour2": str(query_end.hour),
        "minute2": str(query_end.minute),
        "tz": "UTC",
        "format": "onlycomma",
        "latlon": "no",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": ["1", "2"],
        "data": ["tmpf", "dwpf", "sknt", "drct", "relh", "mslp", "skyc1", "skyc2", "skyc3", "skyc4"],
    }
    captured_bucket = query_end.replace(minute=0, second=0, microsecond=0)
    return ArtifactRequest(
        source_id="iem_metar",
        source_key=f"iem_metar/{target.station}/{target.market_date}/{captured_bucket:%Y%m%d%H}",
        url=IEM_ASOS_URL,
        station=target.station,
        market_date=target.market_date.isoformat(),
        valid_start_at_utc=start.isoformat(),
        valid_end_at_utc=query_end.isoformat(),
        params=params,
        metadata={
            "fields": params["data"],
            "report_types": ["routine", "special"],
            "first_supported_listing_at_utc": target.first_supported_listing_at_utc,
            "listing_source": target.listing_source,
        },
    )


def bbox_params(target: MarketTarget, radius: float) -> dict[str, str]:
    return {
        "leftlon": f"{target.longitude - radius:.4f}",
        "rightlon": f"{target.longitude + radius:.4f}",
        "toplat": f"{target.latitude + radius:.4f}",
        "bottomlat": f"{target.latitude - radius:.4f}",
    }


def source_limitations(coverage: dict[str, Any]) -> list[str]:
    by_source = {row["source_id"]: row for row in coverage["sources"]}
    limitations = [
        "NOAA/IEM replay availability is first successful collector observation; Last-Modified is provenance only.",
        "research_markets.discovered_at is the current collection-start bound, not an authoritative venue listing timestamp.",
        "F1 establishes collection/replay substrate only; it makes no forecast-skill or trading-edge claim.",
    ]
    if not by_source.get("weathernext_2", {}).get("artifacts"):
        limitations.append(
            "WeatherNext 2 has no collected artifacts; approved Google access and an ingestion_time manifest are required."
        )
    if not by_source.get("rrfs", {}).get("artifacts"):
        limitations.append(
            "RRFS remains fail-closed until an operational product/version and endpoint are frozen in a manifest."
        )
    return limitations


def count_by_source(requests_to_fetch: Iterable[ArtifactRequest]) -> dict[str, int]:
    output: dict[str, int] = {}
    for item in requests_to_fetch:
        output[item.source_id] = output.get(item.source_id, 0) + 1
    return dict(sorted(output.items()))


def request_to_dict(item: ArtifactRequest) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source_key": item.source_key,
        "station": item.station,
        "market_date": item.market_date,
        "cycle_at_utc": item.cycle_at_utc,
        "valid_start_at_utc": item.valid_start_at_utc,
        "url": item.url,
        "params": dict(item.params),
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# Forecast Source Catalog Coverage",
        "",
        f"- Contract: `{SOURCE_VINTAGE_CONTRACT_VERSION}`",
        f"- Generated: `{report['generated_at_utc']}`",
        f"- Targets loaded: {report['targets_loaded']}",
        f"- Planned requests: {report['planned_requests']}",
        "",
        "## Source Coverage",
        "",
        "| Source | Artifacts | Bytes | Station/dates | Revised keys | Successful attempts | Failed attempts | Latest causal availability |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["coverage"]["sources"]:
        lines.append(
            f"| {row['source_id']} | {row['artifacts']} | {row['bytes']} | "
            f"{row['station_dates']} | {row['revised_keys']} | {row['successful_attempts'] or 0} | "
            f"{row['failed_attempts'] or 0} | {row['latest_causal_at'] or '—'} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    (path / "report.md").write_text("\n".join(lines) + "\n")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
