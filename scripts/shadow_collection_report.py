#!/usr/bin/env python3
"""Report steady-state shadow quote collection health from the live ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weather_trader.config import DEFAULT_LIVE_DB
from weather_trader.execution.store import ExecutionStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_LIVE_DB, help="Live SQLite ledger path.")
    parser.add_argument("--since-timestamp", help="Optional ISO timestamp lower bound for candidate/quote health.")
    parser.add_argument("--candidate-id", help="Optional live_candidate_id to reconstruct.")
    args = parser.parse_args()

    store = ExecutionStore(args.db)
    try:
        health = store.shadow_collection_health(since_timestamp=args.since_timestamp)
        reconstruction = store.shadow_candidate_reconstruction(args.candidate_id)
    finally:
        store.close()
    print(render_report(args.db, health, reconstruction))
    return 0


def render_report(db: Path, health: dict[str, Any], reconstruction: dict[str, Any] | None) -> str:
    lines = [
        "# Shadow Collection Health",
        "",
        f"- DB: `{db}`",
        f"- Since: `{health.get('since_timestamp') or 'all'}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Policy candidates | {health['policy_candidates']} |",
        f"| Candidates with quotes | {health['candidates_with_quotes']} |",
        f"| Quote intents | {health['quote_intents']} |",
        f"| Unique quote specs | {health['unique_quote_specs']} |",
        f"| Would-post intents | {health['would_post_quote_intents']} |",
        f"| Candidates with token | {health['candidates_with_token']} |",
        f"| Candidates with book snapshots | {health['candidates_with_book_snapshots']} |",
        f"| Candidates with CLOB feed events | {health['candidates_with_feed_events']} |",
        "",
        "Quote states:",
    ]
    states = health.get("quote_states") or {}
    if states:
        for state, count in sorted(states.items()):
            lines.append(f"- `{state}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Reconstruction Sample", ""])
    if reconstruction is None:
        lines.append("No candidate with shadow quote intents was found.")
        return "\n".join(lines)

    candidate = reconstruction["candidate"]
    quotes = reconstruction["quote_intents"]
    spec_ids = {quote.get("quote_spec_id") for quote in quotes if quote.get("quote_spec_id")}
    state_counts: dict[str, int] = {}
    postable = 0
    skipped = 0
    for quote in quotes:
        state = str(quote.get("state") or "UNKNOWN")
        state_counts[state] = state_counts.get(state, 0) + 1
        if quote.get("would_post") == 1:
            postable += 1
        if quote.get("skip_reason"):
            skipped += 1
    quote_features = _json(candidate.get("quote_features_json"))
    lines.extend(
        [
            f"- Candidate: `{candidate['candidate_id']}`",
            f"- Station/date: `{candidate['station']}` `{candidate['market_date']}`",
            f"- Market/token: `{candidate['selected_market_id']}` `{candidate['selected_token_id']}`",
            f"- Side/bucket: `{candidate['selected_side']}` `{candidate['selected_bucket']}`",
            f"- Price sheets: {len(reconstruction['price_sheets'])}",
            f"- Quote intents/specs: {len(quotes)} intents / {len(spec_ids)} specs",
            f"- Would-post/skipped: {postable} / {skipped}",
            f"- Initial book: bid={quote_features.get('best_bid')} ask={quote_features.get('best_ask')} spread={quote_features.get('spread')}",
            f"- Book snapshots after quote: {len(reconstruction['book_snapshots'])}",
            f"- CLOB feed events after quote: {len(reconstruction['clob_feed_events'])}",
            f"- Markout status: `{reconstruction['markout_status']}`",
            f"- Markout windows: {', '.join(reconstruction['markout_windows']) or 'none'}",
        ]
    )
    lines.append("")
    lines.append("Sample quote states:")
    for state, count in sorted(state_counts.items()):
        lines.append(f"- `{state}`: {count}")
    return "\n".join(lines)


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
