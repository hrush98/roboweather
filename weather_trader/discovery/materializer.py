from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from weather_trader.discovery.contracts import BroadDiscoveryRow, DiscoveryRunSpec
from weather_trader.pricing.contracts import stable_hash
from weather_trader.tape.replay import CausalBookProvider, sweep_asks


class BookProvider(Protocol):
    def book_at(
        self,
        token_id: str,
        ready: datetime,
        *,
        pre_signal_seconds: int,
    ) -> tuple[dict[str, Any] | None, str | None]: ...


def materialize_broad_discovery_view(
    research: sqlite3.Connection,
    tape: sqlite3.Connection,
    run: DiscoveryRunSpec,
    *,
    book_provider: BookProvider | None = None,
) -> tuple[list[BroadDiscoveryRow], dict[str, Any]]:
    """Build policy-neutral causal rows without writing either source database."""
    research.row_factory = sqlite3.Row
    tape.row_factory = sqlite3.Row
    source_rows = _load_source_rows(research, run)
    token_lookup = _token_lookup(tape, source_rows)
    target_tokens = {value for value in token_lookup.values() if value}
    provider = book_provider or CausalBookProvider(
        tape,
        target_tokens,
        allowed_session_ids=run.tape_session_ids,
        allowed_partition_ids=run.tape_partition_ids,
    )
    diagnostics: Counter[str] = Counter()
    output: list[BroadDiscoveryRow] = []

    for source in source_rows:
        row = dict(source)
        reasons = _source_reasons(row)
        token_id = token_lookup.get((str(row.get("selected_market_id")), _outcome(row)))
        if token_id is None:
            reasons.append("TOKEN_NOT_CATALOGED")
        snapshot_at = _timestamp(row.get("timestamp"))
        ready = snapshot_at + timedelta(milliseconds=run.latency_ms) if snapshot_at else None
        book: dict[str, Any] | None = None
        tape_reason: str | None = None
        if token_id is not None and ready is not None:
            book, tape_reason = provider.book_at(
                token_id,
                ready,
                pre_signal_seconds=run.pre_signal_seconds,
            )
        elif ready is None:
            tape_reason = "invalid_snapshot_timestamp"
        if tape_reason:
            reasons.append(f"TAPE:{tape_reason}")

        asks = tuple(sorted((float(p), float(s)) for p, s in (book or {}).get("asks", {}).items()))
        bids = tuple(sorted((float(p), float(s)) for p, s in (book or {}).get("bids", {}).items()))
        best_ask = asks[0][0] if asks else None
        best_bid = bids[-1][0] if bids else None
        if best_bid is not None and best_ask is not None and best_bid > best_ask:
            reasons.append("TAPE:CROSSED_BOOK")
        cost, shares, vwap = sweep_asks(
            asks,
            price_cap=0.50,
            target_cost=run.target_cost_usd,
        )
        if cost <= 0:
            reasons.append("TAPE:NO_ASKS_AT_OR_BELOW_DISCOVERY_CAP")

        research_label = _research_label(row)
        venue_label = _venue_label(row)
        if research_label is None and venue_label is None:
            reasons.append("MISSING_SETTLEMENT_LABEL")
        disagreement = (
            research_label != venue_label
            if research_label is not None and venue_label is not None
            else None
        )
        if disagreement:
            reasons.append("SETTLEMENT_DISAGREEMENT")

        row_id = f"p3d_row_{stable_hash({'snapshot_id': int(row['id']), 'token_id': token_id, 'latency_ms': run.latency_ms})[:24]}"
        built = BroadDiscoveryRow(
            row_id=row_id,
            row_hash="",
            discovery_run_id=run.run_id,
            build_hash=run.build_hash,
            source_prediction_snapshot_ids=(int(row["id"]),),
            source_snapshot_payload_hash=stable_hash(str(row.get("raw_json") or "")),
            snapshot_timestamp_utc=_iso(snapshot_at) if snapshot_at else str(row.get("timestamp")),
            decision_time_utc=str(row.get("decision_time_utc") or ""),
            quote_ready_timestamp_utc=_iso(ready) if ready else "",
            latest_observation_time_utc=str(row.get("latest_obs_time_utc") or ""),
            observation_age_minutes=_finite(row.get("obs_age_minutes")),
            station=str(row.get("station") or ""),
            market_date=str(row.get("market_date") or ""),
            market_family=str(row.get("market_family") or "HIGH_TEMP"),
            model_id=str(row.get("model_name") or ""),
            strategy_bucket=str(row.get("strategy_bucket") or ""),
            observation_delay_bucket=str(row.get("obs_delay_bucket") or ""),
            local_decision_hhmm=_local_hhmm(row.get("decision_time_local")),
            lifecycle_horizon=_lifecycle_horizon(row.get("decision_time_local"), row.get("market_date")),
            selected_market_id=str(row.get("selected_market_id") or ""),
            selected_bucket=str(row.get("selected_bucket") or ""),
            selected_side=str(row.get("selected_side") or ""),
            token_id=token_id,
            raw_model_fair=_selected_value(row, "selected_fair_yes", "selected_fair_no"),
            snapshot_entry_price=_selected_value(row, "selected_yes_ask", "selected_no_ask"),
            high_conviction=bool(row.get("high_conviction")),
            tape_eligible=book is not None and tape_reason is None and best_ask is not None,
            tape_ineligibility_reason=tape_reason,
            tape_session_id=str(book["session_id"]) if book and book.get("session_id") else None,
            coverage_interval_id=int(book["coverage_interval_id"]) if book and book.get("coverage_interval_id") is not None else None,
            reconstruction_hash=str(book["reconstruction_hash"]) if book and book.get("reconstruction_hash") else None,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=(best_ask - best_bid) if best_ask is not None and best_bid is not None else None,
            depth_at_best_ask=asks[0][1] if asks else None,
            ask_levels=asks,
            taker_cost_usd=cost,
            taker_shares=shares,
            taker_vwap=vwap,
            fill_fraction=min(cost / run.target_cost_usd, 1.0),
            research_outcome_label=research_label,
            research_outcome_source=str(row["outcome_source"]) if row.get("outcome_source") else None,
            venue_outcome_label=venue_label,
            venue_resolution_source=str(row["venue_source"]) if row.get("venue_source") else None,
            settlement_disagreement=disagreement,
            markouts_valid=False,
            markout_midpoints=(),
            actual_fill_status="UNAVAILABLE_PUBLIC_TAPE_COUNTERFACTUAL",
            discovery_eligible=not reasons,
            discovery_ineligibility_reasons=tuple(sorted(set(reasons))),
        ).with_hash()
        output.append(built)
        diagnostics["ELIGIBLE" if built.discovery_eligible else "INELIGIBLE"] += 1
        for reason in built.discovery_ineligibility_reasons:
            diagnostics[reason] += 1

    source_payload = [row.row_hash for row in output]
    return output, {
        "run_id": run.run_id,
        "source_rows": len(source_rows),
        "materialized_rows": len(output),
        "row_set_hash": stable_hash(source_payload),
        "counts": dict(sorted(diagnostics.items())),
    }


def write_broad_view(
    rows: list[BroadDiscoveryRow],
    diagnostics: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "broad_discovery_view.jsonl").write_text(
        "".join(json.dumps(row.canonical_payload(), sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output_dir / "broad_discovery_manifest.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_source_rows(connection: sqlite3.Connection, run: DiscoveryRunSpec) -> list[sqlite3.Row]:
    required = {
        "id", "timestamp", "station", "market_date", "decision_time_utc",
        "decision_time_local", "latest_obs_time_utc", "obs_age_minutes", "obs_delay_bucket",
        "strategy_bucket", "selected_market_id", "selected_bucket", "selected_side",
        "selected_fair_yes", "selected_fair_no", "selected_yes_ask", "selected_no_ask",
        "high_conviction", "model_name", "market_family", "raw_json",
    }
    columns = {str(row[1]) for row in connection.execute("pragma table_info(prediction_snapshots)")}
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"prediction_snapshots lacks Phase 3D columns: {', '.join(missing)}")
    has_resolutions = connection.execute(
        "select 1 from sqlite_master where type='table' and name='resolutions'"
    ).fetchone() is not None
    outcome_columns = {
        str(row[1]) for row in connection.execute("pragma table_info(station_date_outcomes)")
    }
    final_low_field = "sdo.final_low_tmpf" if "final_low_tmpf" in outcome_columns else "null"
    resolution_join = (
        "left join resolutions r on r.market_id=ps.selected_market_id and r.resolved_at<=?" if has_resolutions else ""
    )
    resolution_fields = (
        "r.winning_side venue_winning_side,r.source venue_source,r.resolved_at venue_resolved_at"
        if has_resolutions else
        "null venue_winning_side,null venue_source,null venue_resolved_at"
    )
    sql = f"""
        select ps.*,sdo.final_high_tmpf,{final_low_field} final_low_tmpf,sdo.source outcome_source,
               sdo.resolved_at outcome_resolved_at,{resolution_fields}
        from prediction_snapshots ps
        left join station_date_outcomes sdo
          on sdo.station=ps.station and sdo.market_date=ps.market_date
         and sdo.resolved_at<=?
        {resolution_join}
        where ps.id<=? and ps.market_date>=? and ps.market_date<?
          and ps.selected_market_id is not null and ps.selected_bucket is not null
        order by ps.timestamp,ps.id
    """
    params: list[Any] = [run.outcome_watermark]
    if has_resolutions:
        params.append(run.venue_settlement_watermark)
    params.extend((run.research_watermark, run.source_start_date, run.discovery_cutoff_exclusive))
    return list(connection.execute(sql, params))


def _token_lookup(
    tape: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> dict[tuple[str, str], str]:
    market_ids = sorted({str(row["selected_market_id"]) for row in rows if row["selected_market_id"]})
    lookup: dict[tuple[str, str], str] = {}
    for offset in range(0, len(market_ids), 800):
        chunk = market_ids[offset:offset + 800]
        placeholders = ",".join("?" for _ in chunk)
        for token in tape.execute(
            f"select market_id,outcome,token_id from tape_tokens where market_id in ({placeholders})",
            chunk,
        ):
            lookup[(str(token["market_id"]), str(token["outcome"]).upper())] = str(token["token_id"])
    return lookup


def _source_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    snapshot = _timestamp(row.get("timestamp"))
    decision = _timestamp(row.get("decision_time_utc"))
    observation = _timestamp(row.get("latest_obs_time_utc"))
    resolved = _timestamp(row.get("outcome_resolved_at"))
    if not snapshot or not decision or not observation or observation > decision or decision > snapshot:
        reasons.append("INVALID_CAUSAL_TIMESTAMP_ORDER")
    if resolved is not None and snapshot is not None and resolved <= snapshot:
        reasons.append("NONCAUSAL_OUTCOME_RESOLUTION")
    if _selected_value(row, "selected_fair_yes", "selected_fair_no") is None:
        reasons.append("MISSING_RAW_MODEL_FAIR")
    if str(row.get("selected_side")) not in {"BUY_YES", "BUY_NO"}:
        reasons.append("INVALID_SELECTED_SIDE")
    return reasons


def _research_label(row: dict[str, Any]) -> int | None:
    outcome_value = _finite(
        row.get("final_low_tmpf")
        if str(row.get("market_family")) == "LOW_TEMP"
        else row.get("final_high_tmpf")
    )
    if outcome_value is None:
        return None
    lower, upper = _parse_bucket(str(row.get("selected_bucket") or ""))
    if lower is None and upper is None:
        return None
    yes_won = (lower is None or outcome_value >= lower) and (upper is None or outcome_value <= upper)
    return int(yes_won if row.get("selected_side") == "BUY_YES" else not yes_won)


def _venue_label(row: dict[str, Any]) -> int | None:
    winning = str(row.get("venue_winning_side") or "").upper()
    selected = str(row.get("selected_side") or "")
    if winning not in {"YES", "NO", "BUY_YES", "BUY_NO"}:
        return None
    winning = winning.removeprefix("BUY_")
    return int(selected == f"BUY_{winning}")


def _parse_bucket(value: str) -> tuple[float | None, float | None]:
    text = value.strip().removesuffix("F").removesuffix("C")
    if text.startswith(">="):
        return _finite(text[2:]), None
    if text.startswith("<="):
        return None, _finite(text[2:])
    if "-" in text:
        lower, upper = text.split("-", 1)
        return _finite(lower), _finite(upper)
    return None, None


def _selected_value(row: dict[str, Any], yes: str, no: str) -> float | None:
    field = yes if row.get("selected_side") == "BUY_YES" else no if row.get("selected_side") == "BUY_NO" else ""
    return _probability(row.get(field)) if field else None


def _outcome(row: sqlite3.Row | dict[str, Any]) -> str:
    return "YES" if row["selected_side"] == "BUY_YES" else "NO"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _probability(value: Any) -> float | None:
    result = _finite(value)
    return result if result is not None and 0 <= result <= 1 else None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _local_hhmm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 and "T" in text else ""


def _lifecycle_horizon(decision_time_local: Any, market_date: Any) -> str:
    text = str(decision_time_local or "")
    try:
        local = datetime.fromisoformat(text)
    except ValueError:
        return "UNKNOWN"
    local_date = local.date().isoformat()
    target_date = str(market_date or "")
    if local_date < target_date:
        return "D_MINUS_1"
    if local.hour < 12:
        return "D0_EARLY"
    if local.hour < 16:
        return "D0_LATE"
    return "D0_CLOSE"
