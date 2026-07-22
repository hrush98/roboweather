from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_trader.execution.contracts import MarketFamily, StrategyBucket, TradeAction
from weather_trader.pricing.contracts import (
    V2A_DATASET_VERSION,
    DatasetRole,
    MarketReferenceKind,
    OutcomeLabelSource,
    SignalSpec,
    stable_hash,
)


FIT_STRATEGY_BUCKETS = frozenset(
    {str(StrategyBucket.HIGH_CONVICTION), str(StrategyBucket.BEST_BUCKET), str(StrategyBucket.TAIL)}
)
FIT_SELECTED_SIDES = frozenset({str(TradeAction.BUY_YES), str(TradeAction.BUY_NO)})


@dataclass(frozen=True)
class V2ADatasetRow:
    dataset_version: str
    dataset_role: DatasetRole
    signal_spec_id: str
    signal_spec_hash: str
    decision_id: str
    source_prediction_snapshot_ids: tuple[int, ...]
    source_snapshot_timestamp_utc: str
    decision_time_utc: str
    decision_time_local: str
    quote_ready_time_utc: str
    latest_observation_time_utc: str
    station: str
    market_date: str
    market_family: str
    lifecycle_horizon: str
    model_id: str
    strategy_bucket: str
    observation_delay_bucket: str
    selected_market_id: str
    selected_bucket: str
    selected_side: str
    raw_model_fair: float
    selected_entry_price: float | None
    selected_edge: float | None
    market_reference: float | None
    market_reference_kind: MarketReferenceKind
    market_reference_timestamp_utc: str | None
    market_reference_age_seconds: float | None
    market_reference_stale: bool
    outcome_label: int
    outcome_label_source: OutcomeLabelSource
    final_high_tmpf: float
    outcome_source: str
    outcome_resolved_at_utc: str
    venue_resolution_source: str | None
    is_forward: bool
    station_date_cluster_weight: float
    market_date_cluster_weight: float
    quality_flags: tuple[str, ...]
    row_hash: str

    def canonical_payload(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        payload["dataset_role"] = self.dataset_role.value
        payload["market_reference_kind"] = self.market_reference_kind.value
        payload["outcome_label_source"] = self.outcome_label_source.value
        payload["source_prediction_snapshot_ids"] = list(self.source_prediction_snapshot_ids)
        payload["quality_flags"] = list(self.quality_flags)
        if not include_hash:
            payload.pop("row_hash", None)
        return payload


@dataclass(frozen=True)
class V2ADatasetArtifact:
    dataset_version: str
    signal_spec_id: str
    signal_spec_hash: str
    fit_cutoff_date_exclusive: str | None
    evaluation_start_date: str
    evaluation_end_date: str | None
    fit_rows: tuple[V2ADatasetRow, ...]
    evaluation_rows: tuple[V2ADatasetRow, ...]
    diagnostics: dict[str, Any]

    def manifest(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "signal_spec_id": self.signal_spec_id,
            "signal_spec_hash": self.signal_spec_hash,
            "fit_cutoff_date_exclusive": self.fit_cutoff_date_exclusive,
            "evaluation_start_date": self.evaluation_start_date,
            "evaluation_end_date": self.evaluation_end_date,
            "fit_rows": len(self.fit_rows),
            "evaluation_rows": len(self.evaluation_rows),
            "fit_corpus_rule": (
                "same model/family/stations/late window; BUY_YES or BUY_NO; "
                "HIGH_CONVICTION, BEST_BUCKET, or TAIL; resolved selected token"
            ),
            "evaluation_corpus_rule": "exact frozen signal spec followed by declared first-entry dedupe",
            "diagnostics": self.diagnostics,
        }


def materialize_v2a_dataset(
    connection: sqlite3.Connection,
    signal_spec: SignalSpec,
    *,
    fit_cutoff_date_exclusive: str | None = None,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
) -> V2ADatasetArtifact:
    connection.row_factory = sqlite3.Row
    start_date = evaluation_start_date or signal_spec.forward_start_date
    fit_cutoff = fit_cutoff_date_exclusive or start_date
    _validate_date_bounds(fit_cutoff, start_date, evaluation_end_date)
    source_rows = _load_source_rows(connection, signal_spec)
    diagnostics: Counter[str] = Counter()

    fit_candidates = []
    evaluation_candidates = []
    for raw in source_rows:
        row = dict(raw)
        if _matches_fit_corpus(row, signal_spec):
            if str(row["market_date"]) < fit_cutoff:
                fit_candidates.append(row)
        if _matches_frozen_signal(row, signal_spec):
            market_date = str(row["market_date"])
            if market_date >= start_date and (evaluation_end_date is None or market_date <= evaluation_end_date):
                evaluation_candidates.append(row)

    evaluation_candidates = _first_by_declared_scope(evaluation_candidates, signal_spec)
    fit_rows = _build_rows(fit_candidates, signal_spec, DatasetRole.CALIBRATION_FIT, diagnostics)
    evaluation_rows = _build_rows(
        evaluation_candidates,
        signal_spec,
        DatasetRole.FROZEN_POLICY_EVALUATION,
        diagnostics,
    )
    fit_rows = _apply_cluster_weights(fit_rows)
    evaluation_rows = _apply_cluster_weights(evaluation_rows)

    diagnostics_payload: dict[str, Any] = {
        "source_rows": len(source_rows),
        "fit_candidates": len(fit_candidates),
        "evaluation_candidates_after_dedupe": len(evaluation_candidates),
        "fit_rows": len(fit_rows),
        "evaluation_rows": len(evaluation_rows),
        "counts": dict(sorted(diagnostics.items())),
        "fit_market_dates": _date_range(fit_rows),
        "evaluation_market_dates": _date_range(evaluation_rows),
        "outcome_sources": _value_counts((*fit_rows, *evaluation_rows), "outcome_source"),
        "market_reference_kinds": _value_counts((*fit_rows, *evaluation_rows), "market_reference_kind"),
    }
    return V2ADatasetArtifact(
        dataset_version=V2A_DATASET_VERSION,
        signal_spec_id=signal_spec.signal_spec_id,
        signal_spec_hash=signal_spec.spec_hash,
        fit_cutoff_date_exclusive=fit_cutoff,
        evaluation_start_date=start_date,
        evaluation_end_date=evaluation_end_date,
        fit_rows=tuple(fit_rows),
        evaluation_rows=tuple(evaluation_rows),
        diagnostics=diagnostics_payload,
    )


def write_v2a_dataset_artifact(artifact: V2ADatasetArtifact, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "manifest.json", artifact.manifest())
    _write_jsonl(output_dir / "calibration_fit.jsonl", artifact.fit_rows)
    _write_jsonl(output_dir / "frozen_policy_evaluation.jsonl", artifact.evaluation_rows)


def _load_source_rows(connection: sqlite3.Connection, signal_spec: SignalSpec) -> list[sqlite3.Row]:
    snapshot_columns = _table_columns(connection, "prediction_snapshots")
    required = {
        "id",
        "timestamp",
        "station",
        "market_date",
        "decision_time_utc",
        "decision_time_local",
        "latest_obs_time_utc",
        "obs_delay_bucket",
        "strategy_bucket",
        "selected_market_id",
        "selected_bucket",
        "selected_side",
        "selected_fair_yes",
        "selected_fair_no",
        "selected_yes_ask",
        "selected_no_ask",
        "model_name",
    }
    missing = sorted(required - snapshot_columns)
    if missing:
        raise ValueError(f"prediction_snapshots lacks required V2a columns: {', '.join(missing)}")
    if not _table_exists(connection, "station_date_outcomes"):
        raise ValueError("station_date_outcomes is required for V2a materialization")

    def optional(name: str, fallback: str = "null") -> str:
        return f"ps.{name}" if name in snapshot_columns else fallback

    outcome_columns = _table_columns(connection, "station_date_outcomes")
    outcome_source = "sdo.source" if "source" in outcome_columns else "'UNKNOWN'"
    outcome_resolved_at = "sdo.resolved_at" if "resolved_at" in outcome_columns else "sdo.timestamp"
    market_join = ""
    venue_resolution = "null"
    if _table_exists(connection, "markets"):
        market_columns = _table_columns(connection, "markets")
        if "resolution_source" in market_columns:
            venue_resolution = "m.resolution_source"
        market_join = "left join markets m on m.market_id = ps.selected_market_id"

    model_placeholders = ",".join("?" for _ in signal_spec.model_ids)
    station_placeholders = ",".join("?" for _ in signal_spec.station_allowlist)
    sql = f"""
        select
            ps.id,
            ps.timestamp,
            ps.station,
            ps.market_date,
            ps.decision_time_utc,
            ps.decision_time_local,
            ps.latest_obs_time_utc,
            ps.obs_delay_bucket,
            ps.strategy_bucket,
            ps.selected_market_id,
            ps.selected_bucket,
            ps.selected_side,
            {optional('selected_edge')} as selected_edge,
            ps.selected_fair_yes,
            ps.selected_fair_no,
            ps.selected_yes_ask,
            ps.selected_no_ask,
            ps.model_name,
            {optional('market_family', "'HIGH_TEMP'")} as market_family,
            {optional('selected_best_bid')} as selected_best_bid,
            {optional('selected_best_ask')} as selected_best_ask,
            {optional('selected_book_timestamp')} as selected_book_timestamp,
            {optional('selected_book_age_seconds')} as selected_book_age_seconds,
            sdo.final_high_tmpf,
            {outcome_source} as outcome_source,
            {outcome_resolved_at} as outcome_resolved_at,
            {venue_resolution} as venue_resolution_source
        from prediction_snapshots ps
        left join station_date_outcomes sdo
          on sdo.station = ps.station and sdo.market_date = ps.market_date
        {market_join}
        where ps.model_name in ({model_placeholders})
          and ps.station in ({station_placeholders})
          and ps.market_date >= ?
        order by ps.timestamp, ps.id
    """
    params = [*signal_spec.model_ids, *signal_spec.station_allowlist, signal_spec.retrospective_start_date]
    return list(connection.execute(sql, params).fetchall())


def _matches_fit_corpus(row: dict[str, Any], signal_spec: SignalSpec) -> bool:
    if str(row.get("market_family") or MarketFamily.HIGH_TEMP) != str(signal_spec.market_family):
        return False
    if str(row.get("selected_side")) not in FIT_SELECTED_SIDES:
        return False
    if str(row.get("strategy_bucket")) not in FIT_STRATEGY_BUCKETS:
        return False
    local = _local_hhmm(row.get("decision_time_local"))
    return signal_spec.local_decision_start <= local < signal_spec.local_decision_end


def _matches_frozen_signal(row: dict[str, Any], signal_spec: SignalSpec) -> bool:
    if str(row.get("model_name")) not in signal_spec.model_ids:
        return False
    if str(row.get("market_family") or MarketFamily.HIGH_TEMP) != str(signal_spec.market_family):
        return False
    if str(row.get("station")) not in signal_spec.station_allowlist:
        return False
    if str(row.get("selected_side")) not in {str(value) for value in signal_spec.selected_sides}:
        return False
    if str(row.get("strategy_bucket")) not in {str(value) for value in signal_spec.strategy_buckets}:
        return False
    if signal_spec.observation_delay_buckets and str(row.get("obs_delay_bucket")) not in signal_spec.observation_delay_buckets:
        return False
    local = _local_hhmm(row.get("decision_time_local"))
    if not signal_spec.local_decision_start <= local < signal_spec.local_decision_end:
        return False
    entry = _selected_value(row, "selected_yes_ask", "selected_no_ask")
    return entry is not None and signal_spec.entry_price_min <= entry <= signal_spec.entry_price_max


def _first_by_declared_scope(rows: list[dict[str, Any]], signal_spec: SignalSpec) -> list[dict[str, Any]]:
    selected = []
    seen: set[tuple[Any, ...]] = set()
    for row in sorted(rows, key=lambda item: (str(item.get("timestamp")), int(item.get("id") or 0))):
        key = tuple(row.get(field) or (str(signal_spec.market_family) if field == "market_family" else None) for field in signal_spec.dedupe_key_fields)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def _build_rows(
    candidates: list[dict[str, Any]],
    signal_spec: SignalSpec,
    role: DatasetRole,
    diagnostics: Counter[str],
) -> list[V2ADatasetRow]:
    output = []
    for candidate in candidates:
        try:
            row = _build_row(candidate, signal_spec, role)
        except ValueError as exc:
            diagnostics[f"excluded:{exc}"] += 1
            continue
        diagnostics[f"included:{role.value}"] += 1
        for flag in row.quality_flags:
            diagnostics[f"quality:{flag}"] += 1
        output.append(row)
    return output


def _build_row(row: dict[str, Any], signal_spec: SignalSpec, role: DatasetRole) -> V2ADatasetRow:
    snapshot_at = _parse_timestamp(row.get("timestamp"), "snapshot_timestamp")
    decision_at = _parse_timestamp(row.get("decision_time_utc"), "decision_time")
    observation_at = _parse_timestamp(row.get("latest_obs_time_utc"), "latest_observation_time")
    final_high = _finite_float(row.get("final_high_tmpf"))
    if final_high is None or not row.get("outcome_resolved_at"):
        raise ValueError("MISSING_OUTCOME_LABEL")
    resolved_at = _parse_timestamp(row.get("outcome_resolved_at"), "outcome_resolved_at")
    if observation_at > decision_at or decision_at > snapshot_at or resolved_at <= decision_at:
        raise ValueError("INVALID_TIMESTAMP_ORDER")
    if not row.get("selected_market_id") or not row.get("selected_bucket"):
        raise ValueError("MISSING_SELECTED_MARKET_OR_BUCKET")

    raw_fair = _selected_value(row, "selected_fair_yes", "selected_fair_no")
    if raw_fair is None or not 0.0 < raw_fair < 1.0:
        raise ValueError("MISSING_OR_INVALID_RAW_FAIR")
    entry_price = _selected_value(row, "selected_yes_ask", "selected_no_ask")
    label = _selected_token_won(final_high, str(row["selected_bucket"]), str(row["selected_side"]))

    reference, reference_kind, reference_at, reference_age, stale, reference_flags = _market_reference(
        row,
        quote_ready_at=snapshot_at,
        max_age_seconds=signal_spec.max_market_reference_age_seconds,
    )
    flags = list(reference_flags)
    if signal_spec.outcome_label_source == OutcomeLabelSource.IEM_ASOS_RESEARCH_HIGH:
        flags.append("OUTCOME_SOURCE_NOT_VENUE_ALIGNED")
    activation_at = _parse_timestamp(signal_spec.activation_timestamp, "activation_timestamp")
    source_ids = (int(row["id"]),)
    built = V2ADatasetRow(
        dataset_version=V2A_DATASET_VERSION,
        dataset_role=role,
        signal_spec_id=signal_spec.signal_spec_id,
        signal_spec_hash=signal_spec.spec_hash,
        decision_id=signal_spec.decision_id(row),
        source_prediction_snapshot_ids=source_ids,
        source_snapshot_timestamp_utc=_iso_utc(snapshot_at),
        decision_time_utc=_iso_utc(decision_at),
        decision_time_local=str(row["decision_time_local"]),
        quote_ready_time_utc=_iso_utc(snapshot_at),
        latest_observation_time_utc=_iso_utc(observation_at),
        station=str(row["station"]),
        market_date=str(row["market_date"]),
        market_family=str(row.get("market_family") or MarketFamily.HIGH_TEMP),
        lifecycle_horizon=signal_spec.lifecycle_horizon,
        model_id=str(row["model_name"]),
        strategy_bucket=str(row["strategy_bucket"]),
        observation_delay_bucket=str(row["obs_delay_bucket"]),
        selected_market_id=str(row["selected_market_id"]),
        selected_bucket=str(row["selected_bucket"]),
        selected_side=str(row["selected_side"]),
        raw_model_fair=raw_fair,
        selected_entry_price=entry_price,
        selected_edge=_finite_float(row.get("selected_edge")),
        market_reference=reference,
        market_reference_kind=reference_kind,
        market_reference_timestamp_utc=reference_at,
        market_reference_age_seconds=reference_age,
        market_reference_stale=stale,
        outcome_label=label,
        outcome_label_source=signal_spec.outcome_label_source,
        final_high_tmpf=final_high,
        outcome_source=str(row.get("outcome_source") or "UNKNOWN"),
        outcome_resolved_at_utc=_iso_utc(resolved_at),
        venue_resolution_source=str(row["venue_resolution_source"]) if row.get("venue_resolution_source") else None,
        is_forward=snapshot_at >= activation_at,
        station_date_cluster_weight=0.0,
        market_date_cluster_weight=0.0,
        quality_flags=tuple(sorted(set(flags))),
        row_hash="",
    )
    return replace(built, row_hash=stable_hash(built.canonical_payload(include_hash=False)))


def _market_reference(
    row: dict[str, Any],
    *,
    quote_ready_at: datetime,
    max_age_seconds: float,
) -> tuple[float | None, MarketReferenceKind, str | None, float | None, bool, tuple[str, ...]]:
    flags: list[str] = []
    book_timestamp = None
    raw_book_timestamp = row.get("selected_book_timestamp")
    if raw_book_timestamp:
        try:
            book_timestamp = _parse_timestamp(raw_book_timestamp, "market_reference_timestamp")
        except ValueError:
            flags.append("UNPARSEABLE_MARKET_REFERENCE_TIMESTAMP")
    explicit_age = _finite_float(row.get("selected_book_age_seconds"))
    age = explicit_age
    if age is None and book_timestamp is not None:
        age = (quote_ready_at - book_timestamp).total_seconds()
    if book_timestamp is not None and book_timestamp > quote_ready_at:
        flags.append("FUTURE_MARKET_REFERENCE")
        return None, MarketReferenceKind.MISSING, _iso_utc(book_timestamp), age, False, tuple(flags)
    if age is not None and age < 0:
        flags.append("NEGATIVE_MARKET_REFERENCE_AGE")
        return None, MarketReferenceKind.MISSING, _iso_utc(book_timestamp) if book_timestamp else None, age, False, tuple(flags)
    stale = age is not None and age > max_age_seconds
    if stale:
        flags.append("STALE_MARKET_REFERENCE")
        return None, MarketReferenceKind.MISSING, _iso_utc(book_timestamp) if book_timestamp else None, age, True, tuple(flags)

    bid = _probability(row.get("selected_best_bid"))
    ask = _probability(row.get("selected_best_ask"))
    if bid is not None and ask is not None:
        if bid > ask:
            flags.append("CROSSED_MARKET_REFERENCE")
            return None, MarketReferenceKind.MISSING, _iso_utc(book_timestamp) if book_timestamp else None, age, False, tuple(flags)
        return round((bid + ask) / 2.0, 6), MarketReferenceKind.MIDPOINT, _iso_utc(book_timestamp) if book_timestamp else None, age, False, tuple(flags)
    if ask is None:
        ask = _selected_value(row, "selected_yes_ask", "selected_no_ask")
        if ask is not None:
            flags.append("MARKET_REFERENCE_SELECTED_ASK_FALLBACK")
    if ask is not None:
        return ask, MarketReferenceKind.SAME_SIDE_ASK, _iso_utc(book_timestamp) if book_timestamp else None, age, False, tuple(flags)
    flags.append("MISSING_MARKET_REFERENCE")
    return None, MarketReferenceKind.MISSING, _iso_utc(book_timestamp) if book_timestamp else None, age, False, tuple(flags)


def _apply_cluster_weights(rows: list[V2ADatasetRow]) -> list[V2ADatasetRow]:
    station_date_counts = Counter((row.market_date, row.station) for row in rows)
    market_date_station_dates: dict[str, set[str]] = {}
    for row in rows:
        market_date_station_dates.setdefault(row.market_date, set()).add(row.station)
    weighted = []
    for row in rows:
        station_count = station_date_counts[(row.market_date, row.station)]
        clusters_on_date = len(market_date_station_dates[row.market_date])
        station_weight = 1.0 / station_count
        market_weight = station_weight / clusters_on_date
        updated = replace(
            row,
            station_date_cluster_weight=station_weight,
            market_date_cluster_weight=market_weight,
            row_hash="",
        )
        weighted.append(replace(updated, row_hash=stable_hash(updated.canonical_payload(include_hash=False))))
    return weighted


def _selected_token_won(final_high: float, bucket: str, side: str) -> int:
    lower, upper = _parse_bucket(bucket)
    if lower is None and upper is None:
        raise ValueError("UNPARSEABLE_BUCKET")
    yes_won = (lower is None or final_high >= lower) and (upper is None or final_high <= upper)
    if side == str(TradeAction.BUY_YES):
        return int(yes_won)
    if side == str(TradeAction.BUY_NO):
        return int(not yes_won)
    raise ValueError("INVALID_SELECTED_SIDE")


def _parse_bucket(value: str) -> tuple[float | None, float | None]:
    text = value.strip().removesuffix("F").removesuffix("C")
    if text.startswith(">="):
        return _finite_float(text[2:]), None
    if text.startswith("<="):
        return None, _finite_float(text[2:])
    if "-" in text:
        lower, upper = text.split("-", 1)
        return _finite_float(lower), _finite_float(upper)
    return None, None


def _selected_value(row: dict[str, Any], yes_field: str, no_field: str) -> float | None:
    side = str(row.get("selected_side"))
    if side == str(TradeAction.BUY_YES):
        return _probability(row.get(yes_field))
    if side == str(TradeAction.BUY_NO):
        return _probability(row.get(no_field))
    return None


def _probability(value: Any) -> float | None:
    result = _finite_float(value)
    return result if result is not None and 0.0 <= result <= 1.0 else None


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_{label.upper()}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"INVALID_{label.upper()}")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _local_hhmm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 and "T" in text else ""


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("select 1 from sqlite_master where type = 'table' and name = ?", (name,)).fetchone() is not None


def _table_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"pragma table_info({name})").fetchall()}


def _validate_date_bounds(cutoff: str | None, start: str, end: str | None) -> None:
    if cutoff:
        date.fromisoformat(cutoff)
        if cutoff > start:
            raise ValueError("fit cutoff must not follow evaluation start date")
    date.fromisoformat(start)
    if end:
        date.fromisoformat(end)
        if end < start:
            raise ValueError("evaluation end date precedes start date")


def _date_range(rows: Iterable[V2ADatasetRow]) -> dict[str, str | None]:
    values = sorted({row.market_date for row in rows})
    return {"first": values[0] if values else None, "last": values[-1] if values else None}


def _value_counts(rows: Iterable[V2ADatasetRow], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        value = getattr(row, field)
        counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[V2ADatasetRow]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.canonical_payload(), sort_keys=True, separators=(",", ":")) + "\n")
