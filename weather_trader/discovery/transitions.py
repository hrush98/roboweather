from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from weather_trader.discovery.contracts import CandidateRule
from weather_trader.discovery.registry import DiscoveryRegistry


TRANSITION_POLICY_VERSION = "phase3d_research_roles_v1"


class TransitionPolicyError(RuntimeError):
    """Raised when registry state cannot be transitioned safely."""


@dataclass(frozen=True)
class TransitionPolicy:
    minimum_aligned_rr_delta: float = 0.02
    minimum_negative_scorecards: int = 2
    uncertainty_z: float = 1.96
    maximum_station_cost_share: float = 0.60
    maximum_market_date_cost_share: float = 0.40

    def __post_init__(self) -> None:
        if (
            self.minimum_aligned_rr_delta < 0
            or self.minimum_negative_scorecards < 1
            or self.uncertainty_z <= 0
            or not 0 < self.maximum_station_cost_share <= 1
            or not 0 < self.maximum_market_date_cost_share <= 1
        ):
            raise ValueError("transition policy thresholds must be nonnegative/positive")


class ResearchRoleTransitionEngine:
    """Apply deterministic, research-only roles from immutable C4 evidence."""

    def __init__(
        self,
        registry: DiscoveryRegistry,
        *,
        policy: TransitionPolicy | None = None,
    ) -> None:
        if registry.read_only:
            raise ValueError("role transitions require a writable registry")
        self.registry = registry
        self.policy = policy or TransitionPolicy()

    def apply(self, *, effective_at_utc: str) -> dict[str, Any]:
        effective = _utc(effective_at_utc).isoformat()
        decisions: list[dict[str, Any]] = []
        forward = _by_candidate(self.registry.latest_scorecards("FORWARD_SHADOW"))

        # C3 nominations become active challengers only after C4 has created an
        # activation-bounded scorecard. These two events keep the state-machine
        # history explicit instead of skipping SHADOW_ACTIVE.
        for candidate in self.registry.active_candidate_versions():
            candidate_id = str(candidate["candidate_version_id"])
            if candidate["to_role"] != "NOMINATED" or candidate_id not in forward:
                continue
            self._transition(
                candidate,
                event_type="SHADOW_ACTIVATED",
                to_role="SHADOW_ACTIVE",
                effective=effective,
                reason="activation-bounded C4 cohort and scorecard exist",
                scorecard=forward[candidate_id],
                decisions=decisions,
            )
            candidate["to_role"] = "SHADOW_ACTIVE"
            self._transition(
                candidate,
                event_type="CHALLENGER_ASSIGNED",
                to_role="CHALLENGER",
                effective=effective,
                reason="active research candidate begins aligned forward comparison",
                scorecard=forward[candidate_id],
                decisions=decisions,
            )

        candidates = self.registry.active_candidate_versions()
        common = _by_candidate(self.registry.latest_scorecards("COMMON_DATE"))
        by_family: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            if candidate["to_role"] not in {
                "NOMINATED", "SHADOW_ACTIVE", "CHALLENGER", "CHAMPION", "PROBATION"
            }:
                continue
            by_family.setdefault(str(candidate["family_id"]), []).append(candidate)

        for family_id in sorted(by_family):
            family = sorted(
                by_family[family_id], key=lambda item: str(item["candidate_version_id"])
            )
            champions = [item for item in family if item["to_role"] == "CHAMPION"]
            if len(champions) > 1:
                raise TransitionPolicyError(
                    f"family {family_id} has multiple champions; refusing to choose silently"
                )

            for candidate in family:
                candidate_id = str(candidate["candidate_version_id"])
                scorecard = forward.get(candidate_id)
                if scorecard is None:
                    continue
                if _hard_rejection(scorecard["statistics"]):
                    self._transition(
                        candidate,
                        event_type="REJECTED",
                        to_role="REJECTED",
                        effective=effective,
                        reason="fail-closed settlement or immutable configuration gate failed",
                        scorecard=scorecard,
                        decisions=decisions,
                    )
                    candidate["to_role"] = "REJECTED"
                    continue
                if _negative_economics(scorecard["statistics"]):
                    negative_count = self._consecutive_negative_count(candidate_id)
                    if candidate["to_role"] == "CHAMPION" and negative_count < self.policy.minimum_negative_scorecards:
                        self._transition(
                            candidate,
                            event_type="DEGRADED",
                            to_role="PROBATION",
                            effective=effective,
                            reason="latest settlement-aligned conservative/base economics degraded",
                            scorecard=scorecard,
                            decisions=decisions,
                        )
                        candidate["to_role"] = "PROBATION"
                    elif negative_count >= self.policy.minimum_negative_scorecards:
                        self._transition(
                            candidate,
                            event_type="RETIRED",
                            to_role="RETIRED",
                            effective=effective,
                            reason="repeated settlement-aligned negative conservative/base economics",
                            scorecard=scorecard,
                            decisions=decisions,
                        )
                        candidate["to_role"] = "RETIRED"

            active_family = [
                item for item in family
                if item["to_role"] in {"CHALLENGER", "CHAMPION", "PROBATION"}
            ]
            champions = [item for item in active_family if item["to_role"] == "CHAMPION"]
            eligible = [
                item for item in active_family
                if _promotion_ready(
                    forward.get(str(item["candidate_version_id"])), self.policy
                )
            ]
            if not eligible:
                continue
            if not champions:
                winner = min(eligible, key=lambda item: _ranking_key(item, forward))
                self._transition(
                    winner,
                    event_type="CHAMPION_ASSIGNED",
                    to_role="CHAMPION",
                    effective=effective,
                    reason="best predeclared settlement-aligned research candidate; no incumbent",
                    scorecard=forward[str(winner["candidate_version_id"])],
                    decisions=decisions,
                )
                winner["to_role"] = "CHAMPION"
                continue

            incumbent = champions[0]
            incumbent_id = str(incumbent["candidate_version_id"])
            replacements = [
                item for item in eligible
                if item["to_role"] == "CHALLENGER"
                and _beats_incumbent(
                    item,
                    incumbent,
                    forward[str(item["candidate_version_id"])],
                    common.get(str(item["candidate_version_id"])),
                    policy=self.policy,
                )
            ]
            if not replacements:
                continue
            winner = min(replacements, key=lambda item: _ranking_key(item, forward))
            winner_score = forward[str(winner["candidate_version_id"])]
            self._transition(
                incumbent,
                event_type="DEGRADED",
                to_role="PROBATION",
                effective=effective,
                reason=f"aligned replacement evidence favored {winner['candidate_version_id']}",
                scorecard=forward[incumbent_id],
                decisions=decisions,
            )
            incumbent["to_role"] = "PROBATION"
            self._transition(
                winner,
                event_type="CHAMPION_ASSIGNED",
                to_role="CHAMPION",
                effective=effective,
                reason=f"common-date replacement evidence beat incumbent {incumbent_id}",
                scorecard=winner_score,
                decisions=decisions,
                comparison_scorecard=common[str(winner["candidate_version_id"])],
            )
            winner["to_role"] = "CHAMPION"

        roles = {
            str(candidate["candidate_version_id"]): candidate["to_role"]
            for candidate in self.registry.active_candidate_versions()
        }
        return {
            "status": "COMPLETED",
            "policy_version": TRANSITION_POLICY_VERSION,
            "decisions": decisions,
            "active_roles": dict(sorted(roles.items())),
            "funded_authorization": False,
            "phase4_request_created": False,
        }

    def _transition(
        self,
        candidate: Mapping[str, Any],
        *,
        event_type: str,
        to_role: str,
        effective: str,
        reason: str,
        scorecard: Mapping[str, Any],
        decisions: list[dict[str, Any]],
        comparison_scorecard: Mapping[str, Any] | None = None,
    ) -> None:
        candidate_id = str(candidate["candidate_version_id"])
        from_role = str(candidate["to_role"])
        if _utc(effective) < _utc(str(scorecard["created_at_utc"])):
            raise TransitionPolicyError(
                "role transition cannot precede its controlling forward scorecard"
            )
        family_history = self.registry.family_failure_history(str(candidate["family_id"]))
        metadata = {
            "transition_policy_version": TRANSITION_POLICY_VERSION,
            "forward_scorecard_id": scorecard["scorecard_id"],
            "common_date_scorecard_id": (
                comparison_scorecard["scorecard_id"] if comparison_scorecard else None
            ),
            "family_failure_history": family_history,
            "funded_authorization": False,
        }
        event_id = self.registry.append_lifecycle_event(
            candidate_version_id=candidate_id,
            event_type=event_type,
            from_role=from_role,
            to_role=to_role,
            occurred_at_utc=effective,
            reason=reason,
            source_run_id=str(candidate["source_run_id"]),
            metadata=metadata,
        )
        decisions.append({
            "event_id": event_id,
            "candidate_version_id": candidate_id,
            "family_id": candidate["family_id"],
            "from_role": from_role,
            "to_role": to_role,
            "reason": reason,
            "forward_scorecard_id": scorecard["scorecard_id"],
            "common_date_scorecard_id": metadata["common_date_scorecard_id"],
            "funded_authorization": False,
        })

    def _consecutive_negative_count(self, candidate_version_id: str) -> int:
        rows = self.registry.connection.execute(
            """select statistics_json from candidate_scorecards
               where candidate_version_id=? and evidence_kind='FORWARD_SHADOW'
               order by created_at_utc desc,rowid desc""",
            (candidate_version_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if not _negative_economics(json.loads(row["statistics_json"])):
                break
            count += 1
        return count


def _by_candidate(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["candidate_version_id"]): row for row in rows}


def _hard_rejection(statistics: Mapping[str, Any]) -> bool:
    if int(statistics.get("settlement", {}).get("disagreements", 0)) > 0:
        return True
    return any(
        str(blocker).startswith("INVALID_OR_MISSING_")
        for blocker in statistics.get("blockers", [])
    )


def _negative_economics(statistics: Mapping[str, Any]) -> bool:
    counts = statistics.get("counts", {})
    requirements = statistics.get("requirements", {})
    enough_dates = len(statistics.get("effective_venue_resolved_market_dates", [])) >= int(
        requirements.get("minimum_effective_dates", 0)
    )
    enough_station_dates = int(
        statistics.get("effective_venue_resolved_station_dates", 0)
    ) >= int(requirements.get("minimum_station_dates", 0))
    if not enough_dates or not enough_station_dates:
        return False
    if int(counts.get("venue_resolved_executions", 0)) <= 0:
        return False
    scenarios = statistics.get("fill_scenarios", {})
    base = scenarios.get("base_displayed_depth", {})
    conservative = scenarios.get("conservative_half_displayed_depth", {})
    return (
        float(base.get("venue_pnl_usd") or 0) <= 0
        or float(conservative.get("venue_pnl_usd") or 0) <= 0
    )


def _promotion_ready(
    scorecard: Mapping[str, Any] | None, policy: TransitionPolicy
) -> bool:
    if scorecard is None:
        return False
    statistics = scorecard["statistics"]
    if statistics.get("review_state") != "READY_FOR_C5_REVIEW":
        return False
    if statistics.get("funded_authorization") is not False:
        return False
    if statistics.get("blockers"):
        return False
    scenarios = statistics.get("fill_scenarios", {})
    for name in ("conservative_half_displayed_depth", "base_displayed_depth"):
        summary = scenarios.get(name, {})
        if float(summary.get("venue_cost_usd") or 0) <= 0:
            return False
        if float(summary.get("venue_pnl_usd") or 0) <= 0:
            return False
        uncertainty = summary.get("uncertainty", {})
        mean = uncertainty.get("mean_pnl_usd")
        standard_error = uncertainty.get("standard_error_usd")
        if mean is None or standard_error is None:
            return False
        if float(mean) - policy.uncertainty_z * float(standard_error) <= 0:
            return False
    settlement = statistics.get("settlement", {})
    markouts = statistics.get("markouts", {})
    concentration = statistics.get("concentration", {})
    station_share = concentration.get("maximum_station_cost_share")
    date_share = concentration.get("maximum_market_date_cost_share")
    return (
        int(settlement.get("disagreements", 0)) == 0
        and int(settlement.get("venue_resolved", 0)) > 0
        and int(markouts.get("missing_or_invalid_executions", 0)) == 0
        and station_share is not None
        and float(station_share) <= policy.maximum_station_cost_share
        and date_share is not None
        and float(date_share) <= policy.maximum_market_date_cost_share
    )


def _ranking_key(
    candidate: Mapping[str, Any],
    forward: Mapping[str, Mapping[str, Any]],
) -> tuple[float, float, int, str]:
    candidate_id = str(candidate["candidate_version_id"])
    statistics = forward[candidate_id]["statistics"]
    scenarios = statistics["fill_scenarios"]
    conservative_rr = float(
        scenarios["conservative_half_displayed_depth"].get("venue_rr") or 0
    )
    base_rr = float(scenarios["base_displayed_depth"].get("venue_rr") or 0)
    complexity = CandidateRule(**candidate["definition"]["rule"]).complexity
    return (-conservative_rr, -base_rr, complexity, candidate_id)


def _beats_incumbent(
    challenger: Mapping[str, Any],
    incumbent: Mapping[str, Any],
    forward_scorecard: Mapping[str, Any],
    common_scorecard: Mapping[str, Any] | None,
    *,
    policy: TransitionPolicy,
) -> bool:
    if common_scorecard is None or not _promotion_ready(forward_scorecard, policy):
        return False
    statistics = common_scorecard["statistics"]
    comparison = next(
        (
            item for item in statistics.get("comparisons", [])
            if item.get("peer_candidate_version_id") == incumbent["candidate_version_id"]
        ),
        None,
    )
    if comparison is None:
        return False
    minimum_dates = int(
        forward_scorecard["statistics"].get("requirements", {}).get(
            "minimum_effective_dates", 0
        )
    )
    if int(comparison.get("common_market_date_count", 0)) < minimum_dates:
        return False
    replacement = comparison.get("replacement_incremental", {})
    pnl_delta = float(replacement.get("pnl_delta_usd") or 0)
    rr_delta = replacement.get("rr_delta")
    if pnl_delta <= 0 or rr_delta is None:
        return False
    if float(rr_delta) >= policy.minimum_aligned_rr_delta:
        return True
    challenger_complexity = CandidateRule(**challenger["definition"]["rule"]).complexity
    incumbent_complexity = CandidateRule(**incumbent["definition"]["rule"]).complexity
    return float(rr_delta) >= 0 and challenger_complexity < incumbent_complexity


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)
