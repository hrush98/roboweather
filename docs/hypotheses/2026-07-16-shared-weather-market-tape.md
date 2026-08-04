# 2026-07-16 Shared Weather Market Tape

## Status

Operational collection hypothesis. Retained collection has run from July 23 onward and the tape now supports exact quote-ready batch taker replay plus a policy-neutral Phase 3D discovery/freeze implementation. No prospective strategy manifest, venue-settlement cohort, or markout-complete forward result exists. Funded trading remains paused.

Implementation details and sprint acceptance belong in `docs/implementation/phase-3-market-tape-replay.md`. Current sequencing and promotion gates belong in `docs/execution-rebuild-roadmap.md`.

## Hypothesis

RoboWeather can avoid policy-specific data dead ends by collecting one causal market-event tape per active weather token and joining every model snapshot to that shared tape by token and timestamp. New simple strategies can then be discovered from the policy-neutral joined substrate without having had to materialize every model, quote price, size, and TTL combination in real time. The strategy is frozen only after constrained pre-cutoff discovery and must then pass untouched later tape.

## Expected Mechanism

CLOB events belong to a market token, not to a forecast model. When several models consider the same token, they should reuse one sequence of book and trade events rather than duplicate market data for every model or policy.

The collection layers should remain separate:

1. Continue broad causal model snapshots with decision and observation timestamps.
2. Discover relevant active weather tokens independently of the currently favored policy.
3. Record normalized market WebSocket events once per token, including exchange and local receipt timestamps.
4. Link all eligible causal model decisions to the tape by token and realistic quote-availability time, not only decisions already selected by a policy.
5. Add valid execution/markout labels and venue-authoritative settlement without copying the raw tape.
6. Search a predeclared grammar of simple strategies with date-ordered folds, market-date clustering, complexity penalties, and correlated-family collapse.
7. Select at most one primary winner initially and freeze its exact signal, price, execution, size, caps, source cutoffs, hashes, and activation time.
8. Replay only post-activation opportunities as forward confirmation; pre-freeze tape remains discovery evidence.

Do not emit and persist every quote grid for every model snapshot. Store the reusable event tape once and materialize quote outcomes only for analysis candidates or frozen forward hypotheses.

## Discovery And Freeze Requirement

The tape is a measurement substrate, not a predefined strategy. Strategy
discovery must remain policy-neutral until selection:

- expose a broad joined view across every eligible causal prediction snapshot;
- predeclare the candidate grammar, discovery cutoff, folds, metrics, costs,
  fill scenario, size, caps, stability rules, and complexity penalty;
- count independent market dates as the primary sample and collapse nearby
  variants into correlated families;
- prefer the simplest stable family to the highest exploratory return;
- freeze one immutable strategy manifest before the forward activation
  boundary;
- score the manifest only on untouched later tape for confirmation;
- reject a failed manifest rather than adding a filter learned from its
  holdout.

The implementation contract is
`docs/implementation/tape-strategy-discovery.md`.

## Scope

- Market family: all collected weather markets
- Stations/regimes: all supported active stations
- Side: YES and NO tokens
- Model/source: all current and future research model families
- Execution: non-funded market-data collection and counterfactual quote replay
- Policy/sleeve name: infrastructure shared across policies

## Non-Goals

- The tape does not create forecast alpha or repair an unprofitable price sheet.
- Public L2 data does not establish exact passive queue position.
- Exploratory replay over many quote rules is not forward confirmation.
- Existing candidate-scoped shadow labels are not promotion evidence.
- This phase does not restart funded trading or build a learned quote policy.

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
- A discovery materializer restricted to current V2 pilots can recreate the same policy-selection blind spot at the analysis layer.
- Millions of tape events can hide a very small number of independent weather dates.
- Raw event retention can become another unbounded database if partitioning and compaction are omitted.
- Collecting only current policy candidates recreates the original policy-specific blind spot.

## Gates Added Or Required

- Completed: resolve and cap research-loop memory growth before adding another continuous collector.
- Correct and test shadow trade-direction, queue, cancellation, and book-touch labeling.
- Add collector health checks for subscription coverage, feed gaps, local receipt lag, and storage growth.
- Materialize a policy-neutral causal snapshot/tape/settlement discovery view in addition to frozen V2 views.
- Freeze discovery inputs, grammar, folds, complexity, winner selection, and activation before inspecting forward tape.
- Require an immutable hypothesis version and activation timestamp for forward-confirmation reports.
- Keep funded trading paused until shadow evidence and controlled real canaries pass fill-conditioned gates.
- Permit minimum-risk funded orders only for replay/plumbing validation after shadow reconstruction passes; they provide no capacity or promotion evidence.
- Require direct `$50` and `$100` validation for claims at those sizes.

## Review Trigger

Review at each Phase 3 implementation exit gate and again before any private user-channel or funded canary work begins.

## Decision Log

- 2026-08-04: Implemented the policy-neutral materializer, fixed simple-rule
  discovery, correlated-family collapse, immutable winner/no-winner artifact,
  and activation-gated forward evaluator. This changed implementation status,
  not the economic verdict: no strategy was selected and venue settlement plus
  markouts remain required.

- 2026-07-30: Made constrained policy-neutral discovery and immutable winner freezing a first-class Phase 3D requirement. The tape and broad causal snapshots now feed one predeclared walk-forward search, while only a post-freeze untouched period can provide forward confirmation.
- 2026-07-29: Confirmed the shared-tape premise with a pre-cutoff discovery/post-cutoff execution holdout: 12 exact capped taker sweeps reconstructed from later valid tape produced preliminary positive weather-outcome PnL. Kept lifecycle, passive-fill, markout, settlement, and funding gates open.
- 2026-07-16: Recorded the shared-market-tape design so memory stabilization can be addressed first without losing the execution-data plan.
- 2026-07-16: Confirmed the memory issue is resolved, approved the hypothesis for Phase 3 implementation, and split implementation details into `docs/implementation/phase-3-market-tape-replay.md`.
- 2026-07-16: Recorded operator confirmation that the collector is built and running. The tape remains execution evidence rather than profitability evidence; valid tape windows feed the Price Sheet V2b plan in `docs/implementation/price-sheet-v2.md`.
