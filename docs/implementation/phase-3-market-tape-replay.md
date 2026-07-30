# Phase 3 Market Tape And Replay Implementation Plan

Status: Slices 1-4 implemented; first batch taker holdout complete; Slice 2 lifecycle, Slice 4 real join, and Slices 5-6 remain open

Last updated: 2026-07-30

## Feature Goal

Build one causal, token-level weather market tape from first listing through closure/settlement that can replay taker and passive quote tactics without requiring every model, price, size, TTL, and cancellation combination to be materialized in real time.

This document is the implementation contract. The economic rationale and falsification criteria live in `docs/hypotheses/2026-07-16-shared-weather-market-tape.md`; phase sequencing lives in `docs/execution-rebuild-roadmap.md`.

The current pricing consumer is specified in `docs/implementation/price-sheet-v2.md`. V2a can proceed independently; V2b must use only tape windows that pass this document's validity contracts.

Policy-neutral candidate discovery, complexity control, immutable winner
selection, and untouched holdout activation are specified in
`docs/implementation/tape-strategy-discovery.md`. This tape must support that
broad discovery substrate without requiring a strategy to exist during
collection.

The lifecycle expansion consuming this tape is specified in `docs/implementation/full-market-lifecycle-trading.md`. It adds no new fill assumption: every horizon must still satisfy this document's continuous-coverage and replay gates.

## Non-Goals

- Do not restart funded trading.
- Do not treat public L2 replay as exact passive-fill truth.
- Do not build a learned quote model in this phase.
- Do not register a large quote grid as funded strategies.
- Do not restrict reusable decision/tape materialization to named V2 pilots or
  policies.
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
venue settlement --------------------+               |
                                      v               |
                         broad joined discovery view  |
                                      |               |
                                      v               |
                         constrained strategy search  |
                                      |               |
                                      v               |
                         immutable strategy manifest  |
                                      |               |
                                      +-------> deterministic replay
                                                    |
frozen quote specifications ------------------------+
                                                    |
                                                    v
                                fill bounds + cancellation + markouts
                                                    |
                                                    v
                              untouched forward evidence/audit report

private user/order channel -> real canary lifecycle -> replay validation truth
```

## Data Contracts

### Token Registry

Required fields:

- market and condition identifiers;
- token identifier and YES/NO outcome identity;
- station, market date, market family, and bucket bounds;
- sibling token/market relationships;
- venue listing time when available, local discovery timestamp, discovery lag, active interval, and resolution source;
- subscription state and last health status.

Token discovery must be independent of model or policy selection, include future-dated active weather markets, and begin as close to first listing as the venue interfaces permit. Late discovery must be retained as a coverage limitation rather than backfilled implicitly.

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

Lifecycle research additionally requires the coverage ledger to distinguish first-listing coverage, late discovery, closure, and settlement. A valid short quote interval must not be reported as a complete-market-lifecycle capture.

### Decision Join

Join model decisions by token and realistic information availability, not merely by cycle timestamp.

Required timing fields:

- observation/source timestamp;
- local observation receipt timestamp;
- model decision start/end timestamp;
- quote-ready timestamp;
- configured latency arm;
- first tape event visible after quote availability.

### Broad Discovery View

The causal join must support every eligible snapshot/token decision, not only a
decision already admitted by a named policy or V2 pilot. The derived view must
retain source snapshot/model/observation IDs and timestamps, quote-ready market
state, coverage and reconstruction references, execution/markout fields, and
settlement provenance. Invalid rows remain visible with explicit reasons and
receive no executable label.

The view is derived from existing immutable sources and must not duplicate raw
tape. It must expose both broad discovery rows and an identically defined
frozen-manifest evaluation view. Detailed search, complexity, selection, and
manifest contracts live in
`docs/implementation/tape-strategy-discovery.md`.

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
- separately versioned D-1 open, D-1 post-update, D0 early, and D0 late-control arms when their forecast gates pass;
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
- Finalized collector partitions now rotate every five minutes for the bounded lifecycle run, compress atomically to `.jsonl.gz`, retain stable uncompressed-offset event IDs, and replay through the same checksum validator. The first real compressed partition held 65,196 events in 9,947,300 bytes and passed strict exact-event verification; the early retained-growth projection was 9.03 GB/day against the 25 GiB/day budget.
- Slice 1 remains open for a complete lifecycle/daily-growth measurement and final rotation/retention review; the short sample validates the format but does not establish long-run capacity.

### Slice 2: Active-Token Recorder

- Build policy-independent discovery of current and future-dated active weather tokens from first listing.
- Add dynamic subscribe/unsubscribe and reconnect supervision.
- Persist raw segments and session/catalog metadata through a bounded queue.
- Add receipt-lag, gap, queue-depth, memory, and disk telemetry.

Exit: a complete dry-run market lifecycle stays inside resource budgets with known coverage.

Implementation status, 2026-07-23:

- Added a separate SQLite tape catalog for token registry entries, collector sessions, immutable subscription generations/membership, and token coverage intervals.
- Added policy-independent all-scope discovery that converts active weather markets into sibling YES/NO token records before policy selection. Complete refreshes retire missing tokens; incomplete refreshes preserve the last known universe.
- Added opt-in `scripts/run_market_tape.py` collection with a bounded queue, dynamic generation refresh, fail-closed transport errors, checksummed raw writes, and `RESYNCING`/`VALID`/`CLOSED` coverage transitions. A token cannot become valid before its initial full-book message.
- Added receipt-time UTC segment rotation (hourly by default), durable partition catalog rows, exponential bounded reconnects, explicit `GAPPED -> RECONNECTING -> RESYNCING` coverage, and fail-closed zero-event sessions. No reconnect can restore `VALID` without a fresh full book.
- Added persisted queue depth/high-water, RSS, raw disk bytes, receipt lag, and reconnect telemetry plus strict `scripts/market_tape_health.py` verification of the latest session, resource budgets, cataloged files, checksums, and event counts.
- A second repository-backed live probe subscribed to 616 tokens and captured 30 messages / 674 token events in 17.8 seconds. It wrote one cataloged 1.79 MB UTC partition, ended at 103 MiB RSS (108 MiB observed peak), used 679 / 10,000 queue slots, reported 484 ms final receipt lag, and passed the strict health command with all 616 tokens reaching `VALID` before terminal `CLOSED`.
- Corrected active-universe discovery so tape refreshes merge targeted current events with broad active Gamma markets instead of returning only same-day targets. Gamma `createdAt` now supplies explicit listing provenance; discovery-time fallback is labeled and cannot pass a first-listing claim.
- Changed scheduled discovery refreshes to run beside the live feed and apply Polymarket's dynamic subscribe/unsubscribe protocol. An unchanged universe no longer reconnects, and added/removed tokens receive a new immutable generation without invalidating existing-token coverage.
- Added `scripts/market_tape_lifecycle_report.py`, which aggregates all catalog sessions and fails closed on incomplete listing-to-close coverage, late discovery, invalid coverage gaps, collector errors, queue saturation, reconstruction errors, receipt lag, RSS, projected daily disk growth, or absent authoritative listing timestamps.
- Added a bounded user-systemd probe unit at `deploy/systemd/roboweather-market-tape-lifecycle.service`. It is a 72-hour evidence run, is not enabled as a production service, and keeps its catalog/raw data outside the repository.
- The predeclared probe budgets are: at least 12 recorded hours, Gamma listing discovery within 300 seconds, no coverage gap over 5 seconds, receipt lag at or below 10 seconds, RSS at or below 1 GiB, queue high-water below capacity, projected raw growth at or below 25 GiB/day, and a 14-day retention projection.
- A 2026-07-23 host audit found no active recorder and only the July 16 short probes. The latest deterministic-book catalog covered 17.7 seconds, 644 events, one 1.76 MB partition, 124,284,928 bytes peak RSS, 1,115 / 10,000 queue high-water, and 898 ms final receipt lag. These results remain valid short-probe evidence but do not satisfy the duration or complete-lifecycle gate.
- The first dynamic-subscription host probe preserved all existing coverage across its scheduled refresh, but optional `custom_feature_enabled` events grew the raw tape to 405 MB in four minutes and failed the disk-growth intent. That probe was stopped and archived; the core L2 collector does not request those optional events.
- Final compressed core-L2 host session `tape-20260723T173900Z-b9392349` started at 2026-07-23 17:39:00 UTC. Its post-rotation strict check was healthy: 616 authoritative-listing tokens spanning current and future dates, all 616 initial books `VALID`, 106,869 events, zero reconstruction errors, 134,975,488 bytes RSS, queue high-water 1 / 10,000, 807 ms receipt lag, and one exactly replayed gzip partition. The unit is disabled, capped at 1 GiB, and will stop no later than 2026-07-26 18:39 UTC.
- Slice 2 repository work is complete. Its exit remains open until the bounded host run records at least one eligible first-listing-through-close market per discovered station/family and the lifecycle report passes. The same run supplies the still-open Slice 1 daily-growth/retention evidence.

Repair status, 2026-07-30:

- Direct tape discovery now queries deterministic current, D+1, and D+2 weather event slugs in each city timezone before merging the broad active Gamma universe. Ordinary execution discovery remains current-day only.
- WebSocket closures recover inside one collector session through `GAPPED -> RECONNECTING -> RESYNCING`; the consecutive retry budget resets only after real token events, and every reconnect requires a new full book before `VALID`.
- Live incremental events over the 10-second receipt-lag limit force a fail-closed resync. Full-book timestamps are treated as book-state age rather than transport lag and may establish a valid baseline when the book reconstructs.
- Initial and dynamic WebSocket subscriptions are chunked in 500-token requests. Expected pre-book deltas remain raw `RESYNCING` evidence but are not misclassified as reconstruction failures; malformed books and post-`VALID` reconstruction errors still fail health.
- Strict health now requires nonzero messages/events, a subscription generation, and a valid full-book transition for every latest-generation member, including correct remove/re-add behavior.
- The systemd probe preserves one wall-clock deadline across automatic restarts. Lifecycle acceptance can be scoped by validation timestamps or exact session IDs so historical sessions outside the declared run do not poison it; failures inside the selected run remain fail-closed.
- Validation-cohort integrity now persists one restart-stable validation-run ID plus recorder-scoped build and collector-config fingerprints on every session. The build identity hashes the recorder's transitive source/data files, service unit, Python version, and relevant dependency versions, so unrelated repository commits do not split a supervised cohort. Initial and scheduled discovery attempts persist completion/error status, warnings, and exact token/market membership. The strict lifecycle report can select the run directly and fails on missing/mixed fingerprints or failed/incomplete refreshes.
- Lifecycle acceptance now reports every refresh-member market listed inside the selected observation window, then defines the pass/fail cohort as the matured subset whose scheduled close is at or before the validation end. Late, fallback-listed, or coverage-incomplete matured markets remain counted and fail the run. Listed markets whose scheduled close is later than the validation end are reported explicitly as `RIGHT_CENSORED` and do not fail the run; authoritative pre-window seed markets remain reported outside the cohort.
- A 200-second adversarial live probe on July 30 crossed two scheduled refresh boundaries and persisted three complete 682-market/1,364-token discoveries with stable run/build/config fingerprints. It recorded 157,807 token events in four exactly verified partitions, reached `VALID` for all 1,364 tokens, had zero reconnects or reconstruction errors, used queue high-water 1/10,000 and about 173 MiB RSS, and passed strict health. Its lifecycle result remained failed on the expected duration/no-new-listing gates, so the complete lifecycle exit remains open.
- The obsolete July 23-30 service was stopped with its tape preserved. Final short verification session `tape-20260730T165046Z-aa80a5e1` subscribed to 1,364 tokens, recorded 40,249 events, reached `VALID` for all 1,364 tokens, used queue high-water 1 and about 172 MiB RSS, recorded zero reconstruction errors/reconnects, and passed strict health. Its lifecycle report failed only the expected short-duration and no-closed-market gates.
- The lifecycle cohort was corrected before the next long run so continuously listed near-end markets are right-censored rather than impossible open-market failures. Recorder identity was narrowed from whole-Git-state hashing to recorder-relevant code/data/unit/dependency hashing, preventing unrelated concurrent development from creating a mixed-build failure.
- A post-correction 90-second probe crossed two complete discovery refreshes, subscribed 1,364 tokens, recorded 60,401 events, reached `VALID` for every token, and passed strict health with queue high-water 1/10,000, no reconnects or reconstruction errors, about 172 MiB peak RSS, and under 69 ms observed receipt lag.
- Disabled bounded validation run `tape-validation-20260730T183750Z-69556cbf` started at 2026-07-30 18:37:50 UTC with build fingerprint `13bec627bcacdc7561161e4527bc9bd63025b51f3d81b2492ea7bf4f9c509661`. It is scheduled to stop at 2026-08-02 18:37:50 UTC. Initial discovery was complete, all 1,364 tokens reached `VALID`, and the first strict health check passed with 47,296 events and no failures.
- Slice 2 exit remains open pending the final scoped first-listing-through-close lifecycle report for that run.

### Slice 3: Book Reconstruction

- Store initial/periodic checkpoints.
- Apply normalized deltas deterministically.
- Add stale/gap/resync invalidation.
- Test repeated reconstruction hashes at selected timestamps.

Exit: deterministic book replay passes fixtures and recorded-session spot checks.

Implementation status, 2026-07-16:

- Added deterministic token-level full-book reconstruction and absolute-size `BUY`/`SELL` price-level deltas with canonical SHA-256 state hashes and stable bid/ask ordering.
- Reconstruction accepts a `RESYNCING` full book as the new baseline, invalidates non-valid deltas, refuses deltas before a full book, and cannot become valid again without another full book.
- Added catalog-persisted book checkpoints and `scripts/rebuild_market_tape_books.py` for repeatable parser rebuilds from checksummed raw segments. Rebuilding the 674-event host probe twice produced the same 616 token checkpoints.
- Added inclusive arbitrary receipt-time or stable-event reconstruction across causally ordered partitions, with strict rejection of mixed sessions, missing boundaries, and non-monotonic receipt sequences.
- Integrated initial and periodic checkpoints into the live collector. Malformed books never mark coverage valid; malformed deltas invalidate formerly valid coverage, persist reconstruction errors, and fail the strict health report.
- A follow-up live probe captured 644 events across 616 tokens, persisted 616 online initial-book checkpoints with zero reconstruction errors, and passed health verification. A real token book was then reconstructed at an arbitrary receipt timestamp with stable hash `cbb49f672ce3c8743e457a93950ef522973a8a31544157c6991fc7df16e2a341`.
- Slice 3 exit gate is complete. Long-session capacity and complete-lifecycle collection remain separate Slice 1/2 acceptance items.

### Slice 4: Causal Decision Join

- Link research decisions by token and quote-ready time.
- Support policy-neutral bulk joins across all eligible causal snapshot/token
  decisions, with selected V2 quotes as a narrower consumer.
- Add configurable latency arms.
- Prove pre-signal coverage for joined decisions.

Exit: a random decision can be reconstructed from observation availability through quote termination.

Implementation status, 2026-07-17:

- Added immutable decision timing and tape-join contracts with observation source/receipt time, model decision start/end, hypothesis activation, explicit latency, quote readiness, first visible token event, coverage verdict, and reconstruction hash.
- Added a fail-closed causal join that rejects time-travel timestamp ordering and pre-activation decisions, applies the configured latency arm, selects the first token event at or after quote readiness, and requires one continuous `VALID` coverage interval from the configured pre-signal window through that event.
- Added persisted join rows and `scripts/join_market_tape_decision.py` for versioned JSON decision records. Synthetic cross-time fixtures prove valid joins and known coverage breaks.
- Added a read-only execution-ledger adapter that maps a persisted postable price-sheet quote, its live candidate, and all source prediction snapshots into the immutable decision contract. It derives the frozen price-sheet/signal/quote-arm version, uses persisted availability timestamps, and selects observed shadow cancellation or declared GTD expiry as the quote termination boundary.
- Corrected the quote-ready reconstruction to stop at or before quote readiness. The first later event remains an audit reference but is no longer included in the causal book hash.
- Extended join persistence with source references, the exact coverage interval, raw-tape watermark, quote termination, last token event at/before termination, and a separate termination book hash. A join now fails closed when raw segments have not reached termination or when continuous `VALID` coverage ends early.
- An execution-ledger integration fixture reconstructs a persisted Price Sheet V2-style quote from source observation through GTD termination, including a post-readiness book change and a session watermark at expiry. Backward-compatible catalog migration and early-cancel handling are covered.
- Slice 4 repository implementation is complete. The exit evidence remains open until the operator runs the adapter against one real persisted quote and matching captured tape segments and records the successful reconstruction.

### Slice 5: Fill Bounds And Markouts

- Correct trade-direction classification.
- Implement queue scenarios, cancellation-before-fill, partial-fill bounds, and markouts.
- Fail closed on invalid coverage.
- Add synthetic truth fixtures for placements, cancellations, trades, touches, gaps, and reconnects.

Exit: repeated replay is deterministic and known false-fill cases remain unfilled.

### Slice 6: Forward Shadow From Frozen Discovery Manifest

- Consume an immutable Phase 3D strategy manifest frozen before activation.
- Materialize only its replay outcomes.
- Report selected, valid, postable, filled/missed, markout, settlement, capacity, and effective market-date sample.
- Reject pre-activation rows and prohibit holdout-derived retuning.

Exit: the report answers whether base-case fill-conditioned PnL is positive without using optimistic-only labels.

Implementation status, 2026-07-29:

- Added `scripts/tape_strategy_holdout_report.py` as a read-only bridge between broad raw-snapshot discovery and exact later tape. It freezes built-in sleeve order, applies station/date portfolio deduplication, maps the selected side to its token, requires continuous valid pre-signal coverage, reconstructs from a valid checkpoint and checksummed deltas through quote readiness, and simulates a capped immediate ask sweep.
- The first frozen-family replay used a July 22 discovery cutoff and July 23 holdout start. Across six resolved July 23-28 market dates it deduplicated 19 signals, executed 12, rejected three for invalid continuous coverage and four for absent asks under the cap, and produced `+$93.22` weather-outcome PnL on `$205.51` cost.
- This is a batch taker-holdout milestone, not Slice 6 completion. It permits partial fills, has no passive queue/fill labels or markouts, is not venue-settlement aligned, and was activated retrospectively at the cutoff rather than prospectively registered before collection.

## Acceptance Checklist

- [ ] Policy-independent token discovery covers current and future-dated supported weather markets.
- [ ] Listing/discovery lag is measured and complete-lifecycle claims begin at actual coverage.
- [ ] Subscription begins before candidate generation and updates dynamically.
- [ ] Valid coverage spans at least one complete listing-to-close lifecycle per supported station family.
- [ ] Memory, disk, queue depth, and receipt lag remain within explicit budgets.
- [ ] Raw events survive restart and parser rebuilds.
- [ ] Coverage gaps invalidate affected replay intervals.
- [ ] Book reconstruction is deterministic.
- [ ] Trade flow excludes placements, cancellations, and plain price changes.
- [ ] Decision joins use realistic quote availability and latency.
- [ ] Broad joined discovery rows are independent of current policy and V2
      pilot selection.
- [ ] Fill scenarios, cancellations, and markouts are reproducible.
- [ ] A Phase 3D manifest is frozen before its forward activation boundary.
- [ ] Forward reports use frozen hypothesis versions and activation timestamps.
- [ ] Private user-channel design is ready before any funded canary.
- [ ] Existing shadow labels are not used as promotion evidence.

## Decision Log

- 2026-07-30: Made Phase 3 a policy-neutral measurement substrate for the new
  Phase 3D discovery/freeze gate. Required broad causal snapshot/tape/settlement
  rows and changed Slice 6 to consume an immutable pre-activation manifest
  rather than presuming a named pilot is the final strategy.
- 2026-07-30: Removed two pre-run acceptance hazards: lifecycle pass/fail now
  applies only to markets listed in-window whose scheduled close is within the
  validation window, with later closes reported as right-censored; recorder
  build identity now covers only recorder-relevant source/data, the service
  unit, runtime versions, and collector configuration rather than unrelated Git
  changes.
- 2026-07-30: Repaired the long-probe failures without weakening replay validity. Added direct future-event discovery, in-process reconnect/resync, incremental-event lag enforcement, restart-stable bounds, chunked subscription seeding, strict generation health, scoped lifecycle acceptance, and expected pre-seed delta handling. A 1,364-token short probe passed; the complete lifecycle gate remains open.
- 2026-07-29: Added the first reusable frozen-portfolio taker holdout over later valid tape. Recorded its preliminary positive result while keeping Slice 2 failed and Slices 5-6 open for lifecycle validity, passive-fill bounds, markouts, settlement, and true forward activation.
- 2026-07-23: Audited the remote host and corrected the prior “running” assumption: no recorder was active and all retained catalogs were approximately 18-second probes. Completed the missing future-market discovery, listing-provenance, lifecycle-gate, and bounded-supervision repository work; kept Slice 2 open pending elapsed host evidence rather than treating the short probes as a pass.
- 2026-07-17: Extended the acceptance target to explicit first-listing-through-close/settlement collection, including future-dated weather markets, lifecycle discovery-lag reporting, and horizon-tagged forward arms. Existing fill-validity requirements remain unchanged.
- 2026-07-17: Completed the Slice 4 repository path from persisted execution quote/source snapshots through a strictly causal quote-ready book and continuous termination-boundary coverage. Kept the exit evidence open for one real host quote/tape reconstruction.
- 2026-07-16: Started Slice 4 with an immutable, latency-aware causal decision join and continuous pre-signal coverage gate. Kept the slice open pending a real decision-export integration and recorded decision replay.
- 2026-07-16: Completed the Slice 3 repository exit gate after online checkpointing produced 616 valid token books with zero reconstruction errors and arbitrary-time reconstruction passed on the captured segment. Kept long-run recorder capacity separate and open.
- 2026-07-16: Started Slice 3 with deterministic full-book/delta reconstruction and persisted state hashes. Rebuilt all 616 initial books in the bounded host segment twice; kept arbitrary-time and long-session checkpoint gates open.
- 2026-07-16: Hardened Slice 2 with UTC rotation/cataloging, resource telemetry, bounded exponential reconnects, explicit gap invalidation, strict health verification, and zero-event failure. A 616-token live probe passed; kept complete-lifecycle capacity and unattended supervision gates open.
- 2026-07-16: Validated the first repository-backed live all-universe recorder probe: 616 tokens, 50 feed messages, 714 token events, exact replay, and bounded queue use. Kept lifecycle and retention gates open.
- 2026-07-16: Recorded operator confirmation that the Phase 3 collector is built and running. Kept the acceptance checklist open until representative resource budgets, deterministic replay, valid coverage, and fill/markout evidence are documented.
- 2026-07-16: Started Slice 1 with a separate `weather_trader.tape` boundary. The active segment format is checksummed canonical JSONL with stable byte-offset IDs; gzip is measured as a finalized-segment candidate but is not yet a locked retention choice.
- 2026-07-16: Phase 3 approved after confirmation that the research-loop memory issue is resolved. Split the economic hypothesis from this implementation plan and made the shared tape the current execution-rebuild phase.
