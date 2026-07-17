from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from weather_trader.tape.contracts import DecisionTiming


def decision_timing_from_execution_quote(
    execution_db: Path,
    *,
    quote_id: str | None = None,
    activation_timestamp: str | None = None,
    hypothesis_version: str | None = None,
    latency_ms: int = 0,
) -> DecisionTiming:
    """Export one persisted price-sheet quote as an immutable tape decision.

    The source database is opened read-only. Observation availability is taken
    from the persisted source prediction snapshots; decision completion uses
    the quote-intent creation time; and termination uses an observed shadow
    cancellation time when present, otherwise the declared GTD expiry.
    """
    if latency_ms < 0:
        raise ValueError("latency_ms must be non-negative")
    path = execution_db.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("pragma query_only = ON")
        quote = _quote_row(connection, quote_id)
        candidate = connection.execute(
            "select * from live_candidate_snapshots where candidate_id = ?",
            (quote["live_candidate_id"],),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"quote candidate is missing: {quote['live_candidate_id']}")
        sheet = connection.execute(
            """
            select * from live_price_sheets
            where live_candidate_id = ? and version = ?
            order by id desc limit 1
            """,
            (quote["live_candidate_id"], quote["price_sheet_version"]),
        ).fetchone()
        if sheet is None:
            raise ValueError("quote price sheet is missing")
        snapshots = _source_snapshots(connection, candidate)
    finally:
        connection.close()

    quote_payload = _json_object(quote["raw_json"])
    sheet_payload = _json_object(sheet["raw_json"])
    activation = activation_timestamp or _find_timestamp(
        sheet_payload,
        keys=("activation_timestamp", "signal_activation_timestamp"),
    )
    if activation is None:
        raise ValueError(
            "activation_timestamp is required when the price-sheet record does not contain one"
        )
    source_ids = tuple(int(row["id"]) for row in snapshots)
    snapshot_ref = ",".join(str(item) for item in source_ids)
    source_observation = max((_utc(str(row["latest_obs_time_utc"])) for row in snapshots))
    observation_received = max((_utc(str(row["timestamp"])) for row in snapshots))
    decision_started = _utc(str(candidate["local_receipt_timestamp"]))
    decision_finished = _utc(str(quote["timestamp"]))
    termination = _quote_termination(quote, quote_payload)
    token_id = str(quote["selected_token_id"] or "")
    if not token_id:
        raise ValueError("quote has no selected_token_id")
    version = hypothesis_version or _derived_hypothesis_version(quote, sheet_payload)
    return DecisionTiming(
        decision_id=str(quote["quote_id"]),
        hypothesis_version=version,
        activation_timestamp=_utc(activation).isoformat(),
        token_id=token_id,
        observation_source_timestamp=source_observation.isoformat(),
        observation_received_at_utc=observation_received.isoformat(),
        decision_started_at_utc=decision_started.isoformat(),
        decision_finished_at_utc=decision_finished.isoformat(),
        latency_ms=latency_ms,
        quote_termination_at_utc=termination.isoformat(),
        source_type="execution_quote_intent",
        source_ref=f"{quote['quote_id']}:snapshots={snapshot_ref}",
    )


def _quote_row(connection: sqlite3.Connection, quote_id: str | None) -> sqlite3.Row:
    if quote_id is not None:
        row = connection.execute(
            "select * from live_quote_intents where quote_id = ?", (quote_id,)
        ).fetchone()
    else:
        row = connection.execute(
            """
            select * from live_quote_intents
            where selected_token_id is not null and coalesce(would_post, 0) = 1
            order by id desc limit 1
            """
        ).fetchone()
    if row is None:
        suffix = f": {quote_id}" if quote_id is not None else ""
        raise ValueError(f"no persisted postable quote intent found{suffix}")
    if row["would_post"] != 1:
        raise ValueError(f"quote was not postable: {row['quote_id']}")
    return row


def _source_snapshots(
    connection: sqlite3.Connection,
    candidate: sqlite3.Row,
) -> list[sqlite3.Row]:
    try:
        raw_ids = json.loads(str(candidate["source_prediction_snapshot_ids"] or "[]"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("candidate source_prediction_snapshot_ids is not valid JSON") from exc
    ids = {int(value) for value in raw_ids}
    if not ids and candidate["prediction_snapshot_id"] is not None:
        ids.add(int(candidate["prediction_snapshot_id"]))
    if not ids:
        raise ValueError("quote candidate has no source prediction snapshots")
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        select id, timestamp, latest_obs_time_utc
        from prediction_snapshots
        where id in ({placeholders})
        order by id
        """,
        tuple(sorted(ids)),
    ).fetchall()
    found = {int(row["id"]) for row in rows}
    if found != ids:
        missing = ",".join(str(item) for item in sorted(ids - found))
        raise ValueError(f"source prediction snapshots are missing: {missing}")
    return list(rows)


def _quote_termination(quote: sqlite3.Row, payload: dict[str, Any]) -> datetime:
    expiry = _utc(str(quote["gtd_expiry"]))
    shadow_cancel = payload.get("shadow_cancel")
    if isinstance(shadow_cancel, dict) and shadow_cancel.get("checked_at"):
        observed = _utc(str(shadow_cancel["checked_at"]))
        return min(expiry, observed)
    return expiry


def _derived_hypothesis_version(quote: sqlite3.Row, sheet_payload: dict[str, Any]) -> str:
    signal_spec = _find_text(sheet_payload, keys=("signal_spec_id",))
    parts = [str(quote["price_sheet_version"])]
    if signal_spec:
        parts.append(signal_spec)
    parts.append(str(quote["quote_spec_id"] or "unversioned_quote_arm"))
    return ":".join(parts)


def _find_timestamp(payload: dict[str, Any], *, keys: tuple[str, ...]) -> str | None:
    value = _find_text(payload, keys=keys)
    if value is not None:
        _utc(value)
    return value


def _find_text(value: Any, *, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate is not None and str(candidate):
                return str(candidate)
        for child in value.values():
            found = _find_text(child, keys=keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_text(child, keys=keys)
            if found is not None:
                return found
    return None


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("source raw_json is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("source raw_json must be an object")
    return parsed


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)
