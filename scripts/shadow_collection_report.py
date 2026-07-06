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
    parser.add_argument("--no-fail", action="store_true", help="Render failures but exit 0.")
    args = parser.parse_args()

    store = ExecutionStore(args.db)
    try:
        health = store.shadow_collection_health(since_timestamp=args.since_timestamp)
        reconstruction = store.shadow_candidate_reconstruction(args.candidate_id)
    finally:
        store.close()
    failures = milestone_failures(health, reconstruction)
    print(render_report(args.db, health, reconstruction, failures=failures))
    return 0 if args.no_fail or not failures else 1


def render_report(db: Path, health: dict[str, Any], reconstruction: dict[str, Any] | None, *, failures: list[str] | None = None) -> str:
    failures = failures or []
    lines = [
        "# Shadow Collection Health",
        "",
        f"- DB: `{db}`",
        f"- Since: `{health.get('since_timestamp') or 'all'}`",
        f"- Milestone status: `{'FAIL' if failures else 'PASS'}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Policy candidates | {health['policy_candidates']} |",
        f"| Candidates with quotes | {health['candidates_with_quotes']} |",
        f"| Quote intents | {health['quote_intents']} |",
        f"| Unique quote specs | {health['unique_quote_specs']} |",
        f"| Would-post intents | {health['would_post_quote_intents']} |",
        f"| $50 quote intents | {health['useful_50_quote_intents']} |",
        f"| $100 quote intents | {health['useful_100_quote_intents']} |",
        f"| Candidates with $50 quotes | {health['candidates_with_50_quotes']} |",
        f"| Candidates with $100 quotes | {health['candidates_with_100_quotes']} |",
        f"| Max intended quote size | {health['max_quote_size_usd']} |",
        f"| Candidates with token | {health['candidates_with_token']} |",
        f"| Candidates with book snapshots | {health['candidates_with_book_snapshots']} |",
        f"| Candidates with CLOB feed events | {health['candidates_with_feed_events']} |",
        f"| Shadow outcomes | {health['shadow_outcomes']} |",
        f"| $50 shadow outcomes | {health['useful_50_outcomes']} |",
        f"| $100 shadow outcomes | {health['useful_100_outcomes']} |",
        "",
        "Milestone failures:",
    ]
    if failures:
        lines.extend(f"- `{failure}`" for failure in failures)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "Quote states:",
    ])
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
    notional_counts = {"50": 0, "100": 0}
    for quote in quotes:
        state = str(quote.get("state") or "UNKNOWN")
        state_counts[state] = state_counts.get(state, 0) + 1
        if quote.get("would_post") == 1:
            postable += 1
        if quote.get("skip_reason"):
            skipped += 1
        size = _float_or_none(quote.get("quote_size_usd")) or 0.0
        if size >= 50.0:
            notional_counts["50"] += 1
        if size >= 100.0:
            notional_counts["100"] += 1
    outcomes = reconstruction.get("shadow_outcomes") or []
    quote_features = _json(candidate.get("quote_features_json"))
    lines.extend(
        [
            f"- Candidate: `{candidate['candidate_id']}`",
            f"- Station/date: `{candidate['station']}` `{candidate['market_date']}`",
            f"- Market/token: `{candidate['selected_market_id']}` `{candidate['selected_token_id']}`",
            f"- Side/bucket: `{candidate['selected_side']}` `{candidate['selected_bucket']}`",
            f"- Price sheets: {len(reconstruction['price_sheets'])}",
            f"- Quote intents/specs: {len(quotes)} intents / {len(spec_ids)} specs",
            f"- Useful-size intents: {notional_counts['50']} at >=$50 / {notional_counts['100']} at >=$100",
            f"- Would-post/skipped: {postable} / {skipped}",
            f"- Initial book: bid={quote_features.get('best_bid')} ask={quote_features.get('best_ask')} spread={quote_features.get('spread')}",
            f"- Book snapshots after quote: {len(reconstruction['book_snapshots'])}",
            f"- CLOB feed events after quote: {len(reconstruction['clob_feed_events'])}",
            f"- Shadow outcomes: {len(outcomes)}",
            f"- Markout status: `{reconstruction['markout_status']}`",
            f"- Markout windows: {', '.join(reconstruction['markout_windows']) or 'none'}",
        ]
    )
    lines.append("")
    lines.append("Sample quote states:")
    for state, count in sorted(state_counts.items()):
        lines.append(f"- `{state}`: {count}")
    return "\n".join(lines)


def milestone_failures(health: dict[str, Any], reconstruction: dict[str, Any] | None) -> list[str]:
    failures: list[str] = []
    policy_candidates = int(health.get("policy_candidates") or 0)
    if policy_candidates <= 0:
        failures.append("NO_POLICY_CANDIDATES")
    if int(health.get("candidates_with_token") or 0) < policy_candidates:
        failures.append("MISSING_TOKEN_COVERAGE")
    if int(health.get("candidates_with_quotes") or 0) < policy_candidates:
        failures.append("MISSING_QUOTE_INTENTS")
    if int(health.get("candidates_with_feed_events") or 0) < int(health.get("candidates_with_token") or 0):
        failures.append("MISSING_CLOB_FEED_COVERAGE")
    if int(health.get("candidates_with_book_snapshots") or 0) < int(health.get("candidates_with_token") or 0):
        failures.append("MISSING_BOOK_SNAPSHOT_COVERAGE")
    if int(health.get("useful_50_quote_intents") or 0) <= 0 or int(health.get("useful_100_quote_intents") or 0) <= 0:
        failures.append("ONLY_TINY_SIZE_COVERAGE")
    if int(health.get("candidates_with_50_quotes") or 0) < int(health.get("candidates_with_quotes") or 0):
        failures.append("MISSING_50_CANDIDATE_COVERAGE")
    if int(health.get("candidates_with_100_quotes") or 0) < int(health.get("candidates_with_quotes") or 0):
        failures.append("MISSING_100_CANDIDATE_COVERAGE")
    if int(health.get("shadow_outcomes") or 0) <= 0 and int(health.get("quote_intents") or 0) > 0:
        failures.append("MISSING_SHADOW_OUTCOME_LABELS")
    if reconstruction is not None:
        quotes = reconstruction.get("quote_intents") or []
        useful_50 = sum(1 for quote in quotes if (_float_or_none(quote.get("quote_size_usd")) or 0.0) >= 50.0)
        useful_100 = sum(1 for quote in quotes if (_float_or_none(quote.get("quote_size_usd")) or 0.0) >= 100.0)
        if useful_50 == 0 or useful_100 == 0:
            failures.append("SAMPLE_MISSING_USEFUL_SIZE_QUOTES")
        if not reconstruction.get("clob_feed_events"):
            failures.append("SAMPLE_MISSING_CLOB_FEED_EVENTS")
        if not reconstruction.get("book_snapshots"):
            failures.append("SAMPLE_MISSING_BOOK_SNAPSHOTS")
    return sorted(set(failures))


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
