# Phase 3 Market Tape And Replay Implementation Plan

Status: Recorder vertical slice validated; repository acceptance evidence remains in progress

Last updated: 2026-07-16

## Feature Goal

Build one causal, token-level weather market tape that can replay taker and passive quote tactics without requiring every model, price, size, TTL, and cancellation combination to be materialized in real time.

This document is the implementation contract. The economic rationale and falsification criteria live in `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`; phase sequencing lives in `docs/execution-rebuild-roadmap.md`.

The current pricing consumer is specified in `docs/implementation/price-sheet-v2.md`. V2a can proceed independently; V2b must use only tape windows that pass this document's validity contracts.

## Non-Goals

- Do not restart funded trading.
- Do not treat public L2 replay as exact passive-fill truth.
- Do not build a learned quote model in this phase.
- Do not register a large quote grid as funded strategies.
- Do not make the current research or live SQLite database an unbounded raw-event store.
- Do not use existing candidate-scoped shadow labels as promotion evidence.

## System Schematic

```text
weather-market discovery
        |
        v
active token registry <------------------------------+
        |                                             |
        v                                             |
dynamic subscription manager                         |
        |                                             |
        v                                             |
market WebSocket sessions -> bounded event queue -> raw partition writer
                                      |                    |
                                      v                    v
                              normalized event log   session/catalog DB
                                      |
                                      v
                         book checkpoints + gap ledger
                                      |
model/observation snapshots -> causal decision join  |
                                      |               |
                                      +-------> deterministic replay
                                                    |
frozen quote specifications ------------------------+
                                                    |
                                                    v
                                fill bounds + cancellation + markouts
                                                    |
                                                    v
                                     forward evidence/audit report

private user/order channel -> real canary lifecycle -> replay validation truth
```

## Data Contracts

### Token Registry

Required fields:

- market and condition identifiers;
- token identifier and YES/NO outcome identity;
- station, market date, market family, and bucket bounds;
- sibling token/market relationships;
- discovery timestamp, active interval, and resolution source;
- subscription state and last health status.

Token discovery must be independent of model or policy selection.

### Event Envelope

Every raw or normalized event must carry:

- storage schema/parser version;
- collector session identifier;
- token and market identifiers;
- event type and original payload;
- feed/exchange timestamp when supplied;
- local UTC receipt timestamp;
- local monotonic receipt sequence/time;
- partition identifier and append offset or stable event ID;
- subscription generation;
- gap/resync validity metadata.

Do not copy the entire subscribed-token list or candidate list into each event. Store subscription membership once per session/generation and join it later.

### Coverage Ledger

Track intervals by token and collector session:

- `VALID`;
- `PRE_SUBSCRIPTION`;
- `RECONNECTING`;
- `GAPPED`;
- `STALE`;
- `RESYNCING`;
- `CLOSED`.

A replay may receive a fill label only when the required interval is valid from before quote availability through fill, cancellation, or expiry.

### Decision Join

Join model decisions by token and realistic information availability, not merely by cycle timestamp.

Required timing fields:

- observation/source timestamp;
- local observation receipt timestamp;
- model decision start/end timestamp;
- quote-ready timestamp;
- configured latency arm;
- first tape event visible after quote availability.

## Storage Design

Use two layers:

1. Rotated/partitioned append-only raw event segments for durable replay and parser rebuilds.
2. A compact catalog for token metadata, sessions, partitions, coverage intervals, checkpoints, and materialized replay outcomes.

Benchmark candidate formats before locking retention. The acceptance decision must consider:

- messages and bytes per token/hour;
- write latency and queue depth;
- compression ratio;
- replay scan latency;
- full-universe daily disk growth;
- recovery after restart or partial segment writes.

Raw retention may be shorter than compact replay outcomes, but no raw segment may be deleted until all covered forward hypotheses have passed their required replay/markout windows.

## Book Reconstruction

- Capture an initial full book before applying deltas.
- Preserve ordered deltas and periodic full checkpoints.
- Detect reconnects, malformed events, missing ordering information, and stale intervals.
- Resync from a fresh book after invalidation.
- Never bridge a known gap as though the book were continuous.
- Provide deterministic reconstruction at an arbitrary event or receipt timestamp.

Where the venue does not provide a sufficient sequence number, use conservative session/receipt ordering and mark ambiguous intervals rather than claiming exact continuity.

## Replay Semantics

The replay input is immutable:

```text
decision_id
hypothesis_version
activation_timestamp
token_id
quote_ready_timestamp
side
quote_price_rule
size
TTL/GTD expiry
cancellation rule
latency assumption
queue assumption
```

The replay output must include:

- coverage validity and invalid reason;
- reconstructed initial book and queue-ahead estimate;
- postable/crossing status;
- authoritative executed flow at or through the quote;
- conservative, base, and optimistic fill result;
- partial-fill path where supportable;
- cancellation trigger and time;
- adverse movement before and after fill;
- markouts at `10s`, `30s`, `2m`, `10m`, next weather update, close, and settlement;
- Polymarket-settlement PnL if filled;
- replay/parser versions.

`price_change`, placement, cancellation, and a book merely touching the quote are not authoritative trades. Optimistic touch labels must remain visibly separate from executed-flow labels.

## Forward Experiment Design

The broad quote grid is exploratory. Forward confirmation requires a frozen small set of quote arms with an activation timestamp.

Initial arm families:

- conservative post-only quote derived from the revised price sheet;
- one or two safe aggressiveness offsets;
- short and medium TTL/cancellation variants only when predeclared;
- separately tagged stable-visible-ask taker control;
- no-trade/missed-candidate comparison.

Randomized price aggressiveness may be used inside a preapproved safe band to estimate fill response without purely self-selected placement.

## Private Canary Integration

Public tape replay must later be compared against authoritative order lifecycle events:

- local submit time;
- exchange acknowledgment and placement;
- order identifier and version;
- partial/full fills with price and size;
- cancel request and acknowledgment;
- expiry/rejection reason;
- private user-channel event time and local receipt time.

Minimum-risk orders are permitted to validate plumbing and replay fidelity after shadow reconstruction passes. They do not count toward useful-size promotion. Capacity evidence remains size-specific at `$50` and `$100`.

## Proposed Module Boundaries

Final names may change during implementation, but responsibilities should remain separate:

```text
weather_trader/tape/contracts.py      event/session/coverage contracts
weather_trader/tape/discovery.py      active weather-token registry
weather_trader/tape/collector.py      subscriptions, reconnects, backpressure
weather_trader/tape/storage.py        partitions, catalog, retention
weather_trader/tape/books.py          checkpoint and delta reconstruction
weather_trader/tape/replay.py         causal quote replay
weather_trader/tape/fills.py          fill bounds and markouts
scripts/run_market_tape.py            supervised collector entry point
scripts/market_tape_health.py         operational health and coverage
scripts/replay_market_tape.py         deterministic analysis entry point
```

Do not reuse the current candidate-token collector as the service boundary. Reuse parsing or store helpers only where their semantics pass the new contracts.

## Sprint Slices

### Slice 1: Contracts And Benchmarks

- Define event, session, coverage, token, checkpoint, and replay contracts.
- Capture representative event samples.
- Benchmark message rate, disk growth, compression, and write/replay latency.
- Choose partition rotation and retention defaults.

Exit: schema and resource budgets are reviewed, and a sample segment round-trips exactly.

Implementation status, 2026-07-16:

- Added immutable contracts for token registry entries, collector sessions, subscription generations, coverage intervals, raw event envelopes, book checkpoints, and replay inputs/outputs under `weather_trader/tape/`.
- Added checksummed append-only JSONL segments with byte-offset stable event IDs, exact raw-payload replay, and fail-closed handling for truncated or modified records.
- Added `scripts/benchmark_market_tape.py` to measure raw/gzip bytes per message, compression, append latency/throughput, replay throughput, and exact round-trip behavior on captured representative WebSocket JSONL.
- A bounded live probe captured 50 WebSocket messages / 714 token events across 616 policy-independent tokens. The 1.81 MB checksummed segment round-tripped exactly, compressed to 15.4% of raw size, replayed at about 19.7k events/second, and had 0.23 ms median append latency on the host.
- The live probe established an explicit 8 MiB WebSocket-frame ceiling after the 616-token initial snapshot exceeded the client library's 1 MiB default. Queue high-water was 492 events against the provisional 10,000-event bound.
- Slice 1 remains open for a complete lifecycle/daily-growth measurement and final rotation/retention review; the short sample validates the format but does not establish long-run capacity.

### Slice 2: Active-Token Recorder

- Build policy-independent weather-token discovery.
- Add dynamic subscribe/unsubscribe and reconnect supervision.
- Persist raw segments and session/catalog metadata through a bounded queue.
- Add receipt-lag, gap, queue-depth, memory, and disk telemetry.

Exit: a complete dry-run market lifecycle stays inside resource budgets with known coverage.

Implementation status, 2026-07-16:

- Added a separate SQLite tape catalog for token registry entries, collector sessions, immutable subscription generations/membership, and token coverage intervals.
- Added policy-independent all-scope discovery that converts active weather markets into sibling YES/NO token records before policy selection. Complete refreshes retire missing tokens; incomplete refreshes preserve the last known universe.
- Added opt-in `scripts/run_market_tape.py` collection with a bounded queue, dynamic generation refresh, fail-closed transport errors, checksummed raw writes, and `RESYNCING`/`VALID`/`CLOSED` coverage transitions. A token cannot become valid before its initial full-book message.
- The first repository-backed live probe subscribed to 616 tokens and persisted catalog/coverage state. Slice 2 remains open for hourly rotation, durable restart supervision, reconnect backoff, lag/memory/disk telemetry, complete-lifecycle collection, and an operational health command.

### Slice 3: Book Reconstruction

- Store initial/periodic checkpoints.
- Apply normalized deltas deterministically.
- Add stale/gap/resync invalidation.
- Test repeated reconstruction hashes at selected timestamps.

Exit: deterministic book replay passes fixtures and recorded-session spot checks.

### Slice 4: Causal Decision Join

- Link research decisions by token and quote-ready time.
- Add configurable latency arms.
- Prove pre-signal coverage for joined decisions.

Exit: a random decision can be reconstructed from observation availability through quote termination.

### Slice 5: Fill Bounds And Markouts

- Correct trade-direction classification.
- Implement queue scenarios, cancellation-before-fill, partial-fill bounds, and markouts.
- Fail closed on invalid coverage.
- Add synthetic truth fixtures for placements, cancellations, trades, touches, gaps, and reconnects.

Exit: repeated replay is deterministic and known false-fill cases remain unfilled.

### Slice 6: Forward Shadow Report

- Freeze initial signal and quote-policy versions.
- Materialize only their replay outcomes.
- Report selected, valid, postable, filled/missed, markout, settlement, capacity, and effective market-date sample.

Exit: the report answers whether base-case fill-conditioned PnL is positive without using optimistic-only labels.

## Acceptance Checklist

- [ ] Policy-independent token discovery covers the supported weather universe.
- [ ] Subscription begins before candidate generation and updates dynamically.
- [ ] Memory, disk, queue depth, and receipt lag remain within explicit budgets.
- [ ] Raw events survive restart and parser rebuilds.
- [ ] Coverage gaps invalidate affected replay intervals.
- [ ] Book reconstruction is deterministic.
- [ ] Trade flow excludes placements, cancellations, and plain price changes.
- [ ] Decision joins use realistic quote availability and latency.
- [ ] Fill scenarios, cancellations, and markouts are reproducible.
- [ ] Forward reports use frozen hypothesis versions and activation timestamps.
- [ ] Private user-channel design is ready before any funded canary.
- [ ] Existing shadow labels are not used as promotion evidence.

## Decision Log

- 2026-07-16: Validated the first repository-backed live all-universe recorder probe: 616 tokens, 50 feed messages, 714 token events, exact replay, and bounded queue use. Kept lifecycle and retention gates open.
- 2026-07-16: Recorded operator confirmation that the Phase 3 collector is built and running. Kept the acceptance checklist open until representative resource budgets, deterministic replay, valid coverage, and fill/markout evidence are documented.
- 2026-07-16: Started Slice 1 with a separate `weather_trader.tape` boundary. The active segment format is checksummed canonical JSONL with stable byte-offset IDs; gzip is measured as a finalized-segment candidate but is not yet a locked retention choice.
- 2026-07-16: Phase 3 approved after confirmation that the research-loop memory issue is resolved. Split the economic hypothesis from this implementation plan and made the shared tape the current execution-rebuild phase.
