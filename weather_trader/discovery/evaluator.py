from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean, stdev
from typing import Any, Mapping, Sequence

from weather_trader.discovery.contracts import BroadDiscoveryRow, CandidateRule
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.pricing.contracts import stable_hash
from weather_trader.tape.replay import sweep_asks


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: Mapping[str, Any]
    cohort_id: str
    statistics: dict[str, Any]
    rejection_counts: dict[str, int]
    venue_executions: tuple[dict[str, Any], ...]


class ContinuousCohortEvaluator:
    """Append post-activation scorecards without changing candidate roles."""

    def __init__(self, registry: DiscoveryRegistry) -> None:
        if registry.read_only:
            raise ValueError("continuous evaluation requires a writable registry")
        self.registry = registry

    def evaluate(
        self,
        *,
        rows: Sequence[BroadDiscoveryRow],
        as_of_watermarks: Mapping[str, Any],
        materialization_diagnostics: Mapping[str, Any],
        created_at_utc: str,
    ) -> dict[str, Any]:
        created = _utc(created_at_utc).isoformat()
        candidates = self.registry.active_candidate_versions()
        evaluations: list[CandidateEvaluation] = []
        scorecards: list[dict[str, str]] = []
        for candidate in candidates:
            cohort_id = self._ensure_cohort(candidate, created)
            evaluation = _evaluate_candidate(list(rows), candidate, cohort_id)
            scorecard_id = self.registry.append_scorecard(
                candidate_version_id=str(candidate["candidate_version_id"]),
                cohort_id=cohort_id,
                evidence_kind="FORWARD_SHADOW",
                as_of_watermarks=as_of_watermarks,
                statistics=evaluation.statistics,
                rejection_counts=evaluation.rejection_counts,
                source_refs={
                    "row_set_hash": materialization_diagnostics.get("row_set_hash"),
                    "materialization_counts": materialization_diagnostics.get("counts", {}),
                    "evidence_contract": "activation_bounded_stable_taker_v1",
                },
                created_at_utc=created,
            )
            evaluations.append(evaluation)
            scorecards.append({
                "candidate_version_id": str(candidate["candidate_version_id"]),
                "cohort_id": cohort_id,
                "evidence_kind": "FORWARD_SHADOW",
                "scorecard_id": scorecard_id,
            })

        for evaluation in evaluations:
            statistics = _common_date_statistics(evaluation, evaluations)
            scorecard_id = self.registry.append_scorecard(
                candidate_version_id=str(evaluation.candidate["candidate_version_id"]),
                cohort_id=evaluation.cohort_id,
                evidence_kind="COMMON_DATE",
                as_of_watermarks=as_of_watermarks,
                statistics=statistics,
                rejection_counts={},
                source_refs={
                    "row_set_hash": materialization_diagnostics.get("row_set_hash"),
                    "comparison_contract": "active_family_peers_exact_market_date_intersection_v1",
                },
                created_at_utc=created,
            )
            scorecards.append({
                "candidate_version_id": str(evaluation.candidate["candidate_version_id"]),
                "cohort_id": evaluation.cohort_id,
                "evidence_kind": "COMMON_DATE",
                "scorecard_id": scorecard_id,
            })
        return {
            "status": "COMPLETED" if candidates else "NO_ACTIVE_CANDIDATES",
            "active_candidate_count": len(candidates),
            "scorecards": scorecards,
            "as_of_watermark_hash": stable_hash(dict(as_of_watermarks)),
            "funded_authorization": False,
            "role_transitions_applied": False,
        }

    def _ensure_cohort(self, candidate: Mapping[str, Any], created: str) -> str:
        candidate_id = str(candidate["candidate_version_id"])
        existing = self.registry.evaluation_cohort(candidate_id)
        if existing is not None:
            if existing["activation_timestamp_utc"] != candidate["activation_timestamp_utc"]:
                raise ValueError("existing cohort activation differs from candidate activation")
            return str(existing["cohort_id"])
        source_watermarks = {
            "discovery_run_id": candidate["source_run_id"],
            "research": candidate["research_watermark"],
            "tape": candidate["tape_watermark_hash"],
            "outcome": candidate["outcome_watermark"],
            "venue_settlement": candidate["venue_settlement_watermark"],
        }
        return self.registry.register_evaluation_cohort(
            candidate_version_id=candidate_id,
            activation_timestamp_utc=str(candidate["activation_timestamp_utc"]),
            eligible_start_utc=str(candidate["activation_timestamp_utc"]),
            eligible_end_utc=None,
            source_watermarks=source_watermarks,
            requirements={
                "post_activation_only": True,
                "continuous_valid_tape": True,
                "venue_authoritative_settlement_for_economics": True,
                "valid_markouts_before_role_review": True,
                "actual_order_evidence_separate": True,
                "execution_arm": "stable_taker",
            },
            initial_completeness={"state": "PENDING", "reason": "COHORT_OPENED"},
            created_at_utc=created,
        )


def _evaluate_candidate(
    rows: list[BroadDiscoveryRow],
    candidate: Mapping[str, Any],
    cohort_id: str,
) -> CandidateEvaluation:
    definition = dict(candidate["definition"])
    rule = CandidateRule(**definition["rule"])
    activation = _utc(str(candidate["activation_timestamp_utc"]))
    sizing = dict(definition.get("sizing_and_risk") or {})
    config_errors = _config_errors(sizing)
    target = float(sizing.get("target_cost_usd", 0))
    station_cap = float(sizing.get("station_date_cap_usd", 0))
    daily_cap = float(sizing.get("daily_risk_cap_usd", sizing.get("daily_cap_usd", 0)))
    position_cost = min(target, station_cap) if not config_errors else 0.0

    model_rows = [
        row for row in rows
        if row.model_id == rule.model_id and row.market_family == rule.market_family
    ]
    pre_activation = [row for row in model_rows if _utc(row.snapshot_timestamp_utc) < activation]
    post_activation = [row for row in model_rows if _utc(row.snapshot_timestamp_utc) >= activation]
    signal_rows = [row for row in post_activation if _signal_matches(row, rule)]
    invalid_rows = [row for row in signal_rows if not row.discovery_eligible]
    eligible = [
        row for row in signal_rows
        if row.discovery_eligible
        and row.best_ask is not None
        and rule.entry_price_min <= row.best_ask <= rule.entry_price_max
    ]
    deduplicated = _first_station_date(eligible)
    selected = _apply_daily_cap(deduplicated, position_cost, daily_cap) if not config_errors else []

    scenarios = {
        "conservative_half_displayed_depth": _execute(selected, rule, position_cost, 0.5),
        "base_displayed_depth": _execute(selected, rule, position_cost, 1.0),
        "optimistic_full_displayed_depth": _execute(selected, rule, position_cost, 1.0),
    }
    base = scenarios["base_displayed_depth"]
    venue = tuple(
        item for item in base
        if item["venue_outcome_label"] is not None and not item["settlement_disagreement"]
    )
    venue_dates = sorted({item["market_date"] for item in venue})
    venue_station_dates = {(item["station"], item["market_date"]) for item in venue}
    minimum_dates = int(candidate["source_run_spec"]["minimum_effective_dates"])
    minimum_station_dates = int(
        candidate["source_run_spec"]["minimum_executable_station_dates"]
    )
    blockers = list(config_errors)
    if len(venue_dates) < minimum_dates:
        blockers.append("INSUFFICIENT_VENUE_RESOLVED_MARKET_DATES")
    if len(venue_station_dates) < minimum_station_dates:
        blockers.append("INSUFFICIENT_VENUE_RESOLVED_STATION_DATES")
    if any(row.settlement_disagreement for row in signal_rows):
        blockers.append("SETTLEMENT_DISAGREEMENT")
    if venue and not all(item["markouts_valid"] for item in venue):
        blockers.append("MARKOUTS_UNAVAILABLE_OR_INVALID")
    base_summary = _scenario_summary(base)
    if base_summary["venue_cost_usd"] <= 0 or base_summary["venue_pnl_usd"] <= 0:
        blockers.append("NONPOSITIVE_OR_ABSENT_BASE_ECONOMICS")

    rejections = Counter(reason for row in invalid_rows for reason in row.discovery_ineligibility_reasons)
    for error in config_errors:
        rejections[error] += 1
    actual_counts = Counter(row.actual_fill_status for row in selected)
    statistics = {
        "candidate_version_id": candidate["candidate_version_id"],
        "family_id": candidate["family_id"],
        "role_at_evaluation": candidate["to_role"],
        "cohort_id": cohort_id,
        "activation_timestamp_utc": candidate["activation_timestamp_utc"],
        "evidence_kind": "FORWARD_SHADOW",
        "execution_interpretation": "PUBLIC_TAPE_STABLE_TAKER_COUNTERFACTUAL",
        "counts": {
            "raw_model_rows": len(model_rows),
            "pre_activation_excluded": len(pre_activation),
            "raw_post_activation_rows": len(post_activation),
            "signal_matching_rows": len(signal_rows),
            "eligible_rows": len(eligible),
            "deduplicated_signals": len(deduplicated),
            "valid_tape_rows": sum(row.tape_eligible for row in signal_rows),
            "postable": len(selected),
            "executed": len(base),
            "partial": sum(item["fill_fraction"] < 1 for item in base),
            "missed": len(selected) - len(base),
            "invalid": len(invalid_rows),
            "venue_resolved_executions": len(venue),
        },
        "effective_venue_resolved_market_dates": venue_dates,
        "effective_venue_resolved_station_dates": len(venue_station_dates),
        "fill_scenarios": {
            name: _scenario_summary(executions)
            for name, executions in scenarios.items()
        } | {
            "actual_order": {
                "status": "SEPARATE_STREAM_NOT_INFERRED_FROM_PUBLIC_TAPE"
            }
        },
        "actual_order_evidence": {
            "status": "SEPARATE_STREAM_NOT_INFERRED_FROM_PUBLIC_TAPE",
            "observed_status_counts": dict(sorted(actual_counts.items())),
            "scorecard_kind_required": "ACTUAL_ORDER",
        },
        "selected_vs_executed": {
            "selected": len(selected),
            "executed": len(base),
            "missed": len(selected) - len(base),
        },
        "filled_vs_missed": {
            "full": sum(item["fill_fraction"] >= 1 for item in base),
            "partial": sum(item["fill_fraction"] < 1 for item in base),
            "missed": len(selected) - len(base),
        },
        "markouts": _markout_summary(list(venue)),
        "concentration": _concentration_summary(list(venue)),
        "settlement": {
            "required_source": "VENUE_AUTHORITATIVE",
            "venue_resolved": len(venue),
            "research_only_not_credited": sum(
                item["venue_outcome_label"] is None
                and item["research_outcome_label"] is not None
                for item in base
            ),
            "disagreements": sum(bool(row.settlement_disagreement) for row in signal_rows),
        },
        "requirements": {
            "minimum_effective_dates": minimum_dates,
            "minimum_station_dates": minimum_station_dates,
        },
        "review_state": "READY_FOR_C5_REVIEW" if not blockers else "CONTINUE_COLLECTING",
        "blockers": sorted(set(blockers)),
        "funded_authorization": False,
    }
    return CandidateEvaluation(
        candidate=candidate,
        cohort_id=cohort_id,
        statistics=statistics,
        rejection_counts=dict(sorted(rejections.items())),
        venue_executions=venue,
    )


def _config_errors(sizing: Mapping[str, Any]) -> list[str]:
    errors = []
    for key in ("target_cost_usd", "station_date_cap_usd"):
        if not isinstance(sizing.get(key), (int, float)) or float(sizing[key]) <= 0:
            errors.append(f"INVALID_OR_MISSING_{key.upper()}")
    daily = sizing.get("daily_risk_cap_usd", sizing.get("daily_cap_usd"))
    if not isinstance(daily, (int, float)) or float(daily) <= 0:
        errors.append("INVALID_OR_MISSING_DAILY_RISK_CAP_USD")
    return errors


def _signal_matches(row: BroadDiscoveryRow, rule: CandidateRule) -> bool:
    return (
        (rule.selected_side == "ANY" or row.selected_side == rule.selected_side)
        and (rule.strategy_bucket == "ANY" or row.strategy_bucket == rule.strategy_bucket)
        and (
            rule.observation_delay_bucket is None
            or row.observation_delay_bucket == rule.observation_delay_bucket
        )
        and (not rule.require_high_conviction or row.high_conviction)
        and rule.local_start <= row.local_decision_hhmm < rule.local_end
    )


def _first_station_date(rows: Sequence[BroadDiscoveryRow]) -> list[BroadDiscoveryRow]:
    selected = []
    seen: set[tuple[str, str]] = set()
    for row in sorted(rows, key=lambda item: (item.snapshot_timestamp_utc, item.row_id)):
        key = (row.station, row.market_date)
        if key not in seen:
            seen.add(key)
            selected.append(row)
    return selected


def _apply_daily_cap(
    rows: Sequence[BroadDiscoveryRow], position_cost: float, daily_cap: float
) -> list[BroadDiscoveryRow]:
    selected = []
    used: dict[str, float] = {}
    for row in rows:
        current = used.get(row.market_date, 0.0)
        if current + position_cost <= daily_cap + 1e-9:
            used[row.market_date] = current + position_cost
            selected.append(row)
    return selected


def _execute(
    rows: Sequence[BroadDiscoveryRow],
    rule: CandidateRule,
    target_cost: float,
    depth_multiplier: float,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        levels = tuple((price, size * depth_multiplier) for price, size in row.ask_levels)
        cost, shares, vwap = sweep_asks(
            levels, price_cap=rule.entry_price_max, target_cost=target_cost
        )
        if cost <= 0 or vwap is None:
            continue
        result.append({
            "row_id": row.row_id,
            "station": row.station,
            "market_date": row.market_date,
            "cost": cost,
            "shares": shares,
            "vwap": vwap,
            "fill_fraction": min(cost / target_cost, 1.0),
            "venue_outcome_label": row.venue_outcome_label,
            "research_outcome_label": row.research_outcome_label,
            "settlement_disagreement": bool(row.settlement_disagreement),
            "markouts_valid": row.markouts_valid,
            "markout_midpoints": dict(row.markout_midpoints),
        })
    return result


def _pnl(row: Mapping[str, Any]) -> float:
    return float(row["shares"]) - float(row["cost"]) if row["venue_outcome_label"] else -float(row["cost"])


def _scenario_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    venue = [
        row for row in rows
        if row["venue_outcome_label"] is not None and not row["settlement_disagreement"]
    ]
    costs = [float(row["cost"]) for row in venue]
    pnls = [_pnl(row) for row in venue]
    cumulative = peak = max_drawdown = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    cost = sum(costs)
    pnl = sum(pnls)
    wins = sum(int(row["venue_outcome_label"]) == 1 for row in venue)
    uncertainty = _uncertainty(pnls, wins)
    return {
        "counterfactual_executions": len(rows),
        "venue_resolved_executions": len(venue),
        "venue_cost_usd": round(cost, 6),
        "fees_usd": 0.0,
        "venue_pnl_usd": round(pnl, 6),
        "venue_rr": round(pnl / cost, 6) if cost else None,
        "wins": wins,
        "losses": len(venue) - wins,
        "weighted_vwap": round(
            sum(float(row["vwap"]) * float(row["shares"]) for row in venue)
            / sum(float(row["shares"]) for row in venue),
            6,
        ) if venue else None,
        "average_fill_fraction": round(
            fmean(float(row["fill_fraction"]) for row in rows), 6
        ) if rows else None,
        "capacity_usd": round(sum(float(row["cost"]) for row in rows), 6),
        "maximum_drawdown_usd": round(max_drawdown, 6),
        "uncertainty": uncertainty,
    }


def _uncertainty(pnls: Sequence[float], wins: int) -> dict[str, Any]:
    count = len(pnls)
    if not count:
        return {"sample_size": 0, "mean_pnl_usd": None, "standard_error_usd": None, "win_rate_95_interval": None}
    mean = fmean(pnls)
    standard_error = stdev(pnls) / math.sqrt(count) if count > 1 else None
    proportion = wins / count
    z = 1.96
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(
        proportion * (1 - proportion) / count + z * z / (4 * count * count)
    ) / denominator
    return {
        "sample_size": count,
        "mean_pnl_usd": round(mean, 6),
        "standard_error_usd": round(standard_error, 6) if standard_error is not None else None,
        "win_rate_95_interval": [round(max(0.0, center - radius), 6), round(min(1.0, center + radius), 6)],
    }


def _markout_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["markouts_valid"]]
    horizons: dict[str, list[float]] = {}
    for row in valid:
        for horizon, midpoint in row["markout_midpoints"].items():
            horizons.setdefault(str(horizon), []).append(float(midpoint) - float(row["vwap"]))
    return {
        "required_executions": len(rows),
        "valid_executions": len(valid),
        "missing_or_invalid_executions": len(rows) - len(valid),
        "mean_token_markout_by_horizon": {
            horizon: round(fmean(values), 6)
            for horizon, values in sorted(horizons.items())
        },
    }


def _concentration_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total_cost = sum(float(row["cost"]) for row in rows)
    by_station: dict[str, float] = {}
    by_date: dict[str, float] = {}
    for row in rows:
        cost = float(row["cost"])
        station = str(row["station"])
        market_date = str(row["market_date"])
        by_station[station] = by_station.get(station, 0.0) + cost
        by_date[market_date] = by_date.get(market_date, 0.0) + cost
    return {
        "maximum_station_cost_share": (
            round(max(by_station.values()) / total_cost, 6) if total_cost else None
        ),
        "maximum_market_date_cost_share": (
            round(max(by_date.values()) / total_cost, 6) if total_cost else None
        ),
        "station_cost_usd": {
            key: round(value, 6) for key, value in sorted(by_station.items())
        },
        "market_date_cost_usd": {
            key: round(value, 6) for key, value in sorted(by_date.items())
        },
    }


def _common_date_statistics(
    subject: CandidateEvaluation,
    evaluations: Sequence[CandidateEvaluation],
) -> dict[str, Any]:
    comparisons = []
    subject_by_date = _executions_by_date(subject.venue_executions)
    for peer in evaluations:
        if peer.candidate["candidate_version_id"] == subject.candidate["candidate_version_id"]:
            continue
        if peer.candidate["family_id"] != subject.candidate["family_id"]:
            continue
        peer_by_date = _executions_by_date(peer.venue_executions)
        common_dates = sorted(set(subject_by_date) & set(peer_by_date))
        subject_rows = [row for date in common_dates for row in subject_by_date[date]]
        peer_rows = [row for date in common_dates for row in peer_by_date[date]]
        subject_summary = _scenario_summary(subject_rows)
        peer_summary = _scenario_summary(peer_rows)
        subject_sizing = dict(subject.candidate["definition"].get("sizing_and_risk") or {})
        additive_rows = _incremental_after_peer(
            subject_rows,
            peer_rows,
            station_date_cap_usd=float(subject_sizing.get("station_date_cap_usd", 0)),
            daily_risk_cap_usd=float(
                subject_sizing.get(
                    "daily_risk_cap_usd", subject_sizing.get("daily_cap_usd", 0)
                )
            ),
        )
        additive_summary = _scenario_summary(additive_rows)
        comparisons.append({
            "peer_candidate_version_id": peer.candidate["candidate_version_id"],
            "peer_role": peer.candidate["to_role"],
            "common_market_dates": common_dates,
            "common_market_date_count": len(common_dates),
            "subject": subject_summary,
            "peer": peer_summary,
            "pnl_delta_usd": round(
                subject_summary["venue_pnl_usd"] - peer_summary["venue_pnl_usd"], 6
            ),
            "replacement_incremental": {
                "pnl_delta_usd": round(
                    subject_summary["venue_pnl_usd"] - peer_summary["venue_pnl_usd"], 6
                ),
                "rr_delta": _optional_delta(
                    subject_summary["venue_rr"], peer_summary["venue_rr"]
                ),
                "subject_complexity": CandidateRule(
                    **subject.candidate["definition"]["rule"]
                ).complexity,
                "peer_complexity": CandidateRule(
                    **peer.candidate["definition"]["rule"]
                ).complexity,
            },
            "additive_after_peer_caps": additive_summary,
        })
    return {
        "candidate_version_id": subject.candidate["candidate_version_id"],
        "family_id": subject.candidate["family_id"],
        "comparison_rule": (
            "EXACT_VENUE_RESOLVED_MARKET_DATE_INTERSECTION_WITH_"
            "REPLACEMENT_AND_ADDITIVE_CAPS_V1"
        ),
        "comparison_status": "AVAILABLE" if comparisons else "NO_ACTIVE_FAMILY_PEER",
        "comparisons": comparisons,
        "funded_authorization": False,
    }


def _executions_by_date(
    rows: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row["market_date"]), []).append(row)
    return result


def _incremental_after_peer(
    subject_rows: Sequence[dict[str, Any]],
    peer_rows: Sequence[dict[str, Any]],
    *,
    station_date_cap_usd: float,
    daily_risk_cap_usd: float,
) -> list[dict[str, Any]]:
    """Apply a subject after the peer has consumed shared portfolio caps.

    Rows are proportionally clipped when only part of a cap remains. This is a
    deterministic capacity counterfactual, not an additional fill claim.
    """
    if station_date_cap_usd <= 0 or daily_risk_cap_usd <= 0:
        return []
    station_used: dict[tuple[str, str], float] = {}
    daily_used: dict[str, float] = {}
    for row in peer_rows:
        key = (str(row["station"]), str(row["market_date"]))
        cost = float(row["cost"])
        station_used[key] = station_used.get(key, 0.0) + cost
        market_date = str(row["market_date"])
        daily_used[market_date] = daily_used.get(market_date, 0.0) + cost

    incremental: list[dict[str, Any]] = []
    for row in subject_rows:
        market_date = str(row["market_date"])
        key = (str(row["station"]), market_date)
        station_remaining = max(
            station_date_cap_usd - station_used.get(key, 0.0), 0.0
        )
        daily_remaining = max(
            daily_risk_cap_usd - daily_used.get(market_date, 0.0), 0.0
        )
        admitted_cost = min(float(row["cost"]), station_remaining, daily_remaining)
        if admitted_cost <= 0:
            continue
        ratio = admitted_cost / float(row["cost"])
        admitted = dict(row)
        admitted["cost"] = admitted_cost
        admitted["shares"] = float(row["shares"]) * ratio
        admitted["fill_fraction"] = float(row["fill_fraction"]) * ratio
        incremental.append(admitted)
        station_used[key] = station_used.get(key, 0.0) + admitted_cost
        daily_used[market_date] = daily_used.get(market_date, 0.0) + admitted_cost
    return incremental


def _optional_delta(subject: Any, peer: Any) -> float | None:
    if subject is None or peer is None:
        return None
    return round(float(subject) - float(peer), 6)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)
