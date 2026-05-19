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
DYNAMIC_TUNED_MODEL = "dynamic_bucket_tuned_pm_active_us12_obs_2022_2025"
CATBOOST_MODEL = "catboost_bucket_pm_active_us12_obs_2022_2025"
MVP_MODEL = "mvp_pm_active_us12_obs_2022_2025"
HIGH_REGRESSION_MODEL = "high_regression_pm_active_us12_obs_2022_2025"
NGBOOST_MODEL = "ngboost_normal_pm_active_us12_obs_2022_2025"
LOW_DYNAMIC_MODEL = "low_dynamic_bucket_obs_2022_2025"
LOW_MVP_MODEL = "low_mvp_obs_2022_2025"

DYNAMIC_HRRR_V2_MODEL = "dynamic_bucket_hrrr_v2_obs_2022_2025"
DYNAMIC_TUNED_HRRR_V2_MODEL = "dynamic_bucket_tuned_hrrr_v2_obs_2022_2025"
CATBOOST_HRRR_V2_MODEL = "catboost_bucket_hrrr_v2_obs_2022_2025"
MVP_HRRR_V2_MODEL = "mvp_hrrr_v2_obs_2022_2025"
HIGH_REGRESSION_HRRR_V2_MODEL = "high_regression_hrrr_v2_obs_2022_2025"
NGBOOST_HRRR_V2_MODEL = "ngboost_normal_hrrr_v2_obs_2022_2025"

BROAD_STRATEGY_BUCKETS: tuple[StrategyBucket, ...] = (
    StrategyBucket.HIGH_CONVICTION,
    StrategyBucket.TAIL,
    StrategyBucket.BEST_BUCKET,
)

MODEL_FAMILIES: dict[str, dict[str, str]] = {
    "obs": {
        "dynamic_default": DYNAMIC_MODEL,
        "dynamic_tuned": DYNAMIC_TUNED_MODEL,
        "catboost": CATBOOST_MODEL,
        "mvp": MVP_MODEL,
        "high_regression": HIGH_REGRESSION_MODEL,
        "ngboost": NGBOOST_MODEL,
    },
    "hrrr_v2": {
        "dynamic_default": DYNAMIC_HRRR_V2_MODEL,
        "dynamic_tuned": DYNAMIC_TUNED_HRRR_V2_MODEL,
        "catboost": CATBOOST_HRRR_V2_MODEL,
        "mvp": MVP_HRRR_V2_MODEL,
        "high_regression": HIGH_REGRESSION_HRRR_V2_MODEL,
        "ngboost": NGBOOST_HRRR_V2_MODEL,
    },
}

CONSENSUS_GROUPS: dict[str, tuple[str, ...]] = {
    f"{family}_dynamic_tuned_mvp": (models["dynamic_tuned"], models["mvp"])
    for family, models in MODEL_FAMILIES.items()
}
CONSENSUS_GROUPS.update(
    {
        f"{family}_catboost_mvp": (models["catboost"], models["mvp"])
        for family, models in MODEL_FAMILIES.items()
    }
)
CONSENSUS_GROUPS.update(
    {
        f"{family}_bucket_consensus": (models["dynamic_tuned"], models["catboost"])
        for family, models in MODEL_FAMILIES.items()
    }
)
CONSENSUS_GROUPS.update(
    {
        f"{family}_three_model_consensus": (models["dynamic_tuned"], models["catboost"], models["mvp"])
        for family, models in MODEL_FAMILIES.items()
    }
)
CONSENSUS_GROUPS_BY_MODEL: dict[str, tuple[str, ...]] = {}
for _group_name, _model_names in CONSENSUS_GROUPS.items():
    for _model_name in _model_names:
        CONSENSUS_GROUPS_BY_MODEL.setdefault(_model_name, ())
        CONSENSUS_GROUPS_BY_MODEL[_model_name] = (*CONSENSUS_GROUPS_BY_MODEL[_model_name], _group_name)

CONSENSUS_GROUPS["low_pm_active_us12_dynamic_mvp"] = (LOW_DYNAMIC_MODEL, LOW_MVP_MODEL)
for _model_name in CONSENSUS_GROUPS["low_pm_active_us12_dynamic_mvp"]:
    CONSENSUS_GROUPS_BY_MODEL.setdefault(_model_name, ())
    CONSENSUS_GROUPS_BY_MODEL[_model_name] = (*CONSENSUS_GROUPS_BY_MODEL[_model_name], "low_pm_active_us12_dynamic_mvp")


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


def _strategy_slug(strategy_bucket: StrategyBucket) -> str:
    return str(strategy_bucket).lower()


def _broad_policy_specs() -> tuple[ResearchPolicySpec, ...]:
    specs: list[ResearchPolicySpec] = []
    for family, models in MODEL_FAMILIES.items():
        for alias, model_name in models.items():
            for strategy_bucket in BROAD_STRATEGY_BUCKETS:
                specs.append(
                    ResearchPolicySpec(
                        name=f"broad_{family}_{alias}_{_strategy_slug(strategy_bucket)}_first",
                        source="model",
                        strategy_bucket=strategy_bucket,
                        model_name=model_name,
                    )
                )
        for group_name in CONSENSUS_GROUPS:
            if not group_name.startswith(f"{family}_"):
                continue
            short_group_name = group_name.removeprefix(f"{family}_")
            for strategy_bucket in BROAD_STRATEGY_BUCKETS:
                specs.append(
                    ResearchPolicySpec(
                        name=f"broad_{family}_{short_group_name}_{_strategy_slug(strategy_bucket)}_first",
                        source="consensus",
                        strategy_bucket=strategy_bucket,
                        model_group=group_name,
                    )
                )
    specs.append(ResearchPolicySpec("broad_max_so_far_first", "max_so_far", StrategyBucket.MAX_SO_FAR))
    return tuple(specs)


LOW_POLICIES: tuple[ResearchPolicySpec, ...] = (
    ResearchPolicySpec("low_pm_us12_consensus_hc_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="low_pm_active_us12_dynamic_mvp"),
    ResearchPolicySpec("low_pm_us12_consensus_hc_by_bucket_side_delay_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="low_pm_active_us12_dynamic_mvp", uniqueness_key_mode="station_date_bucket_side_obs_delay"),
    ResearchPolicySpec("low_pm_us12_consensus_hc_10m_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="low_pm_active_us12_dynamic_mvp", obs_delay_bucket="10m"),
    ResearchPolicySpec("low_pm_us12_consensus_hc_15m_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="low_pm_active_us12_dynamic_mvp", obs_delay_bucket="15m"),
    ResearchPolicySpec("low_pm_us12_consensus_hc_15m_by_bucket_side_delay_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="low_pm_active_us12_dynamic_mvp", obs_delay_bucket="15m", uniqueness_key_mode="station_date_bucket_side_obs_delay"),
    ResearchPolicySpec("low_pm_us12_mvp_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_MVP_MODEL),
    ResearchPolicySpec("low_pm_us12_mvp_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_MVP_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("low_pm_us12_mvp_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("low_pm_us12_mvp_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=LOW_MVP_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("low_pm_us12_dynamic_hc_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_DYNAMIC_MODEL),
    ResearchPolicySpec("low_pm_us12_dynamic_hc_10m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_DYNAMIC_MODEL, obs_delay_bucket="10m"),
    ResearchPolicySpec("low_pm_us12_dynamic_hc_15m_first", "model", StrategyBucket.HIGH_CONVICTION, model_name=LOW_DYNAMIC_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("low_pm_us12_dynamic_best_15m_first", "model", StrategyBucket.BEST_BUCKET, model_name=LOW_DYNAMIC_MODEL, obs_delay_bucket="15m"),
    ResearchPolicySpec("low_min_so_far_first", "max_so_far", StrategyBucket.MAX_SO_FAR),
    ResearchPolicySpec("low_min_so_far_10m_first", "max_so_far", StrategyBucket.MAX_SO_FAR, obs_delay_bucket="10m"),
    ResearchPolicySpec("low_min_so_far_15m_first", "max_so_far", StrategyBucket.MAX_SO_FAR, obs_delay_bucket="15m"),
)


POLICIES: tuple[ResearchPolicySpec, ...] = (*_broad_policy_specs(), *LOW_POLICIES)


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
            group_names = CONSENSUS_GROUPS_BY_MODEL.get(model_name)
            if not group_names:
                continue
            for group_name in group_names:
                key = (
                    group_name,
                    item.get("market_family") or "HIGH_TEMP",
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
        for key, snapshots_by_model in by_key.items():
            group_name = str(key[0])
            required_models = CONSENSUS_GROUPS[group_name]
            participants = [snapshots_by_model.get(model_name) for model_name in required_models]
            if any(item is None for item in participants):
                continue
            agreed = [item for item in participants if item is not None]
            edge_values = [_float_or_none(item.get("selected_edge")) for item in agreed]
            fair_values = [_selected_fair(item) for item in agreed]
            newer = max(agreed, key=lambda item: (str(item.get("timestamp")), int(item.get("id", 0))))
            base = min(agreed, key=lambda item: int(item.get("id", 0)))
            rows.append(
                {
                    **base,
                    **_liquidity_fields(newer),
                    **_execution_mode_fields(newer),
                    **_hrrr_fields(newer),
                    "id": min(int(item["id"]) for item in agreed),
                    "timestamp": max(str(item.get("timestamp")) for item in agreed),
                    "model_name": group_name,
                    "selected_edge": _mean_present(edge_values),
                    "selected_fair": _mean_present(fair_values),
                    "source_prediction_snapshot_ids": [int(item["id"]) for item in agreed],
                    "raw_policy": {
                        "model_group": group_name,
                        "model_names": list(required_models),
                        "model_snapshot_ids": {str(item.get("model_name")): int(item["id"]) for item in agreed},
                        "model_edges": {str(item.get("model_name")): item.get("selected_edge") for item in agreed},
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
            market_family=candidate.get("market_family") or "HIGH_TEMP",
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
            **_hrrr_fields(candidate),
            **_liquidity_fields(candidate),
            **_execution_mode_fields(candidate),
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
    key = [item.get("station"), item.get("market_date"), item.get("market_family") or "HIGH_TEMP"]
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


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
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


HRRR_FIELD_NAMES: tuple[str, ...] = (
    "hrrr_remaining_max",
    "hrrr_current_temp",
    "hrrr_current_temp_minus_current_temp",
    "hrrr_remaining_max_minus_selected_lower",
    "hrrr_remaining_max_minus_selected_upper",
    "hrrr_remaining_min_minus_selected_lower",
    "hrrr_remaining_min_minus_selected_upper",
    "hrrr_temp_next_3h_max",
    "hrrr_temp_next_3h_mean",
    "hrrr_remaining_min",
    "hrrr_wind_speed_current",
    "hrrr_wind_speed_next_3h_mean",
    "hrrr_wind_speed_remaining_max",
    "hrrr_gust_remaining_max",
    "hrrr_cloud_cover_current",
    "hrrr_cloud_cover_next_3h_mean",
    "hrrr_cloud_cover_remaining_mean",
    "hrrr_cloud_cover_remaining_max",
    "hrrr_rh_current",
    "hrrr_rh_next_3h_mean",
    "hrrr_rh_remaining_mean",
    "hrrr_shortwave_next_3h_mean",
    "hrrr_shortwave_remaining_max",
)


def _hrrr_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {name: _float_or_none(item.get(name)) for name in HRRR_FIELD_NAMES}


def _liquidity_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_best_bid": _float_or_none(item.get("selected_best_bid")),
        "selected_best_ask": _float_or_none(item.get("selected_best_ask")),
        "selected_spread": _float_or_none(item.get("selected_spread")),
        "selected_depth_at_ask": _float_or_none(item.get("selected_depth_at_ask")),
        "selected_depth_ask_plus_0_01": _float_or_none(item.get("selected_depth_ask_plus_0_01")),
        "selected_depth_ask_plus_0_03": _float_or_none(item.get("selected_depth_ask_plus_0_03")),
        "selected_depth_ask_plus_0_05": _float_or_none(item.get("selected_depth_ask_plus_0_05")),
        "selected_book_timestamp": None if item.get("selected_book_timestamp") is None else str(item.get("selected_book_timestamp")),
        "selected_book_age_seconds": _float_or_none(item.get("selected_book_age_seconds")),
        "selected_liquidity": item.get("selected_liquidity"),
    }


def _execution_mode_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_ask_sweep": item.get("selected_ask_sweep"),
        "selected_bid_ladder": item.get("selected_bid_ladder"),
        "selected_sweep_price_cap": _float_or_none(item.get("selected_sweep_price_cap")),
        "selected_sweep_depth_to_cap": _float_or_none(item.get("selected_sweep_depth_to_cap")),
        "selected_sweep_fillable_25_usd": _float_or_none(item.get("selected_sweep_fillable_25_usd")),
        "selected_sweep_fillable_50_usd": _float_or_none(item.get("selected_sweep_fillable_50_usd")),
        "selected_sweep_fillable_100_usd": _float_or_none(item.get("selected_sweep_fillable_100_usd")),
        "selected_sweep_vwap_25": _float_or_none(item.get("selected_sweep_vwap_25")),
        "selected_sweep_vwap_50": _float_or_none(item.get("selected_sweep_vwap_50")),
        "selected_sweep_vwap_100": _float_or_none(item.get("selected_sweep_vwap_100")),
        "selected_bid_ladder_top_price": _float_or_none(item.get("selected_bid_ladder_top_price")),
        "selected_bid_ladder_low_price": _float_or_none(item.get("selected_bid_ladder_low_price")),
        "selected_bid_ladder_levels": _int_or_none(item.get("selected_bid_ladder_levels")),
        "selected_bid_ladder_total_notional_usd": _float_or_none(item.get("selected_bid_ladder_total_notional_usd")),
        "selected_bid_ladder_top_distance_from_ask": _float_or_none(item.get("selected_bid_ladder_top_distance_from_ask")),
        "selected_bid_ladder_top_improvement_over_best_bid": _float_or_none(item.get("selected_bid_ladder_top_improvement_over_best_bid")),
        "selected_bid_ladder_min_edge": _float_or_none(item.get("selected_bid_ladder_min_edge")),
        "selected_bid_ladder_max_edge": _float_or_none(item.get("selected_bid_ladder_max_edge")),
    }
