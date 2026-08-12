from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from weather_trader.pricing.contracts import stable_hash


PHASE3D_CONTRACT_VERSION = "phase3d_discovery_v2"


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DiscoveryRunSpec:
    source_start_date: str
    discovery_cutoff_exclusive: str
    earliest_activation_timestamp: str
    research_watermark: int
    tape_session_ids: tuple[str, ...]
    tape_partition_ids: tuple[str, ...]
    build_hash: str
    outcome_watermark: str
    venue_settlement_watermark: str
    model_ids: tuple[str, ...]
    model_market_families: tuple[tuple[str, str], ...]
    grammar_version: str = "phase3d_simple_rules_v1"
    side_rules: tuple[str, ...] = ("ANY", "BUY_NO", "BUY_YES")
    observation_delay_rules: tuple[str, ...] = ("ANY", "10m", "15m")
    local_windows: tuple[tuple[str, str], ...] = (("00:00", "24:00"), ("12:00", "15:00"))
    entry_bands: tuple[tuple[float, float], ...] = ((0.0, 0.50), (0.05, 0.50), (0.05, 0.35))
    fold_rule: str = "contiguous_equal_count_market_date_folds_no_fitted_thresholds"
    nomination_rule: str = "positive_total_and_two_thirds_positive_folds_then_max_penalized_rr"
    cost_rule: str = "binary_payout_no_fee_discovery_v1"
    fold_count: int = 3
    minimum_effective_dates: int = 8
    minimum_executable_station_dates: int = 20
    fill_scenario: str = "stable_taker_immediate_ask_sweep"
    latency_ms: int = 250
    pre_signal_seconds: int = 60
    markout_horizons_seconds: tuple[tuple[str, int], ...] = (("30s", 30), ("2m", 120))
    target_cost_usd: float = 25.0
    maximum_challengers: int = 3
    maximum_candidate_rules: int = 5_000
    complexity_penalty_per_unit: float = 0.01
    portfolio_station_date_cap_usd: float = 25.0
    daily_risk_cap_usd: float = 300.0
    settlement_rule: str = "venue_authoritative_with_research_disagreement_flag"

    def __post_init__(self) -> None:
        start = date.fromisoformat(self.source_start_date)
        cutoff = date.fromisoformat(self.discovery_cutoff_exclusive)
        activation = _utc(self.earliest_activation_timestamp)
        cutoff_at = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)
        if start >= cutoff:
            raise ValueError("source start must precede the exclusive discovery cutoff")
        if activation < cutoff_at:
            raise ValueError("activation must not precede the exclusive discovery cutoff")
        if self.research_watermark < 0 or not self.build_hash:
            raise ValueError("run requires a nonnegative watermark and build hash")
        if not self.model_ids or not self.model_market_families or not self.outcome_watermark or not self.venue_settlement_watermark:
            raise ValueError("run requires frozen model and settlement source watermarks")
        if set(self.model_ids) != {model for model, _ in self.model_market_families}:
            raise ValueError("model IDs and model/market-family grammar must agree")
        if not self.side_rules or not set(self.side_rules) <= {"ANY", "BUY_YES", "BUY_NO"}:
            raise ValueError("run contains an invalid side grammar")
        if not self.local_windows or any(start >= end for start, end in self.local_windows):
            raise ValueError("run contains an invalid local-window grammar")
        if not self.entry_bands or any(not 0 <= lower <= upper <= 1 for lower, upper in self.entry_bands):
            raise ValueError("run contains an invalid entry-band grammar")
        if self.fold_count < 2 or self.minimum_effective_dates < self.fold_count:
            raise ValueError("walk-forward configuration requires at least two folds")
        if self.minimum_executable_station_dates < self.minimum_effective_dates:
            raise ValueError("station/date minimum cannot be below effective-date minimum")
        if self.latency_ms < 0 or self.pre_signal_seconds < 0:
            raise ValueError("latency and pre-signal coverage must be nonnegative")
        markout_labels = [label for label, _ in self.markout_horizons_seconds]
        if (
            not markout_labels
            or len(set(markout_labels)) != len(markout_labels)
            or any(not label or seconds <= 0 for label, seconds in self.markout_horizons_seconds)
        ):
            raise ValueError(
                "markout horizons must have unique labels and positive seconds"
            )
        if (
            self.target_cost_usd <= 0
            or self.portfolio_station_date_cap_usd <= 0
            or self.daily_risk_cap_usd <= 0
            or self.maximum_challengers < 1
            or self.maximum_candidate_rules < self.maximum_challengers
        ):
            raise ValueError(
                "Phase 3D runs require positive size and bounded candidate/challenger budgets"
            )

    @property
    def run_id(self) -> str:
        return f"p3d_run_{stable_hash(self.canonical_payload())[:24]}"

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["contract_version"] = PHASE3D_CONTRACT_VERSION
        return payload


@dataclass(frozen=True)
class CandidateRule:
    model_id: str
    market_family: str
    selected_side: str
    strategy_bucket: str
    observation_delay_bucket: str | None
    local_start: str
    local_end: str
    entry_price_min: float
    entry_price_max: float
    require_high_conviction: bool
    minimum_model_edge_at_best_ask: float = 0.0
    maximum_spread: float | None = None
    dedupe_scope: str = "station_date"
    execution_arm: str = "stable_taker"

    def __post_init__(self) -> None:
        if (
            not self.model_id
            or not self.market_family
            or not self.strategy_bucket
            or self.selected_side not in {"BUY_YES", "BUY_NO", "ANY"}
        ):
            raise ValueError("candidate requires a model and a valid side rule")
        if self.local_start >= self.local_end:
            raise ValueError("candidate local window must be increasing")
        if not 0 <= self.entry_price_min <= self.entry_price_max <= 1:
            raise ValueError("candidate entry band must be ordered")
        if not 0 <= self.minimum_model_edge_at_best_ask <= 1:
            raise ValueError("candidate minimum edge must be between zero and one")
        if self.maximum_spread is not None and not 0 <= self.maximum_spread <= 1:
            raise ValueError("candidate maximum spread must be between zero and one")
        if self.dedupe_scope != "station_date" or self.execution_arm != "stable_taker":
            raise ValueError("initial grammar supports station/date stable-taker rules only")

    @property
    def candidate_id(self) -> str:
        return f"p3d_candidate_{stable_hash(asdict(self))[:24]}"

    @property
    def complexity(self) -> int:
        return sum(
            (
                self.selected_side != "ANY",
                self.observation_delay_bucket is not None,
                self.local_start != "00:00" or self.local_end != "24:00",
                self.entry_price_min > 0 or self.entry_price_max < 1,
                self.minimum_model_edge_at_best_ask > 0,
                self.maximum_spread is not None,
                self.require_high_conviction,
            )
        )

    @property
    def correlated_family_id(self) -> str:
        # Nearby delay, price-band, and clock variants intentionally collapse.
        payload = {
            "model_id": self.model_id,
            "market_family": self.market_family,
            "selected_side": self.selected_side,
        }
        return f"p3d_family_{stable_hash(payload)[:20]}"


@dataclass(frozen=True)
class BroadDiscoveryRow:
    row_id: str
    row_hash: str
    discovery_run_id: str
    build_hash: str
    source_prediction_snapshot_ids: tuple[int, ...]
    source_snapshot_payload_hash: str
    snapshot_timestamp_utc: str
    decision_time_utc: str
    quote_ready_timestamp_utc: str
    latest_observation_time_utc: str
    observation_age_minutes: float | None
    station: str
    market_date: str
    market_family: str
    model_id: str
    strategy_bucket: str
    observation_delay_bucket: str
    local_decision_hhmm: str
    lifecycle_horizon: str
    selected_market_id: str
    selected_bucket: str
    selected_side: str
    token_id: str | None
    raw_model_fair: float | None
    snapshot_entry_price: float | None
    high_conviction: bool
    tape_eligible: bool
    tape_ineligibility_reason: str | None
    tape_session_id: str | None
    coverage_interval_id: int | None
    reconstruction_hash: str | None
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    depth_at_best_ask: float | None
    ask_levels: tuple[tuple[float, float], ...]
    taker_cost_usd: float
    taker_shares: float
    taker_vwap: float | None
    fill_fraction: float
    research_outcome_label: int | None
    research_outcome_source: str | None
    venue_outcome_label: int | None
    venue_resolution_source: str | None
    settlement_disagreement: bool | None
    markouts_valid: bool
    markout_midpoints: tuple[tuple[str, float], ...]
    actual_fill_status: str
    discovery_eligible: bool
    discovery_ineligibility_reasons: tuple[str, ...]

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_prediction_snapshot_ids"] = list(self.source_prediction_snapshot_ids)
        payload["ask_levels"] = [list(level) for level in self.ask_levels]
        payload["markout_midpoints"] = [list(markout) for markout in self.markout_midpoints]
        payload["discovery_ineligibility_reasons"] = list(self.discovery_ineligibility_reasons)
        if not include_hash:
            payload.pop("row_hash", None)
        return payload

    def with_hash(self) -> BroadDiscoveryRow:
        return replace(self, row_hash=stable_hash(self.canonical_payload(include_hash=False)))


@dataclass(frozen=True)
class StrategyManifest:
    strategy_id: str
    version: int
    discovery_run_id: str
    discovery_run_hash: str
    candidate: CandidateRule
    activation_timestamp: str
    untouched_holdout_start: str
    source_cutoff_exclusive: str
    pricing_version: str
    cost_version: str
    execution_arm: str
    latency_ms: int
    pre_signal_seconds: int
    target_cost_usd: float
    price_cap: float
    station_date_cap_usd: float
    daily_risk_cap_usd: float
    minimum_forward_effective_dates: int
    minimum_forward_station_dates: int
    settlement_source: str
    source_hash: str
    code_hash: str
    manifest_hash: str = ""

    def __post_init__(self) -> None:
        activation = _utc(self.activation_timestamp)
        holdout = _utc(self.untouched_holdout_start)
        cutoff = date.fromisoformat(self.source_cutoff_exclusive)
        cutoff_at = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)
        if activation < cutoff_at or holdout < activation:
            raise ValueError("manifest activation and holdout must follow discovery cutoff")
        if self.version < 1 or self.target_cost_usd <= 0:
            raise ValueError("manifest requires a positive version and size")
        if self.minimum_forward_effective_dates < 1 or self.minimum_forward_station_dates < 1:
            raise ValueError("manifest forward sample gates must be positive")
        if self.execution_arm != self.candidate.execution_arm:
            raise ValueError("manifest execution arm differs from selected candidate")

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_hash:
            payload.pop("manifest_hash", None)
        return payload

    def frozen(self) -> StrategyManifest:
        return replace(self, manifest_hash=stable_hash(self.canonical_payload(include_hash=False)))


def write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(f"immutable artifact already exists with different content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
