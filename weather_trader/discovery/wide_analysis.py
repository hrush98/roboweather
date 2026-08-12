from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any

from weather_trader.pricing.contracts import stable_hash


@dataclass(frozen=True)
class WideSearchConfig:
    source_start_date: str
    cutoff_exclusive: str
    holdout_dates: int = 5
    fold_count: int = 3
    minimum_discovery_dates: int = 8
    minimum_discovery_trades: int = 20
    minimum_holdout_dates: int = 4
    minimum_holdout_trades: int = 8
    bootstrap_repetitions: int = 2_000
    maximum_unique_behaviors_per_model: int = 2_000_000
    maximum_holdout_representatives: int = 250
    complexity_penalty: float = 0.005
    workers: int = 8
    grammar_version: str = "phase3d_absolute_wide_grid_v1"
    outcome_enrichment_version: str = "weather_outcome_v1"


@dataclass(frozen=True)
class WideRow:
    mapping_id: str
    source_snapshot_id: int
    quote_ready_timestamp_utc: str
    station: str
    market_date: str
    model_id: str
    market_family: str
    selected_side: str
    strategy_bucket: str
    high_conviction: bool
    delay: str
    lifecycle: str
    local_hhmm: str
    selected_bucket: str
    observation_age_minutes: float
    best_ask: float
    spread: float | None
    edge: float
    execution_delay_ms: float
    label: int
    summaries: dict[str, dict[str, float | None]]


@dataclass(frozen=True)
class Behavior:
    mask: int
    rule: dict[str, Any]
    complexity: int
    target_cost_usd: float
    price_cap: float


ENTRY_FLOORS = (0.0, 0.01, 0.025, 0.05, 0.10, 0.15, 0.20, 0.30)
EDGE_FLOORS = (-0.10, -0.05, 0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30)
SPREAD_CAPS = (0.01, 0.02, 0.03, 0.05, 0.10, 0.20)
OBSERVATION_AGE_CAPS = (5.0, 10.0, 15.0, 20.0)
EXECUTION_DELAY_CAPS_MS = (1_000.0, 5_000.0, 10_000.0, 20_000.0)
TIME_WINDOWS = (
    ("00:00", "24:00"), ("06:00", "12:00"), ("09:00", "12:00"),
    ("12:00", "15:00"), ("15:00", "18:00"), ("18:00", "24:00"),
    ("06:00", "15:00"), ("09:00", "15:00"), ("12:00", "18:00"),
    ("15:00", "24:00"), ("06:00", "18:00"), ("09:00", "18:00"),
)
TARGET_COSTS = (25.0, 50.0, 100.0)
PRICE_CAPS = (0.35, 0.50)
FILL_FRACTIONS = (0.25, 0.50, 0.75, 1.0)
DAILY_RISK_CAPS = (100.0, 200.0, 300.0)


def load_wide_rows(
    cache: sqlite3.Connection,
    *,
    contract_hash: str,
    config: WideSearchConfig,
) -> tuple[list[WideRow], dict[str, Any]]:
    cache.row_factory = sqlite3.Row
    pending = int(cache.execute(
        "select count(*) from executable_decisions where contract_hash=? and status='PENDING'",
        (contract_hash,),
    ).fetchone()[0])
    source = cache.execute(
        """select m.mapping_id,m.source_snapshot_id,d.quote_ready_timestamp_utc,
                  m.station,m.market_date,m.model_id,m.market_family,m.selected_side,
                  m.strategy_bucket,m.high_conviction,m.observation_delay_bucket,
                  m.lifecycle_horizon,m.local_decision_hhmm,m.selected_bucket,
                  m.observation_age_minutes,m.raw_model_fair,d.best_ask,d.spread,
                  d.execution_delay_ms_after_ready,d.execution_summaries_json,
                  e.value_json outcome_value_json
           from model_decision_mappings m
           join executable_decisions d on d.decision_id=m.decision_id
           join decision_enrichments e on e.decision_id=d.decision_id
             and e.enrichment_kind='RESEARCH_OUTCOME'
             and e.enrichment_version=? and e.status='AVAILABLE'
           where m.contract_hash=? and m.market_date>=? and m.market_date<?
             and d.status='SUCCESS'
           order by m.market_date,d.quote_ready_timestamp_utc,m.source_snapshot_id,m.mapping_id""",
        (config.outcome_enrichment_version, contract_hash, config.source_start_date, config.cutoff_exclusive),
    ).fetchall()
    rows: list[WideRow] = []
    invalid = 0
    for item in source:
        try:
            outcome = json.loads(str(item["outcome_value_json"]))
            summaries = json.loads(str(item["execution_summaries_json"]))
            fair = float(item["raw_model_fair"])
            ask = float(item["best_ask"])
            label = int(outcome["label"])
            if label not in (0, 1) or not math.isfinite(fair) or not math.isfinite(ask):
                raise ValueError
            rows.append(WideRow(
                mapping_id=str(item["mapping_id"]),
                source_snapshot_id=int(item["source_snapshot_id"]),
                quote_ready_timestamp_utc=str(item["quote_ready_timestamp_utc"]),
                station=str(item["station"]), market_date=str(item["market_date"]),
                model_id=str(item["model_id"]), market_family=str(item["market_family"]),
                selected_side=str(item["selected_side"]), strategy_bucket=str(item["strategy_bucket"]),
                high_conviction=bool(item["high_conviction"]), delay=str(item["observation_delay_bucket"]),
                lifecycle=str(item["lifecycle_horizon"]), local_hhmm=str(item["local_decision_hhmm"]),
                selected_bucket=str(item["selected_bucket"]),
                observation_age_minutes=float(item["observation_age_minutes"] or 0.0),
                best_ask=ask, spread=float(item["spread"]) if item["spread"] is not None else None,
                edge=fair - ask,
                execution_delay_ms=float(item["execution_delay_ms_after_ready"] or 0.0),
                label=label, summaries=summaries,
            ))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            invalid += 1
    return rows, {
        "contract_hash": contract_hash,
        "pending_decisions": pending,
        "eligible_rows": len(rows),
        "invalid_rows": invalid,
        "row_set_hash": stable_hash([stable_hash(row.__dict__) for row in rows]),
    }


def run_wide_search(
    rows: list[WideRow],
    *,
    config: WideSearchConfig,
    cache_diagnostics: dict[str, Any],
    sealed_manifest: dict[str, Any],
    progress: Any | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    dates = sorted({row.market_date for row in rows})
    manifest = {
        **sealed_manifest,
        "configuration": config.__dict__,
        "grammar": grammar_manifest(rows),
        "cache": cache_diagnostics,
        "funded_authorization": False,
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    if cache_diagnostics.get("pending_decisions"):
        return _finish("INCOMPLETE_CACHE", manifest, [], {}, started)
    if len(dates) <= config.holdout_dates + config.fold_count:
        return _finish("COMPLETED_NO_EMERGED_STRATEGIES", manifest, [], {"dates": dates}, started)
    discovery_dates = dates[:-config.holdout_dates]
    holdout_dates = dates[-config.holdout_dates:]
    discovery_set = set(discovery_dates)
    folds = _folds(discovery_dates, config.fold_count)
    by_model: dict[tuple[str, str], list[WideRow]] = defaultdict(list)
    for row in rows:
        by_model[(row.model_id, row.market_family)].append(row)

    scored: list[dict[str, Any]] = []
    theoretical = 0
    unique = 0
    per_model: dict[str, Any] = {}
    tasks = []
    with ProcessPoolExecutor(max_workers=max(1, config.workers)) as pool:
        for model_key in sorted(by_model):
            training = [row for row in by_model[model_key] if row.market_date in discovery_set]
            tasks.append(pool.submit(_score_model, model_key, training, discovery_dates, folds, config))
        completed = []
        for future in as_completed(tasks):
            completed.append(future.result())
            if progress:
                progress({"stage": "MODEL_COMPLETE", **completed[-1][0]})
    for diagnostics, passing_rows in sorted(completed, key=lambda item: item[0]["model"]):
        theoretical += diagnostics["theoretical_rules"] * len(DAILY_RISK_CAPS)
        unique += diagnostics["unique_behaviors"] * len(DAILY_RISK_CAPS)
        scored.extend(passing_rows)
        per_model[diagnostics["model"]] = diagnostics

    ranked = sorted(scored, key=_rank_key)
    representatives: list[dict[str, Any]] = []
    families: set[str] = set()
    for item in ranked:
        if item["family_id"] in families:
            continue
        families.add(item["family_id"])
        representatives.append(item)
        if len(representatives) >= config.maximum_holdout_representatives:
            break
    frozen_hash = stable_hash([{"rule_id": x["rule_id"], "score_hash": stable_hash(x)} for x in representatives])
    evaluated = []
    for item in representatives:
        model_key = (item["rule"]["model_id"], item["rule"]["market_family"])
        holdout_model_rows = [row for row in by_model[model_key] if row.market_date in set(holdout_dates)]
        holdout_behavior = behavior_from_rule(item["rule"], holdout_model_rows)
        holdout = score_period(holdout_behavior, holdout_model_rows, set(holdout_dates), item["rule"]["daily_risk_cap_usd"])
        boot = _bootstrap(holdout["daily"], config.bootstrap_repetitions, f"holdout:{item['rule_id']}")
        survives = bool(
            holdout["effective_dates"] >= min(config.minimum_holdout_dates, len(holdout_dates))
            and holdout["trades"] >= config.minimum_holdout_trades
            and holdout["pnl"] > 0
            and boot["lower_5pct"] is not None and boot["lower_5pct"] > 0
        )
        evaluated.append({**item, "representative_freeze_hash": frozen_hash, "holdout": _no_daily(holdout), "holdout_daily": holdout["daily"], "holdout_bootstrap": boot, "survives_holdout": survives})
    emerged = [item for item in evaluated if item["survives_holdout"]]
    status = "COMPLETED_WITH_EMERGED_STRATEGIES" if emerged else "COMPLETED_NO_EMERGED_STRATEGIES"
    result = {
        "status": status,
        "plain_language_answer": f"{len(emerged)} broad correlated strategy families survived the strict untouched holdout." if emerged else "No broad correlated strategy family survived the strict untouched holdout.",
        "manifest": manifest,
        "grid": {
            "theoretical_syntactic_rules": theoretical,
            "unique_discovery_behaviors_with_risk_caps": unique,
            "passing_rules": len(scored),
            "passing_correlated_families": len(representatives),
            "surviving_holdout_families": len(emerged),
            "discovery_dates": discovery_dates, "holdout_dates": holdout_dates,
            "folds": folds, "representative_freeze_hash": frozen_hash,
            "per_model": per_model,
        },
        "family_representatives": evaluated,
        "top_discovery_rules": ranked[:1000],
        "evidence_provenance": {"economics": "WEATHER_OUTCOME_DIAGNOSTIC", "execution": "PUBLIC_TAPE_DELAYED_TAKER_COUNTERFACTUAL", "funded_authorization": False},
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "funded_authorization": False,
    }
    result["result_content_hash"] = stable_hash(result)
    return result


def enumerate_behaviors(rows: list[WideRow], *, config: WideSearchConfig) -> tuple[list[Behavior], dict[str, int]]:
    if not rows:
        return [], {"source_rows": 0, "theoretical_rules": 0, "unique_behaviors": 0}
    all_mask = (1 << len(rows)) - 1
    base_rule = {
        "model_id": rows[0].model_id, "market_family": rows[0].market_family,
        "selected_side": "ANY", "strategy_bucket": "ANY", "conviction": "ANY",
        "observation_delay_bucket": "ANY", "lifecycle_horizon": "ANY",
        "geography": "ANY", "local_window": ["00:00", "24:00"], "bucket_shape": "ANY",
        "entry_floor": 0.0, "minimum_model_edge": None, "maximum_spread": None,
        "maximum_observation_age_minutes": None, "maximum_execution_delay_ms": None,
        "minimum_fill_fraction": None,
    }
    dimensions = _dimensions(rows)
    theoretical = 1
    states: dict[tuple[int, float, float], Behavior] = {}
    for target in TARGET_COSTS:
        for cap in PRICE_CAPS:
            for fill in FILL_FRACTIONS:
                mask = _mask(rows, lambda row, t=target, c=cap, f=fill: _fillable(row, c, t, f))
                if mask.bit_count() < config.minimum_discovery_trades:
                    continue
                rule = {**base_rule, "price_cap": cap, "target_cost_usd": target, "minimum_fill_fraction": fill}
                key = (mask, target, cap)
                candidate = Behavior(mask, rule, 1, target, cap)
                _retain(states, key, candidate)
    theoretical *= len(TARGET_COSTS) * len(PRICE_CAPS) * len(FILL_FRACTIONS)
    for name, options in dimensions:
        theoretical *= 1 + len(options)
        expanded = dict(states)
        for behavior in states.values():
            for value, option_mask in options:
                combined = behavior.mask & option_mask
                if combined == behavior.mask or combined.bit_count() < config.minimum_discovery_trades:
                    continue
                rule = dict(behavior.rule)
                rule[name] = value
                candidate = replace(behavior, mask=combined, rule=rule, complexity=behavior.complexity + 1)
                _retain(expanded, (combined, candidate.target_cost_usd, candidate.price_cap), candidate)
        states = expanded
        if len(states) > config.maximum_unique_behaviors_per_model:
            raise RuntimeError(f"wide grammar exceeded {config.maximum_unique_behaviors_per_model:,} unique behaviors for {rows[0].model_id} after {name}")
    return sorted(states.values(), key=lambda item: (item.complexity, stable_hash(item.rule))), {
        "source_rows": len(rows), "theoretical_rules": theoretical, "unique_behaviors": len(states),
    }



def _score_model(
    model_key: tuple[str, str],
    rows: list[WideRow],
    discovery_dates: list[str],
    folds: list[list[str]],
    config: WideSearchConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    behaviors, diagnostics = enumerate_behaviors(rows, config=config)
    passing = []
    for behavior in behaviors:
        for daily_cap in DAILY_RISK_CAPS:
            item = score_behavior(behavior, rows, discovery_dates, folds, daily_cap, config)
            if item["passes_discovery_gate"]:
                passing.append(item)
    return ({
        "model": f"{model_key[0]}|{model_key[1]}",
        **diagnostics,
        "scored_behaviors_with_risk_caps": len(behaviors) * len(DAILY_RISK_CAPS),
        "passing": len(passing),
    }, passing)

def score_behavior(behavior: Behavior, rows: list[WideRow], dates: list[str], folds: list[list[str]], daily_cap: float, config: WideSearchConfig) -> dict[str, Any]:
    period = score_period(behavior, rows, set(dates), daily_cap)
    fold_scores = [_no_daily(score_period(behavior, rows, set(fold), daily_cap)) for fold in folds]
    positive_folds = sum(item["trades"] > 0 and item["pnl"] > 0 for item in fold_scores)
    cluster = _cluster_lcb(period["daily"])
    profit_concentration = _profit_concentration(period["daily"])
    passes = bool(
        period["effective_dates"] >= config.minimum_discovery_dates
        and period["trades"] >= config.minimum_discovery_trades
        and positive_folds == len(folds)
        and cluster["lcb"] > 0
        and profit_concentration <= 0.50
    )
    rule = {**behavior.rule, "daily_risk_cap_usd": daily_cap, "dedupe_scope": "first_station_date", "execution": "first_post_ready_checkpoint_taker_v1"}
    rule_id = f"wide_rule_{stable_hash(rule)[:24]}"
    family = {
        "model_root": _model_root(rule["model_id"]), "market_family": rule["market_family"],
        "selected_side": rule["selected_side"], "strategy_bucket": rule["strategy_bucket"],
        "geography": rule["geography"],
    }
    return {
        "rule_id": rule_id, "family_id": f"wide_family_{stable_hash(family)[:20]}", "rule": rule,
        "complexity": behavior.complexity, **_no_daily(period), "daily": period["daily"],
        "folds": fold_scores, "positive_folds": positive_folds,
        "cluster_mean_daily_rr": cluster["mean"], "cluster_lcb_daily_rr": cluster["lcb"],
        "profit_concentration": profit_concentration,
        "penalized_cluster_lcb": round(cluster["lcb"] - config.complexity_penalty * behavior.complexity, 8),
        "passes_discovery_gate": passes,
    }


def score_period(behavior: Behavior, rows: list[WideRow], dates: set[str], daily_cap: float) -> dict[str, Any]:
    selected = [rows[index] for index in _indices(behavior.mask) if rows[index].market_date in dates]
    first: dict[tuple[str, str], WideRow] = {}
    for row in selected:
        first.setdefault((row.station, row.market_date), row)
    executions = []
    used: dict[str, float] = defaultdict(float)
    key = _execution_key(behavior.price_cap, behavior.target_cost_usd)
    for row in first.values():
        values = row.summaries.get(key) or {}
        cost = float(values.get("cost_usd") or 0.0)
        shares = float(values.get("shares") or 0.0)
        vwap = values.get("vwap")
        if vwap is None or used[row.market_date] + cost > daily_cap + 1e-9:
            continue
        used[row.market_date] += cost
        executions.append((row, cost, shares, float(vwap), shares - cost if row.label else -cost))
    daily: dict[str, dict[str, float]] = {}
    for date in sorted({item[0].market_date for item in executions}):
        subset = [item for item in executions if item[0].market_date == date]
        cost = sum(item[1] for item in subset); pnl = sum(item[4] for item in subset)
        daily[date] = {"cost": round(cost, 8), "pnl": round(pnl, 8), "rr": round(pnl / cost, 8)}
    cost = sum(item[1] for item in executions); pnl = sum(item[4] for item in executions)
    stations = Counter(item[0].station for item in executions)
    return {
        "trades": len(executions), "effective_dates": len(daily), "cost": round(cost, 8), "pnl": round(pnl, 8),
        "rr": round(pnl / cost, 8) if cost else None,
        "win_rate": round(sum(item[4] > 0 for item in executions) / len(executions), 8) if executions else None,
        "average_vwap": round(sum(item[3] for item in executions) / len(executions), 8) if executions else None,
        "maximum_station_trade_share": round(max(stations.values()) / len(executions), 8) if executions else None,
        "daily": daily,
    }


def behavior_from_rule(rule: dict[str, Any], rows: list[WideRow]) -> Behavior:
    mask = _mask(rows, lambda row: _rule_matches(row, rule))
    return Behavior(mask, rule, int(rule.get("complexity", 0)), float(rule["target_cost_usd"]), float(rule["price_cap"]))


def grammar_manifest(rows: list[WideRow]) -> dict[str, Any]:
    return {
        "version": "phase3d_absolute_wide_grid_v1", "models": sorted({row.model_id for row in rows}),
        "market_families": sorted({row.market_family for row in rows}),
        "sides": ["ANY"] + sorted({row.selected_side for row in rows}),
        "strategy_buckets": ["ANY"] + sorted({row.strategy_bucket for row in rows}),
        "convictions": ["ANY", "HIGH", "LOW"], "delays": ["ANY"] + sorted({row.delay for row in rows}),
        "lifecycles": ["ANY"] + sorted({row.lifecycle for row in rows}),
        "geographies": "ANY, US, INTERNATIONAL, and every observed station",
        "time_windows": TIME_WINDOWS, "bucket_shapes": ["ANY", "INTERVAL", "LOWER_TAIL", "UPPER_TAIL"],
        "entry_floors": ENTRY_FLOORS, "edge_floors": EDGE_FLOORS, "spread_caps": SPREAD_CAPS,
        "observation_age_caps_minutes": OBSERVATION_AGE_CAPS, "execution_delay_caps_ms": EXECUTION_DELAY_CAPS_MS,
        "target_costs_usd": TARGET_COSTS, "price_caps": PRICE_CAPS, "fill_fractions": FILL_FRACTIONS,
        "daily_risk_caps_usd": DAILY_RISK_CAPS,
        "behavioral_normalization": "identical discovery-row masks collapse outcome-blind to least-complex canonical rule",
    }


def _dimensions(rows: list[WideRow]) -> list[tuple[str, list[tuple[Any, int]]]]:
    stations = sorted({row.station for row in rows})
    return [
        ("strategy_bucket", [(v, _mask(rows, lambda r, v=v: r.strategy_bucket == v)) for v in sorted({r.strategy_bucket for r in rows})]),
        ("selected_side", [(v, _mask(rows, lambda r, v=v: r.selected_side == v)) for v in sorted({r.selected_side for r in rows})]),
        ("conviction", [(v, _mask(rows, lambda r, v=v: r.high_conviction == (v == "HIGH"))) for v in ("HIGH", "LOW")]),
        ("observation_delay_bucket", [(v, _mask(rows, lambda r, v=v: r.delay == v)) for v in sorted({r.delay for r in rows})]),
        ("lifecycle_horizon", [(v, _mask(rows, lambda r, v=v: r.lifecycle == v)) for v in sorted({r.lifecycle for r in rows})]),
        ("geography", [("US", _mask(rows, lambda r: r.station.startswith("K"))), ("INTERNATIONAL", _mask(rows, lambda r: not r.station.startswith("K")))] + [(v, _mask(rows, lambda r, v=v: r.station == v)) for v in stations]),
        ("local_window", [(list(v), _mask(rows, lambda r, v=v: v[0] <= r.local_hhmm < v[1])) for v in TIME_WINDOWS[1:]]),
        ("bucket_shape", [(v, _mask(rows, lambda r, v=v: _bucket_shape(r.selected_bucket) == v)) for v in ("INTERVAL", "LOWER_TAIL", "UPPER_TAIL")]),
        ("entry_floor", [(v, _mask(rows, lambda r, v=v: r.best_ask >= v - 1e-12)) for v in ENTRY_FLOORS[1:]]),
        ("minimum_model_edge", [(v, _mask(rows, lambda r, v=v: r.edge >= v - 1e-12)) for v in EDGE_FLOORS]),
        ("maximum_spread", [(v, _mask(rows, lambda r, v=v: r.spread is not None and r.spread <= v + 1e-12)) for v in SPREAD_CAPS]),
        ("maximum_observation_age_minutes", [(v, _mask(rows, lambda r, v=v: r.observation_age_minutes <= v + 1e-12)) for v in OBSERVATION_AGE_CAPS]),
        ("maximum_execution_delay_ms", [(v, _mask(rows, lambda r, v=v: r.execution_delay_ms <= v + 1e-12)) for v in EXECUTION_DELAY_CAPS_MS]),
    ]


def _rule_matches(row: WideRow, rule: dict[str, Any]) -> bool:
    checks = (
        rule["selected_side"] == "ANY" or row.selected_side == rule["selected_side"],
        rule["strategy_bucket"] == "ANY" or row.strategy_bucket == rule["strategy_bucket"],
        rule["conviction"] == "ANY" or row.high_conviction == (rule["conviction"] == "HIGH"),
        rule["observation_delay_bucket"] == "ANY" or row.delay == rule["observation_delay_bucket"],
        rule["lifecycle_horizon"] == "ANY" or row.lifecycle == rule["lifecycle_horizon"],
        _geography_matches(row.station, rule["geography"]),
        rule["local_window"][0] <= row.local_hhmm < rule["local_window"][1],
        rule["bucket_shape"] == "ANY" or _bucket_shape(row.selected_bucket) == rule["bucket_shape"],
        row.best_ask >= float(rule["entry_floor"]) - 1e-12,
        rule["minimum_model_edge"] is None or row.edge >= float(rule["minimum_model_edge"]) - 1e-12,
        rule["maximum_spread"] is None or (row.spread is not None and row.spread <= float(rule["maximum_spread"]) + 1e-12),
        rule["maximum_observation_age_minutes"] is None or row.observation_age_minutes <= float(rule["maximum_observation_age_minutes"]) + 1e-12,
        rule["maximum_execution_delay_ms"] is None or row.execution_delay_ms <= float(rule["maximum_execution_delay_ms"]) + 1e-12,
        _fillable(row, float(rule["price_cap"]), float(rule["target_cost_usd"]), float(rule["minimum_fill_fraction"])),
    )
    return all(checks)


def _fillable(row: WideRow, cap: float, target: float, fraction: float) -> bool:
    values = row.summaries.get(_execution_key(cap, target)) or {}
    return row.best_ask <= cap + 1e-12 and values.get("vwap") is not None and float(values.get("cost_usd") or 0.0) >= target * fraction - 1e-9


def _execution_key(cap: float, target: float) -> str:
    return f"cap={cap:.8f}|target={target:.8f}"


def _mask(rows: list[WideRow], predicate: Any) -> int:
    value = 0
    for index, row in enumerate(rows):
        if predicate(row): value |= 1 << index
    return value


def _indices(mask: int):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _retain(states: dict[tuple[int, float, float], Behavior], key: tuple[int, float, float], candidate: Behavior) -> None:
    current = states.get(key)
    if current is None or (candidate.complexity, stable_hash(candidate.rule)) < (current.complexity, stable_hash(current.rule)):
        states[key] = candidate


def _bucket_shape(value: str) -> str:
    if value.startswith("<="): return "LOWER_TAIL"
    if value.startswith(">="): return "UPPER_TAIL"
    return "INTERVAL"


def _geography_matches(station: str, value: str) -> bool:
    if value == "ANY": return True
    if value == "US": return station.startswith("K")
    if value == "INTERNATIONAL": return not station.startswith("K")
    return station == value


def _model_root(model: str) -> str:
    for suffix in ("_metar_hrrr_rich_pm_active_us12_obs_2022_2025", "_hrrr_rich_pm_active_us12_obs_2022_2025", "_hrrr_v2_obs_2022_2025", "_pm_active_us12_obs_2022_2025", "_international_celsius_high_obs_2022_2025", "_international_celsius_low_obs_2022_2025", "_obs_2022_2025"):
        if model.endswith(suffix): return model[:-len(suffix)]
    return model


def _folds(values: list[str], count: int) -> list[list[str]]:
    output = [[] for _ in range(count)]
    for index, value in enumerate(values): output[min(index * count // len(values), count - 1)].append(value)
    return output


def _cluster_lcb(daily: dict[str, dict[str, float]]) -> dict[str, float]:
    values = [item["rr"] for item in daily.values()]
    if len(values) < 2: return {"mean": -1e9, "lcb": -1e9}
    mean = statistics.fmean(values); se = statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": round(mean, 8), "lcb": round(mean - 1.645 * se, 8)}


def _profit_concentration(daily: dict[str, dict[str, float]]) -> float:
    wins = [max(0.0, item["pnl"]) for item in daily.values()]
    return round(max(wins) / sum(wins), 8) if sum(wins) else 1.0


def _bootstrap(daily: dict[str, dict[str, float]], repetitions: int, seed: str) -> dict[str, Any]:
    values = list(daily.values())
    if not values: return {"dates": 0, "lower_5pct": None, "median": None, "probability_positive": None}
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)); samples = []
    for _ in range(repetitions):
        chosen = [values[rng.randrange(len(values))] for _ in values]
        cost = sum(x["cost"] for x in chosen); samples.append(sum(x["pnl"] for x in chosen) / cost if cost else 0.0)
    samples.sort()
    return {"dates": len(values), "lower_5pct": round(samples[int(.05 * (len(samples) - 1))], 8), "median": round(samples[int(.5 * (len(samples) - 1))], 8), "probability_positive": round(sum(x > 0 for x in samples) / len(samples), 8)}


def _rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (-item["penalized_cluster_lcb"], -(item["rr"] or -1e9), item["complexity"], item["rule_id"])


def _no_daily(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "daily"}


def _finish(status: str, manifest: dict[str, Any], representatives: list[Any], grid: dict[str, Any], started: float) -> dict[str, Any]:
    result = {"status": status, "manifest": manifest, "grid": grid, "family_representatives": representatives, "funded_authorization": False, "elapsed_seconds": round(time.monotonic() - started, 3)}
    result["result_content_hash"] = stable_hash(result)
    return result
