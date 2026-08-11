from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from weather_trader.pricing.contracts import stable_hash
from weather_trader.tape.contracts import CoverageState
from weather_trader.tape.storage import iter_segment


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def apply_event(bids: dict[float, float], asks: dict[float, float], event: Any) -> bool:
    payload = event.raw_payload
    if event.event_type == "book":
        if not isinstance(payload, dict):
            return False
        replacement: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
        try:
            for key in ("bids", "asks"):
                for level in payload.get(key) or []:
                    price = float(level["price"])
                    size = float(level["size"])
                    if price <= 0 or size < 0:
                        return False
                    if size > 0:
                        replacement[key][price] = size
        except (KeyError, TypeError, ValueError):
            return False
        bids.clear()
        asks.clear()
        bids.update(replacement["bids"])
        asks.update(replacement["asks"])
        return True
    if event.coverage_state is not CoverageState.VALID:
        return False
    if event.event_type != "price_change":
        return True
    source = payload.get("price_change") if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        source = payload
    if not isinstance(source, dict):
        return False
    try:
        price = float(source["price"])
        size = float(source["size"])
        side = str(source["side"]).upper()
    except (KeyError, TypeError, ValueError):
        return False
    levels = bids if side == "BUY" else asks if side == "SELL" else None
    if levels is None or price <= 0 or size < 0:
        return False
    if size == 0:
        levels.pop(price, None)
    else:
        levels[price] = size
    return True


class CausalBookProvider:
    """Reconstruct quote-ready books while caching checksummed raw partitions."""

    def __init__(
        self,
        tape: sqlite3.Connection,
        target_tokens: Iterable[str],
        *,
        allowed_session_ids: Iterable[str] | None = None,
        allowed_partition_ids: Iterable[str] | None = None,
    ) -> None:
        self.tape = tape
        self.target_tokens = set(target_tokens)
        self.allowed_session_ids = set(allowed_session_ids or ())
        self.allowed_partition_ids = set(allowed_partition_ids or ())
        # Partition payloads are indexed once by token.  The previous cache kept
        # one flat list and every book lookup scanned events for every target
        # token, which made repeated historical reconstruction proportional to
        # partition size rather than the selected token's event stream.
        self.event_cache: OrderedDict[Path, dict[str, list[Any]]] = OrderedDict()
        self.max_cached_partitions = 2

    def book_at(
        self,
        token_id: str,
        ready: datetime,
        *,
        pre_signal_seconds: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        window_start = ready - timedelta(seconds=pre_signal_seconds)
        intervals = self.tape.execute(
            """
            select id, session_id, started_at_utc, ended_at_utc
            from tape_coverage_intervals
            where token_id=? and state='VALID' and started_at_utc<=?
              and (ended_at_utc is null or ended_at_utc>=?)
            order by started_at_utc desc
            """,
            (token_id, window_start.isoformat(), ready.isoformat()),
        ).fetchall()
        interval = next(
            (
                row for row in intervals
                if not self.allowed_session_ids or str(row["session_id"]) in self.allowed_session_ids
            ),
            None,
        )
        if interval is None:
            return None, "no_continuous_valid_interval"
        checkpoint = self.tape.execute(
            """
            select event_id, captured_at_utc, reconstruction_hash, raw_json
            from tape_book_checkpoints
            where token_id=? and session_id=? and coverage_state='VALID'
              and captured_at_utc<=? and captured_at_utc>=?
            order by captured_at_utc desc limit 1
            """,
            (token_id, interval["session_id"], ready.isoformat(), interval["started_at_utc"]),
        ).fetchone()
        if checkpoint is None:
            return None, "no_seed_checkpoint"
        try:
            seed = json.loads(checkpoint["raw_json"])
            bids = {float(price): float(size) for price, size in seed.get("bids") or []}
            asks = {float(price): float(size) for price, size in seed.get("asks") or []}
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "invalid_seed_checkpoint"

        checkpoint_time = utc(checkpoint["captured_at_utc"])
        partitions = self.tape.execute(
            """
            select partition_id, path, closed_at_utc
            from tape_raw_partitions
            where session_id=? and closed_at_utc>=? and path is not null
            order by partition_id
            """,
            (interval["session_id"], checkpoint["captured_at_utc"]),
        ).fetchall()
        reached_ready = checkpoint_time >= ready
        used_partition_ids: list[str] = []
        for partition in partitions:
            if self.allowed_partition_ids and str(partition["partition_id"]) not in self.allowed_partition_ids:
                continue
            path = Path(partition["path"])
            if not path.exists():
                return None, "missing_raw_partition"
            if path not in self.event_cache:
                by_token: dict[str, list[Any]] = defaultdict(list)
                for event in iter_segment(path):
                    if event.token_id in self.target_tokens:
                        by_token[event.token_id].append(event)
                self.event_cache[path] = dict(by_token)
                self.event_cache.move_to_end(path)
                while len(self.event_cache) > self.max_cached_partitions:
                    self.event_cache.popitem(last=False)
            else:
                self.event_cache.move_to_end(path)
            used_partition_ids.append(str(partition["partition_id"]))
            for event in self.event_cache[path].get(token_id, ()):
                event_time = utc(event.received_at_utc)
                if event_time <= checkpoint_time:
                    continue
                if event_time > ready:
                    reached_ready = True
                    break
                if not apply_event(bids, asks, event):
                    return None, "invalid_event_after_checkpoint"
            if utc(partition["closed_at_utc"]) >= ready:
                reached_ready = True
            if reached_ready:
                break
        if not reached_ready:
            return None, "raw_tape_does_not_reach_quote_ready"
        payload = {
            "token_id": token_id,
            "quote_ready": ready.isoformat(),
            "bids": sorted(bids.items()),
            "asks": sorted(asks.items()),
        }
        return {
            "bids": bids,
            "asks": asks,
            "checkpoint_age_s": (ready - checkpoint_time).total_seconds(),
            "session_id": str(interval["session_id"]),
            "coverage_interval_id": int(interval["id"]),
            "checkpoint_event_id": str(checkpoint["event_id"]),
            "checkpoint_captured_at_utc": str(checkpoint["captured_at_utc"]),
            "checkpoint_reconstruction_hash": str(checkpoint["reconstruction_hash"]),
            "partition_ids": tuple(used_partition_ids),
            "reconstruction_hash": stable_hash(payload),
        }, None


def sweep_asks(
    asks: dict[float, float] | Iterable[tuple[float, float]],
    *,
    price_cap: float,
    target_cost: float,
) -> tuple[float, float, float | None]:
    remaining = target_cost
    cost = 0.0
    shares = 0.0
    levels = asks.items() if isinstance(asks, dict) else asks
    for price, size in sorted(levels):
        if price > price_cap or remaining <= 1e-9:
            break
        if price <= 0 or size <= 0:
            continue
        take_cost = min(remaining, price * size)
        cost += take_cost
        shares += take_cost / price
        remaining -= take_cost
    return cost, shares, (cost / shares if shares else None)
