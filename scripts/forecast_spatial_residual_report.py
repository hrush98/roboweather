#!/usr/bin/env python3
"""Reproduce the F4 causal ASOS-neighbor spatial residual ablation."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd
import pygrib
import requests

from scripts.forecast_remaining_heating_report import _load_forward_cohort
from weather_trader.forecasting.evaluation import EvaluationContract
from weather_trader.forecasting.nbm_benchmark import fit_convex_weight, metric_differences
from weather_trader.forecasting.remaining_heating import enforce_high_so_far_lower_bound
from weather_trader.forecasting.spatial_residual import (
    FROZEN_NEIGHBORS, NeighborSite, SpatialResidualCalibrator,
    SpatialResidualContract, frozen_neighbor_stations, materialize_spatial_features,
)
from weather_trader.forecasts.hrrr_archive import HRRRArchiveClient, _parse_index
from weather_trader.forecasts.hrrr_client import _kelvin_to_f, _nearest_index

DEFAULT_DATABASE = Path("/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite")
DEFAULT_F3 = ROOT / "reports/forecast-edge/f3-current/remaining_heating_weather_ensemble.joblib"
DEFAULT_STATE = Path("/home/maxrush/.local/state/roboweather/forecast_sources/f4_spatial")
DEFAULT_OUT = ROOT / "reports/forecast-edge/f4-current"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UTC = timezone.utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--f3-artifact", type=Path, default=DEFAULT_F3)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--refresh-observations", action="store_true")
    args = parser.parse_args()
    result = run_report(
        args.db, args.f3_artifact, args.state_dir, args.out,
        bootstrap_samples=args.bootstrap_samples, workers=args.workers,
        refresh_observations=args.refresh_observations,
    )
    print(json.dumps({
        "status": result["status"], "verdict": result["verdict"],
        "coverage": result["cohort"]["eligible_spatial_rate"],
        "checks": result["acceptance_checks"],
    }, indent=2))


def run_report(
    database: Path, f3_path: Path, state_dir: Path, out: Path, *,
    bootstrap_samples: int = 5000, workers: int = 8,
    refresh_observations: bool = False,
) -> dict[str, Any]:
    evaluation = EvaluationContract(
        validation_start="2026-01-01", validation_end_exclusive="2100-01-01",
        bootstrap_samples=bootstrap_samples,
    )
    spatial = SpatialResidualContract()
    forward, hrrr, market, exclusions = _load_forward_cohort(database, evaluation)
    if forward.empty:
        raise ValueError("F4 forward cohort is empty")
    artifact = joblib.load(f3_path)
    remaining = artifact["remaining_heating_model"].predict_proba(forward)
    coherent_hrrr = enforce_high_so_far_lower_bound(
        hrrr, forward["max_temp_so_far"], evaluation.support
    )
    remaining_weight = float(artifact["remaining_heating_weight"])
    predecessor = remaining_weight * remaining + (1.0 - remaining_weight) * coherent_hrrr
    decisions = pd.DataFrame({
        "decision_id": forward.index.astype(str),
        "station": forward["station"].astype(str),
        "decision_time_utc": forward["decision_time_utc"].astype(str),
        "local_date": forward["local_date"].astype(str),
    })
    state_dir.mkdir(parents=True, exist_ok=True)
    observations, observation_meta = fetch_iem(
        decisions, state_dir, refresh=refresh_observations
    )
    points, hrrr_meta = materialize_hrrr(
        decisions, observations, state_dir / "hrrr_neighbor_points.sqlite",
        workers=max(1, workers), contract=spatial,
    )
    features = materialize_spatial_features(decisions, observations, points, spatial)
    dates = forward["local_date"].astype(str).to_numpy()
    targets = forward["target_value"].to_numpy(int)
    fit_dates = set(str(value) for value in artifact["weight_fit_dates"])
    activation = str(artifact["activation_date"])
    fit_mask = np.asarray([value in fit_dates for value in dates])
    holdout_mask = dates >= activation
    recent_dates = set(sorted(set(dates))[-min(14, len(set(dates))):])
    recent_mask = np.asarray([value in recent_dates for value in dates])
    if fit_mask.sum() < 10 or not holdout_mask.any():
        raise ValueError("F4 needs at least 10 F3 fit rows and a nonempty holdout")

    calibrator = SpatialResidualCalibrator(spatial).fit(
        features.loc[fit_mask].reset_index(drop=True), predecessor[fit_mask],
        targets[fit_mask], evaluation.support,
    )
    candidate = calibrator.predict_proba(
        features, predecessor, forward["max_temp_so_far"], evaluation.support
    )
    market_weight = fit_convex_weight(
        candidate[fit_mask], market[fit_mask], targets[fit_mask], evaluation
    )
    market_stack = market_weight * candidate + (1.0 - market_weight) * market
    comparisons = {
        "spatial_minus_f3_holdout": metric_differences(
            candidate[holdout_mask], predecessor[holdout_mask],
            targets[holdout_mask], dates[holdout_mask], evaluation,
        ),
        "spatial_minus_f3_recent": metric_differences(
            candidate[recent_mask], predecessor[recent_mask],
            targets[recent_mask], dates[recent_mask], evaluation,
        ),
        "market_stack_minus_market_holdout": metric_differences(
            market_stack[holdout_mask], market[holdout_mask],
            targets[holdout_mask], dates[holdout_mask], evaluation,
        ),
        "market_stack_minus_market_recent": metric_differences(
            market_stack[recent_mask], market[recent_mask],
            targets[recent_mask], dates[recent_mask], evaluation,
        ),
    }
    eligible = pd.to_numeric(features["spatial_neighbor_count"]).to_numpy() >= spatial.minimum_neighbors
    checks = build_acceptance_checks(
        comparisons, eligible_rate=float(eligible.mean()),
        fit_rows=int(fit_mask.sum()),
        holdout_dates=len(set(dates[holdout_mask])),
        recent_dates=len(set(dates[recent_mask])), market_weight=market_weight,
    )
    result = {
        "status": "COMPLETE",
        "verdict": "ACCEPT_F4_FOR_PRICE_SHEET_V2_RESEARCH" if all(checks.values()) else "REJECT_F4_SPATIAL_RESIDUAL",
        "contract": {
            "evaluation": {**evaluation.to_dict(), "fingerprint": evaluation.fingerprint},
            "spatial": {**asdict(spatial), "fingerprint": spatial.fingerprint},
            "predecessor_forecast_version": artifact["forecast_version"],
            "f3_activation_date": activation,
            "fit_dates": sorted(fit_dates),
            "frozen_neighbors": {
                target: [asdict(site) for site in sites]
                for target, sites in sorted(FROZEN_NEIGHBORS.items())
            },
        },
        "inputs": {
            "database": str(database), "f3_artifact": str(f3_path),
            "f3_artifact_sha256": sha256_path(f3_path),
            "iem": observation_meta, "hrrr": hrrr_meta,
            "forward_exclusions": exclusions,
        },
        "cohort": {
            "rows": len(forward), "weather_dates": len(set(dates)),
            "fit_rows": int(fit_mask.sum()),
            "fit_weather_dates": len(set(dates[fit_mask])),
            "holdout_rows": int(holdout_mask.sum()),
            "holdout_weather_dates": len(set(dates[holdout_mask])),
            "holdout_first_date": min(dates[holdout_mask]),
            "holdout_last_date": max(dates[holdout_mask]),
            "recent_weather_dates": sorted(recent_dates),
            "eligible_spatial_rows": int(eligible.sum()),
            "eligible_spatial_rate": float(eligible.mean()),
            "neighbor_count_distribution": {
                str(key): int(value) for key, value in
                features["spatial_neighbor_count"].value_counts().sort_index().items()
            },
        },
        "calibrator": calibrator.training_summary,
        "market_weight": market_weight,
        "comparisons": comparisons, "acceptance_checks": checks,
        "limitations": [
            "IEM archive rows lack historical receipt timestamps; v1 imposes a frozen ten-minute availability lag.",
            "HRRR temperatures use only a cycle available one hour before decision and interpolate to neighbor observation time.",
            "The ridge correction fits only F3 weight-fit dates and is scored after F3's frozen activation date.",
            "Acceptance would authorize pricing research only, never funded trading.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out / "report.md").write_text(render_markdown(result))
    features.assign(
        local_date=dates, target_value=targets,
        spatial_correction_f=calibrator.predict_correction(features),
        is_fit=fit_mask, is_holdout=holdout_mask,
    ).to_csv(out / "ablation_rows.csv", index=False)
    joblib.dump({
        "model_type": "spatial_residual_correction",
        "forecast_version": spatial.version, "contract": spatial,
        "contract_fingerprint": spatial.fingerprint,
        "predecessor_forecast_version": artifact["forecast_version"],
        "activation_date": activation, "model": calibrator,
    }, out / "spatial_residual_calibrator.joblib")
    return result


def build_acceptance_checks(
    comparisons: Mapping[str, Mapping[str, Any]], *, eligible_rate: float,
    fit_rows: int, holdout_dates: int, recent_dates: int, market_weight: float,
) -> dict[str, bool]:
    holdout = comparisons["spatial_minus_f3_holdout"]
    recent = comparisons["spatial_minus_f3_recent"]
    market_holdout = comparisons["market_stack_minus_market_holdout"]
    market_recent = comparisons["market_stack_minus_market_recent"]
    delta = lambda item, metric: float(item["candidate_minus_reference"][metric])
    upper = lambda item, metric: float(item["weather_date_clustered_95pct_ci"][metric][1])
    return {
        "spatial_coverage_at_least_80pct": eligible_rate >= 0.80,
        "fit_has_at_least_10_rows": fit_rows >= 10,
        "holdout_has_at_least_20_weather_dates": holdout_dates >= 20,
        "recent_has_14_weather_dates": recent_dates >= 14,
        "holdout_improves_log_loss": delta(holdout, "log_loss") < 0,
        "holdout_improves_rps": delta(holdout, "rps") < 0,
        "holdout_log_loss_ci_below_zero": upper(holdout, "log_loss") < 0,
        "holdout_rps_ci_below_zero": upper(holdout, "rps") < 0,
        "recent_log_loss_not_negative": delta(recent, "log_loss") <= 0,
        "recent_rps_not_negative": delta(recent, "rps") <= 0,
        "market_assigns_positive_spatial_weight": market_weight >= 0.01,
        "market_holdout_log_loss_not_negative": delta(market_holdout, "log_loss") <= 0,
        "market_holdout_rps_not_negative": delta(market_holdout, "rps") <= 0,
        "market_recent_log_loss_not_negative": delta(market_recent, "log_loss") <= 0,
        "market_recent_rps_not_negative": delta(market_recent, "rps") <= 0,
    }


def fetch_iem(
    decisions: pd.DataFrame, state_dir: Path, *, refresh: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    stations = sorted(
        {value.removeprefix("K") for value in decisions["station"].astype(str)}
        | {site.station for site in frozen_neighbor_stations(decisions["station"])}
    )
    times = pd.to_datetime(decisions["decision_time_utc"], utc=True)
    start, end = times.min() - pd.Timedelta(hours=3), times.max() + pd.Timedelta(hours=1)
    csv_path, meta_path = state_dir / "iem_observations.csv", state_dir / "iem_observations.json"
    identity = {
        "stations": stations, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "report_types": [1, 3, 4], "fields": ["tmpf", "dwpf", "drct", "sknt"],
    }
    if csv_path.exists() and meta_path.exists() and not refresh:
        metadata = json.loads(meta_path.read_text())
        if metadata.get("request") == identity:
            return pd.read_csv(csv_path), metadata
    params: list[tuple[str, str]] = [
        ("sts", start.isoformat()), ("ets", end.isoformat()), ("tz", "UTC"),
        ("format", "onlycomma"), ("latlon", "no"), ("elev", "no"),
        ("missing", "M"), ("trace", "T"), ("direct", "no"),
    ]
    params += [("station", value) for value in stations]
    params += [("data", value) for value in identity["fields"]]
    params += [("report_type", str(value)) for value in identity["report_types"]]
    response = requests.get(IEM_URL, params=params, timeout=180)
    response.raise_for_status()
    if "station,valid" not in response.text[:200].lower():
        raise ValueError("IEM response lacks expected CSV header")
    csv_path.write_bytes(response.content)
    frame = pd.read_csv(csv_path, na_values=["M"])
    metadata = {
        "request": identity, "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "source_url": response.url,
        "content_sha256": hashlib.sha256(response.content).hexdigest(),
        "rows": len(frame), "stations_with_rows": int(frame["station"].nunique()),
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return frame, metadata


def materialize_hrrr(
    decisions: pd.DataFrame, observations: pd.DataFrame, cache_path: Path, *,
    workers: int, contract: SpatialResidualContract,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    obs = observations.copy()
    obs["station"] = obs["station"].astype(str).str.upper().str.removeprefix("K")
    obs["valid"] = pd.to_datetime(obs["valid"], utc=True, errors="coerce")
    obs["available"] = obs["valid"] + pd.to_timedelta(
        contract.observation_availability_lag_minutes, unit="m"
    )
    requirements, task_sites = [], {}
    for decision in decisions.itertuples(index=False):
        decision_time = pd.Timestamp(decision.decision_time_utc)
        cycle = (decision_time - pd.Timedelta(hours=1)).floor("h")
        for site in FROZEN_NEIGHBORS.get(str(decision.station), ()):
            group = obs.loc[
                (obs["station"] == site.station)
                & (obs["available"] <= decision_time)
                & (obs["valid"] >= decision_time - pd.Timedelta(minutes=contract.maximum_observation_age_minutes))
                & pd.to_numeric(obs["tmpf"], errors="coerce").between(-80, 140)
            ].sort_values("valid")
            if group.empty:
                continue
            valid = pd.Timestamp(group.iloc[-1]["valid"])
            elapsed = (valid - cycle).total_seconds() / 3600.0
            hours = {
                max(0, int(math.floor(elapsed))), max(0, int(math.ceil(elapsed))),
                max(0, int(math.floor(elapsed - 1))), max(0, int(math.ceil(elapsed - 1))),
            }
            for hour in hours:
                task_sites.setdefault((cycle.isoformat(), hour), {})[site.station] = site
            requirements.append({
                "decision_id": str(decision.decision_id), "station": site.station,
                "cycle": cycle.isoformat(), "valid": valid.isoformat(),
                "elapsed": elapsed,
            })
    cache = HrrrPointCache(cache_path)
    pending = [
        (cycle, hour, list(sites.values()))
        for (cycle, hour), sites in sorted(task_sites.items())
        if not cache.has_task(cycle, hour)
    ]
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_hrrr_points, cycle, hour, sites): (cycle, hour)
            for cycle, hour, sites in pending
        }
        for future in as_completed(futures):
            cycle, hour = futures[future]
            try:
                cache.write_task(cycle, hour, future.result())
            except Exception as exc:
                message = "%s: %s" % (type(exc).__name__, exc)
                cache.write_failure(cycle, hour, message)
                errors.append("%s f%02d %s" % (cycle, hour, message))
    rows = []
    for item in requirements:
        rows.append({
            "decision_id": item["decision_id"], "station": item["station"],
            "hrrr_tmpf": interpolate(cache, item["cycle"], item["station"], item["elapsed"]),
            "hrrr_previous_tmpf": interpolate(cache, item["cycle"], item["station"], item["elapsed"] - 1),
            "hrrr_cycle_utc": item["cycle"], "observation_valid_utc": item["valid"],
        })
    summary = cache.summary()
    cache.close()
    summary.update({
        "path": str(cache_path), "requirements": len(requirements),
        "tasks_requested": len(task_sites), "tasks_fetched_this_run": len(pending),
        "errors_this_run": errors[:20],
    })
    return pd.DataFrame(rows), summary


class HrrrPointCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
        create table if not exists tasks(
          cycle text not null,hour integer not null,status text not null,error text,
          primary key(cycle,hour));
        create table if not exists points(
          cycle text not null,hour integer not null,station text not null,tmpf real not null,
          primary key(cycle,hour,station));
        """)
        self.connection.commit()

    def has_task(self, cycle: str, hour: int) -> bool:
        row = self.connection.execute(
            "select status from tasks where cycle=? and hour=?", (cycle, hour)
        ).fetchone()
        return row is not None and row[0] == "DONE"

    def write_task(self, cycle: str, hour: int, rows: list[dict[str, Any]]) -> None:
        with self.connection:
            self.connection.executemany(
                "insert or replace into points values(?,?,?,?)",
                [(cycle, hour, row["station"], row["tmpf"]) for row in rows],
            )
            self.connection.execute(
                "insert or replace into tasks values(?,?,'DONE',null)", (cycle, hour)
            )

    def write_failure(self, cycle: str, hour: int, error: str) -> None:
        with self.connection:
            self.connection.execute(
                "insert or replace into tasks values(?,?,'FAILED',?)",
                (cycle, hour, error[:1000]),
            )

    def value(self, cycle: str, hour: int, station: str) -> float | None:
        row = self.connection.execute(
            "select tmpf from points where cycle=? and hour=? and station=?",
            (cycle, hour, station),
        ).fetchone()
        return None if row is None else float(row[0])

    def summary(self) -> dict[str, Any]:
        return {
            "tasks": {status: int(count) for status, count in
                      self.connection.execute("select status,count(*) from tasks group by status")},
            "point_rows": int(self.connection.execute("select count(*) from points").fetchone()[0]),
        }

    def close(self) -> None:
        self.connection.close()


def fetch_hrrr_points(cycle_text: str, hour: int, sites: list[NeighborSite]) -> list[dict[str, Any]]:
    cycle = datetime.fromisoformat(cycle_text)
    url = HRRRArchiveClient._grib_url(cycle, hour)
    index_response = requests.get(url + ".idx", timeout=120)
    index_response.raise_for_status()
    record = next(
        (item for item in _parse_index(index_response.text)
         if item.variable == "TMP" and item.level == "2 m above ground"), None
    )
    if record is None:
        raise LookupError("HRRR TMP 2 m record unavailable")
    end = "" if record.next_offset is None else str(record.next_offset - 1)
    response = requests.get(
        url, headers={"Range": "bytes=%d-%s" % (record.offset, end)}, timeout=120
    )
    response.raise_for_status()
    if not response.content.startswith(b"GRIB"):
        raise ValueError("HRRR range response is not GRIB")
    with NamedTemporaryFile(suffix=".grib2") as handle:
        handle.write(response.content)
        handle.flush()
        messages = pygrib.open(handle.name)
        try:
            data, latitudes, longitudes = messages.message(1).data()
        finally:
            messages.close()
        rows = []
        for site in sites:
            row, column = _nearest_index(
                latitudes, longitudes, site.latitude, site.longitude
            )
            rows.append({
                "station": site.station,
                "tmpf": float(_kelvin_to_f(float(data[row, column]))),
            })
    return rows


def interpolate(cache: HrrrPointCache, cycle: str, station: str, elapsed: float) -> float:
    lower, upper = max(0, int(math.floor(elapsed))), max(0, int(math.ceil(elapsed)))
    left, right = cache.value(cycle, lower, station), cache.value(cycle, upper, station)
    if left is None or right is None:
        return np.nan
    if lower == upper:
        return left
    fraction = min(1.0, max(0.0, elapsed - lower))
    return left * (1.0 - fraction) + right * fraction


def render_markdown(result: Mapping[str, Any]) -> str:
    holdout = result["comparisons"]["spatial_minus_f3_holdout"]["candidate_minus_reference"]
    market = result["comparisons"]["market_stack_minus_market_holdout"]["candidate_minus_reference"]
    lines = [
        "# F4 Spatial Residual Ablation", "", "Status: %s" % result["status"], "",
        "Verdict: **%s**" % result["verdict"], "", "## Frozen Contract", "",
        "- Version: %s." % result["contract"]["spatial"]["version"],
        "- Fingerprint: %s." % result["contract"]["spatial"]["fingerprint"],
        "- Five outcome-blind ASOS neighbors per target; ten-minute lag; ninety-minute staleness ceiling.",
        "- Fixed upwind/distance/elevation aggregation and ridge correction; missing state leaves F3 unchanged.",
        "", "## Controlled Ablation", "",
        "- Eligible rows: %d/%d (%.1f%%)." % (
            result["cohort"]["eligible_spatial_rows"], result["cohort"]["rows"],
            100 * result["cohort"]["eligible_spatial_rate"]),
        "- Holdout: %s through %s (%d weather dates)." % (
            result["cohort"]["holdout_first_date"], result["cohort"]["holdout_last_date"],
            result["cohort"]["holdout_weather_dates"]),
        "", "| comparison | delta log loss | delta RPS |", "| --- | ---: | ---: |",
        "| spatial minus F3 holdout | %.5f | %.5f |" % (holdout["log_loss"], holdout["rps"]),
        "| market stack minus market holdout | %.5f | %.5f |" % (market["log_loss"], market["rps"]),
        "", "Negative deltas favor F4.", "", "## Acceptance Checks", "",
    ]
    lines += ["- %s: %s" % ("PASS" if value else "FAIL", key)
              for key, value in result["acceptance_checks"].items()]
    lines += ["", "## Limitations", ""]
    lines += ["- %s" % value for value in result["limitations"]]
    return "\n".join(lines) + "\n"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
