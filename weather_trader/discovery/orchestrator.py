from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable, Mapping, Sequence

from weather_trader.discovery.contracts import (
    BroadDiscoveryRow,
    CandidateRule,
    DiscoveryRunSpec,
)
from weather_trader.discovery.engine import discover
from weather_trader.discovery.registry import DiscoveryRegistry
from weather_trader.pricing.contracts import stable_hash


@dataclass(frozen=True)
class DiscoveryBudgets:
    maximum_active_candidates: int = 12
    maximum_runtime_seconds: float = 900.0
    maximum_persisted_ranked_families: int = 25

    def __post_init__(self) -> None:
        if (
            self.maximum_active_candidates < 1
            or self.maximum_runtime_seconds <= 0
            or self.maximum_persisted_ranked_families < 1
        ):
            raise ValueError("discovery budgets must be positive")


class RecurringDiscoveryOrchestrator:
    """Seal, rank, and register bounded challengers for one recurring run."""

    def __init__(
        self,
        registry: DiscoveryRegistry,
        *,
        budgets: DiscoveryBudgets | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if registry.read_only:
            raise ValueError("recurring discovery requires a writable registry")
        self.registry = registry
        self.budgets = budgets or DiscoveryBudgets()
        self.clock = clock

    def execution_decision(self, run: DiscoveryRunSpec) -> dict[str, Any]:
        outcome = self.registry.discovery_run_outcome(run.run_id)
        if outcome is not None:
            return {"action": "NOOP_ALREADY_COMPLETED", "outcome": outcome}
        sealed = self.registry.connection.execute(
            "select run_hash from discovery_runs where run_id=?", (run.run_id,)
        ).fetchone()
        if sealed is not None:
            if str(sealed["run_hash"]) != stable_hash(run.canonical_payload()):
                raise ValueError("sealed discovery run hash does not match supplied specification")
            return {"action": "RUN_RESUME"}
        latest = self.registry.latest_completed_run_spec()
        if latest is not None and not _has_meaningful_change(latest, run.canonical_payload()):
            return {"action": "NOOP_UNCHANGED_RESOLVED_WATERMARKS"}
        return {"action": "RUN_NEW"}

    def execute(
        self,
        *,
        run: DiscoveryRunSpec,
        rows: Sequence[BroadDiscoveryRow],
        materialization_diagnostics: Mapping[str, Any],
        started_at_utc: str,
        completed_at_utc: str | None = None,
    ) -> dict[str, Any]:
        existing_outcome = self.registry.discovery_run_outcome(run.run_id)
        if existing_outcome is not None:
            return {
                "action": "NOOP_ALREADY_COMPLETED",
                "run_id": run.run_id,
                "outcome": existing_outcome,
                "funded_authorization": False,
            }

        sealed = self.registry.connection.execute(
            "select run_hash from discovery_runs where run_id=?", (run.run_id,)
        ).fetchone()
        if sealed is None:
            latest = self.registry.latest_completed_run_spec()
            if latest is not None and not _has_meaningful_change(latest, run.canonical_payload()):
                return {
                    "action": "NOOP_UNCHANGED_RESOLVED_WATERMARKS",
                    "run_id": run.run_id,
                    "funded_authorization": False,
                }
            self.registry.register_discovery_run(
                run,
                created_at_utc=started_at_utc,
                status="SEALED",
                diagnostics={"materialization_sealed": dict(materialization_diagnostics)},
            )
        elif str(sealed["run_hash"]) != stable_hash(run.canonical_payload()):
            raise ValueError("sealed discovery run hash does not match supplied specification")

        started = self.clock()
        try:
            report = discover(list(rows), run)
        except ValueError as exc:
            if "budget exceeded" not in str(exc):
                raise
            completed = _completion_time(completed_at_utc)
            diagnostics = {
                "reason": str(exc),
                "completed_at_utc": completed,
                "materialization": dict(materialization_diagnostics),
            }
            self.registry.append_discovery_run_outcome(
                run_id=run.run_id,
                status="BUDGET_EXCEEDED",
                completed_at_utc=diagnostics["completed_at_utc"],
                diagnostics=diagnostics,
                nominations=[],
            )
            return _result("BUDGET_EXCEEDED", run.run_id, [], diagnostics)

        elapsed = self.clock() - started
        completed = _completion_time(completed_at_utc)
        if elapsed > self.budgets.maximum_runtime_seconds:
            diagnostics = {
                "reason": "DISCOVERY_RUNTIME_BUDGET_EXCEEDED",
                "elapsed_seconds": elapsed,
                "maximum_runtime_seconds": self.budgets.maximum_runtime_seconds,
                "materialization": dict(materialization_diagnostics),
            }
            self.registry.append_discovery_run_outcome(
                run_id=run.run_id,
                status="BUDGET_EXCEEDED",
                completed_at_utc=completed,
                diagnostics=diagnostics,
                nominations=[],
            )
            return _result("BUDGET_EXCEEDED", run.run_id, [], diagnostics)

        if _utc(run.earliest_activation_timestamp) < _utc(completed):
            diagnostics = {
                "reason": "ACTIVATION_BOUNDARY_ELAPSED_BEFORE_NOMINATION",
                "activation_timestamp": run.earliest_activation_timestamp,
                "completed_at_utc": completed,
                "materialization": dict(materialization_diagnostics),
            }
            self.registry.append_discovery_run_outcome(
                run_id=run.run_id,
                status="ACTIVATION_EXPIRED",
                completed_at_utc=completed,
                diagnostics=diagnostics,
                nominations=[],
            )
            return _result("ACTIVATION_EXPIRED", run.run_id, [], diagnostics)

        active_count = _active_candidate_count(self.registry)
        new_slots = max(self.budgets.maximum_active_candidates - active_count, 0)
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for nomination in report["nominations"]:
            rule = _candidate_rule(nomination["rule"])
            family_id = rule.correlated_family_id
            definition_hash = _candidate_definition_hash(family_id, rule, run)
            prior = self.registry.connection.execute(
                "select candidate_version_id from candidate_versions where definition_hash=?",
                (definition_hash,),
            ).fetchone()
            if prior is None and new_slots == 0:
                skipped.append({
                    "candidate_id": rule.candidate_id,
                    "reason": "ACTIVE_CANDIDATE_BUDGET_EXHAUSTED",
                })
                continue
            family_definition = _family_definition(rule)
            family_id = self.registry.register_family(
                definition=family_definition,
                economic_rationale="Causal model-versus-market disagreement under the sealed simple grammar.",
                grammar_provenance=run.grammar_version,
                correlation_group=rule.correlated_family_id,
                created_at_utc=started_at_utc,
            )
            candidate_version_id = self.registry.register_candidate_version(
                family_id=family_id,
                source_run_id=run.run_id,
                rule=rule,
                activation_timestamp_utc=run.earliest_activation_timestamp,
                pricing_version="raw_model_fair_discovery_only_v1",
                execution_version="stable_taker_v1",
                risk_version="phase3d_fixed_caps_v1",
                sizing_and_risk=_sizing_and_risk(run),
                created_at_utc=started_at_utc,
            )
            reused = prior is not None
            if not reused:
                new_slots -= 1
            role = self.registry.current_role(candidate_version_id)
            if role == "GENERATED":
                self.registry.append_lifecycle_event(
                    candidate_version_id=candidate_version_id,
                    event_type="NOMINATED",
                    from_role="GENERATED",
                    to_role="NOMINATED",
                    occurred_at_utc=completed,
                    reason="passed sealed recurring discovery gates",
                    source_run_id=run.run_id,
                    metadata={"discovery_candidate_id": rule.candidate_id},
                )
            registered.append({
                "candidate_version_id": candidate_version_id,
                "candidate_id": rule.candidate_id,
                "family_id": family_id,
                "reused_version": reused,
                "penalized_rr": nomination["penalized_rr"],
                "effective_market_dates": nomination["effective_market_dates"],
                "executions": nomination["executions"],
            })

        status = "COMPLETED" if registered else "NO_NOMINATION"
        diagnostics = {
            "materialization": dict(materialization_diagnostics),
            "candidate_count": report["candidate_count"],
            "correlated_family_count": report["correlated_family_count"],
            "passing_family_count": len(report["nominations"]),
            "registered_nomination_count": len(registered),
            "skipped_nominations": skipped,
            "active_candidates_before_run": active_count,
            "maximum_active_candidates": self.budgets.maximum_active_candidates,
            "maximum_challengers": run.maximum_challengers,
            "elapsed_seconds": elapsed,
            "ranked_family_diagnostics": [
                _ranking_summary(item)
                for item in report["ranked_families"][: self.budgets.maximum_persisted_ranked_families]
            ],
        }
        self.registry.append_discovery_run_outcome(
            run_id=run.run_id,
            status=status,
            completed_at_utc=completed,
            diagnostics=diagnostics,
            nominations=registered,
        )
        return _result(status, run.run_id, registered, diagnostics)


def _completion_time(value: str | None) -> str:
    return (_utc(value) if value is not None else datetime.now(timezone.utc)).isoformat()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _has_meaningful_change(previous: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    keys = (
        "outcome_watermark",
        "venue_settlement_watermark",
        "grammar_version",
        "build_hash",
        "source_start_date",
        "model_market_families",
    )
    return any(
        stable_hash(previous.get(key)) != stable_hash(current.get(key)) for key in keys
    )


def _candidate_rule(payload: Mapping[str, Any]) -> CandidateRule:
    excluded = {"candidate_id", "correlated_family_id", "complexity"}
    return CandidateRule(**{key: value for key, value in payload.items() if key not in excluded})


def _family_definition(rule: CandidateRule) -> dict[str, Any]:
    return {
        "family_id": rule.correlated_family_id,
        "model_id": rule.model_id,
        "market_family": rule.market_family,
        "selected_side": rule.selected_side,
        "strategy_bucket": rule.strategy_bucket,
        "require_high_conviction": rule.require_high_conviction,
        "dedupe_scope": rule.dedupe_scope,
        "execution_arm": rule.execution_arm,
    }


def _sizing_and_risk(run: DiscoveryRunSpec) -> dict[str, Any]:
    return {
        "target_cost_usd": run.target_cost_usd,
        "price_cap_source": "candidate_rule",
        "station_date_cap_usd": run.portfolio_station_date_cap_usd,
        "daily_risk_cap_usd": run.daily_risk_cap_usd,
        "latency_ms": run.latency_ms,
        "pre_signal_seconds": run.pre_signal_seconds,
    }


def _candidate_definition_hash(
    family_id: str, rule: CandidateRule, run: DiscoveryRunSpec
) -> str:
    from dataclasses import asdict

    return stable_hash({
        "family_id": family_id,
        "rule": asdict(rule),
        "pricing_version": "raw_model_fair_discovery_only_v1",
        "execution_version": "stable_taker_v1",
        "risk_version": "phase3d_fixed_caps_v1",
        "sizing_and_risk": _sizing_and_risk(run),
    })


def _active_candidate_count(registry: DiscoveryRegistry) -> int:
    row = registry.connection.execute(
        """with ranked as (
               select to_role,
                      row_number() over (
                          partition by candidate_version_id
                          order by occurred_at_utc desc,rowid desc
                      ) event_rank
               from candidate_lifecycle_events
           )
           select count(*) from ranked
           where event_rank=1 and to_role in
               ('NOMINATED','SHADOW_ACTIVE','CHALLENGER','CHAMPION','PROBATION','PHASE4_REQUESTED')"""
    ).fetchone()
    return int(row[0])


def _ranking_summary(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "candidate_id",
            "correlated_family_id",
            "passes_selection_gate",
            "penalized_rr",
            "effective_market_dates",
            "executions",
            "pnl",
            "rr",
        )
    }


def _result(
    status: str,
    run_id: str,
    nominations: list[dict[str, Any]],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "action": "RUN_COMPLETED",
        "status": status,
        "run_id": run_id,
        "nominations": nominations,
        "diagnostics": dict(diagnostics),
        "funded_authorization": False,
    }
