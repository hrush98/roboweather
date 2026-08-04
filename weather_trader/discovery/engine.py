from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from weather_trader.discovery.contracts import (
    BroadDiscoveryRow,
    CandidateRule,
    DiscoveryRunSpec,
    StrategyManifest,
)
from weather_trader.pricing.contracts import stable_hash
from weather_trader.tape.replay import sweep_asks


def generate_candidate_rules(
    rows: Iterable[BroadDiscoveryRow],
    run: DiscoveryRunSpec | None = None,
) -> list[CandidateRule]:
    eligible = [row for row in rows if row.discovery_eligible]
    model_families = (
        list(run.model_market_families)
        if run is not None
        else sorted({(row.model_id, row.market_family) for row in eligible if row.model_id})
    )
    delays = sorted({row.observation_delay_bucket for row in eligible})
    declared_delays = run.observation_delay_rules if run is not None else ("ANY", "10m", "15m")
    delay_rules: list[str | None] = [None if value == "ANY" else value for value in declared_delays if value == "ANY" or value in delays]
    sides = run.side_rules if run is not None else ("ANY", "BUY_NO", "BUY_YES")
    windows = run.local_windows if run is not None else (("00:00", "24:00"), ("12:00", "15:00"))
    bands = run.entry_bands if run is not None else ((0.0, 0.50), (0.05, 0.50), (0.05, 0.35))
    rules: dict[str, CandidateRule] = {}
    for model_id, market_family in model_families:
        for side in sides:
            for delay in delay_rules:
                for local_start, local_end in windows:
                    for entry_min, entry_max in bands:
                        rule = CandidateRule(
                            model_id=model_id,
                            market_family=market_family,
                            selected_side=side,
                            strategy_bucket="HIGH_CONVICTION",
                            observation_delay_bucket=delay,
                            local_start=local_start,
                            local_end=local_end,
                            entry_price_min=entry_min,
                            entry_price_max=entry_max,
                            require_high_conviction=True,
                        )
                        rules[rule.candidate_id] = rule
    return [rules[key] for key in sorted(rules)]


def discover(
    rows: list[BroadDiscoveryRow],
    run: DiscoveryRunSpec,
) -> dict[str, Any]:
    rules = generate_candidate_rules(rows, run)
    by_model: dict[tuple[str, str], list[BroadDiscoveryRow]] = {}
    for row in rows:
        if row.discovery_eligible:
            by_model.setdefault((row.model_id, row.market_family), []).append(row)
    scored = [
        _score_candidate(rule, by_model.get((rule.model_id, rule.market_family), []), run)
        for rule in rules
    ]
    ranked = sorted(
        scored,
        key=lambda item: (
            not item["passes_selection_gate"],
            -item["penalized_rr"],
            item["rule"]["complexity"],
            item["candidate_id"],
        ),
    )
    collapsed: list[dict[str, Any]] = []
    seen_families: set[str] = set()
    for result in ranked:
        family = result["correlated_family_id"]
        if family in seen_families:
            continue
        seen_families.add(family)
        collapsed.append(result)
    winner = next((result for result in collapsed if result["passes_selection_gate"]), None)
    return {
        "run_id": run.run_id,
        "run_hash": stable_hash(run.canonical_payload()),
        "candidate_count": len(scored),
        "correlated_family_count": len(collapsed),
        "winner_candidate_id": winner["candidate_id"] if winner else None,
        "winner": winner,
        "ranked_families": collapsed,
        "runner_up_candidates_without_holdout_access": [
            item for item in ranked if winner is None or item["candidate_id"] != winner["candidate_id"]
        ],
        "selection_rule": (
            "highest complexity-penalized R/R among candidates with positive aggregate PnL, "
            "minimum samples, and positive PnL in at least two-thirds of populated date folds; "
            "one representative per correlated family"
        ),
        "threshold_fitting": "none_fixed_predeclared_grammar",
    }


def freeze_winner_manifest(
    report: dict[str, Any],
    run: DiscoveryRunSpec,
    *,
    source_hash: str,
    code_hash: str,
    activation_timestamp: str,
    untouched_holdout_start: str,
) -> StrategyManifest | None:
    winner = report.get("winner")
    if winner is None:
        return None
    candidate_fields = dict(winner["rule"])
    candidate_fields.pop("candidate_id", None)
    candidate_fields.pop("correlated_family_id", None)
    candidate_fields.pop("complexity", None)
    candidate = CandidateRule(**candidate_fields)
    return StrategyManifest(
        strategy_id=f"p3d_strategy_{candidate.candidate_id.removeprefix('p3d_candidate_')}",
        version=1,
        discovery_run_id=run.run_id,
        discovery_run_hash=stable_hash(run.canonical_payload()),
        candidate=candidate,
        activation_timestamp=activation_timestamp,
        untouched_holdout_start=untouched_holdout_start,
        source_cutoff_exclusive=run.discovery_cutoff_exclusive,
        pricing_version="raw_model_fair_discovery_only_v1",
        cost_version="binary_payout_no_fee_v1",
        execution_arm=candidate.execution_arm,
        latency_ms=run.latency_ms,
        pre_signal_seconds=run.pre_signal_seconds,
        target_cost_usd=run.target_cost_usd,
        price_cap=candidate.entry_price_max,
        station_date_cap_usd=run.portfolio_station_date_cap_usd,
        daily_risk_cap_usd=run.daily_risk_cap_usd,
        minimum_forward_effective_dates=run.minimum_effective_dates,
        minimum_forward_station_dates=run.minimum_executable_station_dates,
        settlement_source="POLYMARKET_VENUE_AUTHORITATIVE_REQUIRED_FOR_PASS",
        source_hash=source_hash,
        code_hash=code_hash,
    ).frozen()


def evaluate_frozen_manifest(
    rows: list[BroadDiscoveryRow],
    manifest: StrategyManifest,
) -> dict[str, Any]:
    activation = _utc(manifest.activation_timestamp)
    post_activation = [row for row in rows if _utc(row.snapshot_timestamp_utc) >= activation]
    selected = select_frozen_rows(rows, manifest)
    target_cost = min(manifest.target_cost_usd, manifest.station_date_cap_usd)
    executions = [_execution(row, manifest.candidate, target_cost) for row in selected]
    executions = [row for row in executions if row is not None]
    conservative = [
        result for row in selected
        if (result := _execution(row, manifest.candidate, target_cost, depth_multiplier=0.5)) is not None
    ]
    optimistic = [
        result for row in selected
        if (result := _execution(row, manifest.candidate, target_cost, depth_multiplier=1.0)) is not None
    ]
    venue_executions = [row for row in executions if row["venue_outcome_label"] is not None]
    effective_dates = sorted({row["market_date"] for row in venue_executions})
    station_dates = {(row["station"], row["market_date"]) for row in venue_executions}
    cost = sum(row["cost"] for row in venue_executions)
    pnl = sum(_pnl(row, int(row["venue_outcome_label"])) for row in venue_executions)
    reasons = []
    if len(effective_dates) < manifest.minimum_forward_effective_dates:
        reasons.append("INSUFFICIENT_VENUE_RESOLVED_MARKET_DATES")
    if len(station_dates) < manifest.minimum_forward_station_dates:
        reasons.append("INSUFFICIENT_VENUE_RESOLVED_STATION_DATES")
    if any(row.settlement_disagreement for row in selected):
        reasons.append("SETTLEMENT_DISAGREEMENT")
    if venue_executions and not all(row["markouts_valid"] for row in venue_executions):
        reasons.append("MARKOUTS_UNAVAILABLE_OR_INVALID")
    if cost <= 0 or pnl <= 0:
        reasons.append("NONPOSITIVE_BASE_CASE_ECONOMICS")
    disposition = "PASS_TO_PHASE4_REQUEST" if not reasons else (
        "REJECT" if len(effective_dates) >= manifest.minimum_forward_effective_dates and pnl <= 0
        else "CONTINUE_COLLECTING"
    )
    return {
        "manifest_hash": manifest.manifest_hash,
        "activation_timestamp": manifest.activation_timestamp,
        "raw_post_activation_rows": len(post_activation),
        "deduplicated_signals": len(selected),
        "tape_executions": len(executions),
        "venue_resolved_executions": len(venue_executions),
        "effective_venue_resolved_market_dates": effective_dates,
        "venue_resolved_station_dates": len(station_dates),
        "cost": round(cost, 2),
        "pnl": round(pnl, 2),
        "rr": round(pnl / cost, 4) if cost else None,
        "fill_scenarios": {
            "conservative_half_displayed_depth": _venue_summary(conservative),
            "base_displayed_depth": _venue_summary(executions),
            "optimistic_displayed_depth": _venue_summary(optimistic),
            "actual": {"status": "UNAVAILABLE_COUNTERFACTUAL_PUBLIC_TAPE"},
        },
        "markouts": {
            "valid_executions": sum(row["markouts_valid"] for row in venue_executions),
            "required_executions": len(venue_executions),
        },
        "disposition": disposition,
        "reasons": reasons,
        "reject_counts": dict(sorted(Counter(
            reason for row in rows for reason in row.discovery_ineligibility_reasons
        ).items())),
        "executions": executions,
    }


def select_frozen_rows(
    rows: list[BroadDiscoveryRow],
    manifest: StrategyManifest,
) -> list[BroadDiscoveryRow]:
    activation = _utc(manifest.activation_timestamp)
    return _apply_portfolio_caps(
        _first_station_date(
            row for row in rows
            if _utc(row.snapshot_timestamp_utc) >= activation and _matches(row, manifest.candidate)
        ),
        per_position_cost=min(manifest.target_cost_usd, manifest.station_date_cap_usd),
        daily_cap=manifest.daily_risk_cap_usd,
    )


def _score_candidate(
    rule: CandidateRule,
    rows: list[BroadDiscoveryRow],
    run: DiscoveryRunSpec,
) -> dict[str, Any]:
    per_position_cost = min(run.target_cost_usd, run.portfolio_station_date_cap_usd)
    selected = _apply_portfolio_caps(
        _first_station_date(row for row in rows if _matches(row, rule)),
        per_position_cost=per_position_cost,
        daily_cap=run.daily_risk_cap_usd,
    )
    executions = [_execution(row, rule, per_position_cost) for row in selected]
    executions = [row for row in executions if row is not None]
    dates = sorted({row["market_date"] for row in executions})
    fold_dates = _date_folds(dates, run.fold_count)
    folds = []
    for index, values in enumerate(fold_dates):
        subset = [row for row in executions if row["market_date"] in values]
        folds.append(_summary(subset, index=index, dates=values))
    summary = _summary(executions)
    populated = [fold for fold in folds if fold["executions"]]
    positive_folds = sum(fold["pnl"] > 0 for fold in populated)
    stable = bool(populated) and positive_folds / len(populated) >= 2 / 3
    passes = (
        len(dates) >= run.minimum_effective_dates
        and len(executions) >= run.minimum_executable_station_dates
        and summary["pnl"] > 0
        and stable
    )
    rule_payload = asdict(rule)
    rule_payload.update({
        "candidate_id": rule.candidate_id,
        "correlated_family_id": rule.correlated_family_id,
        "complexity": rule.complexity,
    })
    rr = summary["rr"] or 0.0
    return {
        "candidate_id": rule.candidate_id,
        "correlated_family_id": rule.correlated_family_id,
        "rule": rule_payload,
        **summary,
        "folds": folds,
        "positive_populated_folds": positive_folds,
        "populated_folds": len(populated),
        "stable_across_folds": stable,
        "penalized_rr": round(rr - rule.complexity * run.complexity_penalty_per_unit, 6),
        "passes_selection_gate": passes,
    }


def _matches(row: BroadDiscoveryRow, rule: CandidateRule) -> bool:
    if not row.discovery_eligible or row.model_id != rule.model_id or row.market_family != rule.market_family:
        return False
    if rule.selected_side != "ANY" and row.selected_side != rule.selected_side:
        return False
    if rule.strategy_bucket != "ANY" and row.strategy_bucket != rule.strategy_bucket:
        return False
    if rule.observation_delay_bucket and row.observation_delay_bucket != rule.observation_delay_bucket:
        return False
    if rule.require_high_conviction and not row.high_conviction:
        return False
    if not rule.local_start <= row.local_decision_hhmm < rule.local_end:
        return False
    return row.best_ask is not None and rule.entry_price_min <= row.best_ask <= rule.entry_price_max


def _first_station_date(rows: Iterable[BroadDiscoveryRow]) -> list[BroadDiscoveryRow]:
    selected = []
    seen: set[tuple[str, str]] = set()
    for row in sorted(rows, key=lambda item: (item.snapshot_timestamp_utc, item.row_id)):
        key = (row.station, row.market_date)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def _apply_portfolio_caps(
    rows: list[BroadDiscoveryRow],
    *,
    per_position_cost: float,
    daily_cap: float,
) -> list[BroadDiscoveryRow]:
    selected = []
    used_by_date: dict[str, float] = {}
    for row in rows:
        used = used_by_date.get(row.market_date, 0.0)
        if used + per_position_cost > daily_cap + 1e-9:
            continue
        used_by_date[row.market_date] = used + per_position_cost
        selected.append(row)
    return selected


def _execution(
    row: BroadDiscoveryRow,
    rule: CandidateRule,
    target_cost: float,
    *,
    depth_multiplier: float = 1.0,
) -> dict[str, Any] | None:
    levels = tuple((price, size * depth_multiplier) for price, size in row.ask_levels)
    cost, shares, vwap = sweep_asks(levels, price_cap=rule.entry_price_max, target_cost=target_cost)
    if cost <= 0 or vwap is None:
        return None
    label = row.venue_outcome_label if row.venue_outcome_label is not None else row.research_outcome_label
    return {
        "row_id": row.row_id,
        "station": row.station,
        "market_date": row.market_date,
        "side": row.selected_side,
        "cost": cost,
        "shares": shares,
        "vwap": vwap,
        "fill_fraction": min(cost / target_cost, 1.0),
        "venue_outcome_label": row.venue_outcome_label,
        "research_outcome_label": row.research_outcome_label,
        "scoring_label": label,
        "scoring_source": "VENUE" if row.venue_outcome_label is not None else "RESEARCH_WEATHER",
        "markouts_valid": row.markouts_valid,
        "markout_midpoints": dict(row.markout_midpoints),
        "actual_fill_status": row.actual_fill_status,
    }


def _pnl(row: dict[str, Any], label: int) -> float:
    return row["shares"] - row["cost"] if label else -row["cost"]


def _venue_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row["venue_outcome_label"] is not None]
    cost = sum(row["cost"] for row in resolved)
    pnl = sum(_pnl(row, int(row["venue_outcome_label"])) for row in resolved)
    return {
        "executions": len(resolved),
        "cost": round(cost, 2),
        "pnl": round(pnl, 2),
        "rr": round(pnl / cost, 4) if cost else None,
    }


def _summary(rows: list[dict[str, Any]], *, index: int | None = None, dates: list[str] | None = None) -> dict[str, Any]:
    cost = sum(row["cost"] for row in rows)
    pnl = sum(_pnl(row, int(row["scoring_label"])) for row in rows if row["scoring_label"] is not None)
    result = {
        "executions": len(rows),
        "effective_market_dates": len({row["market_date"] for row in rows}),
        "station_dates": len({(row["station"], row["market_date"]) for row in rows}),
        "venue_labeled": sum(row["venue_outcome_label"] is not None for row in rows),
        "research_weather_labeled": sum(row["venue_outcome_label"] is None and row["research_outcome_label"] is not None for row in rows),
        "cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "rr": round(pnl / cost, 6) if cost else None,
        "wins": sum(row["scoring_label"] == 1 for row in rows),
        "average_fill_fraction": round(sum(row["fill_fraction"] for row in rows) / len(rows), 6) if rows else None,
    }
    if index is not None:
        result.update({"fold": index + 1, "dates": dates or []})
    return result


def _date_folds(dates: list[str], count: int) -> list[list[str]]:
    folds: list[list[str]] = [[] for _ in range(count)]
    if not dates:
        return folds
    for index, value in enumerate(dates):
        folds[min(index * count // len(dates), count - 1)].append(value)
    return folds


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("manifest evaluation timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
