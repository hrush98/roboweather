from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_trader.discovery.cache_analysis import (
    COMPLETED_NO_EMERGED_STRATEGIES,
    COMPLETED_WITH_EMERGED_STRATEGIES,
    HistoricalDiscoveryConfig,
)
from weather_trader.discovery.contracts import CandidateRule
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.pricing.contracts import stable_hash


PRICING_VERSION = "cached_raw_model_fair_v1"
RISK_VERSION = "historical_grid_caps_v1"
SOURCE_KIND = "deterministic_cache_v1"


def candidate_rule_from_discovery_payload(payload: dict[str, Any]) -> CandidateRule:
    """Translate a D3 historical rule without dropping any selection predicate."""
    if payload.get("dedupe_scope") != "first_station_date":
        raise ValueError("emerged rules require first-station/date deduplication")
    if payload.get("execution") != "first_post_ready_checkpoint_taker_v1":
        raise ValueError("emerged rule execution contract is unsupported")
    if payload.get("require_high_conviction") is not True:
        raise ValueError("the accepted historical grammar requires high conviction")
    local_window = list(payload["local_window"])
    entry_band = list(payload["entry_band"])
    if len(local_window) != 2 or len(entry_band) != 2:
        raise ValueError("historical rule windows and bands must each have two bounds")
    delay = payload.get("observation_delay_bucket")
    return CandidateRule(
        model_id=str(payload["model_id"]),
        market_family=str(payload["market_family"]),
        selected_side=str(payload["selected_side"]),
        strategy_bucket="ANY",
        observation_delay_bucket=None if delay == "ANY" else str(delay),
        local_start=str(local_window[0]),
        local_end=str(local_window[1]),
        entry_price_min=float(entry_band[0]),
        entry_price_max=float(entry_band[1]),
        require_high_conviction=True,
        minimum_model_edge_at_best_ask=float(
            payload["minimum_model_edge_at_best_ask"]
        ),
        maximum_spread=(
            None if payload.get("maximum_spread") is None
            else float(payload["maximum_spread"])
        ),
    )


def attach_emerged_candidate_plan(
    result: dict[str, Any],
    *,
    activation_timestamp_utc: str,
    execution_version: str,
    config: HistoricalDiscoveryConfig,
) -> dict[str, Any]:
    """Seal deterministic research-only candidate identities into the report."""
    if result["status"] not in {
        COMPLETED_WITH_EMERGED_STRATEGIES,
        COMPLETED_NO_EMERGED_STRATEGIES,
    }:
        raise ValueError("only a completed analysis can plan candidate registration")
    activation = _utc_text(activation_timestamp_utc)
    candidates = []
    for representative in result.get("family_representatives", []):
        if not representative.get("survives_holdout"):
            continue
        rule = candidate_rule_from_discovery_payload(dict(representative["rule"]))
        family_id = str(representative["family_id"])
        if rule.correlated_family_id != family_id:
            raise ValueError("historical and immutable candidate family identities disagree")
        family_definition = {
            "family_id": family_id,
            "model_id": rule.model_id,
            "market_family": rule.market_family,
            "selected_side": rule.selected_side,
            "correlation_contract": "collapse_delay_clock_price_edge_spread_variants_v1",
        }
        sizing_and_risk = {
            "price_cap": rule.entry_price_max,
            "target_cost_usd": config.target_cost_usd,
            "station_date_cap_usd": config.target_cost_usd,
            "daily_risk_cap_usd": config.daily_risk_cap_usd,
        }
        definition = {
            "family_id": family_id,
            "rule": asdict(rule),
            "pricing_version": PRICING_VERSION,
            "execution_version": execution_version,
            "risk_version": RISK_VERSION,
            "sizing_and_risk": sizing_and_risk,
        }
        candidates.append({
            "candidate_version_id": f"p3d_version_{stable_hash(definition)[:24]}",
            "family_id": family_id,
            "family_definition": family_definition,
            "rule": asdict(rule),
            "activation_timestamp_utc": activation,
            "pricing_version": PRICING_VERSION,
            "execution_version": execution_version,
            "risk_version": RISK_VERSION,
            "sizing_and_risk": sizing_and_risk,
            "source_rule_id": representative["rule_id"],
        })
    combined = dict(result)
    combined.pop("result_content_hash", None)
    combined["emerged_candidate_registration"] = {
        "activation_timestamp_utc": activation,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "research_only": True,
        "funded_authorization": False,
    }
    combined["result_content_hash"] = stable_hash(combined)
    return combined


def register_emerged_candidate_plan(
    registry: DiscoveryRegistry,
    result: dict[str, Any],
    *,
    report_dir: Path,
    registered_at_utc: str,
) -> dict[str, Any]:
    """Atomically append the completed run and its already-sealed candidates."""
    registration = dict(result["emerged_candidate_registration"])
    manifest = dict(result["manifest"])
    configuration = dict(manifest["configuration"])
    run_id = f"p3d_cache_run_{result['result_content_hash'][:24]}"
    run_spec = {
        "run_id": run_id,
        "source_start_date": configuration["source_start_date"],
        "discovery_cutoff_exclusive": configuration["cutoff_exclusive"],
        "earliest_activation_timestamp": registration["activation_timestamp_utc"],
        "research_watermark": manifest.get("sealed_research_watermark", "UNKNOWN"),
        "outcome_watermark": manifest.get("sealed_outcome_watermark", "UNKNOWN"),
        "venue_settlement_watermark": "UNAVAILABLE_IN_CURRENT_CACHE",
        "grammar_version": configuration["grammar_version"],
        "decision_contract_hash": manifest.get("decision_contract_hash"),
        "manifest_hash": manifest["manifest_hash"],
        "result_content_hash": result["result_content_hash"],
    }
    outcome_status = (
        "COMPLETED" if registration["candidate_count"] else "NO_NOMINATION"
    )
    return registry.register_completed_analysis(
        run_spec=run_spec,
        created_at_utc=registered_at_utc,
        completed_at_utc=registered_at_utc,
        outcome_status=outcome_status,
        diagnostics={
            "analysis_status": result["status"],
            "result_content_hash": result["result_content_hash"],
            "surviving_holdout_families": result["grid"].get(
                "surviving_holdout_families", 0
            ),
            "funded_authorization": False,
        },
        output_refs={
            name: _file_hash(report_dir / name)
            for name in ("result.json", "report.md", "ranked_rules.csv")
        },
        nominations=list(registration["candidates"]),
        source_kind=SOURCE_KIND,
    )


def require_future_activation(
    result: dict[str, Any], *, now_utc: str
) -> None:
    registration = result["emerged_candidate_registration"]
    if int(registration["candidate_count"]) and _utc(
        registration["activation_timestamp_utc"]
    ) <= _utc(now_utc):
        raise ValueError(
            "emerged-candidate activation must be in the future when its report completes"
        )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_text(value: str) -> str:
    return _utc(value).isoformat()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)
