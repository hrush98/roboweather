#!/usr/bin/env python3
"""Explore prediction snapshot opportunities without writing policy rows."""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.policy_leaderboard import bucket_type, bucket_won, edge_band, entry_band, probability_band, return_risk, sharpe
from weather_trader.research.policies import CONSENSUS_GROUPS

ACTIVE_LOCAL_DB = Path.home() / ".local/state/roboweather/research_2026-05-08_multimodel.sqlite"

ENTRY_FIELDS = (
    "selected_yes_ask",
    "selected_no_ask",
    "entry_price",
)

LIQUIDITY_FIELDS = (
    "selected_best_bid",
    "selected_best_ask",
    "selected_spread",
    "selected_depth_at_ask",
    "selected_depth_ask_plus_0_01",
    "selected_depth_ask_plus_0_03",
    "selected_depth_ask_plus_0_05",
    "selected_book_timestamp",
    "selected_book_age_seconds",
    "selected_sweep_price_cap",
    "selected_sweep_depth_to_cap",
    "selected_sweep_fillable_25_usd",
    "selected_sweep_fillable_50_usd",
    "selected_sweep_fillable_100_usd",
    "selected_sweep_vwap_25",
    "selected_sweep_vwap_50",
    "selected_sweep_vwap_100",
)

MODE_ORDER = (
    "all_snapshots",
    "first_opportunity",
    "station_date_first",
    "edge_improve_50",
    "best_edge",
    "best_liquidity",
    "hindsight_best",
)

PM_ACTIVE_DYNAMIC_MODEL = "dynamic_bucket_pm_active_us12_obs_2022_2025"
PM_ACTIVE_MVP_MODEL = "mvp_pm_active_us12_obs_2022_2025"
PM_ACTIVE_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025"
PM_ACTIVE_CATBOOST_MODEL = "catboost_bucket_pm_active_us12_obs_2022_2025"
PM_ACTIVE_HIGH_REGRESSION_MODEL = "high_regression_pm_active_us12_obs_2022_2025"
PM_ACTIVE_NGBOOST_MODEL = "ngboost_normal_pm_active_us12_obs_2022_2025"

HRRR_V2_DYNAMIC_MODEL = "dynamic_bucket_hrrr_v2_obs_2022_2025"
HRRR_V2_MVP_MODEL = "mvp_hrrr_v2_obs_2022_2025"
HRRR_V2_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_hrrr_v2_obs_2022_2025"
HRRR_V2_CATBOOST_MODEL = "catboost_bucket_hrrr_v2_obs_2022_2025"
HRRR_V2_HIGH_REGRESSION_MODEL = "high_regression_hrrr_v2_obs_2022_2025"
HRRR_V2_NGBOOST_MODEL = "ngboost_normal_hrrr_v2_obs_2022_2025"

HRRR_RICH_DYNAMIC_MODEL = "dynamic_bucket_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_RICH_MVP_MODEL = "mvp_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_RICH_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_RICH_CATBOOST_MODEL = "catboost_bucket_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_RICH_HIGH_REGRESSION_MODEL = "high_regression_hrrr_rich_pm_active_us12_obs_2022_2025"
HRRR_RICH_NGBOOST_MODEL = "ngboost_normal_hrrr_rich_pm_active_us12_obs_2022_2025"

METAR_HRRR_RICH_DYNAMIC_MODEL = "dynamic_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_MVP_MODEL = "mvp_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_CATBOOST_MODEL = "catboost_bucket_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_HIGH_REGRESSION_MODEL = "high_regression_metar_hrrr_rich_pm_active_us12_obs_2022_2025"
METAR_HRRR_RICH_NGBOOST_MODEL = "ngboost_normal_metar_hrrr_rich_pm_active_us12_obs_2022_2025"

POLICY_SEARCH_CONSENSUS_GROUPS: dict[str, tuple[str, ...]] = {
    **CONSENSUS_GROUPS,
    # Historical pm_us12 research policies used dynamic_default + mvp. The current
    # live evaluator only registers dynamic_tuned + mvp, so the snapshot search
    # must add this group explicitly to reconstruct the older policy families.
    "pm_active_us12_dynamic_mvp": (PM_ACTIVE_DYNAMIC_MODEL, PM_ACTIVE_MVP_MODEL),
    "hrrr_v2_dynamic_mvp": (HRRR_V2_DYNAMIC_MODEL, HRRR_V2_MVP_MODEL),
    "hrrr_rich_dynamic_mvp": (HRRR_RICH_DYNAMIC_MODEL, HRRR_RICH_MVP_MODEL),
    "hrrr_rich_dynamic_tuned_mvp": (HRRR_RICH_DYNAMIC_TUNED_MODEL, HRRR_RICH_MVP_MODEL),
    "hrrr_rich_catboost_mvp": (HRRR_RICH_CATBOOST_MODEL, HRRR_RICH_MVP_MODEL),
    "hrrr_rich_bucket_consensus": (HRRR_RICH_DYNAMIC_TUNED_MODEL, HRRR_RICH_CATBOOST_MODEL),
    "hrrr_rich_three_model_consensus": (HRRR_RICH_DYNAMIC_TUNED_MODEL, HRRR_RICH_CATBOOST_MODEL, HRRR_RICH_MVP_MODEL),
    "metar_hrrr_rich_dynamic_mvp": (METAR_HRRR_RICH_DYNAMIC_MODEL, METAR_HRRR_RICH_MVP_MODEL),
    "metar_hrrr_rich_dynamic_tuned_mvp": (METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL, METAR_HRRR_RICH_MVP_MODEL),
    "metar_hrrr_rich_catboost_mvp": (METAR_HRRR_RICH_CATBOOST_MODEL, METAR_HRRR_RICH_MVP_MODEL),
    "metar_hrrr_rich_bucket_consensus": (METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL, METAR_HRRR_RICH_CATBOOST_MODEL),
    "metar_hrrr_rich_three_model_consensus": (METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL, METAR_HRRR_RICH_CATBOOST_MODEL, METAR_HRRR_RICH_MVP_MODEL),
}

KNOWN_MODEL_ALIASES = {
    PM_ACTIVE_DYNAMIC_MODEL: "pm_us12_dynamic",
    PM_ACTIVE_MVP_MODEL: "pm_us12_mvp",
    PM_ACTIVE_DYNAMIC_TUNED_MODEL: "pm_us12_dynamic_tuned",
    PM_ACTIVE_CATBOOST_MODEL: "pm_us12_catboost",
    PM_ACTIVE_HIGH_REGRESSION_MODEL: "pm_us12_high_regression",
    PM_ACTIVE_NGBOOST_MODEL: "pm_us12_ngboost",
    HRRR_V2_DYNAMIC_MODEL: "hrrr_v2_dynamic",
    HRRR_V2_MVP_MODEL: "hrrr_v2_mvp",
    HRRR_V2_DYNAMIC_TUNED_MODEL: "hrrr_v2_dynamic_tuned",
    HRRR_V2_CATBOOST_MODEL: "hrrr_v2_catboost",
    HRRR_V2_HIGH_REGRESSION_MODEL: "hrrr_v2_high_regression",
    HRRR_V2_NGBOOST_MODEL: "hrrr_v2_ngboost",
    HRRR_RICH_DYNAMIC_MODEL: "hrrr_rich_dynamic",
    HRRR_RICH_MVP_MODEL: "hrrr_rich_mvp",
    HRRR_RICH_DYNAMIC_TUNED_MODEL: "hrrr_rich_dynamic_tuned",
    HRRR_RICH_CATBOOST_MODEL: "hrrr_rich_catboost",
    HRRR_RICH_HIGH_REGRESSION_MODEL: "hrrr_rich_high_regression",
    HRRR_RICH_NGBOOST_MODEL: "hrrr_rich_ngboost",
    METAR_HRRR_RICH_DYNAMIC_MODEL: "metar_hrrr_rich_dynamic",
    METAR_HRRR_RICH_MVP_MODEL: "metar_hrrr_rich_mvp",
    METAR_HRRR_RICH_DYNAMIC_TUNED_MODEL: "metar_hrrr_rich_dynamic_tuned",
    METAR_HRRR_RICH_CATBOOST_MODEL: "metar_hrrr_rich_catboost",
    METAR_HRRR_RICH_HIGH_REGRESSION_MODEL: "metar_hrrr_rich_high_regression",
    METAR_HRRR_RICH_NGBOOST_MODEL: "metar_hrrr_rich_ngboost",
    "low_dynamic_bucket_obs_2022_2025": "low_pm_us12_dynamic",
    "low_mvp_obs_2022_2025": "low_pm_us12_mvp",
}

KNOWN_CONSENSUS_ALIASES = {
    "pm_active_us12_dynamic_mvp": "pm_us12_consensus",
    "obs_dynamic_tuned_mvp": "pm_us12_dynamic_tuned_mvp",
    "obs_catboost_mvp": "pm_us12_catboost_mvp",
    "obs_bucket_consensus": "pm_us12_bucket_consensus",
    "obs_three_model_consensus": "pm_us12_three_model_consensus",
    "hrrr_v2_dynamic_mvp": "hrrr_v2_consensus",
    "hrrr_v2_dynamic_tuned_mvp": "hrrr_v2_dynamic_tuned_mvp",
    "hrrr_v2_catboost_mvp": "hrrr_v2_catboost_mvp",
    "hrrr_v2_bucket_consensus": "hrrr_v2_bucket_consensus",
    "hrrr_v2_three_model_consensus": "hrrr_v2_three_model_consensus",
    "hrrr_rich_dynamic_mvp": "hrrr_rich_consensus",
    "hrrr_rich_dynamic_tuned_mvp": "hrrr_rich_dynamic_tuned_mvp",
    "hrrr_rich_catboost_mvp": "hrrr_rich_catboost_mvp",
    "hrrr_rich_bucket_consensus": "hrrr_rich_bucket_consensus",
    "hrrr_rich_three_model_consensus": "hrrr_rich_three_model_consensus",
    "metar_hrrr_rich_dynamic_mvp": "metar_hrrr_rich_consensus",
    "metar_hrrr_rich_dynamic_tuned_mvp": "metar_hrrr_rich_dynamic_tuned_mvp",
    "metar_hrrr_rich_catboost_mvp": "metar_hrrr_rich_catboost_mvp",
    "metar_hrrr_rich_bucket_consensus": "metar_hrrr_rich_bucket_consensus",
    "metar_hrrr_rich_three_model_consensus": "metar_hrrr_rich_three_model_consensus",
    "low_pm_active_us12_dynamic_mvp": "low_pm_us12_consensus",
}

STRATEGY_ALIASES = {
    "HIGH_CONVICTION": "hc",
    "BEST_BUCKET": "best",
    "TAIL": "tail",
    "MAX_SO_FAR": "max_so_far",
}


@dataclass(frozen=True)
class ModeResult:
    name: str
    rows: list[dict[str, Any]]
    hindsight_only: bool = False


@dataclass(frozen=True)
class EntryBandSpec:
    slug: str | None
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True)
class LocalWindowSpec:
    slug: str | None
    start: str | None
    end: str | None


@dataclass(frozen=True)
class PolicySearchSpec:
    name: str
    source: str
    strategy_bucket: str | None = None
    model_name: str | None = None
    model_group: str | None = None
    obs_delay_bucket: str | None = None
    entry_price_min: float | None = None
    entry_price_max: float | None = None
    local_decision_start: str | None = None
    local_decision_end: str | None = None
    uniqueness_key_mode: str = "station_date"


def default_db_path() -> Path:
    explicit = os.environ.get("ROBOWEATHER_STATUS_DB") or os.environ.get("DB")
    if explicit:
        return Path(explicit).expanduser()
    if ACTIVE_LOCAL_DB.exists():
        return ACTIVE_LOCAL_DB
    return REPO_ROOT / "data/paper/research_2026-05-08_multimodel.sqlite"


def load_snapshot_rows(
    db: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    market_family: str | None = None,
    us_high_temp_only: bool = False,
) -> list[dict[str, Any]]:
    snapshot_columns = table_columns(db, "prediction_snapshots")
    has_station_date_outcomes = table_exists(db, "station_date_outcomes")
    outcome_fields = (
        "sdo.final_high_tmpf,\n            sdo.final_low_tmpf,\n            sdo.resolved_at as weather_resolved_at"
        if has_station_date_outcomes
        else "null as final_high_tmpf,\n            null as final_low_tmpf,\n            null as weather_resolved_at"
    )
    outcome_join = (
        "left join station_date_outcomes sdo\n            on sdo.station = ps.station\n           and sdo.market_date = ps.market_date"
        if has_station_date_outcomes
        else ""
    )

    def ps_col(name: str, fallback: str = "null") -> str:
        return f"ps.{name}" if name in snapshot_columns else fallback

    where = ["ps.selected_side != 'SKIP'", "ps.selected_market_id is not null"]
    params: list[Any] = []
    if start_date:
        where.append("ps.market_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("ps.market_date <= ?")
        params.append(end_date)
    if market_family:
        if "market_family" in snapshot_columns:
            where.append("coalesce(ps.market_family, 'HIGH_TEMP') = ?")
            params.append(market_family)
        elif market_family != "HIGH_TEMP":
            return []
    if us_high_temp_only:
        where.append("ps.station like ?")
        params.append("K%")
        where.append("ps.model_name not like ?")
        params.append("%international%")
        where.append("ps.model_name not like ?")
        params.append("low_%")

    sql = f"""
        select
            ps.id,
            ps.timestamp,
            ps.station,
            ps.market_date,
            ps.decision_time_utc,
            ps.decision_time_local,
            ps.latest_obs_time_utc,
            ps.latest_obs_time_local,
            ps.obs_age_minutes,
            ps.obs_delay_bucket,
            ps.strategy_bucket,
            ps.selected_market_id,
            ps.selected_bucket,
            ps.selected_side,
            coalesce(pr.edge, ps.selected_edge) as selected_edge,
            ps.selected_fair_yes,
            ps.selected_fair_no,
            ps.selected_yes_ask,
            ps.selected_no_ask,
            ps.model_name,
            {ps_col("market_family", "'HIGH_TEMP'")} as market_family,
            {ps_col("selected_best_bid")} as selected_best_bid,
            {ps_col("selected_best_ask")} as selected_best_ask,
            {ps_col("selected_spread")} as selected_spread,
            {ps_col("selected_depth_at_ask")} as selected_depth_at_ask,
            {ps_col("selected_depth_ask_plus_0_01")} as selected_depth_ask_plus_0_01,
            {ps_col("selected_depth_ask_plus_0_03")} as selected_depth_ask_plus_0_03,
            {ps_col("selected_depth_ask_plus_0_05")} as selected_depth_ask_plus_0_05,
            {ps_col("selected_book_timestamp")} as selected_book_timestamp,
            {ps_col("selected_book_age_seconds")} as selected_book_age_seconds,
            {ps_col("selected_sweep_price_cap")} as selected_sweep_price_cap,
            {ps_col("selected_sweep_depth_to_cap")} as selected_sweep_depth_to_cap,
            {ps_col("selected_sweep_fillable_25_usd")} as selected_sweep_fillable_25_usd,
            {ps_col("selected_sweep_fillable_50_usd")} as selected_sweep_fillable_50_usd,
            {ps_col("selected_sweep_fillable_100_usd")} as selected_sweep_fillable_100_usd,
            {ps_col("selected_sweep_vwap_25")} as selected_sweep_vwap_25,
            {ps_col("selected_sweep_vwap_50")} as selected_sweep_vwap_50,
            {ps_col("selected_sweep_vwap_100")} as selected_sweep_vwap_100,
            pr.correct,
            pr.entry_price,
            pr.paper_pnl,
            pr.resolved_at,
            {outcome_fields}
        from prediction_snapshots ps
        left join prediction_results pr on pr.prediction_snapshot_id = ps.id
        {outcome_join}
        where {" and ".join(where)}
        order by ps.timestamp, ps.id
    """
    rows = []
    for row in db.execute(sql, params).fetchall():
        item = dict(row)
        item["source"] = str(item.get("model_name") or "missing_model")
        item["selected_fair"] = selected_fair(item)
        item["entry_price"] = entry_price(item)
        score_snapshot_row(item)
        rows.append(item)
    return rows


def table_exists(db: sqlite3.Connection, table: str) -> bool:
    return bool(db.execute("select 1 from sqlite_master where type = ? and name = ?", ("table", table)).fetchone())


def table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in db.execute(f"pragma table_info({table})")}


def score_snapshot_row(item: dict[str, Any]) -> None:
    if item.get("paper_pnl") is not None and item.get("correct") is not None:
        return
    entry = entry_price(item)
    if entry is None:
        return
    market_family = str(item.get("market_family") or "HIGH_TEMP")
    final_temp = float_or_none(item.get("final_low_tmpf" if market_family == "LOW_TEMP" else "final_high_tmpf"))
    if final_temp is None:
        return
    selected_bucket = item.get("selected_bucket")
    yes_won = bucket_won(final_temp, selected_bucket)
    correct = yes_won if item.get("selected_side") == "BUY_YES" else not yes_won
    item["correct"] = 1 if correct else 0
    item["paper_pnl"] = (1.0 - entry) if correct else -entry
    item["entry_price"] = entry
    item["resolved_at"] = item.get("weather_resolved_at")


def build_consensus_rows(
    rows: list[dict[str, Any]],
    *,
    consensus_groups: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    groups = consensus_groups or CONSENSUS_GROUPS
    groups_by_model = consensus_groups_by_model(groups)
    by_key: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for item in rows:
        model_name = str(item.get("model_name") or "")
        group_names = groups_by_model.get(model_name)
        if not group_names:
            continue
        for group_name in group_names:
            key = consensus_key(group_name, item)
            by_key.setdefault(key, {})[model_name] = item

    consensus: list[dict[str, Any]] = []
    for key, by_model in by_key.items():
        group_name = str(key[0])
        required_models = groups[group_name]
        participants = [by_model.get(model_name) for model_name in required_models]
        if any(item is None for item in participants):
            continue
        agreed = [item for item in participants if item is not None]
        newest = max(agreed, key=sort_key)
        base = min(agreed, key=lambda item: int(item["id"]))
        item = dict(base)
        for field in (*ENTRY_FIELDS, *LIQUIDITY_FIELDS):
            item[field] = newest.get(field)
        item["id"] = min(int(row["id"]) for row in agreed)
        item["timestamp"] = max(str(row.get("timestamp") or "") for row in agreed)
        item["model_name"] = group_name
        item["source"] = f"consensus:{group_name}"
        item["selected_edge"] = mean(float_or_none(row.get("selected_edge")) for row in agreed)
        item["selected_fair"] = mean(selected_fair(row) for row in agreed)
        item["paper_pnl"] = paper_pnl_from_entry(item.get("correct"), entry_price(item))
        item["source_prediction_snapshot_ids"] = [int(row["id"]) for row in agreed]
        item["consensus_models"] = list(required_models)
        item["model_fairs"] = {str(row.get("model_name")): selected_fair(row) for row in agreed}
        consensus.append(item)
    return sorted(consensus, key=sort_key)


def consensus_groups_by_model(groups: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    by_model: dict[str, tuple[str, ...]] = {}
    for group_name, model_names in groups.items():
        for model_name in model_names:
            by_model.setdefault(model_name, ())
            by_model[model_name] = (*by_model[model_name], group_name)
    return by_model


def consensus_key(group_name: str, item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        group_name,
        item.get("market_family") or "HIGH_TEMP",
        item.get("station"),
        item.get("market_date"),
        item.get("obs_delay_bucket"),
        item.get("strategy_bucket"),
        item.get("selected_side"),
        item.get("selected_market_id"),
        item.get("selected_bucket"),
    )


def selected_fair(item: dict[str, Any]) -> float | None:
    consensus_fair = float_or_none(item.get("selected_fair"))
    if consensus_fair is not None and str(item.get("source") or "").startswith("consensus:"):
        return consensus_fair
    if item.get("selected_side") == "BUY_YES":
        return float_or_none(item.get("selected_fair_yes"))
    if item.get("selected_side") == "BUY_NO":
        return float_or_none(item.get("selected_fair_no"))
    return float_or_none(item.get("selected_fair"))


def entry_price(item: dict[str, Any]) -> float | None:
    result_entry = float_or_none(item.get("entry_price"))
    if result_entry is not None:
        return result_entry
    if item.get("selected_side") == "BUY_YES":
        return float_or_none(item.get("selected_yes_ask"))
    if item.get("selected_side") == "BUY_NO":
        return float_or_none(item.get("selected_no_ask"))
    return None


def paper_pnl_from_entry(correct: Any, entry: float | None) -> float | None:
    if correct is None or entry is None:
        return None
    return (1.0 - entry) if int(correct) else -entry


def apply_modes(rows: list[dict[str, Any]]) -> dict[str, ModeResult]:
    return {
        "all_snapshots": ModeResult("all_snapshots", sorted(rows, key=sort_key)),
        "first_opportunity": ModeResult("first_opportunity", first_by_key(rows, opportunity_key)),
        "station_date_first": ModeResult("station_date_first", first_by_key(rows, station_date_key)),
        "edge_improve_50": ModeResult("edge_improve_50", edge_improve_rows(rows)),
        "best_edge": ModeResult("best_edge", best_by_key(rows, opportunity_key, best_edge_key)),
        "best_liquidity": ModeResult("best_liquidity", best_by_key(rows, opportunity_key, best_liquidity_key)),
        "hindsight_best": ModeResult("hindsight_best", best_by_key(rows, opportunity_key, hindsight_key), True),
    }


def build_policy_search_specs(rows: list[dict[str, Any]]) -> list[PolicySearchSpec]:
    specs: list[PolicySearchSpec] = []
    seen_names: set[str] = set()
    present_models = {str(row.get("model_name") or "") for row in rows if row.get("model_name")}
    present_strategies = {str(row.get("strategy_bucket") or "") for row in rows if row.get("strategy_bucket")}

    for model_name in sorted(present_models):
        if not model_name or model_name in POLICY_SEARCH_CONSENSUS_GROUPS:
            continue
        alias = model_alias(model_name)
        for strategy in sorted(present_strategies):
            if strategy == "MAX_SO_FAR":
                continue
            for spec in policy_spec_grid(
                base_alias=alias,
                source="model",
                strategy_bucket=strategy,
                model_name=model_name,
            ):
                add_policy_spec(specs, seen_names, spec)

    for group_name, required_models in sorted(POLICY_SEARCH_CONSENSUS_GROUPS.items()):
        if not set(required_models).issubset(present_models):
            continue
        alias = consensus_alias(group_name)
        for strategy in sorted(strategy for strategy in present_strategies if strategy != "MAX_SO_FAR"):
            for spec in policy_spec_grid(
                base_alias=alias,
                source="consensus",
                strategy_bucket=strategy,
                model_group=group_name,
            ):
                add_policy_spec(specs, seen_names, spec)

    if "MAX_SO_FAR" in present_strategies:
        for spec in policy_spec_grid(base_alias="pm_us12_max_so_far", source="max_so_far", strategy_bucket="MAX_SO_FAR"):
            add_policy_spec(specs, seen_names, spec)

    return specs


def policy_spec_grid(
    *,
    base_alias: str,
    source: str,
    strategy_bucket: str,
    model_name: str | None = None,
    model_group: str | None = None,
) -> list[PolicySearchSpec]:
    entry_bands = (
        EntryBandSpec(None, None, None),
        EntryBandSpec("entry_00_10", 0.00, 0.10),
        EntryBandSpec("entry_05_10", 0.05, 0.10),
        EntryBandSpec("entry_10_25", 0.10, 0.25),
        EntryBandSpec("entry_25_50", 0.25, 0.50),
        EntryBandSpec("entry_50_75", 0.50, 0.75),
        EntryBandSpec("entry_75_100", 0.75, 1.00),
        EntryBandSpec("entry_25_75", 0.25, 0.75),
        EntryBandSpec("no_tiny", 0.05, None),
    )
    windows = (
        LocalWindowSpec(None, None, None),
        LocalWindowSpec("early", "10:00", "12:00"),
        LocalWindowSpec("late", "12:00", "15:00"),
    )
    obs_delays: tuple[str | None, ...] = (None, "5m", "10m", "15m")
    uniqueness_modes = ("station_date", "station_date_bucket_side", "station_date_bucket_side_obs_delay")

    specs = []
    strategy_slug = STRATEGY_ALIASES.get(strategy_bucket, slugify(strategy_bucket))
    for obs_delay in obs_delays:
        for window in windows:
            for entry_band in entry_bands:
                for uniqueness_mode in uniqueness_modes:
                    parts = [base_alias]
                    if not base_alias.endswith(f"_{strategy_slug}"):
                        parts.append(strategy_slug)
                    if obs_delay is not None:
                        parts.append(obs_delay)
                    if window.slug is not None:
                        parts.append(window.slug)
                    if entry_band.slug is not None:
                        parts.append(entry_band.slug)
                    if uniqueness_mode == "station_date_bucket_side":
                        parts.append("by_bucket_side")
                    elif uniqueness_mode == "station_date_bucket_side_obs_delay":
                        parts.append("by_bucket_side_delay")
                    parts.append("first")
                    specs.append(
                        PolicySearchSpec(
                            name="_".join(parts),
                            source=source,
                            strategy_bucket=strategy_bucket,
                            model_name=model_name,
                            model_group=model_group,
                            obs_delay_bucket=obs_delay,
                            entry_price_min=entry_band.minimum,
                            entry_price_max=entry_band.maximum,
                            local_decision_start=window.start,
                            local_decision_end=window.end,
                            uniqueness_key_mode=uniqueness_mode,
                        )
                    )
    return specs


def add_policy_spec(specs: list[PolicySearchSpec], seen_names: set[str], spec: PolicySearchSpec) -> None:
    if spec.name in seen_names:
        return
    seen_names.add(spec.name)
    specs.append(spec)


def build_compact_policy_search_rows(base_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    search_rows = [
        *base_rows,
        *build_consensus_rows(base_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS),
    ]
    specs = build_compact_policy_search_specs(base_rows)
    position_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    indexed: dict[tuple[str, str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in search_rows:
        source = "consensus" if str(row.get("source") or "").startswith("consensus:") else "model"
        group_name = str(row.get("source") or "").removeprefix("consensus:") if source == "consensus" else None
        indexed[(source, str(row.get("model_name") or group_name or ""), str(row.get("strategy_bucket") or ""))].append(row)
    for spec in specs:
        model_key = spec.model_name if spec.source == "model" else spec.model_group
        candidates = [
            row
            for row in indexed.get((spec.source, str(model_key or ""), str(spec.strategy_bucket or "")), [])
            if row_matches_policy_spec(spec, row)
        ]
        selected = first_policy_rows(spec, candidates)
        if not selected:
            continue
        policy_rows = [policy_row(spec, row) for row in selected]
        position_rows.extend(policy_rows)
        summaries.append(summarize_policy_candidate(spec, policy_rows))
    return position_rows, sorted(summaries, key=policy_default_rank)


def build_compact_policy_search_specs(rows: list[dict[str, Any]]) -> list[PolicySearchSpec]:
    specs: list[PolicySearchSpec] = []
    seen_names: set[str] = set()
    present_models = {str(row.get("model_name") or "") for row in rows if row.get("model_name")}
    for model_name in sorted(present_models):
        if not model_name or model_name in POLICY_SEARCH_CONSENSUS_GROUPS:
            continue
        for spec in compact_policy_spec_grid(
            base_alias=model_alias(model_name),
            source="model",
            model_name=model_name,
        ):
            add_policy_spec(specs, seen_names, spec)
    for group_name, required_models in sorted(POLICY_SEARCH_CONSENSUS_GROUPS.items()):
        if not set(required_models).issubset(present_models):
            continue
        for spec in compact_policy_spec_grid(
            base_alias=consensus_alias(group_name),
            source="consensus",
            model_group=group_name,
        ):
            add_policy_spec(specs, seen_names, spec)
    return specs


def compact_policy_spec_grid(
    *,
    base_alias: str,
    source: str,
    model_name: str | None = None,
    model_group: str | None = None,
) -> list[PolicySearchSpec]:
    entry_bands = (
        EntryBandSpec("entry_00_50", 0.00, 0.50),
        EntryBandSpec("entry_05_50", 0.05, 0.50),
    )
    windows = (
        LocalWindowSpec(None, None, None),
        LocalWindowSpec("late", "12:00", "15:00"),
    )
    obs_delays: tuple[str | None, ...] = (None, "10m", "15m")
    uniqueness_modes = ("station_date", "station_date_bucket_side_obs_delay")
    specs = []
    for obs_delay in obs_delays:
        for window in windows:
            for entry_band in entry_bands:
                for uniqueness_mode in uniqueness_modes:
                    parts = [base_alias, "hc"]
                    if obs_delay is not None:
                        parts.append(obs_delay)
                    if window.slug is not None:
                        parts.append(window.slug)
                    if entry_band.slug is not None:
                        parts.append(entry_band.slug)
                    if uniqueness_mode == "station_date_bucket_side_obs_delay":
                        parts.append("by_bucket_side_delay")
                    parts.append("first")
                    specs.append(
                        PolicySearchSpec(
                            name="_".join(parts),
                            source=source,
                            strategy_bucket="HIGH_CONVICTION",
                            model_name=model_name,
                            model_group=model_group,
                            obs_delay_bucket=obs_delay,
                            entry_price_min=entry_band.minimum,
                            entry_price_max=entry_band.maximum,
                            local_decision_start=window.start,
                            local_decision_end=window.end,
                            uniqueness_key_mode=uniqueness_mode,
                        )
                    )
    return specs


def build_policy_search_rows(base_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    search_rows = [
        *base_rows,
        *build_consensus_rows(base_rows, consensus_groups=POLICY_SEARCH_CONSENSUS_GROUPS),
    ]
    specs = build_policy_search_specs(base_rows)
    position_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for spec in specs:
        candidates = [row for row in search_rows if row_matches_policy_spec(spec, row)]
        selected = first_policy_rows(spec, candidates)
        if not selected:
            continue
        policy_rows = [policy_row(spec, row) for row in selected]
        position_rows.extend(policy_rows)
        summaries.append(summarize_policy_candidate(spec, policy_rows))
    return position_rows, sorted(summaries, key=policy_default_rank)


def row_matches_policy_spec(spec: PolicySearchSpec, row: dict[str, Any]) -> bool:
    if spec.source == "model" and row.get("model_name") != spec.model_name:
        return False
    if spec.source == "consensus":
        if row.get("source") != f"consensus:{spec.model_group}":
            return False
    if spec.source == "max_so_far" and row.get("strategy_bucket") != "MAX_SO_FAR":
        return False
    if spec.source != "max_so_far" and row.get("strategy_bucket") == "MAX_SO_FAR":
        return False
    if spec.strategy_bucket is not None and row.get("strategy_bucket") != spec.strategy_bucket:
        return False
    if spec.obs_delay_bucket is not None and row.get("obs_delay_bucket") != spec.obs_delay_bucket:
        return False
    if not in_range(entry_price(row), spec.entry_price_min, spec.entry_price_max):
        return False
    if spec.local_decision_start is not None or spec.local_decision_end is not None:
        decision_time = local_time(row.get("decision_time_local"))
        if decision_time is None:
            return False
        start = parse_hhmm(spec.local_decision_start) if spec.local_decision_start else None
        end = parse_hhmm(spec.local_decision_end) if spec.local_decision_end else None
        if start is not None and decision_time < start:
            return False
        if end is not None and decision_time >= end:
            return False
    return True


def first_policy_rows(spec: PolicySearchSpec, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in sorted(rows, key=sort_key):
        key = policy_uniqueness_key(spec, row)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def policy_uniqueness_key(spec: PolicySearchSpec, row: dict[str, Any]) -> tuple[Any, ...]:
    key = [row.get("station"), row.get("market_date"), row.get("market_family") or "HIGH_TEMP"]
    if spec.uniqueness_key_mode == "station_date_bucket_side":
        key.extend([row.get("selected_bucket"), row.get("selected_side")])
    elif spec.uniqueness_key_mode == "station_date_bucket_side_obs_delay":
        key.extend([row.get("selected_bucket"), row.get("selected_side"), row.get("obs_delay_bucket")])
    elif spec.uniqueness_key_mode != "station_date":
        raise ValueError(f"Unsupported uniqueness_key_mode: {spec.uniqueness_key_mode}")
    return tuple(key)


def policy_row(spec: PolicySearchSpec, row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["policy_name"] = spec.name
    item["policy_source"] = spec.source
    item["policy_model_name"] = spec.model_name
    item["policy_model_group"] = spec.model_group
    item["policy_filters"] = policy_filter_label(spec)
    return item


def summarize_policy_candidate(spec: PolicySearchSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_slice(spec.name, rows, min_n=0)
    summary["policy_name"] = spec.name
    summary["source"] = spec.source
    summary["model"] = spec.model_name or spec.model_group or "max_so_far"
    summary["strategy"] = spec.strategy_bucket
    summary["filters"] = policy_filter_label(spec)
    summary["scope"] = spec.uniqueness_key_mode
    return summary


def policy_filter_label(spec: PolicySearchSpec) -> str:
    parts = []
    if spec.obs_delay_bucket is not None:
        parts.append(f"obs={spec.obs_delay_bucket}")
    if spec.local_decision_start is not None or spec.local_decision_end is not None:
        parts.append(f"local={spec.local_decision_start or '*'}-{spec.local_decision_end or '*'}")
    if spec.entry_price_min is not None or spec.entry_price_max is not None:
        parts.append(f"entry={format_bound(spec.entry_price_min)}-{format_bound(spec.entry_price_max)}")
    return ",".join(parts) if parts else "none"


def opportunity_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("source"),
        item.get("strategy_bucket"),
        item.get("station"),
        item.get("market_date"),
        item.get("market_family") or "HIGH_TEMP",
        item.get("selected_bucket"),
        item.get("selected_side"),
        item.get("obs_delay_bucket"),
    )


def station_date_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("source"),
        item.get("strategy_bucket"),
        item.get("station"),
        item.get("market_date"),
        item.get("market_family") or "HIGH_TEMP",
    )


def sort_key(item: dict[str, Any]) -> tuple[str, int]:
    return (str(item.get("timestamp") or ""), int(item.get("id") or 0))


def first_by_key(rows: list[dict[str, Any]], key_func: Callable[[dict[str, Any]], tuple[Any, ...]]) -> list[dict[str, Any]]:
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in sorted(rows, key=sort_key):
        selected.setdefault(key_func(row), row)
    return list(selected.values())


def edge_improve_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    last_positive_edge: dict[tuple[Any, ...], float | None] = {}
    for row in sorted(rows, key=sort_key):
        key = opportunity_key(row)
        if key not in last_positive_edge:
            selected.append(row)
            edge = float_or_none(row.get("selected_edge"))
            last_positive_edge[key] = edge if edge is not None and edge > 0 else None
            continue
        edge = float_or_none(row.get("selected_edge"))
        if edge is None or edge <= 0:
            continue
        baseline = last_positive_edge.get(key)
        if baseline is None or edge >= baseline * 1.5:
            selected.append(row)
            last_positive_edge[key] = edge
    return selected


def best_by_key(
    rows: list[dict[str, Any]],
    key_func: Callable[[dict[str, Any]], tuple[Any, ...]],
    score_func: Callable[[dict[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key_func(row)].append(row)
    return [sorted(group, key=score_func)[0] for group in grouped.values()]


def best_edge_key(item: dict[str, Any]) -> tuple[float, str, int]:
    edge = float_or_none(item.get("selected_edge"))
    return (-(edge if edge is not None else -math.inf), *sort_key(item))


def best_liquidity_key(item: dict[str, Any]) -> tuple[float, float, str, int]:
    liquidity = float_or_none(item.get("selected_sweep_fillable_50_usd"))
    return (-(liquidity if liquidity is not None else -math.inf), entry_price(item) or math.inf, *sort_key(item))


def hindsight_key(item: dict[str, Any]) -> tuple[float, str, int]:
    pnl = float_or_none(item.get("paper_pnl"))
    return (-(pnl if pnl is not None else -math.inf), *sort_key(item))


def summarize_slice(
    label: str,
    rows: list[dict[str, Any]],
    *,
    min_n: int,
    hindsight_only: bool = False,
) -> dict[str, Any]:
    resolved_rows = [row for row in rows if row.get("paper_pnl") is not None]
    pending = len(rows) - len(resolved_rows)
    pnls = [float(row["paper_pnl"]) for row in resolved_rows]
    entries = [entry for row in rows if (entry := entry_price(row)) is not None]
    resolved_entries = [entry for row in resolved_rows if (entry := entry_price(row)) is not None]
    fair_values = [fair for row in rows if (fair := selected_fair(row)) is not None]
    edge_values = [edge for row in rows if (edge := float_or_none(row.get("selected_edge"))) is not None]
    liquidity_values = [
        value for row in rows if (value := float_or_none(row.get("selected_sweep_fillable_50_usd"))) is not None
    ]
    book_ages = [value for row in rows if (value := float_or_none(row.get("selected_book_age_seconds"))) is not None]
    risk = sum(resolved_entries)
    pnl = sum(pnls)
    rr = return_risk(pnl, risk)
    flags = []
    if len(resolved_rows) < min_n:
        flags.append("LOW_N")
    if pending > len(resolved_rows):
        flags.append("PENDING_HEAVY")
    if hindsight_only:
        flags.append("HINDSIGHT_ONLY")
    avg_liquidity = mean(liquidity_values)
    if avg_liquidity is not None and avg_liquidity < 50.0:
        flags.append("EXECUTION_WEAK")
    return {
        "label": label,
        "n": len(rows),
        "resolved": len(resolved_rows),
        "pending": pending,
        "win_rate": mean(1.0 if int(row["correct"]) else 0.0 for row in resolved_rows if row.get("correct") is not None),
        "pnl": pnl if resolved_rows else None,
        "risk": risk if resolved_rows else None,
        "rr": rr,
        "sharpe": sharpe(pnls),
        "avg_entry": mean(entries),
        "avg_edge": mean(edge_values),
        "avg_fair": mean(fair_values),
        "avg_sweep_50": avg_liquidity,
        "avg_book_age": mean(book_ages),
        "flags": ",".join(flags) if flags else "OK",
    }


def coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = sum(1 for row in rows if row.get("paper_pnl") is not None)
    return {
        "rows": len(rows),
        "resolved": resolved,
        "pending": len(rows) - resolved,
        "unique_opportunities": len({opportunity_key(row) for row in rows}),
        "stations": len({row.get("station") for row in rows}),
        "market_dates": len({row.get("market_date") for row in rows}),
        "sources": len({row.get("source") for row in rows}),
        "strategies": len({row.get("strategy_bucket") for row in rows}),
    }


def slice_tables(rows: list[dict[str, Any]], *, min_n: int, hindsight_only: bool) -> dict[str, list[dict[str, Any]]]:
    specs: tuple[tuple[str, Callable[[dict[str, Any]], tuple[Any, ...] | str]], ...] = (
        ("By Source", lambda row: str(row.get("source") or "missing")),
        ("By Source Strategy", lambda row: (row.get("source"), row.get("strategy_bucket"))),
        ("By Source Strategy Side", lambda row: (row.get("source"), row.get("strategy_bucket"), row.get("selected_side"))),
        (
            "By Source Strategy Obs Delay",
            lambda row: (row.get("source"), row.get("strategy_bucket"), row.get("obs_delay_bucket") or "missing"),
        ),
        (
            "By Source Strategy Entry Side",
            lambda row: (row.get("source"), row.get("strategy_bucket"), entry_band_value(row), row.get("selected_side")),
        ),
        (
            "By Source Strategy Edge Side",
            lambda row: (row.get("source"), row.get("strategy_bucket"), edge_band(float_or_none(row.get("selected_edge"))), row.get("selected_side")),
        ),
        ("By Station", lambda row: str(row.get("station") or "missing")),
        ("By Bucket Type", lambda row: bucket_type(row.get("selected_bucket"))),
        ("By Decision Hour", lambda row: decision_hour_band(row.get("decision_time_local"))),
        ("By Liquidity", lambda row: liquidity_band(float_or_none(row.get("selected_sweep_fillable_50_usd")))),
    )
    tables = {}
    for name, key_func in specs:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = key_func(row)
            label = " + ".join(str(part) for part in key) if isinstance(key, tuple) else str(key)
            grouped[label].append(row)
        stats = [summarize_slice(label, group, min_n=min_n, hindsight_only=hindsight_only) for label, group in grouped.items()]
        tables[name] = sorted(stats, key=rank_key)
    return tables


def calibration_tables(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    specs = {
        "Fair Calibration": lambda row: probability_band(selected_fair(row)),
        "Edge Calibration": lambda row: edge_band(float_or_none(row.get("selected_edge"))),
        "Entry Calibration": lambda row: entry_band_value(row),
        "Side Calibration": lambda row: str(row.get("selected_side") or "missing"),
        "Obs Delay Calibration": lambda row: str(row.get("obs_delay_bucket") or "missing"),
    }
    output = {}
    for name, key_func in specs.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[key_func(row)].append(row)
        output[name] = sorted([calibration_row(label, group) for label, group in grouped.items()], key=calibration_rank_key)
    return output


def calibration_row(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("correct") is not None]
    fair_values = [fair for row in rows if (fair := selected_fair(row)) is not None]
    resolved_fair_values = [fair for row in resolved if (fair := selected_fair(row)) is not None]
    hit_rate = mean(1.0 if int(row["correct"]) else 0.0 for row in resolved)
    avg_resolved_fair = mean(resolved_fair_values)
    return {
        "band": label,
        "n": len(rows),
        "resolved": len(resolved),
        "pending": len(rows) - len(resolved),
        "avg_fair": mean(fair_values),
        "resolved_avg_fair": avg_resolved_fair,
        "hit_rate": hit_rate,
        "cal_error": None if avg_resolved_fair is None or hit_rate is None else hit_rate - avg_resolved_fair,
    }


def promotion_candidates(mode_tables: dict[str, dict[str, list[dict[str, Any]]]], *, min_n: int, top_n: int) -> list[dict[str, Any]]:
    rows = []
    for mode_name, tables in mode_tables.items():
        if mode_name == "hindsight_best":
            continue
        for table_name, table_rows in tables.items():
            for row in table_rows:
                flags = set(str(row["flags"]).split(",")) if row["flags"] != "OK" else set()
                if row["resolved"] < min_n or "EXECUTION_WEAK" in flags:
                    continue
                if (row["rr"] or 0) > 0 and (row["sharpe"] or 0) > 0:
                    rows.append({"mode": mode_name, "table": table_name, **row})
    return sorted(rows, key=rank_key)[:top_n]


def best_recorded_execution_policies(
    mode_tables: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    min_n: int,
    top_n: int,
) -> dict[str, list[dict[str, Any]]]:
    # These modes represent rules we could have executed without hindsight: first signal,
    # first station/date signal, and later re-entry only after a large edge improvement.
    execution_modes = {"first_opportunity", "station_date_first", "edge_improve_50"}
    policy_tables = {
        "By Source",
        "By Source Strategy",
        "By Source Strategy Side",
        "By Source Strategy Obs Delay",
        "By Source Strategy Entry Side",
        "By Source Strategy Edge Side",
    }
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for mode_name, tables in mode_tables.items():
        if mode_name not in execution_modes:
            continue
        for table_name, table_rows in tables.items():
            if table_name not in policy_tables:
                continue
            for row in table_rows:
                if row["resolved"] < min_n or row.get("win_rate") is None:
                    continue
                key = (mode_name, table_name, str(row.get("label") or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"mode": mode_name, "table": table_name, **row})
    return {
        "Highest Sharpe": sorted(rows, key=execution_sharpe_rank)[:top_n],
        "Highest Win Rate": sorted(rows, key=execution_win_rate_rank)[:top_n],
        "Highest R/R": sorted(rows, key=execution_rr_rank)[:top_n],
    }


def execution_rr_rank(row: dict[str, Any]) -> tuple[float, int, float, float, str, str, str]:
    rr = row.get("rr")
    sharpe_value = row.get("sharpe")
    win_rate = row.get("win_rate")
    return (
        -(rr if rr is not None else -math.inf),
        -int(row.get("resolved") or 0),
        -(sharpe_value if sharpe_value is not None else -math.inf),
        -(win_rate if win_rate is not None else -math.inf),
        str(row.get("mode") or ""),
        str(row.get("table") or ""),
        str(row.get("label") or ""),
    )


def execution_sharpe_rank(row: dict[str, Any]) -> tuple[float, float, int, float, str, str, str]:
    sharpe_value = row.get("sharpe")
    rr = row.get("rr")
    return (
        -(sharpe_value if sharpe_value is not None else -math.inf),
        -(rr if rr is not None else -math.inf),
        -int(row.get("resolved") or 0),
        -(row.get("win_rate") if row.get("win_rate") is not None else -math.inf),
        str(row.get("mode") or ""),
        str(row.get("table") or ""),
        str(row.get("label") or ""),
    )


def execution_win_rate_rank(row: dict[str, Any]) -> tuple[float, int, float, float, str, str, str]:
    win_rate = row.get("win_rate")
    sharpe_value = row.get("sharpe")
    rr = row.get("rr")
    return (
        -(win_rate if win_rate is not None else -math.inf),
        -int(row.get("resolved") or 0),
        -(sharpe_value if sharpe_value is not None else -math.inf),
        -(rr if rr is not None else -math.inf),
        str(row.get("mode") or ""),
        str(row.get("table") or ""),
        str(row.get("label") or ""),
    )


def render_report(
    mode_results: dict[str, ModeResult],
    *,
    min_n: int,
    top_n: int,
    policy_summaries: list[dict[str, Any]] | None = None,
    min_policy_n: int | None = None,
) -> str:
    lines = ["# Snapshot Opportunity Sweep", ""]
    all_mode_tables: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for mode_name in MODE_ORDER:
        mode = mode_results[mode_name]
        lines.extend([f"## {mode.name}", ""])
        if mode.hindsight_only:
            lines.extend(["Exploratory upper bound: selected by realized paper PnL. Do not use for promotion rules.", ""])
        coverage = coverage_summary(mode.rows)
        lines.extend(render_kv_table(coverage))
        tables = slice_tables(mode.rows, min_n=min_n, hindsight_only=mode.hindsight_only)
        all_mode_tables[mode_name] = tables
        for table_name, rows in tables.items():
            lines.extend(["", f"### {table_name}", ""])
            lines.extend(render_metric_table(rows[:top_n]))
        for table_name, rows in calibration_tables(mode.rows).items():
            lines.extend(["", f"### {table_name}", ""])
            lines.extend(render_calibration_table(rows[:top_n]))
        lines.append("")

    lines.extend(["## Promotion Candidates", ""])
    candidates = promotion_candidates(all_mode_tables, min_n=min_n, top_n=top_n)
    if candidates:
        lines.extend(render_promotion_table(candidates))
    else:
        lines.append("No non-hindsight slices met the promotion screen.")

    lines.extend(["", "## Best Recorded Execution Policies", ""])
    lines.append(
        "Ranked from non-hindsight execution modes only: first opportunity, station/date first, "
        "and 50% edge-improvement re-entry. Liquidity is shown but is not used as a gate here."
    )
    summaries = best_recorded_execution_policies(all_mode_tables, min_n=min_n, top_n=top_n)
    for title, rows in summaries.items():
        lines.extend(["", f"### {title}", ""])
        if rows:
            lines.extend(render_execution_policy_table(rows))
        else:
            lines.append("No slices met the minimum resolved sample size.")
    if policy_summaries is not None:
        policy_min = min_n if min_policy_n is None else min_policy_n
        eligible_policy_summaries = [
            row
            for row in policy_summaries
            if int(row.get("resolved") or 0) >= policy_min
            and float_value(row.get("pnl")) > 0.0
            and float_value(row.get("rr")) > 0.0
        ]
        lines.extend(["", "## Policy Search Candidates", ""])
        lines.append(
            "Built by enumerating policy specs from prediction snapshots, replaying first-eligible rows, "
            "and ranking the resulting policy-level PnL. This section is not a grouped slice of existing policy rows."
        )
        lines.extend(["", "### Highest Sharpe", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_sharpe_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
        lines.extend(["", "### Highest R/R", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_rr_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
        lines.extend(["", "### Highest PnL", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_pnl_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
        lines.extend(["", "### Highest Win Rate", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_win_rate_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
        lines.extend(["", "### Low-Risk Consistency", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_low_risk_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
        lines.extend(["", "### Degenerate Upside", ""])
        lines.extend(render_policy_search_table(sorted(eligible_policy_summaries, key=policy_degenerate_rank)[:top_n]) if eligible_policy_summaries else ["No generated policies met the minimum resolved sample size."])
    return "\n".join(lines).rstrip() + "\n"


def render_rolling_policy_report(
    base_rows: list[dict[str, Any]],
    *,
    min_policy_n: int,
    top_n: int,
) -> str:
    max_resolved_date = latest_resolved_market_date(base_rows)
    if max_resolved_date is None:
        return "# Snapshot Policy Rolling Replay\n\nNo resolved snapshot rows were available.\n"

    windows: list[tuple[str, date | None]] = [
        ("last_7", max_resolved_date - timedelta(days=6)),
        ("last_30", max_resolved_date - timedelta(days=29)),
        ("all_time", None),
    ]
    window_summaries: dict[str, list[dict[str, Any]]] = {}
    lines = [
        "# Snapshot Policy Rolling Replay",
        "",
        f"Resolved through market_date {max_resolved_date.isoformat()}.",
        "Replays generated policies from raw prediction_snapshots; no research_policy_positions rows are required.",
        "",
    ]
    for label, start_date in windows:
        rows = [row for row in base_rows if row_in_window(row, start_date, max_resolved_date)]
        _, summaries = build_compact_policy_search_rows(rows)
        eligible = eligible_policy_rows(summaries, min_policy_n=min_policy_n)
        window_summaries[label] = eligible
        lines.extend([f"## {label}", ""])
        lines.extend(render_window_overview(label, rows, summaries, eligible))
        lines.extend(["", "### Top R/R", ""])
        lines.extend(render_policy_search_table(sorted(eligible, key=policy_rr_rank)[:top_n]) if eligible else ["No generated policies met the screen."])
        lines.extend(["", "### Top Sharpe", ""])
        lines.extend(render_policy_search_table(sorted(eligible, key=policy_sharpe_rank)[:top_n]) if eligible else ["No generated policies met the screen."])
        lines.extend(["", "### By Model Family", ""])
        family_rows = family_rollup(eligible)
        lines.extend(render_family_table(family_rows[:top_n]) if family_rows else ["No family rows met the screen."])
        lines.append("")

    lines.extend(["## Emerging Patterns", ""])
    emerging = emerging_patterns(window_summaries)
    if emerging:
        lines.extend(render_emerging_table(emerging[:top_n]))
    else:
        lines.append("No last-7-day policy candidates clearly improved versus the broader windows under the current screen.")
    return "\n".join(lines).rstrip() + "\n"


def latest_resolved_market_date(rows: list[dict[str, Any]]) -> date | None:
    dates = []
    for row in rows:
        if row.get("paper_pnl") is None:
            continue
        try:
            dates.append(date.fromisoformat(str(row.get("market_date"))))
        except ValueError:
            continue
    return max(dates) if dates else None


def row_in_window(row: dict[str, Any], start_date: date | None, end_date: date) -> bool:
    if row.get("paper_pnl") is None:
        return False
    try:
        market_date = date.fromisoformat(str(row.get("market_date")))
    except ValueError:
        return False
    if market_date > end_date:
        return False
    return start_date is None or market_date >= start_date


def eligible_policy_rows(summaries: list[dict[str, Any]], *, min_policy_n: int) -> list[dict[str, Any]]:
    return [
        row
        for row in summaries
        if int(row.get("resolved") or 0) >= min_policy_n
        and float_value(row.get("pnl")) > 0.0
        and float_value(row.get("rr")) > 0.0
    ]


def render_window_overview(label: str, rows: list[dict[str, Any]], summaries: list[dict[str, Any]], eligible: list[dict[str, Any]]) -> list[str]:
    resolved_rows = [row for row in rows if row.get("paper_pnl") is not None]
    values = {
        "window": label,
        "snapshot_rows": len(rows),
        "resolved_snapshot_rows": len(resolved_rows),
        "generated_policies": len(summaries),
        "eligible_positive_policies": len(eligible),
        "sources": len({row.get("source") for row in rows}),
        "market_dates": len({row.get("market_date") for row in resolved_rows}),
    }
    return render_kv_table(values)


def family_rollup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[model_family_label(str(row.get("model") or row.get("policy_name") or "missing"))].append(row)
    output = []
    for family, group in grouped.items():
        output.append({
            "family": family,
            "policies": len(group),
            "best_rr": max(float_value(row.get("rr")) for row in group),
            "best_sharpe": max(float_value(row.get("sharpe")) for row in group),
            "total_pnl": sum(float_value(row.get("pnl"), default=0.0) for row in group),
            "avg_resolved": mean(float_or_none(row.get("resolved")) for row in group),
        })
    return sorted(output, key=lambda row: (-float_value(row.get("best_rr")), -float_value(row.get("total_pnl")), str(row.get("family"))))


def model_family_label(value: str) -> str:
    if "metar_hrrr_rich" in value or "metar+hrrr" in value:
        return "metar_hrrr_rich"
    if "hrrr_rich" in value:
        return "hrrr_rich"
    if "hrrr_v2" in value:
        return "hrrr_v2_basic"
    if "pm_us12" in value or "pm_active_us12" in value or value.startswith("obs_"):
        return "obs_pm_us12"
    if value.startswith("low_"):
        return "low"
    if value.startswith("global_"):
        return "global"
    return "other"


def emerging_patterns(window_summaries: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    last_7 = {str(row.get("policy_name")): row for row in window_summaries.get("last_7", [])}
    last_30 = {str(row.get("policy_name")): row for row in window_summaries.get("last_30", [])}
    all_time = {str(row.get("policy_name")): row for row in window_summaries.get("all_time", [])}
    rows = []
    for name, recent in last_7.items():
        rr_7 = float_value(recent.get("rr"))
        rr_30 = float_value(last_30.get(name, {}).get("rr"), default=0.0)
        rr_all = float_value(all_time.get(name, {}).get("rr"), default=0.0)
        if rr_7 <= 0:
            continue
        improvement = rr_7 - max(rr_30, rr_all)
        if improvement <= 0 and rr_7 < 0.25:
            continue
        rows.append({
            "policy_name": name,
            "family": model_family_label(str(recent.get("model") or name)),
            "resolved_7": recent.get("resolved"),
            "rr_7": recent.get("rr"),
            "rr_30": last_30.get(name, {}).get("rr"),
            "rr_all": all_time.get(name, {}).get("rr"),
            "sharpe_7": recent.get("sharpe"),
            "pnl_7": recent.get("pnl"),
            "filters": recent.get("filters"),
            "scope": recent.get("scope"),
            "improvement": improvement,
        })
    return sorted(rows, key=lambda row: (-float_value(row.get("improvement")), -float_value(row.get("rr_7")), str(row.get("policy_name"))))


def render_family_table(rows: list[dict[str, Any]]) -> list[str]:
    return render_rows(("family", "policies", "best_rr", "best_sharpe", "total_pnl", "avg_resolved"), rows)


def render_emerging_table(rows: list[dict[str, Any]]) -> list[str]:
    return render_rows(("policy_name", "family", "resolved_7", "rr_7", "rr_30", "rr_all", "sharpe_7", "pnl_7", "filters", "scope", "improvement"), rows)


def render_kv_table(values: dict[str, Any]) -> list[str]:
    lines = ["| metric | value |", "|---|---:|"]
    for key, value in values.items():
        lines.append(f"| {key} | {value} |")
    return lines


def render_metric_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("label", "n", "resolved", "pending", "win_rate", "pnl", "risk", "rr", "sharpe", "avg_entry", "avg_edge", "avg_fair", "avg_sweep_50", "avg_book_age", "flags")
    return render_rows(columns, rows)


def render_calibration_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("band", "n", "resolved", "pending", "avg_fair", "resolved_avg_fair", "hit_rate", "cal_error")
    return render_rows(columns, rows)


def render_promotion_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = ("mode", "table", "label", "resolved", "rr", "sharpe", "pnl", "avg_entry", "avg_edge", "avg_sweep_50")
    return render_rows(columns, rows)


def render_execution_policy_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = (
        "mode",
        "table",
        "label",
        "resolved",
        "win_rate",
        "sharpe",
        "rr",
        "pnl",
        "avg_entry",
        "avg_edge",
        "avg_fair",
        "avg_sweep_50",
        "flags",
    )
    return render_rows(columns, rows)


def render_policy_search_table(rows: list[dict[str, Any]]) -> list[str]:
    columns = (
        "policy_name",
        "resolved",
        "pending",
        "win_rate",
        "sharpe",
        "rr",
        "pnl",
        "risk",
        "avg_entry",
        "avg_edge",
        "avg_fair",
        "avg_sweep_50",
        "source",
        "model",
        "strategy",
        "filters",
        "scope",
        "flags",
    )
    return render_rows(columns, rows)


def render_rows(columns: Iterable[str], rows: list[dict[str, Any]]) -> list[str]:
    cols = list(columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(format_value(row.get(col)) for col in cols) + " |")
    return lines


def rank_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    rr = row.get("rr")
    pnl = row.get("pnl")
    return (-int(row.get("resolved") or 0), -(rr if rr is not None else -math.inf), -(pnl if pnl is not None else -math.inf), str(row.get("label") or ""))


def policy_default_rank(row: dict[str, Any]) -> tuple[int, float, float, float, str]:
    return (
        -int(row.get("resolved") or 0),
        -float_value(row.get("sharpe")),
        -float_value(row.get("rr")),
        -float_value(row.get("pnl")),
        str(row.get("policy_name") or ""),
    )


def policy_sharpe_rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    return (
        -float_value(row.get("sharpe")),
        -float_value(row.get("rr")),
        -int(row.get("resolved") or 0),
        str(row.get("policy_name") or ""),
    )


def policy_rr_rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    return (
        -float_value(row.get("rr")),
        -float_value(row.get("sharpe")),
        -int(row.get("resolved") or 0),
        str(row.get("policy_name") or ""),
    )


def policy_pnl_rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    return (
        -float_value(row.get("pnl")),
        -float_value(row.get("sharpe")),
        -int(row.get("resolved") or 0),
        str(row.get("policy_name") or ""),
    )


def policy_win_rate_rank(row: dict[str, Any]) -> tuple[float, int, float, str]:
    return (
        -float_value(row.get("win_rate")),
        -int(row.get("resolved") or 0),
        -float_value(row.get("sharpe")),
        str(row.get("policy_name") or ""),
    )


def policy_low_risk_rank(row: dict[str, Any]) -> tuple[int, float, float, float, str]:
    return (
        -int(row.get("resolved") or 0),
        float_value(row.get("avg_entry"), default=math.inf),
        -float_value(row.get("win_rate")),
        -float_value(row.get("sharpe")),
        str(row.get("policy_name") or ""),
    )


def policy_degenerate_rank(row: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        float_value(row.get("avg_entry"), default=math.inf),
        -float_value(row.get("rr")),
        -float_value(row.get("pnl")),
        str(row.get("policy_name") or ""),
    )


def calibration_rank_key(row: dict[str, Any]) -> tuple[int, str]:
    return (-int(row.get("resolved") or 0), str(row.get("band") or ""))


def entry_band_value(row: dict[str, Any]) -> str:
    entry = entry_price(row)
    return "missing" if entry is None else entry_band(entry)


def decision_hour_band(value: Any) -> str:
    if value is None:
        return "missing"
    try:
        hour = int(str(value).split("T", 1)[1][:2])
    except (IndexError, ValueError):
        return "missing"
    if hour < 8:
        return "00-08"
    if hour < 12:
        return "08-12"
    if hour < 16:
        return "12-16"
    if hour < 20:
        return "16-20"
    return "20-24"


def liquidity_band(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < 25:
        return "0-25"
    if value < 50:
        return "25-50"
    if value < 100:
        return "50-100"
    return ">=100"


def in_range(value: float | None, minimum: float | None, maximum: float | None) -> bool:
    if minimum is None and maximum is None:
        return True
    if value is None:
        return False
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def local_time(value: Any) -> time | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError:
        return None


def parse_hhmm(value: str | None) -> time:
    if value is None:
        raise ValueError("missing time value")
    return time.fromisoformat(value)


def model_alias(model_name: str) -> str:
    if model_name in KNOWN_MODEL_ALIASES:
        return KNOWN_MODEL_ALIASES[model_name]
    return f"model_{slugify(model_name)}"


def consensus_alias(group_name: str) -> str:
    if group_name in KNOWN_CONSENSUS_ALIASES:
        return KNOWN_CONSENSUS_ALIASES[group_name]
    return f"consensus_{slugify(group_name)}"


def slugify(value: str) -> str:
    text = "".join(ch if ch.isalnum() else "_" for ch in str(value).lower())
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def format_bound(value: float | None) -> str:
    if value is None:
        return "*"
    return f"{value:.2f}"


def float_value(value: Any, *, default: float = -math.inf) -> float:
    parsed = float_or_none(value)
    return parsed if parsed is not None else default


def mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db_path())
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--market-family")
    parser.add_argument("--us-high-temp-only", action="store_true", help="Restrict to US high-temperature station/model snapshots, excluding global and low-temp families.")
    parser.add_argument("--min-n", type=int, default=10)
    parser.add_argument("--min-policy-n", type=int)
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-consensus", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--policy-search", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rolling-summary", action="store_true", help="Show compact 7-day/30-day/all-time policy replay summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = sqlite3.connect(str(args.db))
    db.row_factory = sqlite3.Row
    try:
        base_rows = load_snapshot_rows(
            db,
            start_date=args.start_date,
            end_date=args.end_date,
            market_family=args.market_family,
            us_high_temp_only=args.us_high_temp_only,
        )
    finally:
        db.close()
    if args.rolling_summary:
        report = render_rolling_policy_report(
            base_rows,
            min_policy_n=args.min_policy_n if args.min_policy_n is not None else args.min_n,
            top_n=args.top_n,
        )
    else:
        rows = list(base_rows)
        if args.include_consensus:
            rows = [*rows, *build_consensus_rows(rows)]
        policy_summaries = None
        if args.policy_search:
            _, policy_summaries = build_policy_search_rows(base_rows)
        report = render_report(
            apply_modes(rows),
            min_n=args.min_n,
            top_n=args.top_n,
            policy_summaries=policy_summaries,
            min_policy_n=args.min_policy_n,
        )
    if args.output:
        args.output.write_text(report)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
