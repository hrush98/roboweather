# 2026-07-16 Shared Weather Market Tape

## Status

Proposed. Queue this work after the research-loop memory growth is understood and bounded.

## Hypothesis

RoboWeather can avoid policy-specific data dead ends by collecting one causal market-event tape per active weather token and joining every model snapshot to that shared tape by token and timestamp. New execution policies can then be explored without having had to materialize every model, quote price, size, and TTL combination in real time.

## Expected Mechanism

CLOB events belong to a market token, not to a forecast model. When several models consider the same token, they should reuse one sequence of book and trade events rather than duplicate market data for every model or policy.

The collection layers should remain separate:

1. Continue broad causal model snapshots with decision and observation timestamps.
2. Discover relevant active weather tokens independently of the currently favored policy.
3. Record normalized market WebSocket events once per token, including exchange and local receipt timestamps.
4. Link model decisions to the tape by selected token and realistic quote-availability time.
5. Replay candidate quote price, size, TTL, latency, and cancellation rules later from the stored tape.
6. Treat tape before a hypothesis freeze as exploratory evidence and only post-activation opportunities as forward confirmation.

Do not emit and persist every quote grid for every model snapshot. Store the reusable event tape once and materialize quote outcomes only for analysis candidates or frozen forward hypotheses.

## Scope

- Market family: all collected weather markets
- Stations/regimes: all supported active stations
- Side: YES and NO tokens
- Model/source: all current and future research model families
- Execution: non-funded market-data collection and counterfactual quote replay
- Policy/sleeve name: infrastructure shared across policies

## Collection And Retention Requirements

- Run as a separate bounded service, not inside the current monolithic research worker.
- Subscribe before candidate generation so the immediate post-signal event window is not lost.
- Dynamically subscribe and unsubscribe as active weather tokens change.
- Use bounded in-memory queues, explicit backpressure, resource telemetry, and restart supervision.
- Partition or rotate event storage rather than adding an unbounded raw-event table to the current research SQLite database.
- Retain raw events long enough to replay candidate windows; retain compact summaries and materialized fill labels longer term.
- Deduplicate token streams across models, policies, quote sizes, and TTL variants.
- Benchmark message rate, disk growth, gaps, and replay latency before selecting final retention periods.

## Evidence Required

- Deterministic replay of a hypothetical quote from a fixed signal timestamp and recorded event tape.
- Continuous book/trade coverage from before quote placement through expiry or cancellation.
- Conservative, base, and optimistic fill bounds that distinguish actual trades from placements and cancellations.
- Fill-conditioned markouts and settlement results linked back to both filled and unfilled model candidates.
- Resource use that remains inside explicit memory and disk budgets for the full active weather-token universe.
- A small real-order canary before treating inferred shadow fills as exact exchange fills.

## Risks And Failure Modes

- Public level-two data cannot reveal the exact queue position of a hypothetical order.
- Feed gaps around signal time can make fill inference unusable.
- Treating `price_change` as executed flow can create false fills.
- Retrospective quote-rule search can overfit even when the underlying tape was collected prospectively.
- Raw event retention can become another unbounded database if partitioning and compaction are omitted.
- Collecting only current policy candidates recreates the original policy-specific blind spot.

## Gates Added Or Required

- Resolve and cap current research-loop memory growth before adding another continuous collector.
- Correct and test shadow trade-direction, queue, cancellation, and book-touch labeling.
- Add collector health checks for subscription coverage, feed gaps, local receipt lag, and storage growth.
- Require an immutable hypothesis version and activation timestamp for forward-confirmation reports.
- Keep funded trading paused until shadow evidence and any approved real canary pass fill-conditioned gates.

## Review Trigger

Review after the research loop has a measured stable memory ceiling and before implementing the next shadow-execution collection phase.

## Decision Log

- 2026-07-16: Recorded the shared-market-tape design so memory stabilization can be addressed first without losing the execution-data plan.
