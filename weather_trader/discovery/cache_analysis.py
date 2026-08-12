from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from weather_trader.pricing.contracts import stable_hash


COMPLETED_WITH_EMERGED_STRATEGIES = "COMPLETED_WITH_EMERGED_STRATEGIES"
COMPLETED_NO_EMERGED_STRATEGIES = "COMPLETED_NO_EMERGED_STRATEGIES"
INCOMPLETE_CACHE = "INCOMPLETE_CACHE"
FAILED_ANALYSIS = "FAILED_ANALYSIS"

SIDE_RULES = ("ANY", "BUY_NO", "BUY_YES")
DELAY_RULES: tuple[str | None, ...] = (None, "10m", "15m")
LOCAL_WINDOWS = (("00:00", "24:00"), ("12:00", "15:00"))
ENTRY_BANDS = ((0.0, 0.50), (0.05, 0.50), (0.05, 0.35))
EDGE_MINIMUMS = (0.0, 0.03, 0.05, 0.08, 0.10, 0.15)
SPREAD_MAXIMUMS: tuple[float | None, ...] = (None, 0.05)


@dataclass(frozen=True)
class HistoricalDiscoveryConfig:
    source_start_date: str
    cutoff_exclusive: str
    holdout_dates: int = 5
    fold_count: int = 3
    minimum_discovery_dates: int = 6
    minimum_discovery_trades: int = 20
    minimum_fill_fraction: float = 0.50
    target_cost_usd: float = 25.0
    daily_risk_cap_usd: float = 300.0
    bootstrap_repetitions: int = 2_000
    complexity_penalty_per_unit: float = 0.005
    grammar_version: str = "phase3d_bounded_grid_v1"
    outcome_enrichment_version: str = "weather_outcome_v1"

    def __post_init__(self) -> None:
        if self.source_start_date >= self.cutoff_exclusive:
            raise ValueError("source start date must precede discovery cutoff")
        if self.holdout_dates < 1 or self.fold_count < 2:
            raise ValueError("holdout dates and fold count must be positive")
        if self.minimum_discovery_dates < self.fold_count:
            raise ValueError("minimum discovery dates must cover every fold")
        if self.minimum_discovery_trades < 1 or not 0 < self.minimum_fill_fraction <= 1:
            raise ValueError("discovery sample and fill gates must be positive")
        if self.target_cost_usd <= 0 or self.daily_risk_cap_usd < self.target_cost_usd:
            raise ValueError("target cost and daily risk cap are inconsistent")
        if self.bootstrap_repetitions < 1 or self.complexity_penalty_per_unit < 0:
            raise ValueError("bootstrap and complexity settings must be nonnegative")


@dataclass(frozen=True)
class CachedAnalysisRow:
    mapping_id: str
    source_snapshot_id: int
    decision_id: str
    quote_ready_timestamp_utc: str
    execution_timestamp_utc: str
    station: str
    market_date: str
    model_id: str
    market_family: str
    selected_side: str
    strategy_bucket: str
    high_conviction: bool
    observation_delay_bucket: str
    local_hhmm: str
    best_bid: float | None
    best_ask: float
    spread: float | None
    model_fair: float
    edge_at_best: float
    label: int
    execution_summaries: dict[str, dict[str, float | None]]
    decision_result_hash: str
    outcome_result_hash: str


@dataclass(frozen=True)
class DiscoveryRule:
    model_id: str
    market_family: str
    selected_side: str
    observation_delay_bucket: str | None
    local_start: str
    local_end: str
    entry_min: float
    entry_max: float
    edge_minimum: float
    spread_maximum: float | None

    @property
    def rule_id(self) -> str:
        return f"p3d_rule_{stable_hash(self.payload())[:24]}"

    @property
    def family_id(self) -> str:
        # Delay, clock, price, edge, and spread variants are deliberately one
        # correlated family and cannot multiply holdout looks.
        payload = {
            'model_id': self.model_id,
            'market_family': self.market_family,
            'selected_side': self.selected_side,
        }
        return f"p3d_family_{stable_hash(payload)[:20]}"

    @property
    def complexity(self) -> int:
        return sum((
            self.selected_side != "ANY",
            self.observation_delay_bucket is not None,
            (self.local_start, self.local_end) != ("00:00", "24:00"),
            (self.entry_min, self.entry_max) != (0.0, 0.50),
            self.edge_minimum > 0,
            self.spread_maximum is not None,
        ))

    def payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "market_family": self.market_family,
            "selected_side": self.selected_side,
            "observation_delay_bucket": self.observation_delay_bucket or "ANY",
            "local_window": [self.local_start, self.local_end],
            "entry_band": [self.entry_min, self.entry_max],
            "minimum_model_edge_at_best_ask": self.edge_minimum,
            "maximum_spread": self.spread_maximum,
            "require_high_conviction": True,
            "dedupe_scope": "first_station_date",
            "execution": "first_post_ready_checkpoint_taker_v1",
        }


def load_cached_analysis_rows(
    cache: sqlite3.Connection,
    *,
    contract_hash: str,
    config: HistoricalDiscoveryConfig,
    require_high_conviction: bool = True,
) -> tuple[list[CachedAnalysisRow], dict[str, Any]]:
    cache.row_factory = sqlite3.Row
    pending = int(cache.execute(
        "select count(*) from executable_decisions where contract_hash=? and status='PENDING'",
        (contract_hash,),
    ).fetchone()[0])
    total_mappings = int(cache.execute(
        """select count(*) from model_decision_mappings
           where contract_hash=? and market_date>=? and market_date<?""",
        (contract_hash, config.source_start_date, config.cutoff_exclusive),
    ).fetchone()[0])
    rows = cache.execute(
        """select m.mapping_id,m.source_snapshot_id,m.decision_id,
                  d.quote_ready_timestamp_utc,d.execution_timestamp_utc,
                  m.station,m.market_date,m.model_id,m.market_family,m.selected_side,
                  m.strategy_bucket,m.high_conviction,
                  m.observation_delay_bucket,m.local_decision_hhmm,m.raw_model_fair,
                  d.best_bid,d.best_ask,d.spread,d.execution_summaries_json,
                  d.result_hash decision_result_hash,e.result_hash outcome_result_hash,
                  e.value_json outcome_value_json
           from model_decision_mappings m
           join executable_decisions d on d.decision_id=m.decision_id
           join decision_enrichments e on e.decision_id=d.decision_id
             and e.enrichment_kind='RESEARCH_OUTCOME'
             and e.enrichment_version=? and e.status='AVAILABLE'
           where m.contract_hash=? and m.market_date>=? and m.market_date<?
             and (?=0 or m.high_conviction=1) and d.status='SUCCESS'
           order by m.market_date,d.quote_ready_timestamp_utc,m.source_snapshot_id,m.mapping_id""",
        (
            config.outcome_enrichment_version,
            contract_hash,
            config.source_start_date,
            config.cutoff_exclusive,
            int(require_high_conviction),
        ),
    ).fetchall()
    output: list[CachedAnalysisRow] = []
    invalid = Counter()
    for source in rows:
        try:
            outcome = json.loads(str(source["outcome_value_json"]))
            summaries = json.loads(str(source["execution_summaries_json"]))
            fair = float(source["raw_model_fair"])
            best_ask = float(source["best_ask"])
            label = int(outcome["label"])
            if label not in {0, 1} or not math.isfinite(fair) or not math.isfinite(best_ask):
                raise ValueError("non-finite cached analysis value")
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            invalid["INVALID_CACHED_ROW"] += 1
            continue
        output.append(CachedAnalysisRow(
            mapping_id=str(source["mapping_id"]),
            source_snapshot_id=int(source["source_snapshot_id"]),
            decision_id=str(source["decision_id"]),
            quote_ready_timestamp_utc=str(source["quote_ready_timestamp_utc"]),
            execution_timestamp_utc=str(source["execution_timestamp_utc"]),
            station=str(source["station"]),
            market_date=str(source["market_date"]),
            model_id=str(source["model_id"]),
            market_family=str(source["market_family"]),
            selected_side=str(source["selected_side"]),
            strategy_bucket=str(source["strategy_bucket"]),
            high_conviction=bool(source["high_conviction"]),
            observation_delay_bucket=str(source["observation_delay_bucket"]),
            local_hhmm=str(source["local_decision_hhmm"]),
            best_bid=float(source["best_bid"]) if source["best_bid"] is not None else None,
            best_ask=best_ask,
            spread=float(source["spread"]) if source["spread"] is not None else None,
            model_fair=fair,
            edge_at_best=fair - best_ask,
            label=label,
            execution_summaries=summaries,
            decision_result_hash=str(source["decision_result_hash"]),
            outcome_result_hash=str(source["outcome_result_hash"]),
        ))
    row_hashes = [stable_hash(_row_payload(row)) for row in output]
    return output, {
        "contract_hash": contract_hash,
        "total_source_mappings": total_mappings,
        "pending_decisions": pending,
        "eligible_analysis_rows": len(output),
        "invalid_cached_rows": invalid["INVALID_CACHED_ROW"],
        "row_set_hash": stable_hash(row_hashes),
    }


def run_historical_discovery(
    rows: list[CachedAnalysisRow],
    *,
    config: HistoricalDiscoveryConfig,
    cache_diagnostics: dict[str, Any],
    sealed_manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        **sealed_manifest,
        "configuration": asdict(config),
        "grammar": {
            "sides": list(SIDE_RULES),
            "delays": [value or "ANY" for value in DELAY_RULES],
            "local_windows": [list(value) for value in LOCAL_WINDOWS],
            "entry_bands": [list(value) for value in ENTRY_BANDS],
            "edge_minimums": list(EDGE_MINIMUMS),
            "spread_maximums": [value if value is not None else "ANY" for value in SPREAD_MAXIMUMS],
        },
        "cache": cache_diagnostics,
        "funded_authorization": False,
    }
    manifest_hash = stable_hash(manifest)
    manifest["manifest_hash"] = manifest_hash
    if int(cache_diagnostics.get("pending_decisions", 0)):
        return _finalize({
            "status": INCOMPLETE_CACHE,
            "plain_language_answer": "Discovery did not run because the sealed cache still has pending decisions.",
            "manifest": manifest,
            "grid": _empty_grid(),
            "family_representatives": [],
            "existing_candidates": [],
            "attrition": {"pending_decisions": cache_diagnostics["pending_decisions"]},
            "funded_authorization": False,
        })
    market_dates = sorted({row.market_date for row in rows})
    if len(market_dates) <= config.holdout_dates + config.fold_count:
        return _finalize({
            "status": COMPLETED_NO_EMERGED_STRATEGIES,
            "plain_language_answer": "No strategy emerged because too few resolved executable dates exist for the declared folds and untouched holdout.",
            "manifest": manifest,
            "grid": {**_empty_grid(), "available_resolved_dates": market_dates},
            "family_representatives": [],
            "existing_candidates": [],
            "attrition": {"eligible_analysis_rows": len(rows)},
            "funded_authorization": False,
        })

    discovery_dates = market_dates[:-config.holdout_dates]
    holdout_dates = market_dates[-config.holdout_dates:]
    discovery_rows = [row for row in rows if row.market_date in set(discovery_dates)]
    holdout_rows = [row for row in rows if row.market_date in set(holdout_dates)]
    folds = _contiguous_folds(discovery_dates, config.fold_count)
    rules = generate_rules(discovery_rows)
    by_model: dict[tuple[str, str], list[CachedAnalysisRow]] = defaultdict(list)
    for row in discovery_rows:
        by_model[(row.model_id, row.market_family)].append(row)
    scored = [
        score_discovery_rule(rule, by_model[(rule.model_id, rule.market_family)], discovery_dates, folds, config)
        for rule in rules
    ]
    ranked = sorted(scored, key=_rank_key)
    frozen_representatives: list[dict[str, Any]] = []
    seen_families: set[str] = set()
    for item in ranked:
        if not item["passes_discovery_gate"] or item["family_id"] in seen_families:
            continue
        seen_families.add(item["family_id"])
        frozen_representatives.append({
            "rule_id": item["rule_id"],
            "family_id": item["family_id"],
            "rule": item["rule"],
            "discovery_score_hash": stable_hash(item),
            "discovery": item,
        })
    freeze_hash = stable_hash([
        {key: item[key] for key in ("rule_id", "family_id", "discovery_score_hash")}
        for item in frozen_representatives
    ])
    evaluated = evaluate_frozen_holdout(
        frozen_representatives,
        holdout_rows,
        holdout_dates,
        config,
        representative_freeze_hash=freeze_hash,
    )
    emerged = [item for item in evaluated if item["survives_holdout"]]
    status = COMPLETED_WITH_EMERGED_STRATEGIES if emerged else COMPLETED_NO_EMERGED_STRATEGIES
    answer = (
        f"{len(emerged)} correlated strategy families survived the untouched historical holdout."
        if emerged
        else "No correlated strategy family survived the untouched historical holdout."
    )
    return _finalize({
        "status": status,
        "plain_language_answer": answer,
        "manifest": manifest,
        "grid": {
            "candidate_rules": len(rules),
            "passing_rules": sum(bool(item["passes_discovery_gate"]) for item in ranked),
            "passing_correlated_families": len(frozen_representatives),
            "surviving_holdout_families": len(emerged),
            "discovery_dates": discovery_dates,
            "holdout_dates": holdout_dates,
            "folds": folds,
            "representative_freeze_hash": freeze_hash,
            "ranked_rules": ranked,
        },
        "family_representatives": evaluated,
        "existing_candidates": [],
        "existing_candidate_evaluation_status": "D4_NOT_YET_INTEGRATED",
        "attrition": {
            "total_source_mappings": cache_diagnostics.get("total_source_mappings", 0),
            "eligible_analysis_rows": len(rows),
            "invalid_cached_rows": cache_diagnostics.get("invalid_cached_rows", 0),
        },
        "evidence_provenance": {
            "economics": "WEATHER_OUTCOME_DIAGNOSTIC",
            "execution": "PUBLIC_TAPE_DELAYED_TAKER_COUNTERFACTUAL",
            "venue_settlement": "NOT_USED_FOR_D3",
            "actual_orders": "UNAVAILABLE",
        },
        "funded_authorization": False,
    })


def generate_rules(rows: list[CachedAnalysisRow]) -> list[DiscoveryRule]:
    model_families = sorted({(row.model_id, row.market_family) for row in rows})
    return [
        DiscoveryRule(
            model_id=model_id,
            market_family=market_family,
            selected_side=side,
            observation_delay_bucket=delay,
            local_start=local_start,
            local_end=local_end,
            entry_min=entry_min,
            entry_max=entry_max,
            edge_minimum=edge_minimum,
            spread_maximum=spread_maximum,
        )
        for model_id, market_family in model_families
        for side in SIDE_RULES
        for delay in DELAY_RULES
        for local_start, local_end in LOCAL_WINDOWS
        for entry_min, entry_max in ENTRY_BANDS
        for edge_minimum in EDGE_MINIMUMS
        for spread_maximum in SPREAD_MAXIMUMS
    ]


def score_discovery_rule(
    rule: DiscoveryRule,
    rows: list[CachedAnalysisRow],
    discovery_dates: list[str],
    folds: list[list[str]],
    config: HistoricalDiscoveryConfig,
) -> dict[str, Any]:
    period = score_period(rule, rows, set(discovery_dates), config)
    fold_scores = [_without_daily(score_period(rule, rows, set(fold), config)) for fold in folds]
    populated = [fold for fold in fold_scores if fold["trades"]]
    positive_folds = sum(fold["pnl"] > 0 for fold in populated)
    stable = bool(populated) and positive_folds / len(populated) >= 2 / 3
    cluster = _cluster_lcb(period["daily"])
    passes = bool(
        period["effective_dates"] >= config.minimum_discovery_dates
        and period["trades"] >= config.minimum_discovery_trades
        and period["pnl"] > 0
        and stable
    )
    return {
        "rule_id": rule.rule_id,
        "family_id": rule.family_id,
        "rule": rule.payload(),
        "complexity": rule.complexity,
        **_without_daily(period),
        "daily": period["daily"],
        "folds": fold_scores,
        "positive_populated_folds": positive_folds,
        "populated_folds": len(populated),
        "stable_across_folds": stable,
        "cluster_mean_daily_rr": cluster["mean"],
        "cluster_lcb_daily_rr": cluster["lcb"],
        "penalized_cluster_lcb": round(
            cluster["lcb"] - config.complexity_penalty_per_unit * rule.complexity,
            8,
        ),
        "passes_discovery_gate": passes,
    }


def evaluate_frozen_holdout(
    frozen_representatives: list[dict[str, Any]],
    holdout_rows: list[CachedAnalysisRow],
    holdout_dates: list[str],
    config: HistoricalDiscoveryConfig,
    *,
    representative_freeze_hash: str,
) -> list[dict[str, Any]]:
    expected = stable_hash([
        {key: item[key] for key in ("rule_id", "family_id", "discovery_score_hash")}
        for item in frozen_representatives
    ])
    if expected != representative_freeze_hash:
        raise ValueError("representatives changed after the holdout boundary was frozen")
    by_model: dict[tuple[str, str], list[CachedAnalysisRow]] = defaultdict(list)
    for row in holdout_rows:
        by_model[(row.model_id, row.market_family)].append(row)
    evaluated = []
    for frozen in frozen_representatives:
        rule = _rule_from_payload(frozen["rule"])
        holdout = score_period(
            rule,
            by_model[(rule.model_id, rule.market_family)],
            set(holdout_dates),
            config,
        )
        bootstrap = _bootstrap_rr(
            holdout["daily"],
            config.bootstrap_repetitions,
            seed=f"holdout:{rule.rule_id}",
        )
        survives = bool(
            holdout["effective_dates"] >= min(3, len(holdout_dates))
            and holdout["pnl"] > 0
            and bootstrap["median"] is not None
            and bootstrap["median"] > 0
        )
        evaluated.append({
            **frozen,
            "representative_freeze_hash": representative_freeze_hash,
            "holdout": _without_daily(holdout),
            "holdout_daily": holdout["daily"],
            "holdout_bootstrap_rr": bootstrap,
            "survives_holdout": survives,
        })
    return sorted(evaluated, key=lambda item: (
        not item["survives_holdout"],
        -(item["holdout"]["rr"] if item["holdout"]["rr"] is not None else -1_000_000_000.0),
        item["rule_id"],
    ))


def score_period(
    rule: DiscoveryRule,
    rows: list[CachedAnalysisRow],
    dates: set[str],
    config: HistoricalDiscoveryConfig,
) -> dict[str, Any]:
    matches = [row for row in rows if row.market_date in dates and _matches(row, rule)]
    first: dict[tuple[str, str], CachedAnalysisRow] = {}
    for row in sorted(matches, key=lambda item: (
        item.quote_ready_timestamp_utc,
        item.source_snapshot_id,
        item.mapping_id,
    )):
        first.setdefault((row.station, row.market_date), row)
    executions = []
    used_by_date: dict[str, float] = defaultdict(float)
    key = _execution_key(rule.entry_max, config.target_cost_usd)
    for row in sorted(first.values(), key=lambda item: (
        item.quote_ready_timestamp_utc,
        item.source_snapshot_id,
        item.mapping_id,
    )):
        values = row.execution_summaries.get(key)
        if not values:
            continue
        cost = float(values.get("cost_usd") or 0.0)
        shares = float(values.get("shares") or 0.0)
        vwap = values.get("vwap")
        if (
            cost < config.target_cost_usd * config.minimum_fill_fraction
            or vwap is None
            or used_by_date[row.market_date] + cost > config.daily_risk_cap_usd + 1e-9
        ):
            continue
        used_by_date[row.market_date] += cost
        pnl = shares - cost if row.label else -cost
        executions.append({
            "station": row.station,
            "market_date": row.market_date,
            "cost": cost,
            "shares": shares,
            "vwap": float(vwap),
            "pnl": pnl,
        })
    daily: dict[str, dict[str, float]] = {}
    for market_date in sorted({row["market_date"] for row in executions}):
        subset = [row for row in executions if row["market_date"] == market_date]
        cost = sum(row["cost"] for row in subset)
        pnl = sum(row["pnl"] for row in subset)
        daily[market_date] = {
            "cost": round(cost, 8),
            "pnl": round(pnl, 8),
            "rr": round(pnl / cost, 8) if cost else 0.0,
        }
    total_cost = sum(row["cost"] for row in executions)
    total_pnl = sum(row["pnl"] for row in executions)
    station_counts = Counter(row["station"] for row in executions)
    return {
        "trades": len(executions),
        "effective_dates": len(daily),
        "cost": round(total_cost, 8),
        "pnl": round(total_pnl, 8),
        "rr": round(total_pnl / total_cost, 8) if total_cost else None,
        "win_rate": round(sum(row["pnl"] > 0 for row in executions) / len(executions), 8) if executions else None,
        "average_vwap": round(sum(row["vwap"] for row in executions) / len(executions), 8) if executions else None,
        "maximum_station_trade_share": round(max(station_counts.values()) / len(executions), 8) if executions else None,
        "daily": daily,
    }


def write_discovery_report(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(_markdown_report(result), encoding="utf-8")
    _write_ranked_csv(output_dir / "ranked_rules.csv", result["grid"].get("ranked_rules", []))


def _matches(row: CachedAnalysisRow, rule: DiscoveryRule) -> bool:
    if rule.selected_side != "ANY" and row.selected_side != rule.selected_side:
        return False
    if rule.observation_delay_bucket is not None and row.observation_delay_bucket != rule.observation_delay_bucket:
        return False
    if not rule.local_start <= row.local_hhmm < rule.local_end:
        return False
    if not rule.entry_min <= row.best_ask <= rule.entry_max:
        return False
    if row.edge_at_best + 1e-12 < rule.edge_minimum:
        return False
    if rule.spread_maximum is not None and (row.spread is None or row.spread > rule.spread_maximum + 1e-12):
        return False
    return True


def _execution_key(price_cap: float, target_cost: float) -> str:
    return f"cap={price_cap:.8f}|target={target_cost:.8f}"


def _contiguous_folds(values: list[str], count: int) -> list[list[str]]:
    folds: list[list[str]] = [[] for _ in range(count)]
    for index, value in enumerate(values):
        folds[min(index * count // len(values), count - 1)].append(value)
    return folds


def _cluster_lcb(daily: dict[str, dict[str, float]]) -> dict[str, float]:
    values = [item["rr"] for item in daily.values()]
    if not values:
        return {"mean": -1_000_000_000.0, "lcb": -1_000_000_000.0}
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"mean": round(mean, 8), "lcb": -1_000_000_000.0}
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": round(mean, 8), "lcb": round(mean - 1.645 * standard_error, 8)}


def _bootstrap_rr(
    daily: dict[str, dict[str, float]],
    repetitions: int,
    *,
    seed: str,
) -> dict[str, float | int | None]:
    values = list(daily.values())
    if not values:
        return {
            "dates": 0,
            "lower_5pct": None,
            "median": None,
            "upper_95pct": None,
            "probability_positive": None,
        }
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    samples = []
    for _ in range(repetitions):
        selected = [values[rng.randrange(len(values))] for _ in values]
        cost = sum(item["cost"] for item in selected)
        pnl = sum(item["pnl"] for item in selected)
        samples.append(pnl / cost if cost else 0.0)
    samples.sort()

    def quantile(fraction: float) -> float:
        return samples[min(int(fraction * (len(samples) - 1)), len(samples) - 1)]

    return {
        "dates": len(values),
        "lower_5pct": round(quantile(0.05), 8),
        "median": round(quantile(0.50), 8),
        "upper_95pct": round(quantile(0.95), 8),
        "probability_positive": round(sum(value > 0 for value in samples) / len(samples), 8),
    }


def _rule_from_payload(payload: dict[str, Any]) -> DiscoveryRule:
    return DiscoveryRule(
        model_id=str(payload["model_id"]),
        market_family=str(payload["market_family"]),
        selected_side=str(payload["selected_side"]),
        observation_delay_bucket=(
            None if payload["observation_delay_bucket"] == "ANY"
            else str(payload["observation_delay_bucket"])
        ),
        local_start=str(payload["local_window"][0]),
        local_end=str(payload["local_window"][1]),
        entry_min=float(payload["entry_band"][0]),
        entry_max=float(payload["entry_band"][1]),
        edge_minimum=float(payload["minimum_model_edge_at_best_ask"]),
        spread_maximum=(
            None if payload["maximum_spread"] is None
            else float(payload["maximum_spread"])
        ),
    )


def _rank_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        not item["passes_discovery_gate"],
        -item["penalized_cluster_lcb"],
        -(item["rr"] if item["rr"] is not None else -1_000_000_000.0),
        item["complexity"],
        item["rule_id"],
    )


def _without_daily(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "daily"}


def _row_payload(row: CachedAnalysisRow) -> dict[str, Any]:
    return asdict(row)


def _empty_grid() -> dict[str, Any]:
    return {
        "candidate_rules": 0,
        "passing_rules": 0,
        "passing_correlated_families": 0,
        "surviving_holdout_families": 0,
        "discovery_dates": [],
        "holdout_dates": [],
        "folds": [],
        "representative_freeze_hash": stable_hash([]),
        "ranked_rules": [],
    }


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload["result_content_hash"] = stable_hash(result)
    return payload


def _write_ranked_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "rule_id", "family_id", "passes_discovery_gate", "penalized_cluster_lcb",
        "cluster_lcb_daily_rr", "rr", "pnl", "cost", "trades", "effective_dates",
        "stable_across_folds", "positive_populated_folds", "complexity", "rule_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **{field: row.get(field) for field in fields if field != "rule_json"},
                "rule_json": json.dumps(row["rule"], sort_keys=True, separators=(",", ":")),
            })


def _markdown_report(result: dict[str, Any]) -> str:
    manifest = result["manifest"]
    grid = result["grid"]
    lines = [
        "# Deterministic Tape-Backed Discovery",
        "",
        f"Status: **{result['status']}**",
        "",
        result["plain_language_answer"],
        "",
        "> Weather-outcome diagnostic and delayed public-tape taker counterfactual only. Funded authorization is false.",
        "",
        "## Sealed inputs",
        "",
        f"- Manifest hash: `{manifest['manifest_hash']}`",
        f"- Result content hash: `{result['result_content_hash']}`",
        f"- Decision contract: `{manifest.get('decision_contract_hash', manifest['cache'].get('contract_hash'))}`",
        f"- Cutoff exclusive: `{manifest['configuration']['cutoff_exclusive']}`",
        f"- Cache row set: `{manifest['cache']['row_set_hash']}`",
        "",
        "## Cache health and grid",
        "",
        f"- Source model mappings: {manifest['cache']['total_source_mappings']}",
        f"- Eligible cached analysis rows: {manifest['cache']['eligible_analysis_rows']}",
        f"- Pending decisions: {manifest['cache']['pending_decisions']}",
        f"- Candidate rules: {grid.get('candidate_rules', 0)}",
        f"- Passing correlated families: {grid.get('passing_correlated_families', 0)}",
        f"- Holdout survivors: {grid.get('surviving_holdout_families', 0)}",
        "",
        "## Frozen family representatives",
        "",
        "| Holdout | Model | Family | Side | Discovery RR | Holdout trades | Holdout RR |",
        "|:---:|---|---|---|---:|---:|---:|",
    ]
    for item in result["family_representatives"]:
        rule = item["rule"]
        lines.append(
            f"| {'PASS' if item['survives_holdout'] else 'FAIL'} | {rule['model_id']} | "
            f"{item['family_id']} | {rule['selected_side']} | {item['discovery']['rr']} | "
            f"{item['holdout']['trades']} | {item['holdout']['rr']} |"
        )
    if not result["family_representatives"]:
        lines.append("| — | — | — | — | — | — | No representative reached holdout |")
    lines.extend((
        "",
        "## Existing candidates",
        "",
    ))
    candidates = result.get("existing_candidates", [])
    if candidates:
        lines.extend((
            "| Candidate | Activation | Pre-activation excluded | Post-activation matches | Weather trades/RR | Promotion |",
            "|---|---|---:|---:|---:|---|",
        ))
        for item in candidates:
            diagnostic = item["weather_outcome_diagnostic"]
            lines.append(
                f"| {item['candidate_version_id']} | {item['activation_timestamp_utc']} | "
                f"{item['matching_rows_before_activation_excluded']} | "
                f"{item['matching_rows_at_or_after_activation']} | "
                f"{diagnostic['trades']} / {diagnostic['rr']} | {item['promotion_disposition']} |"
            )
    else:
        lines.append("No immutable existing candidate versions were present at the sealed registry state.")
    lines.extend((
        "",
        "Venue settlement, valid markouts, and actual-order evidence remain unavailable in the current cache, so weather diagnostics cannot pass promotion.",
        "",
        "## Authority",
        "",
        "`funded_authorization=false`. No discovery result creates a Phase 4 request.",
        "",
    ))
    return "\n".join(lines)
