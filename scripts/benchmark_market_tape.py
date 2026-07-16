#!/usr/bin/env python3
"""Benchmark the Phase 3 append-only segment format on captured feed messages."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_trader.tape.contracts import MarketTapeEvent
from weather_trader.tape.storage import RawSegmentWriter, iter_segment


def load_payloads(path: Path) -> list[dict[str, Any] | list[Any]]:
    payloads: list[dict[str, Any] | list[Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, (dict, list)):
                raise ValueError(f"line {line_number} is not a JSON object or array")
            payloads.append(value)
    if not payloads:
        raise ValueError("input contains no feed messages")
    return payloads


def benchmark(payloads: list[dict[str, Any] | list[Any]], output_dir: Path) -> dict[str, Any]:
    latencies_ms: list[float] = []
    started = time.perf_counter()
    writer = RawSegmentWriter(output_dir, session_id="benchmark", partition_id="sample")
    for sequence, payload in enumerate(payloads, start=1):
        event = MarketTapeEvent(
            collector_session_id="benchmark",
            token_id=_field(payload, "asset_id", "token_id") or "unknown-token",
            market_id=_field(payload, "market", "condition_id") or "unknown-market",
            event_type=_field(payload, "event_type", "type") or "unknown",
            raw_payload=payload,
            received_at_utc="1970-01-01T00:00:00+00:00",
            received_monotonic_ns=sequence,
            receipt_sequence=sequence,
            subscription_generation=1,
        )
        before = time.perf_counter()
        writer.append(event)
        latencies_ms.append((time.perf_counter() - before) * 1000)
    stats = writer.close()
    write_seconds = time.perf_counter() - started

    replay_started = time.perf_counter()
    replayed = list(iter_segment(stats.path))
    replay_seconds = time.perf_counter() - replay_started
    if [event.raw_payload for event in replayed] != payloads:
        raise RuntimeError("raw payload round-trip mismatch")

    raw = stats.path.read_bytes()
    compressed = gzip.compress(raw, compresslevel=6)
    return {
        "messages": len(payloads),
        "raw_bytes": len(raw),
        "raw_bytes_per_message": len(raw) / len(payloads),
        "gzip_bytes": len(compressed),
        "gzip_ratio": len(compressed) / len(raw),
        "write_messages_per_second": len(payloads) / write_seconds,
        "write_latency_ms_p50": statistics.median(latencies_ms),
        "write_latency_ms_max": max(latencies_ms),
        "replay_messages_per_second": len(payloads) / replay_seconds,
        "round_trip_exact": True,
    }


def _field(payload: dict[str, Any] | list[Any], *names: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for name in names:
        if payload.get(name) is not None:
            return str(payload[name])
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL file containing raw representative WebSocket messages")
    parser.add_argument("--output-dir", type=Path, help="Keep the generated active segment in this directory")
    args = parser.parse_args()
    payloads = load_payloads(args.input)
    if args.output_dir:
        result = benchmark(payloads, args.output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="roboweather-tape-benchmark-") as temp_dir:
            result = benchmark(payloads, Path(temp_dir))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
