from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
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


@dataclass(frozen=True)
class ResearchPolicySpec:
    name: str
    source: str
    strategy_bucket: StrategyBucket | None = None
    model_name: str | None = None
    model_group: str | None = None
    obs_delay_bucket: str | None = None
    scope_by_strategy: bool = False


POLICIES: tuple[ResearchPolicySpec, ...] = (
    ResearchPolicySpec("pm_us12_consensus_hc_first", "consensus", StrategyBucket.HIGH_CONVICTION, model_group="pm_active_us12_dynamic_mvp"),
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
            rows.append(item)
        return sorted(rows, key=lambda item: (str(item.get("timestamp")), int(item.get("id", 0))))

    def _first_by_scope(self, policy: ResearchPolicySpec, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[object, ...]] = set()
        selected = []
        for item in candidates:
            key = (item.get("station"), item.get("market_date"))
            if policy.scope_by_strategy:
                key = (*key, item.get("strategy_bucket"))
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
        scope_key = "station_date"
        if policy.scope_by_strategy:
            scope_key = f"strategy:{strategy_bucket}"
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
            entry_fair=_float_or_none(candidate.get("selected_fair")) or _selected_fair(candidate),
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
                },
                **dict(candidate.get("raw_policy") or {}),
            },
        )


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
