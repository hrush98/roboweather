"""Causal NBM archive materialization and identical-row forecast evaluation.

Historical archive modification times are provenance, not causal availability.
The benchmark freezes a conservative cycle-plus-two-hour eligibility rule.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pygrib
import requests
from scipy.optimize import minimize_scalar
from scipy.stats import norm

from weather_trader.forecasting.evaluation import (
    EvaluationContract,
    evaluate_probability_matrix,
    normalize_observed_market_ladder,
    normalize_probability_matrix,
    select_horizon_snapshots,
)
from weather_trader.stations.metadata import get_station


UTC = timezone.utc
NBM_ARCHIVE_ROOT = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
NBM_HISTORICAL_CONTRACT_VERSION = "nbm_v5_archive_cycle_plus_2h_v1"
NBM_RELEASE_LAG = timedelta(hours=2)
NBM_OPERATIONAL_START = date(2026, 5, 5)
BASELINE_MODEL = "mvp_hrrr_rich_pm_active_us12_obs_2022_2025"
HORIZONS = ("d1_open_08_local", "d1_revision_20_local", "d0_latest_le_14_local")


@dataclass(frozen=True)
class NbmRequest:
    cycle_at_utc: datetime
    forecast_hour: int

    @property
    def available_at_utc(self) -> datetime:
        return self.cycle_at_utc + NBM_RELEASE_LAG

    @property
    def key(self) -> str:
        return f"{self.cycle_at_utc:%Y%m%d%H}-f{self.forecast_hour:03d}"

    @property
    def object_url(self) -> str:
        cycle = self.cycle_at_utc
        return (
            f"{NBM_ARCHIVE_ROOT}/blend.{cycle:%Y%m%d}/{cycle:%H}/core/"
            f"blend.t{cycle:%H}z.core.f{self.forecast_hour:03d}.co.grib2"
        )

    @property
    def index_url(self) -> str:
        return self.object_url + ".idx"


@dataclass(frozen=True)
class MaterializationTarget:
    station: str
    market_date: date
    horizon: str
    as_of_utc: datetime
    request: NbmRequest


@dataclass(frozen=True)
class GribRange:
    start: int
    end: int
    mean_inventory: str
    std_inventory: str


def conservative_nbm_request(
    as_of_utc: datetime, station: str, market_date: date
) -> NbmRequest:
    """Select the latest eligible cycle and TMAX window containing 15 local."""
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    eligible = as_of_utc.astimezone(UTC) - NBM_RELEASE_LAG
    # The NBM runs hourly, but CONUS TMAX mean/stddev is present only on the
    # full 00Z/12Z core cycles.  Selecting an arbitrary hourly cycle would
    # silently turn a field-availability limitation into missing history.
    cycle_hour = 12 if eligible.hour >= 12 else 0
    cycle = eligible.replace(
        hour=cycle_hour, minute=0, second=0, microsecond=0
    )
    peak = datetime.combine(
        market_date, time(hour=15), tzinfo=ZoneInfo(get_station(station).timezone)
    ).astimezone(UTC)
    hours = (peak - cycle).total_seconds() / 3600.0
    if hours <= 0:
        raise ValueError("selected NBM cycle is not before the target peak")
    forecast_hour = max(12, int(math.ceil(hours / 12.0) * 12))
    if forecast_hour > 264:
        raise ValueError("target is outside NBM projection range")
    return NbmRequest(cycle, forecast_hour)


def horizon_times(
    station: str, market_date: date, d0_as_of_utc: datetime
) -> dict[str, datetime]:
    zone = ZoneInfo(get_station(station).timezone)
    prior = market_date - timedelta(days=1)
    return {
        "d1_open_08_local": datetime.combine(
            prior, time(hour=8), tzinfo=zone
        ).astimezone(UTC),
        "d1_revision_20_local": datetime.combine(
            prior, time(hour=20), tzinfo=zone
        ).astimezone(UTC),
        "d0_latest_le_14_local": d0_as_of_utc.astimezone(UTC),
    }


def parse_tmax_inventory(index_text: str, object_size: int | None = None) -> GribRange:
    rows: list[tuple[int, int, str]] = []
    for line in index_text.splitlines():
        match = re.match(r"^(\d+):(\d+):(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), line))
    if not rows:
        raise ValueError("NBM index contains no parseable inventory rows")
    candidates = [
        (offset, row)
        for offset, (_, _, row) in enumerate(rows)
        if ":TMAX:2 m above ground:" in row
    ]
    means = [(offset, row) for offset, row in candidates if "ens std dev" not in row]
    stds = [(offset, row) for offset, row in candidates if "ens std dev" in row]
    if len(means) != 1 or len(stds) != 1:
        raise ValueError(
            f"expected one NBM TMAX mean/stddev pair, got {len(means)}/{len(stds)}"
        )
    mean_offset, mean_row = means[0]
    std_offset, std_row = stds[0]
    if std_offset != mean_offset + 1:
        raise ValueError("NBM TMAX mean/stddev messages are not adjacent")
    start = rows[mean_offset][1]
    if std_offset + 1 < len(rows):
        end = rows[std_offset + 1][1] - 1
    elif object_size is not None:
        end = object_size - 1
    else:
        raise ValueError("object size required when TMAX stddev is final")
    return GribRange(start, end, mean_row, std_row)


def normal_fixed_support(
    mean_f: Sequence[float], std_f: Sequence[float], contract: EvaluationContract
) -> np.ndarray:
    mean = np.asarray(mean_f, dtype=float)
    scale = np.maximum(np.asarray(std_f, dtype=float), 0.5)
    if mean.shape != scale.shape or mean.ndim != 1:
        raise ValueError("NBM mean and standard deviation arrays must align")
    values = contract.support.values
    # Outcomes are scored as integer Fahrenheit values after rounding, so the
    # probability cells meet halfway between adjacent support values. The
    # endpoint cells absorb their respective tails.
    boundaries = values[:-1].astype(float) + 0.5
    cuts = norm.cdf(boundaries[None, :], loc=mean[:, None], scale=scale[:, None])
    matrix = np.empty((len(mean), len(values)), dtype=float)
    matrix[:, 0] = cuts[:, 0]
    matrix[:, -1] = 1.0 - cuts[:, -1]
    matrix[:, 1:-1] = np.diff(cuts, axis=1)
    return normalize_probability_matrix(matrix)


def fit_convex_weight(
    left: np.ndarray,
    right: np.ndarray,
    targets: Sequence[int],
    contract: EvaluationContract,
) -> float:
    left_n = normalize_probability_matrix(left)
    right_n = normalize_probability_matrix(right)
    indices = np.asarray(targets, dtype=int) - contract.support.minimum
    if len(left_n) != len(right_n) or len(left_n) != len(indices):
        raise ValueError("stacking inputs do not align")

    def objective(weight: float) -> float:
        chosen = weight * left_n[np.arange(len(left_n)), indices] + (
            1.0 - weight
        ) * right_n[np.arange(len(right_n)), indices]
        return float(-np.log(np.clip(chosen, 1e-12, 1.0)).mean())

    fitted = minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded")
    return float(np.clip(fitted.x, 0.0, 1.0))


def metric_differences(
    candidate: np.ndarray,
    reference: np.ndarray,
    targets: Sequence[int],
    local_dates: Sequence[Any],
    contract: EvaluationContract,
) -> dict[str, Any]:
    candidate_n = normalize_probability_matrix(candidate)
    reference_n = normalize_probability_matrix(reference)
    target = np.asarray(targets, dtype=int)
    dates = np.asarray([str(value) for value in local_dates], dtype=object)
    indices = target - contract.support.minimum
    support = contract.support.values

    def losses(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        chosen = np.clip(matrix[np.arange(len(matrix)), indices], 1e-12, 1.0)
        observed = (support[None, :] >= target[:, None]).astype(float)
        rps = np.sum((np.cumsum(matrix, axis=1) - observed) ** 2, axis=1)
        return -np.log(chosen), rps

    cand_log, cand_rps = losses(candidate_n)
    ref_log, ref_rps = losses(reference_n)
    per_date = pd.DataFrame(
        {
            "local_date": dates,
            "log_loss": cand_log - ref_log,
            "rps": cand_rps - ref_rps,
        }
    ).groupby("local_date", observed=True).mean()
    rng = np.random.default_rng(contract.bootstrap_seed)
    values = per_date.to_numpy(float)
    draws = np.empty((contract.bootstrap_samples, values.shape[1]), dtype=float)
    for offset in range(contract.bootstrap_samples):
        draws[offset] = values[
            rng.integers(0, len(values), size=len(values))
        ].mean(axis=0)
    return {
        "rows": int(len(target)),
        "weather_dates": int(len(per_date)),
        "candidate_minus_reference": {
            column: float(per_date[column].mean()) for column in per_date.columns
        },
        "weather_date_clustered_95pct_ci": {
            column: [
                float(value)
                for value in np.quantile(draws[:, index], [0.025, 0.975])
            ]
            for index, column in enumerate(per_date.columns)
        },
    }


class NbmArchive:
    def __init__(self, cache_dir: Path, *, timeout_seconds: float = 60.0) -> None:
        self.cache_dir = cache_dir
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "RoboWeather NBM research benchmark/1.0"

    def fetch(
        self, request: NbmRequest, *, refresh: bool = False
    ) -> tuple[Path, dict[str, Any]]:
        directory = self.cache_dir / request.cycle_at_utc.strftime("%Y%m%d/%H")
        data_path = directory / f"f{request.forecast_hour:03d}-tmax-pair.grib2"
        meta_path = data_path.with_suffix(".json")
        if data_path.exists() and meta_path.exists() and not refresh:
            return data_path, json.loads(meta_path.read_text())
        directory.mkdir(parents=True, exist_ok=True)
        index_response = self.session.get(request.index_url, timeout=self.timeout_seconds)
        index_response.raise_for_status()
        inventory = parse_tmax_inventory(index_response.text)
        response = self.session.get(
            request.object_url,
            headers={"Range": f"bytes={inventory.start}-{inventory.end}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        expected = inventory.end - inventory.start + 1
        if len(response.content) != expected or not response.content.startswith(b"GRIB"):
            raise ValueError(
                f"invalid NBM range response for {request.key}: "
                f"{len(response.content)}/{expected}"
            )
        digest = hashlib.sha256(response.content).hexdigest()
        temporary = data_path.with_suffix(".tmp")
        temporary.write_bytes(response.content)
        temporary.replace(data_path)
        metadata = {
            "contract_version": NBM_HISTORICAL_CONTRACT_VERSION,
            "cycle_at_utc": request.cycle_at_utc.isoformat(),
            "causal_available_at_utc": request.available_at_utc.isoformat(),
            "forecast_hour": request.forecast_hour,
            "source_url": request.object_url,
            "index_url": request.index_url,
            "index_sha256": hashlib.sha256(index_response.content).hexdigest(),
            "content_sha256": digest,
            "byte_range": [inventory.start, inventory.end],
            "byte_count": len(response.content),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "last_modified_is_provenance_only": True,
            "mean_inventory": inventory.mean_inventory,
            "std_inventory": inventory.std_inventory,
        }
        meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        return data_path, metadata


def decode_station_values(
    path: Path, stations: Iterable[str]
) -> dict[str, tuple[float, float]]:
    grib = pygrib.open(str(path))
    try:
        if grib.messages != 2:
            raise ValueError(f"expected two NBM TMAX messages, got {grib.messages}")
        mean_message, std_message = grib.message(1), grib.message(2)
        if (
            "Maximum temperature" not in mean_message.name
            or "derivedForecast" not in set(std_message.keys())
        ):
            raise ValueError("NBM range lacks mean/stddev maximum temperature")
        output: dict[str, tuple[float, float]] = {}
        for station_id in sorted(set(stations)):
            station = get_station(station_id)
            mean = _nearest_value(
                mean_message, station.latitude, station.longitude
            )
            std = _nearest_value(std_message, station.latitude, station.longitude)
            output[station_id] = (
                (mean - 273.15) * 9.0 / 5.0 + 32.0,
                std * 9.0 / 5.0,
            )
        return output
    finally:
        grib.close()


def _nearest_value(message: Any, latitude: float, longitude: float) -> float:
    radius = 0.08
    values, latitudes, longitudes = message.data(
        lat1=latitude - radius,
        lat2=latitude + radius,
        lon1=longitude - radius,
        lon2=longitude + radius,
    )
    if values.size == 0:
        raise ValueError("NBM station stencil is empty")
    distance = (latitudes - latitude) ** 2 + (longitudes - longitude) ** 2
    return float(values.ravel()[int(np.argmin(distance))])


def load_identical_cohort(
    database: Path, contract: EvaluationContract
) -> tuple[pd.DataFrame, dict[str, dict[str, float | None]]]:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshots = pd.read_sql_query(
            """select ps.id, ps.timestamp, ps.station, ps.market_date,
                      ps.decision_time_utc, ps.decision_time_local, ps.raw_json,
                      sdo.final_high_tmpf
                 from prediction_snapshots ps
                 join station_date_outcomes sdo using (station, market_date)
                where ps.market_family='HIGH_TEMP'
                  and ps.station like 'K%'
                  and ps.model_name=?
                  and sdo.final_high_tmpf is not null
                  and ps.market_date>=?
                order by ps.market_date, ps.station, ps.decision_time_utc, ps.id""",
            connection,
            params=(BASELINE_MODEL, NBM_OPERATIONAL_START.isoformat()),
        )
        market_rows = connection.execute(
            """select market_id, lower_f, upper_f from markets
                where market_family='HIGH_TEMP' and station like 'K%'"""
        ).fetchall()
    finally:
        connection.close()
    bounds = {
        str(row["market_id"]): {
            "lower": float(row["lower_f"])
            if row["lower_f"] is not None
            else None,
            "upper": float(row["upper_f"])
            if row["upper_f"] is not None
            else None,
        }
        for row in market_rows
    }
    if snapshots.empty:
        return snapshots, bounds
    snapshots["local_hour"] = snapshots["decision_time_local"].map(
        lambda value: datetime.fromisoformat(value).hour
    )
    snapshots["timezone"] = snapshots["station"].map(
        lambda value: get_station(str(value)).timezone
    )
    snapshots["local_date"] = snapshots["market_date"]
    snapshots["snapshot_time_local"] = snapshots["decision_time_utc"]
    snapshots = select_horizon_snapshots(snapshots, contract)
    return snapshots.sort_values(["market_date", "station"]).reset_index(drop=True), bounds


def extract_snapshot_distributions(
    raw_json: str,
    bounds: Mapping[str, Mapping[str, float | None]],
    contract: EvaluationContract,
) -> tuple[np.ndarray, np.ndarray]:
    candidates = json.loads(raw_json).get("candidate_distribution") or []
    rows = []
    for item in candidates:
        bound = bounds.get(str(item.get("market_id")))
        if (
            bound is None
            or item.get("fair_yes") is None
            or item.get("yes_ask") is None
        ):
            raise ValueError("snapshot lacks a complete observed model/market ladder")
        upper = bound["upper"]
        rows.append(
            {
                "bucket_lower": bound["lower"],
                "bucket_upper": upper + 1.0 if upper is not None else None,
                "model": float(item["fair_yes"]),
                "market": float(item["yes_ask"]),
            }
        )
    if not rows:
        raise ValueError("snapshot candidate distribution is empty")
    ladder = pd.DataFrame(rows)
    return (
        normalize_observed_market_ladder(ladder, contract.support, "model"),
        normalize_observed_market_ladder(ladder, contract.support, "market"),
    )


def materialization_targets(cohort: pd.DataFrame) -> list[MaterializationTarget]:
    output: list[MaterializationTarget] = []
    for row in cohort.itertuples(index=False):
        market_date = date.fromisoformat(str(row.market_date))
        d0_as_of = datetime.fromisoformat(str(row.decision_time_utc))
        for horizon, as_of in horizon_times(
            str(row.station), market_date, d0_as_of
        ).items():
            request = conservative_nbm_request(as_of, str(row.station), market_date)
            output.append(
                MaterializationTarget(
                    str(row.station), market_date, horizon, as_of, request
                )
            )
    return output


def materialize_nbm(
    targets: Sequence[MaterializationTarget],
    archive: NbmArchive,
    *,
    max_workers: int = 8,
    refresh: bool = False,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    grouped: dict[str, tuple[NbmRequest, set[str]]] = {}
    for target in targets:
        _, stations = grouped.setdefault(
            target.request.key, (target.request, set())
        )
        stations.add(target.station)
    decoded: dict[str, dict[str, tuple[float, float]]] = {}
    provenance: list[dict[str, Any]] = []
    failures: dict[str, str] = {}

    def fetch_one(
        item: tuple[str, tuple[NbmRequest, set[str]]]
    ) -> tuple[str, dict[str, tuple[float, float]], dict[str, Any]]:
        key, (request, stations) = item
        path, metadata = archive.fetch(request, refresh=refresh)
        return key, decode_station_values(path, stations), metadata

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(fetch_one, item): item[0] for item in grouped.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                resolved_key, values, metadata = future.result()
                decoded[resolved_key] = values
                provenance.append(metadata)
            except Exception as exc:
                failures[key] = f"{type(exc).__name__}: {exc}"

    rows = []
    for target in targets:
        values = decoded.get(target.request.key, {}).get(target.station)
        rows.append(
            {
                "station": target.station,
                "market_date": target.market_date.isoformat(),
                "horizon": target.horizon,
                "as_of_utc": target.as_of_utc.isoformat(),
                "cycle_at_utc": target.request.cycle_at_utc.isoformat(),
                "causal_available_at_utc": target.request.available_at_utc.isoformat(),
                "forecast_hour": target.request.forecast_hour,
                "nbm_tmax_mean_f": values[0] if values else np.nan,
                "nbm_tmax_std_f": values[1] if values else np.nan,
                "status": "OK" if values else "FAILED",
                "error": failures.get(target.request.key),
                "source_key": target.request.key,
            }
        )
    return pd.DataFrame(rows), sorted(
        provenance, key=lambda item: (item["cycle_at_utc"], item["forecast_hour"])
    )


def run_benchmark(
    database: Path,
    cache_dir: Path,
    out: Path,
    *,
    bootstrap_samples: int = 2000,
    max_workers: int = 8,
    refresh: bool = False,
) -> dict[str, Any]:
    contract = EvaluationContract(
        validation_start=NBM_OPERATIONAL_START.isoformat(),
        validation_end_exclusive="2100-01-01",
        bootstrap_samples=bootstrap_samples,
    )
    cohort, bounds = load_identical_cohort(database, contract)
    if cohort.empty:
        raise ValueError("no resolved HRRR-rich baseline rows exist for F2")
    models, markets, eligible_indices, exclusions = [], [], [], []
    for index, row in cohort.iterrows():
        try:
            model, market = extract_snapshot_distributions(
                row["raw_json"], bounds, contract
            )
            models.append(model)
            markets.append(market)
            eligible_indices.append(index)
        except ValueError as exc:
            exclusions.append({"id": int(row["id"]), "reason": str(exc)})
    cohort = cohort.loc[eligible_indices].reset_index(drop=True)
    baseline = np.asarray(models)
    market = np.asarray(markets)
    materialized, provenance = materialize_nbm(
        materialization_targets(cohort),
        NbmArchive(cache_dir),
        max_workers=max_workers,
        refresh=refresh,
    )
    d0 = materialized.loc[
        materialized["horizon"] == "d0_latest_le_14_local"
    ].copy()
    joined = cohort.merge(
        d0, on=["station", "market_date"], how="left", validate="one_to_one"
    )
    valid = (
        joined["status"].eq("OK")
        & joined["nbm_tmax_mean_f"].notna()
        & joined["nbm_tmax_std_f"].notna()
    )
    scored = joined.loc[valid].reset_index(drop=True)
    baseline = baseline[valid.to_numpy()]
    market = market[valid.to_numpy()]
    nbm = normal_fixed_support(
        scored["nbm_tmax_mean_f"], scored["nbm_tmax_std_f"], contract
    )
    target_values = scored["target_value"].to_numpy(int)
    local_dates = scored["market_date"].astype(str).to_numpy()
    unique_dates = sorted(scored["market_date"].astype(str).unique())
    split_index = max(1, int(math.floor(len(unique_dates) * 0.60)))
    fit_dates = set(unique_dates[:split_index])
    holdout_dates = set(unique_dates[split_index:])
    fit_mask = scored["market_date"].astype(str).isin(fit_dates).to_numpy()
    holdout_mask = scored["market_date"].astype(str).isin(holdout_dates).to_numpy()
    if not holdout_mask.any():
        failed = materialized.loc[materialized["status"].ne("OK"), "error"]
        examples = failed.dropna().value_counts().head(3).to_dict()
        raise ValueError(
            f"F2 chronological holdout is empty; materialization errors={examples}"
        )
    nbm_hrrr_weight = fit_convex_weight(
        nbm[fit_mask], baseline[fit_mask], target_values[fit_mask], contract
    )
    nbm_market_weight = fit_convex_weight(
        nbm[fit_mask], market[fit_mask], target_values[fit_mask], contract
    )
    stacked_hrrr = nbm_hrrr_weight * nbm + (1.0 - nbm_hrrr_weight) * baseline
    stacked_market = nbm_market_weight * nbm + (1.0 - nbm_market_weight) * market
    matrices = {"nbm": nbm, "hrrr_baseline": baseline, "market": market}
    all_metrics = {
        name: evaluate_probability_matrix(
            matrix, target_values, local_dates, contract
        )
        for name, matrix in matrices.items()
    }
    holdout_matrices = {
        **matrices,
        "nbm_hrrr_stack": stacked_hrrr,
        "nbm_market_stack": stacked_market,
    }
    holdout_metrics = {
        name: evaluate_probability_matrix(
            matrix[holdout_mask],
            target_values[holdout_mask],
            local_dates[holdout_mask],
            contract,
        )
        for name, matrix in holdout_matrices.items()
    }
    comparisons = {
        "nbm_minus_hrrr_all": metric_differences(
            nbm, baseline, target_values, local_dates, contract
        ),
        "nbm_hrrr_stack_minus_hrrr_holdout": metric_differences(
            stacked_hrrr[holdout_mask],
            baseline[holdout_mask],
            target_values[holdout_mask],
            local_dates[holdout_mask],
            contract,
        ),
        "nbm_market_stack_minus_market_holdout": metric_differences(
            stacked_market[holdout_mask],
            market[holdout_mask],
            target_values[holdout_mask],
            local_dates[holdout_mask],
            contract,
        ),
    }
    recent_dates = set(unique_dates[-min(14, len(unique_dates)) :])
    recent_mask = scored["market_date"].astype(str).isin(recent_dates).to_numpy()
    comparisons["nbm_minus_hrrr_recent"] = metric_differences(
        nbm[recent_mask],
        baseline[recent_mask],
        target_values[recent_mask],
        local_dates[recent_mask],
        contract,
    )
    station_slices = _slice_metrics(
        scored, nbm, baseline, target_values, contract, "station"
    )
    entropy = -np.sum(market * np.log(np.clip(market, 1e-12, 1.0)), axis=1)
    fit_entropy_median = float(np.median(entropy[fit_mask]))
    scored["regime"] = np.where(
        entropy <= fit_entropy_median,
        "lower_market_entropy",
        "higher_market_entropy",
    )
    regime_slices = _slice_metrics(
        scored, nbm, baseline, target_values, contract, "regime"
    )
    checks = {
        "weather_next_available": False,
        "nbm_materialization_coverage_at_least_95pct": bool(valid.mean() >= 0.95),
        "holdout_has_at_least_20_weather_dates": len(holdout_dates) >= 20,
        "nbm_hrrr_stack_has_positive_weight": nbm_hrrr_weight >= 0.01,
        "nbm_hrrr_stack_improves_holdout_log_loss": comparisons[
            "nbm_hrrr_stack_minus_hrrr_holdout"
        ]["candidate_minus_reference"]["log_loss"] < 0,
        "nbm_hrrr_stack_improves_holdout_rps": comparisons[
            "nbm_hrrr_stack_minus_hrrr_holdout"
        ]["candidate_minus_reference"]["rps"] < 0,
        "nbm_market_stack_has_positive_weight": nbm_market_weight >= 0.01,
        "nbm_market_stack_improves_holdout_log_loss": comparisons[
            "nbm_market_stack_minus_market_holdout"
        ]["candidate_minus_reference"]["log_loss"] < 0,
        "nbm_market_stack_improves_holdout_rps": comparisons[
            "nbm_market_stack_minus_market_holdout"
        ]["candidate_minus_reference"]["rps"] < 0,
        "recent_nbm_not_negative_on_log_loss": comparisons[
            "nbm_minus_hrrr_recent"
        ]["candidate_minus_reference"]["log_loss"] <= 0,
        "recent_nbm_not_negative_on_rps": comparisons["nbm_minus_hrrr_recent"][
            "candidate_minus_reference"
        ]["rps"] <= 0,
    }
    accepted = all(
        value for key, value in checks.items() if key != "weather_next_available"
    )
    verdict = (
        "ACCEPT_NBM_FOR_PRICING_RESEARCH_WEATHERNEXT_UNAVAILABLE"
        if accepted
        else "REJECT_NBM_FOR_F2_WEATHERNEXT_UNAVAILABLE"
    )
    by_horizon = materialized.groupby(
        ["horizon", "status"], observed=True
    ).size().unstack(fill_value=0)
    result = {
        "status": "COMPLETE",
        "verdict": verdict,
        "contract": {
            "evaluation": {**contract.to_dict(), "fingerprint": contract.fingerprint},
            "nbm": {
                "version": NBM_HISTORICAL_CONTRACT_VERSION,
                "operational_version": "5.0",
                "operational_start": NBM_OPERATIONAL_START.isoformat(),
                "availability_rule": "cycle initialization plus two hours",
                "target_transform": (
                    "normal distribution from nearest-grid 12-hour TMAX "
                    "mean/stddev window containing 15:00 station-local"
                ),
                "horizons": list(HORIZONS),
            },
            "stacking": {
                "chronological_fit_fraction": 0.60,
                "fit_weather_dates": sorted(fit_dates),
                "untouched_holdout_weather_dates": sorted(holdout_dates),
                "nbm_weight_over_hrrr": nbm_hrrr_weight,
                "nbm_weight_over_market": nbm_market_weight,
            },
        },
        "inputs": {"database": str(database), "cache_dir": str(cache_dir)},
        "coverage": {
            "initial_identical_rows": len(models),
            "complete_market_ladder_exclusions": len(exclusions),
            "scored_rows": len(scored),
            "weather_dates": len(unique_dates),
            "stations": int(scored["station"].nunique()),
            "first_date": unique_dates[0],
            "last_date": unique_dates[-1],
            "nbm_d0_coverage": float(valid.mean()),
            "materialized_by_horizon": by_horizon.to_dict(orient="index"),
            "archive_objects": len(provenance),
            "archive_bytes": int(sum(item["byte_count"] for item in provenance)),
        },
        "metrics_all": all_metrics,
        "metrics_holdout": holdout_metrics,
        "comparisons": comparisons,
        "station_slices": station_slices,
        "regime_slices": regime_slices,
        "acceptance_checks": checks,
        "limitations": [
            "WeatherNext 2 was not scored because approved Google access and ingestion-time manifests remain unavailable.",
            "Historical NBM Last-Modified metadata is provenance only; causal eligibility uses the frozen conservative cycle-plus-two-hour rule.",
            "The NBM target is a nearest-grid 12-hour daytime-maximum proxy, not an airport-local-day oracle.",
            "D-1 source distributions are materialized, but the ledger has no D-1 baseline or complete market ladders, so only D0 is scored.",
            "Acceptance would authorize Price Sheet V2 research only, never funded trading.",
        ],
        "exclusions": exclusions,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    materialized.to_csv(out / "materialized_nbm.csv", index=False)
    scored[
        [
            "station",
            "market_date",
            "decision_time_utc",
            "target_value",
            "nbm_tmax_mean_f",
            "nbm_tmax_std_f",
            "cycle_at_utc",
            "forecast_hour",
            "regime",
        ]
    ].to_csv(out / "identical_cohort.csv", index=False)
    (out / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    (out / "report.md").write_text(render_report(result))
    return result


def _slice_metrics(
    frame: pd.DataFrame,
    candidate: np.ndarray,
    reference: np.ndarray,
    targets: np.ndarray,
    contract: EvaluationContract,
    column: str,
) -> list[dict[str, Any]]:
    output = []
    for value in sorted(frame[column].astype(str).unique()):
        mask = frame[column].astype(str).eq(value).to_numpy()
        if frame.loc[mask, "market_date"].astype(str).nunique() < 2:
            continue
        comparison = metric_differences(
            candidate[mask],
            reference[mask],
            targets[mask],
            frame.loc[mask, "market_date"],
            contract,
        )
        output.append({column: value, **comparison})
    return output


def render_report(result: Mapping[str, Any]) -> str:
    checks = result["acceptance_checks"]
    comparisons = result["comparisons"]
    lines = [
        "# F2 WeatherNext/NBM Identical-Coverage Benchmark",
        "",
        f"Status: {result['status']}",
        "",
        f"Verdict: **{result['verdict']}**",
        "",
        "## Cohort",
        "",
        f"- {result['coverage']['scored_rows']} identical station/date rows across {result['coverage']['weather_dates']} weather dates and {result['coverage']['stations']} stations.",
        f"- Dates: {result['coverage']['first_date']} through {result['coverage']['last_date']}.",
        f"- D0 NBM coverage: {result['coverage']['nbm_d0_coverage']:.1%}.",
        "",
        "## Held-Out Incremental Results",
        "",
        "| comparison | delta log loss | delta RPS |",
        "| --- | ---: | ---: |",
    ]
    keys = [
        "nbm_hrrr_stack_minus_hrrr_holdout",
        "nbm_market_stack_minus_market_holdout",
        "nbm_minus_hrrr_recent",
    ]
    for key in keys:
        delta = comparisons[key]["candidate_minus_reference"]
        lines.append(
            f"| {key} | {delta['log_loss']:.5f} | {delta['rps']:.5f} |"
        )
    lines.extend(
        [
            "",
            "Negative deltas favor NBM or the NBM stack.",
            "",
            "## Gate",
            "",
        ]
    )
    lines.extend(
        f"- {'PASS' if value else 'FAIL'}: `{key}`"
        for key, value in checks.items()
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.append("")
    return "\n".join(lines)
