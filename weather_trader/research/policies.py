from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from weather_trader.execution.contracts import ResearchPolicyPosition, StrategyBucket, TradeAction
from weather_trader.execution.store import ExecutionStore


LEGACY_DYNAMIC_MODEL = "dynamic_bucket_obs_2022_2025"
LEGACY_MVP_MODEL = "mvp_obs_corrected"
DYNAMIC_MODEL = "dynamic_bucket_pm_active_us12_obs_2022_2025"
MVP_MODEL = "mvp_pm_active_us12_obs_2022_2025"
MODEL_PAIRS = (
    ("legacy_dynamic_mvp", LEGACY_DYNAMIC_MODEL, LEGACY_MVP_MODEL, "consensus_dynamic_mvp"),
    ("pm_active_us12_dynamic_mvp", DYNAMIC_MODEL, MVP_MODEL, "consensus_pm_active_us12_dynamic_mvp"),
)
MODEL_PAIRS_BY_NAME = {pair_name: pair for pair_name, *pair in MODEL_PAIRS}
MODEL_PAIRS_BY_MODEL = {
    model_name: (pair_name, dynamic_model, mvp_model, consensus_name)
    for pair_name, dynamic_model, mvp_model, consensus_name in MODEL_PAIRS
    for model_name in (dynamic_model, mvp_model)
}

EXPERIMENTAL_COASTAL_STATIONS: frozenset[str] = frozenset({"KBOS", "KDCA", "KHOU", "KLAX", "KLGA", "KMIA", "KSEA", "KSFO"})
EXPERIMENTAL_INLAND_STATIONS: frozenset[str] = frozenset({"KATL", "KDEN", "KDFW", "KORD"})
EXPERIMENTAL_MANUAL_STRESS_EXCLUDE_STATIONS: frozenset[str] = frozenset()
ENTRY_BAND_EXPERIMENTS: tuple[tuple[str, float, float], ...] = (
    ("00_10", 0.00, 0.10),
    ("10_25", 0.10, 0.25),
    ("25_50", 0.25, 0.50),
    ("50_75", 0.50, 0.75),
    ("75_100", 0.75, 1.00),
)


@dataclass(frozen=True)
class ResearchPolicySpec:
    name: str
    source: str
    strategy_bucket: StrategyBucket | None = None
    model_name: str | None = None
    model_group: str | None = None
    obs_delay_bucket: str | None = None
    scope_by_strategy: bool = False
    station_allow_set: frozenset[str] | None = None
    station_exclude_set: frozenset[str] | None = None
    entry_price_min: float | None = None
    entry_price_max: float | None = None
    fair_probability_min: float | None = None
    fair_probability_max: float | None = None
    edge_min: float | None = None
    edge_max: float | None = None
    bucket_type: str | None = None
    local_decision_start: str | None = None
    local_decision_end: str | None = None
    uniqueness_key_mode: str = "station_date"


def _entry_band_policy_specs(
    *,
    name_prefix: str,
    source: str,
    strategy_bucket: StrategyBucket,
    model_group: str | None = None,
    model_name: str | None = None,
    obs_delay_bucket: str | None = None,
    local_decision_start: str | None = None,
    local_decision_end: str | None = None,
) -> tuple[ResearchPolicySpec, ...]:
    return tuple(
        ResearchPolicySpec(
            f"{name_prefix}_entry_{suffix}_first",
            source,
            strategy_bucket,
            model_group=model_group,
            model_name=model_name,
            obs_delay_bucket=obs_delay_bucket,
            local_decision_start=local_decision_start,
            local_decision_end=local_decision_end,
            entry_price_min=minimum,
            entry_price_max=maximum,
        )
        for suffix, minimum, maximum in ENTRY_BAND_EXPERIMENTS
    )


def _by_bucket_side_delay_policy_spec(
    *,
    name: str,
    obs_delay_bucket: str | None = None,
    entry_price_min: float | None = None,
    entry_price_max: float | None = None,
    local_decision_start: str | None = None,
    local_decision_end: str | None = None,
) -> ResearchPolicySpec:
    return ResearchPolicySpec(
        name,
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket=obs_delay_bucket,
        entry_price_min=entry_price_min,
        entry_price_max=entry_price_max,
        local_decision_start=local_decision_start,
        local_decision_end=local_decision_end,
        uniqueness_key_mode="station_date_bucket_side_obs_delay",
    )


POLICIES: tuple[ResearchPolicySpec, ...] = (
    ResearchPolicySpec("pm_us12_consensus_hc_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="pm_active_us12_dynamic_mvp"),
    _by_bucket_side_delay_policy_spec(name="pm_us12_consensus_hc_by_bucket_side_delay_first"),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_10m_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="10m",
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_15m_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="15m",
    ),
    _by_bucket_side_delay_policy_spec(
        name="pm_us12_consensus_hc_15m_by_bucket_side_delay_first",
        obs_delay_bucket="15m",
    ),
    *_entry_band_policy_specs(
        name_prefix="pm_us12_consensus_hc_15m",
        source="consensus",
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="15m",
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_15m_entry_25_75_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="15m",
        entry_price_min=0.25,
        entry_price_max=0.75,
    ),
    _by_bucket_side_delay_policy_spec(
        name="pm_us12_consensus_hc_15m_entry_25_75_by_bucket_side_delay_first",
        obs_delay_bucket="15m",
        entry_price_min=0.25,
        entry_price_max=0.75,
    ),
    _by_bucket_side_delay_policy_spec(
        name="pm_us12_consensus_hc_15m_entry_50_75_by_bucket_side_delay_first",
        obs_delay_bucket="15m",
        entry_price_min=0.50,
        entry_price_max=0.75,
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_15m_no_tiny_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="15m",
        entry_price_min=0.05,
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_late_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        local_decision_start="12:00",
        local_decision_end="15:00",
    ),
    _by_bucket_side_delay_policy_spec(
        name="pm_us12_consensus_hc_late_by_bucket_side_delay_first",
        local_decision_start="12:00",
        local_decision_end="15:00",
    ),
    *_entry_band_policy_specs(
        name_prefix="pm_us12_consensus_hc_late",
        source="consensus",
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        local_decision_start="12:00",
        local_decision_end="15:00",
    ),
    _by_bucket_side_delay_policy_spec(
        name="pm_us12_consensus_hc_late_entry_50_75_by_bucket_side_delay_first",
        entry_price_min=0.50,
        entry_price_max=0.75,
        local_decision_start="12:00",
        local_decision_end="15:00",
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_hc_early_first",
        "consensus",
        StrategyBucket.HIGH_CONVICTION,
        model_group="pm_active_us12_dynamic_mvp",
        local_decision_start="10:00",
        local_decision_end="12:00",
    ),
    ResearchPolicySpec(
        "pm_us12_consensus_best_15m_first",
        "consensus",
        StrategyBucket.BEST_BUCKET,
        model_group="pm_active_us12_dynamic_mvp",
        obs_delay_bucket="15m",
    ),
    ResearchPolicySpec("pm_us12_consensus_per_strategy_first", "consensus", model_group="pm_active_us12_dynamic_mvp", scope_by_strategy=True),
    ResearchPolicySpec("pm_us12_mvp_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=MVP_MODEL),
    ResearchPolicySpec("pm_us12_mvp_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=MVP_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("pm_us12_mvp_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("pm_us12_mvp_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("pm_us12_dynamic_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=DYNAMIC_MODEL),
    ResearchPolicySpec("pm_us12_dynamic_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=DYNAMIC_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("pm_us12_dynamic_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=DYNAMIC_MODEL, obs_delay_bucket="15m"),
    *_entry_band_policy_specs(
        name_prefix="pm_us12_dynamic_hc_15m",
        source="model",
        strategy_bucket=StrategyBucket.HIGH_CONVICTION,
        model_name=DYNAMIC_MODEL,
        obs_delay_bucket="15m",
    ),
    ResearchPolicySpec("pm_us12_dynamic_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=DYNAMIC_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("consensus_hc_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="legacy_dynamic_mvp"),
    ResearchPolicySpec("consensus_hc_10m_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="legacy_dynamic_mvp", obs_delay_bucket="10m"),
    ResearchPolicySpec("consensus_hc_15m_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="legacy_dynamic_mvp", obs_delay_bucket="15m"),
    ResearchPolicySpec("consensus_best_15m_first", "consensus", StrategyBucket.BEST_BUCKET, model_group="legacy_dynamic_mvp", obs_delay_bucket="15m"),
    ResearchPolicySpec("consensus_per_strategy_first", "consensus", model_group="legacy_dynamic_mvp", scope_by_strategy=True),
    ResearchPolicySpec("mvp_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_MVP_MODEL),
    ResearchPolicySpec("mvp_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_MVP_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("mvp_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("mvp_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=LEGACY_MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("dynamic_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_DYNAMIC_MODEL),
    ResearchPolicySpec("dynamic_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_DYNAMIC_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("dynamic_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LEGACY_DYNAMIC_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("dynamic_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=LEGACY_DYNAMIC_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("max_so_far_first", "max_so_far", StrategyBucket.MAX_SO_FAR),
    ResearchPolicySpec("max_so_far_10m_first", "max_so_far", StrategyBucket.MAX_SO_FAR, obs_delay_bucket="10m"),
    ResearchPolicySpec("max_so_far_15m_first", "max_so_far", StrategyBucket.MAX_SO_FAR, obs_delay_bucket="15m"),
)


class ResearchPolicyEvaluator:
    def __init__(self, store: ExecutionStore, policies: tuple[ResearchPolicySpec, ...] = POLICIES) -> None:
        self.store = store
        self.policies = policies

    def evaluate(self) -> int:
        snapshots = self._load_snapshots()
        consensus = self._build_consensus(snapshots)
        inserted = 0
        for policy in self.policies:
            candidates = self._candidates_for_policy(policy, snapshots, consensus)
            for candidate in self._first_by_scope(policy, candidates):
                position = self._position_from_candidate(policy, candidate)
                if position is not None and self.store.insert_research_policy_position(position) is not None:
                    inserted += 1
        return inserted

    def _load_snapshots(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            """
            select id, raw_json
            from prediction_snapshots
            where selected_side <> 'SKIP'
                and selected_market_id is not null
            order by timestamp, id
            """
        ).fetchall()
        snapshots = []
        for row in rows:
            item = json.loads(row["raw_json"])
            item["id"] = int(row["id"])
            snapshots.append(item)
        return snapshots

    def _build_consensus(self, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[tuple[object, ...], dict[str, dict[str, Any]]] = {}
        for item in snapshots:
            if item.get("strategy_bucket") == str(StrategyBucket.MAX_SO_FAR):
                continue
            model_name = str(item.get("model_name") or "")
            if model_name not in MODEL_PAIRS_BY_MODEL:
                continue
            pair_name, _, _, _ = MODEL_PAIRS_BY_MODEL[model_name]
            key = (
                pair_name,
                item.get("station"),
                item.get("market_date"),
                item.get("obs_delay_bucket"),
                item.get("strategy_bucket"),
                item.get("selected_side"),
                item.get("selected_market_id"),
                item.get("selected_bucket"),
            )
            by_key.setdefault(key, {})[model_name] = item

        rows: list[dict[str, Any]] = []
        for key, pair in by_key.items():
            pair_name = str(key[0])
            dynamic_model, mvp_model, consensus_name = MODEL_PAIRS_BY_NAME[pair_name]
            dynamic = pair.get(dynamic_model)
            mvp = pair.get(mvp_model)
            if dynamic is None or mvp is None:
                continue
            edge_values = [_float_or_none(dynamic.get("selected_edge")), _float_or_none(mvp.get("selected_edge"))]
            fair_values = [_selected_fair(dynamic), _selected_fair(mvp)]
            rows.append(
                {
                    **dynamic,
                    "id": min(int(dynamic["id"]), int(mvp["id"])),
                    "timestamp": max(str(dynamic.get("timestamp")), str(mvp.get("timestamp"))),
                    "model_name": consensus_name,
                    "selected_edge": _mean_present(edge_values),
                    "selected_fair": _mean_present(fair_values),
                    "source_prediction_snapshot_ids": [int(dynamic["id"]), int(mvp["id"])],
                    "raw_policy": {
                        "dynamic_snapshot_id": int(dynamic["id"]),
                        "mvp_snapshot_id": int(mvp["id"]),
                        "dynamic_edge": dynamic.get("selected_edge"),
                        "mvp_edge": mvp.get("selected_edge"),
                        "model_group": pair_name,
                    },
                }
            )
        return sorted(rows, key=lambda item: (str(item.get("timestamp")), int(item.get("id", 0))))

    def _candidates_for_policy(
        self,
        policy: ResearchPolicySpec,
        snapshots: list[dict[str, Any]],
        consensus: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_rows = consensus if policy.source == "consensus" else snapshots
        rows = []
        for item in source_rows:
            if policy.source == "model" and item.get("model_name") != policy.model_name:
                continue
            if policy.source == "consensus" and policy.model_group is not None:
                raw_policy = item.get("raw_policy") or {}
                if raw_policy.get("model_group") != policy.model_group:
                    continue
            if policy.source == "max_so_far" and item.get("strategy_bucket") != str(StrategyBucket.MAX_SO_FAR):
                continue
            if policy.source != "max_so_far" and item.get("strategy_bucket") == str(StrategyBucket.MAX_SO_FAR):
                continue
            if policy.strategy_bucket is not None and item.get("strategy_bucket") != str(policy.strategy_bucket):
                continue
            if policy.obs_delay_bucket is not None and item.get("obs_delay_bucket") != policy.obs_delay_bucket:
                continue
            if not _passes_policy_filters(policy, item):
                continue
            rows.append(item)
        return sorted(rows, key=lambda item: (str(item.get("timestamp")), int(item.get("id", 0))))

    def _first_by_scope(self, policy: ResearchPolicySpec, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[object, ...]] = set()
        selected = []
        for item in candidates:
            key = _uniqueness_key(policy, item)
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
        return selected

    def _position_from_candidate(
        self,
        policy: ResearchPolicySpec,
        candidate: dict[str, Any],
    ) -> ResearchPolicyPosition | None:
        selected_side = str(candidate.get("selected_side") or "")
        entry_price = (
            _float_or_none(candidate.get("selected_yes_ask"))
            if selected_side == str(TradeAction.BUY_YES)
            else _float_or_none(candidate.get("selected_no_ask"))
        )
        selected_market_id = candidate.get("selected_market_id")
        station = candidate.get("station")
        market_date = candidate.get("market_date")
        if entry_price is None or selected_market_id is None or station is None or market_date is None:
            return None

        strategy_bucket = StrategyBucket(str(candidate.get("strategy_bucket")))
        scope_key = _scope_key(policy, candidate)
        entry_fair = _float_or_none(candidate.get("selected_fair"))
        if entry_fair is None:
            entry_fair = _selected_fair(candidate)
        return ResearchPolicyPosition(
            timestamp=str(candidate.get("timestamp")),
            policy_name=policy.name,
            station=str(station),
            market_date=date.fromisoformat(str(market_date)),
            scope_key=scope_key,
            model_group=str(candidate.get("model_name") or policy.model_name or policy.source),
            strategy_bucket=strategy_bucket,
            obs_delay_bucket=str(candidate.get("obs_delay_bucket")),
            selected_market_id=str(selected_market_id),
            selected_side=TradeAction(selected_side),
            selected_bucket=None if candidate.get("selected_bucket") is None else str(candidate.get("selected_bucket")),
            entry_price=entry_price,
            entry_edge=_float_or_none(candidate.get("selected_edge")),
            entry_fair=entry_fair,
            source_prediction_snapshot_ids=[
                int(value)
                for value in candidate.get("source_prediction_snapshot_ids", [candidate.get("id")])
                if value is not None
            ],
            raw_policy={
                "policy": {
                    "name": policy.name,
                    "source": policy.source,
                    "model_name": policy.model_name,
                    "strategy_bucket": str(policy.strategy_bucket) if policy.strategy_bucket else None,
                    "obs_delay_bucket": policy.obs_delay_bucket,
                    "scope_by_strategy": policy.scope_by_strategy,
                    "station_allow_set": sorted(policy.station_allow_set) if policy.station_allow_set else None,
                    "station_exclude_set": sorted(policy.station_exclude_set) if policy.station_exclude_set else None,
                    "entry_price_min": policy.entry_price_min,
                    "entry_price_max": policy.entry_price_max,
                    "fair_probability_min": policy.fair_probability_min,
                    "fair_probability_max": policy.fair_probability_max,
                    "edge_min": policy.edge_min,
                    "edge_max": policy.edge_max,
                    "bucket_type": policy.bucket_type,
                    "local_decision_start": policy.local_decision_start,
                    "local_decision_end": policy.local_decision_end,
                    "uniqueness_key_mode": policy.uniqueness_key_mode,
                },
                **dict(candidate.get("raw_policy") or {}),
            },
        )


def _passes_policy_filters(policy: ResearchPolicySpec, item: dict[str, Any]) -> bool:
    station = str(item.get("station") or "")
    if policy.station_allow_set is not None and station not in policy.station_allow_set:
        return False
    if policy.station_exclude_set is not None and station in policy.station_exclude_set:
        return False

    entry_price = _entry_price(item)
    if not _in_range(entry_price, policy.entry_price_min, policy.entry_price_max):
        return False

    fair = _float_or_none(item.get("selected_fair"))
    if fair is None:
        fair = _selected_fair(item)
    if not _in_range(fair, policy.fair_probability_min, policy.fair_probability_max):
        return False

    edge = _float_or_none(item.get("selected_edge"))
    if not _in_range(edge, policy.edge_min, policy.edge_max):
        return False

    if policy.bucket_type is not None and _bucket_type(item.get("selected_bucket")) != policy.bucket_type:
        return False

    if policy.local_decision_start is not None or policy.local_decision_end is not None:
        decision_time = _local_time(item.get("decision_time_local"))
        if decision_time is None:
            return False
        start = _parse_hhmm(policy.local_decision_start) if policy.local_decision_start else None
        end = _parse_hhmm(policy.local_decision_end) if policy.local_decision_end else None
        if start is not None and decision_time < start:
            return False
        if end is not None and decision_time >= end:
            return False

    return True


def _in_range(value: float | None, minimum: float | None, maximum: float | None) -> bool:
    if minimum is None and maximum is None:
        return True
    if value is None:
        return False
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


def _entry_price(item: dict[str, Any]) -> float | None:
    selected_side = str(item.get("selected_side") or "")
    if selected_side == str(TradeAction.BUY_YES):
        return _float_or_none(item.get("selected_yes_ask"))
    if selected_side == str(TradeAction.BUY_NO):
        return _float_or_none(item.get("selected_no_ask"))
    return None


def _bucket_type(bucket: object) -> str:
    text = str(bucket or "")
    if not text:
        return "missing"
    if text.startswith("<=") or text.startswith(">="):
        return "tail"
    return "range"


def _local_time(value: object) -> time | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).time()
    except ValueError:
        return None


def _parse_hhmm(value: str | None) -> time:
    if value is None:
        raise ValueError("missing time value")
    return time.fromisoformat(value)


def _uniqueness_key(policy: ResearchPolicySpec, item: dict[str, Any]) -> tuple[object, ...]:
    key = [item.get("station"), item.get("market_date")]
    if policy.uniqueness_key_mode == "station_date_bucket_side":
        key.extend([item.get("selected_bucket"), item.get("selected_side")])
    elif policy.uniqueness_key_mode == "station_date_bucket_side_obs_delay":
        key.extend([item.get("selected_bucket"), item.get("selected_side"), item.get("obs_delay_bucket")])
    elif policy.uniqueness_key_mode != "station_date":
        raise ValueError(f"Unsupported uniqueness_key_mode: {policy.uniqueness_key_mode}")
    if policy.scope_by_strategy:
        key.append(item.get("strategy_bucket"))
    return tuple(key)


def _scope_key(policy: ResearchPolicySpec, item: dict[str, Any]) -> str:
    if policy.uniqueness_key_mode == "station_date":
        if policy.scope_by_strategy:
            return f"strategy:{item.get('strategy_bucket')}"
        return "station_date"

    parts = [policy.uniqueness_key_mode]
    if policy.uniqueness_key_mode in {"station_date_bucket_side", "station_date_bucket_side_obs_delay"}:
        parts.extend([str(item.get("selected_bucket") or ""), str(item.get("selected_side") or "")])
    if policy.uniqueness_key_mode == "station_date_bucket_side_obs_delay":
        parts.append(str(item.get("obs_delay_bucket") or ""))
    if policy.scope_by_strategy:
        parts.extend(["strategy", str(item.get("strategy_bucket") or "")])
    return ":".join(parts)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _selected_fair(item: dict[str, Any]) -> float | None:
    selected_side = str(item.get("selected_side") or "")
    if selected_side == str(TradeAction.BUY_YES):
        return _float_or_none(item.get("selected_fair_yes"))
    if selected_side == str(TradeAction.BUY_NO):
        return _float_or_none(item.get("selected_fair_no"))
    return None


def _mean_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)
