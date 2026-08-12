from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from weather_trader.discovery.cache_analysis import CachedAnalysisRow
from weather_trader.discovery.contracts import CandidateRule
from weather_trader.pricing.contracts import stable_hash


@dataclass(frozen=True)
class ExistingCandidateVersion:
    candidate_version_id: str
    family_id: str
    family_version: int
    definition_hash: str
    source_run_id: str
    activation_timestamp_utc: str
    pricing_version: str
    execution_version: str
    risk_version: str
    current_role: str
    rule: CandidateRule
    sizing_and_risk: dict[str, Any]


@dataclass(frozen=True)
class ForwardEvaluationConfig:
    accepted_execution_version: str = "first_post_ready_checkpoint_taker_v1"
    default_target_cost_usd: float = 25.0
    default_daily_risk_cap_usd: float = 300.0
    default_station_date_cap_usd: float = 25.0

    def __post_init__(self) -> None:
        if not self.accepted_execution_version:
            raise ValueError("forward evaluation requires an execution version")
        if (
            self.default_target_cost_usd <= 0
            or self.default_daily_risk_cap_usd < self.default_target_cost_usd
            or self.default_station_date_cap_usd <= 0
        ):
            raise ValueError("forward evaluation risk defaults are invalid")


def load_existing_candidate_versions(
    registry: sqlite3.Connection,
) -> list[ExistingCandidateVersion]:
    registry.row_factory = sqlite3.Row
    rows = registry.execute(
        """with latest as (
               select candidate_version_id,to_role,
                      row_number() over (
                          partition by candidate_version_id
                          order by occurred_at_utc desc,rowid desc
                      ) event_rank
               from candidate_lifecycle_events
           )
           select candidates.*,coalesce(latest.to_role,'UNKNOWN') current_role
           from candidate_versions candidates
           left join latest on latest.candidate_version_id=candidates.candidate_version_id
                           and latest.event_rank=1
           order by candidates.activation_timestamp_utc,candidates.candidate_version_id"""
    ).fetchall()
    output = []
    for source in rows:
        definition = json.loads(str(source["definition_json"]))
        if stable_hash(definition) != str(source["definition_hash"]):
            raise ValueError(
                f"candidate definition hash mismatch: {source['candidate_version_id']}"
            )
        rule = CandidateRule(**dict(definition["rule"]))
        for field in ("family_id", "pricing_version", "execution_version", "risk_version"):
            if str(definition[field]) != str(source[field]):
                raise ValueError(
                    f"candidate immutable {field} mismatch: {source['candidate_version_id']}"
                )
        output.append(ExistingCandidateVersion(
            candidate_version_id=str(source["candidate_version_id"]),
            family_id=str(source["family_id"]),
            family_version=int(source["family_version"]),
            definition_hash=str(source["definition_hash"]),
            source_run_id=str(source["source_run_id"]),
            activation_timestamp_utc=str(source["activation_timestamp_utc"]),
            pricing_version=str(source["pricing_version"]),
            execution_version=str(source["execution_version"]),
            risk_version=str(source["risk_version"]),
            current_role=str(source["current_role"]),
            rule=rule,
            sizing_and_risk=dict(definition.get("sizing_and_risk") or {}),
        ))
    return output


def evaluate_existing_candidates(
    rows: list[CachedAnalysisRow],
    candidates: list[ExistingCandidateVersion],
    *,
    config: ForwardEvaluationConfig = ForwardEvaluationConfig(),
) -> dict[str, Any]:
    candidates = sorted(
        candidates,
        key=lambda item: (item.activation_timestamp_utc, item.candidate_version_id),
    )
    candidate_results = []
    execution_records: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        result, records = _evaluate_one(rows, candidate, config)
        candidate_results.append(result)
        execution_records[candidate.candidate_version_id] = records

    date_sets = [
        {str(row["market_date"]) for row in execution_records[item.candidate_version_id]}
        for item in candidates
        if execution_records[item.candidate_version_id]
    ]
    common_dates = sorted(set.intersection(*date_sets)) if date_sets else []
    aligned = []
    for candidate in candidates:
        records = [
            row for row in execution_records[candidate.candidate_version_id]
            if row["market_date"] in set(common_dates)
        ]
        aligned.append({
            "candidate_version_id": candidate.candidate_version_id,
            "common_dates": common_dates,
            **_score_records(records),
        })

    incremental = _incremental_cap_aware(candidates, execution_records, config)
    payload = {
        "status": "NO_EXISTING_CANDIDATES" if not candidates else "COMPLETED",
        "candidate_count": len(candidates),
        "candidates": candidate_results,
        "aligned_common_date_comparison": aligned,
        "common_dates": common_dates,
        "incremental_cap_aware_comparison": incremental,
        "portfolio_order_rule": "activation_timestamp_then_candidate_version_id",
        "evidence_provenance": {
            "weather": "DIAGNOSTIC",
            "venue_settlement": "UNAVAILABLE_IN_CURRENT_CACHE",
            "markouts": "UNAVAILABLE_IN_CURRENT_CACHE",
            "actual_orders": "UNAVAILABLE_PUBLIC_TAPE_COUNTERFACTUAL",
        },
        "funded_authorization": False,
    }
    payload["forward_content_hash"] = stable_hash(payload)
    return payload


def attach_forward_evaluation(
    historical_result: dict[str, Any],
    forward_result: dict[str, Any],
) -> dict[str, Any]:
    combined = dict(historical_result)
    combined.pop("result_content_hash", None)
    combined["existing_candidates"] = forward_result["candidates"]
    combined["existing_candidate_evaluation_status"] = forward_result["status"]
    combined["forward_evaluation"] = forward_result
    combined["result_content_hash"] = stable_hash(combined)
    return combined


def _evaluate_one(
    rows: list[CachedAnalysisRow],
    candidate: ExistingCandidateVersion,
    config: ForwardEvaluationConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    activation = _utc(candidate.activation_timestamp_utc)
    exact_rows = [
        row for row in rows
        if row.model_id == candidate.rule.model_id
        and row.market_family == candidate.rule.market_family
        and _matches_rule(row, candidate.rule)
    ]
    pre_activation = [
        row for row in exact_rows if _utc(row.quote_ready_timestamp_utc) < activation
    ]
    post_activation = [
        row for row in exact_rows if _utc(row.quote_ready_timestamp_utc) >= activation
    ]
    reasons = []
    if candidate.execution_version != config.accepted_execution_version:
        reasons.append("EXECUTION_VERSION_MISMATCH")
        post_activation = []
    price_cap = float(candidate.sizing_and_risk.get(
        "price_cap", candidate.rule.entry_price_max
    ))
    target_cost = float(candidate.sizing_and_risk.get(
        "target_cost_usd", config.default_target_cost_usd
    ))
    daily_cap = float(candidate.sizing_and_risk.get(
        "daily_risk_cap_usd",
        candidate.sizing_and_risk.get(
            "daily_cap_usd", config.default_daily_risk_cap_usd
        ),
    ))
    station_date_cap = float(candidate.sizing_and_risk.get(
        "station_date_cap_usd", config.default_station_date_cap_usd
    ))
    execution_key = _execution_key(price_cap, target_cost)
    first: dict[tuple[str, str], CachedAnalysisRow] = {}
    for row in sorted(post_activation, key=lambda item: (
        item.quote_ready_timestamp_utc,
        item.source_snapshot_id,
        item.mapping_id,
    )):
        first.setdefault((row.station, row.market_date), row)
    used_daily: dict[str, float] = defaultdict(float)
    used_station_date: dict[tuple[str, str], float] = defaultdict(float)
    records = []
    rejection_counts = Counter()
    for row in sorted(first.values(), key=lambda item: (
        item.quote_ready_timestamp_utc,
        item.source_snapshot_id,
        item.mapping_id,
    )):
        summary = row.execution_summaries.get(execution_key)
        if not summary:
            rejection_counts["EXECUTION_SUMMARY_UNAVAILABLE"] += 1
            continue
        cost = float(summary.get("cost_usd") or 0.0)
        shares = float(summary.get("shares") or 0.0)
        vwap = summary.get("vwap")
        if cost <= 0 or vwap is None:
            rejection_counts["NO_CAPPED_EXECUTABLE_ASK"] += 1
            continue
        station_key = (row.station, row.market_date)
        if used_daily[row.market_date] + cost > daily_cap + 1e-9:
            rejection_counts["DAILY_RISK_CAP"] += 1
            continue
        if used_station_date[station_key] + cost > station_date_cap + 1e-9:
            rejection_counts["STATION_DATE_CAP"] += 1
            continue
        used_daily[row.market_date] += cost
        used_station_date[station_key] += cost
        records.append({
            "mapping_id": row.mapping_id,
            "decision_id": row.decision_id,
            "quote_ready_timestamp_utc": row.quote_ready_timestamp_utc,
            "station": row.station,
            "market_date": row.market_date,
            "cost": cost,
            "shares": shares,
            "vwap": float(vwap),
            "pnl": shares - cost if row.label else -cost,
        })
    if not post_activation:
        reasons.append("NO_POST_ACTIVATION_MATCHING_DECISIONS")
    reasons.extend(("VENUE_SETTLEMENT_UNAVAILABLE", "MARKOUTS_UNAVAILABLE"))
    score = _score_records(records)
    result = {
        "candidate_version_id": candidate.candidate_version_id,
        "family_id": candidate.family_id,
        "family_version": candidate.family_version,
        "definition_hash": candidate.definition_hash,
        "source_run_id": candidate.source_run_id,
        "activation_timestamp_utc": candidate.activation_timestamp_utc,
        "pricing_version": candidate.pricing_version,
        "execution_version": candidate.execution_version,
        "risk_version": candidate.risk_version,
        "current_role": candidate.current_role,
        "exact_rule": _candidate_rule_payload(candidate.rule),
        "sizing_and_risk": candidate.sizing_and_risk,
        "execution_evidence": {
            "fill_scenario": "PUBLIC_TAPE_FIRST_POST_READY_CHECKPOINT_TAKER",
            "execution_summary_key": execution_key,
            "price_cap": price_cap,
            "target_cost_usd": target_cost,
            "daily_risk_cap_usd": daily_cap,
            "station_date_cap_usd": station_date_cap,
            "actual_fill_claim": False,
        },
        "matching_rows_before_activation_excluded": len(pre_activation),
        "matching_rows_at_or_after_activation": len(post_activation),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "weather_outcome_diagnostic": score,
        "venue_settled_statistics": None,
        "markout_statistics": None,
        "actual_order_statistics": None,
        "promotion_disposition": "CONTINUE_COLLECTING",
        "promotion_blockers": sorted(set(reasons)),
        "funded_authorization": False,
    }
    result["evaluation_hash"] = stable_hash(result)
    return result, records


def _matches_rule(row: CachedAnalysisRow, rule: CandidateRule) -> bool:
    if rule.selected_side != "ANY" and row.selected_side != rule.selected_side:
        return False
    if rule.strategy_bucket != "ANY" and row.strategy_bucket != rule.strategy_bucket:
        return False
    if rule.observation_delay_bucket is not None and row.observation_delay_bucket != rule.observation_delay_bucket:
        return False
    if not rule.local_start <= row.local_hhmm < rule.local_end:
        return False
    if not rule.entry_price_min <= row.best_ask <= rule.entry_price_max:
        return False
    if row.edge_at_best + 1e-12 < rule.minimum_model_edge_at_best_ask:
        return False
    if rule.maximum_spread is not None and (
        row.spread is None or row.spread > rule.maximum_spread + 1e-12
    ):
        return False
    if rule.require_high_conviction and not row.high_conviction:
        return False
    return True


def _incremental_cap_aware(
    candidates: list[ExistingCandidateVersion],
    execution_records: dict[str, list[dict[str, Any]]],
    config: ForwardEvaluationConfig,
) -> list[dict[str, Any]]:
    used_daily: dict[str, float] = defaultdict(float)
    used_station_date: dict[tuple[str, str], float] = defaultdict(float)
    output = []
    for candidate in candidates:
        accepted = []
        cap_rejections = Counter()
        for row in sorted(execution_records[candidate.candidate_version_id], key=lambda item: (
            item["quote_ready_timestamp_utc"], item["mapping_id"],
        )):
            cost = float(row["cost"])
            key = (str(row["station"]), str(row["market_date"]))
            if used_daily[str(row["market_date"])] + cost > config.default_daily_risk_cap_usd + 1e-9:
                cap_rejections["SHARED_DAILY_RISK_CAP"] += 1
                continue
            if used_station_date[key] + cost > config.default_station_date_cap_usd + 1e-9:
                cap_rejections["SHARED_STATION_DATE_CAP"] += 1
                continue
            used_daily[str(row["market_date"])] += cost
            used_station_date[key] += cost
            accepted.append(row)
        output.append({
            "candidate_version_id": candidate.candidate_version_id,
            "incremental_after_prior_candidates": _score_records(accepted),
            "shared_cap_rejections": dict(sorted(cap_rejections.items())),
        })
    return output


def _score_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["cost"]) for row in records)
    pnl = sum(float(row["pnl"]) for row in records)
    dates = sorted({str(row["market_date"]) for row in records})
    station_counts = Counter(str(row["station"]) for row in records)
    return {
        "trades": len(records),
        "effective_dates": len(dates),
        "market_dates": dates,
        "cost": round(cost, 8),
        "pnl": round(pnl, 8),
        "rr": round(pnl / cost, 8) if cost else None,
        "win_rate": round(sum(float(row["pnl"]) > 0 for row in records) / len(records), 8) if records else None,
        "maximum_station_trade_share": round(max(station_counts.values()) / len(records), 8) if records else None,
        "source_mapping_ids": [str(row["mapping_id"]) for row in records],
    }


def _execution_key(price_cap: float, target_cost: float) -> str:
    return f"cap={price_cap:.8f}|target={target_cost:.8f}"


def _candidate_rule_payload(rule: CandidateRule) -> dict[str, Any]:
    return {
        "model_id": rule.model_id,
        "market_family": rule.market_family,
        "selected_side": rule.selected_side,
        "strategy_bucket": rule.strategy_bucket,
        "observation_delay_bucket": rule.observation_delay_bucket,
        "local_start": rule.local_start,
        "local_end": rule.local_end,
        "entry_price_min": rule.entry_price_min,
        "entry_price_max": rule.entry_price_max,
        "require_high_conviction": rule.require_high_conviction,
        "minimum_model_edge_at_best_ask": rule.minimum_model_edge_at_best_ask,
        "maximum_spread": rule.maximum_spread,
        "dedupe_scope": rule.dedupe_scope,
        "execution_arm": rule.execution_arm,
    }


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)
