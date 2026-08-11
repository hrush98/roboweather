# Deterministic Tape-Backed Strategy Discovery

Status: D0-D2 accepted on production; D3-D5 implementation open

Last updated: 2026-08-11

## Decision

Replace the current operator-facing Phase 3D C3/C4/C5/C6 workflow with one
deterministic discovery command backed by an incremental executable-decision
cache.

The previous implementation built durable orchestration, append-only candidate
lifecycle state, scorecards, transitions, scheduling, and status before proving
that production-scale causal materialization could finish inside its operating
budget. It repeatedly reconstructed market tape for model rows even when many
rows referred to the same executable market decision. The scheduler then
terminated recurring discovery after 900 seconds, leaving zero completed runs
and zero candidates.

The corrected order is:

```text
continuous snapshots + tape + outcomes
                  |
                  v
 incremental executable-decision cache
                  |
                  v
 one deterministic discovery command
                  |
                  +--> historical grid and untouched holdout
                  |
                  +--> exact existing-candidate forward test
                  |
                  v
        one human-readable report
```

Collection remains continuous. Expensive tape reconstruction becomes
incremental. Discovery is one bounded reproducible analysis. Candidate
versioning remains only where it protects forward attribution. A completed
zero-candidate report is valid; an incomplete cache, timeout, or failed report
is an analysis failure.

No Phase 3D output authorizes funded trading.

## Goals

1. Reconstruct each distinct executable decision once per declared replay
   version rather than once per model row or discovery run.
2. Make repeated discovery runs proportional to new decisions and newly
   resolved evidence, not to all retained raw tape.
3. Give the operator one command and one report that answer which simple
   strategies emerged, whether existing candidates survived forward testing,
   or why no defensible answer is available.
4. Preserve causal timing, tape-gap rejection, bounded grammar, chronological
   validation, correlated-family collapse, exact candidate definitions,
   activation boundaries, and evidence provenance.
5. Keep weather-outcome diagnostics distinct from venue-settled promotion
   evidence and public-tape counterfactuals distinct from actual fills.

## Non-Goals

- Automatic funded promotion or order authorization.
- A champion/challenger state machine as a prerequisite for running analysis.
- Reconstructing passive fills or private order truth from public tape.
- An expanding grammar, arbitrary station exceptions, or retrospective date
  filters.
- Replacing raw snapshot, tape, outcome, or settlement collection.
- Deleting the existing C2-C6 code before the replacement passes acceptance.

## What Failed In The Previous Design

The previous materializer used a model snapshot as its work unit. Model rows
contain distinct forecast opinions, but the expensive replay state is usually
shared by every model examining the same token at the same quote-ready time.
Replaying the book and markout horizons inside the model-row loop multiplied
identical work.

The recurring CLI also rebuilt historical rows from raw tape on each run. It
had no durable cache of successful or rejected replay decisions, so unchanged
history cost the same on every cycle. Its internal runtime timer began after
materialization; the external scheduler timeout therefore killed the costly
stage without a completed discovery outcome.

This produced a technically governed but operationally empty system:

- repeated approximately 900-second scheduler failures;
- zero completed discovery runs and zero candidate versions;
- C4 evaluation and C5 transitions running with no active candidates;
- no single report containing the useful analytical answer;
- no distinction in operator workflow between a valid no-nomination result and
  a discovery process that never finished.

The lesson is an architectural gate: prove the causal analysis kernel on
production cardinality before automating its lifecycle.

## System Boundaries

### Immutable inputs

- `prediction_snapshots` and official station/date outcomes from the research
  database;
- policy-independent tape catalog, coverage intervals, checkpoints, and raw
  partitions;
- venue-authoritative resolutions when available;
- declared model, pricing, execution, cost, size, and risk versions;
- private order truth only after a separately approved canary.

Collectors remain the only writers to raw research and tape sources. Cache and
analysis code open those sources read-only.

### Derived state

Use an external derived database, initially:

`~/.local/state/roboweather/discovery/decision_cache.sqlite`

It contains replayable decision state, model-to-decision mappings, incremental
watermarks, replay provenance, and compact run/candidate records. It does not
duplicate raw tape partitions, model artifacts, or repository reports. One
writer lock protects cache updates; reports and status surfaces are read-only.

Generated reports remain under `reports/` and uncommitted.

## Incremental Decision Cache

### Grain

The expensive cache grain is one executable market decision under one replay
contract, not one model prediction.

The stable decision identity includes at least:

- selected token/outcome;
- quote-ready timestamp;
- latency and required pre-signal coverage;
- replay/build version;
- execution state version needed to interpret the reconstructed book.

The accepted D1 contract ceilings each source row's persisted availability
timestamp to the next 60-second boundary, then applies the declared 250 ms
latency. The ceiling may delay a model opinion but can never make it available
early; model rows on the same token/outcome within that causal availability
bucket share one decision. The execution arm is separately versioned as the
first full-book tape checkpoint at or after readiness, capped at 30 seconds of
additional delay. Continuous `VALID` coverage must span the full 60-second
pre-signal window through the actual checkpoint time. The cache records that
execution timestamp and delay, and rejects later checkpoints explicitly. This
is a delayed taker counterfactual, not exact-time, passive, or actual-fill
evidence.

Price caps, target notionals, and fill scenarios may be stored as versioned
derived execution summaries when their computation depends only on the same
book. A changed replay or execution contract creates a new cache version; it
does not overwrite prior evidence.

Model-specific state lives in a separate mapping keyed to the decision:

- source prediction snapshot ID and payload hash;
- model and market family;
- selected bucket and side;
- model fair, edge, confidence, forecast/observation freshness, and local
  lifecycle fields;
- causal source availability timestamps.

This preserves every model opinion while reconstructing shared market state
once.

### Cached decision result

Store both successful and rejected decisions so deterministic failures are not
replayed forever. Each decision records:

- best bid/ask, spread, depth, and required ask levels or stable summaries;
- fillable notional and VWAP for declared taker caps/sizes;
- tape session, partition, coverage interval, checkpoint, and reconstruction
  hash;
- `VALID` pre-signal-through-quote-ready coverage or an exact rejection reason;
- replay timestamps, build/config hashes, and source watermarks;
- settlement and markout references when independently available;
- explicit absence of actual or passive-fill evidence.

Markouts that have not matured must not block initial decision reconstruction.
They are appended by an incremental enrichment pass after their horizons exist.
Settlement enrichment is likewise independent from initial book replay.

### Incremental update algorithm

1. Read source and cache watermarks.
2. Load newly available model snapshots up to the sealed source watermark.
3. Derive stable unique decision keys before opening tape partitions.
4. Insert model-to-decision mappings idempotently.
5. Replay only cache misses in deterministic key order.
6. Commit bounded batches with resumable progress.
7. Enrich newly matured markouts and settlements separately.
8. Record raw model-row count, unique decisions, cache hits/misses, rejection
   counts, elapsed time, and peak resource use.

An interrupted refresh resumes from committed keys. It never discards a
completed historical cache or silently bridges tape gaps.

## Single Operator Command

The intended public surface is:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python \
  scripts/run_discovery.py \
  --cutoff-exclusive YYYY-MM-DD \
  --out reports/discovery/RUN_NAME
```

The command may call internal modules, but the operator does not run separate
materialization, nomination, evaluation, transition, or status commands.

One invocation performs these stages in order:

1. **Seal inputs.** Record research/tape/outcome/settlement watermarks, source
   dates, build hashes, grammar, folds, costs, latency, caps, sizes, uncertainty
   method, family mapping, and existing candidate activations.
2. **Refresh cache.** Incrementally materialize missing executable decisions
   and mature eligible markout/settlement fields.
3. **Run historical discovery.** Generate the bounded rule grid and rank it
   only on chronological discovery dates.
4. **Collapse families.** Select at most one representative from nearby
   timing, delay, price, edge, spread, side, and model variants before opening
   the final historical holdout.
5. **Open historical holdout once.** Report whether selected representatives
   survive untouched dates. Holdout performance does not feed rule tuning in
   the same run.
6. **Evaluate existing candidates forward.** Apply each exact immutable rule
   only to decisions at or after its activation, with common-date and cap-aware
   comparisons when evidence permits.
7. **Write one result.** Produce machine-readable JSON/CSV plus one concise
   Markdown report and a deterministic content hash.
8. **Append successful identities.** Only after the complete report succeeds,
   append the run outcome and any bounded research-only emerged candidate
   definitions. A partial run registers no candidate.

Default paths may resolve from the active host configuration, but the sealed
manifest must contain their fingerprints and all effective values.

## Search And Evaluation Contract

### Historical discovery

- Search only a small versioned grammar.
- Use date-ordered folds and market-date-clustered uncertainty.
- Apply quote availability, continuous tape validity, executable capped asks,
  costs, fixed size, station/date caps, and daily risk caps.
- Deduplicate the first qualifying station/date decision only after applying
  the exact candidate rule.
- Penalize complexity and report effective dates, station/dates, concentration,
  fill fraction, capacity, PnL, R/R, drawdown, and rejection reasons.
- Collapse correlated variants before inspecting the final holdout.
- Allow zero passing families without widening the grammar automatically.

Weather outcomes may score historical discovery when venue truth is absent,
but the report must label those results diagnostic only.

### Existing-candidate forward test

- The candidate definition and activation boundary are immutable.
- No pre-activation decision enters its forward result.
- A changed rule is a new version with a new evidence clock.
- Compare candidates on aligned eligible dates and after shared portfolio caps
  when the sample permits.
- Venue-authoritative settlement is required for promotion evidence.
- Missing markouts, settlement disagreement, invalid coverage, insufficient
  size, or unavailable asks remain explicit failures.
- Public tape supports declared taker counterfactuals only. `ACTUAL_ORDER`
  evidence requires private authoritative order data.

### Required status distinction

Every run ends in exactly one top-level analytical state:

- `COMPLETED_WITH_EMERGED_STRATEGIES`;
- `COMPLETED_NO_EMERGED_STRATEGIES`;
- `INCOMPLETE_CACHE`;
- `FAILED_ANALYSIS`.

Timeout, unavailable sources, invalid manifest, and resource-budget exhaustion
are never reported as no nomination.

## Report Contract

The Markdown report leads with a plain-language answer and contains:

1. run status and whether any strategy emerged;
2. sealed source dates, watermarks, build/config hashes, and evidence labels;
3. cache health: model rows, unique decisions, hits, misses, invalid decisions,
   elapsed time, and replay version;
4. historical grid size, fold dates, holdout dates, family collapse, and all
   gates;
5. one row per representative family with discovery, conservative-depth, and
   untouched-holdout results;
6. one row per existing candidate with exact activation-bounded forward
   results;
7. tape-gap, liquidity, settlement, markout, and concentration attrition;
8. weather-diagnostic versus venue-settled versus actual-order provenance;
9. an explicit conclusion: emerged, none emerged, continue collecting, or
   analysis unhealthy;
10. confirmation that funded authorization is false.

The report is the operator surface. Registry tables and lifecycle events are
supporting provenance, not a second workflow the operator must interpret.

## Candidate Identity Without Workflow Sprawl

Retain the useful subset of the append-only registry:

- immutable run manifest and outcome;
- family identity and correlation group;
- exact candidate definition hash;
- source run and activation timestamp;
- append-only forward score snapshots or report references;
- retirement/supersession reason when explicitly decided.

Do not require automated champion, challenger, probation, or transition roles
to run discovery. Those may be reconsidered only after the core command has
produced candidates and accumulated forward evidence. Research identity never
confers funded authority.

## Implementation Plan

### D0: Respecification And Safe Operating Posture

- Make this document, the roadmap, audit, and approved direction agree on the
  corrected architecture.
- Mark the current multi-command C3-C6 workflow compatibility-only and remove
  it from operator guidance.
- Stop scheduling repeated known-failing discovery cycles before implementation
  work begins; leave raw research and tape collection running.
- Record a production benchmark fixture: raw model rows, distinct executable
  decisions, tape calls, elapsed time, and memory.

Exit: one canonical contract and no automated invocation of the known-failing
operator path. Historical code and registry data remain preserved.

### D1: Decision Identity And Incremental Cache

- Define schema, deterministic keys, cache/replay versions, model mappings,
  watermarks, rejection records, locks, and crash-resume semantics.
- Factor replay so unique decision keys are derived before tape access.
- Separate initial book reconstruction from later markout and settlement
  enrichment.
- Add fixtures for duplicate model rows, gaps, reconnects, partial batches,
  changed replay versions, and idempotent resume.

Exit: duplicate model opinions cause one tape reconstruction; repeated refresh
against unchanged watermarks performs zero replay work; invalid decisions are
cached with exact reasons; interrupted batches resume without changing hashes.

### D2: Production Backfill And Performance Proof

- Backfill the historical cache in resumable batches against the active
  research and tape databases.
- Compare cached decisions with direct replay samples across sessions, gaps,
  sides, price levels, and market families.
- Measure cold-backfill throughput, warm no-op runtime, daily incremental
  runtime, memory, cache size, and cache-hit ratio.
- Optimize indexes and batch boundaries from measured bottlenecks only.

Exit:

- sampled cached replay is exactly equal to direct replay;
- the historical backfill completes without an all-or-nothing timeout;
- a warm unchanged run finishes within 120 seconds;
- one ordinary newly resolved day refreshes and reports within 300 seconds;
- peak memory stays within the declared service/command budget;
- progress and failure diagnostics remain available throughout.

### D0-D2 production evidence — 2026-08-11

D0 stopped and disabled `roboweather-phase3d-discovery.service` while leaving
`roboweather-research.service` and `roboweather-market-tape.service` active.
The last three failed scheduler cycles each exhausted the 900-second child
timeout; systemd reported about 14 minutes 38 seconds of CPU and a measured
130.4 MiB peak for the final failed cycle, with zero completed discovery runs.

At the sealed historical watermark `2686364` from source start `2026-07-23`,
the production grain benchmark found 146,937 selected model rows, 146,937
legacy exact-write-time decisions, 19,032 causal minute-ceiling decisions, and
18,628 decisions requiring a tape provider call. The 7.89x minimum replay
reduction was measured read-only in 7.18 seconds. Exact row write timestamps
cannot be used for sharing because serial model persistence makes every one
different; the earlier shared cycle timestamp also cannot be used because it
precedes actual model availability.

The versioned checkpoint cache then completed the cold production backfill in
32.43 seconds internally (33.09 seconds wall) at 325,480 KiB peak RSS. It
persisted all 146,937 model mappings, 19,032 decision identities, zero pending
decisions, 1,396 executable successes, and 17,232 exact rejections. The largest
rejection class was 15,049 checkpoints arriving beyond the declared 30-second
execution bound; those rows remain visible evidence attrition rather than a
fallback price.

The unchanged warm run performed zero replay calls in 0.026 seconds internally
(0.73 seconds wall). A deterministic stratified direct-replay sample matched
200/200 cached result hashes across `HIGH_TEMP` and `LOW_TEMP`, YES and NO
tokens, successful and rejected decisions, and 16 tape sessions. An ordinary
9,216-model-row increment added 1,175 decisions and completed in 2.52 seconds
internally (3.26 seconds wall), below the 300-second daily gate. Unit fixtures
also prove one replay for duplicate model opinions, cached gaps and missing
tokens, changed-version invalidation, writer locking, idempotent warm refresh,
schema migration, and crash-resume equality.

These results accept D0-D2 for the checkpoint execution contract hash
`ab785d646a2143c0db0aa6ca164d4fc64d410fe06b97a5ae5219f6a79bb2afcd`.
They do not accept the abandoned immediate exact-time contract, authorize a
strategy, or satisfy D3-D6.

Performance thresholds may be tightened after the first measured backfill, but
they may not be weakened merely to call the old full-history behavior complete.

### D3: Deterministic Historical Grid And Report

- Refactor the useful parts of `scripts/exhaustive_constraint_grid.py` onto the
  cache rather than direct per-model tape replay.
- Seal the manifest before ranking.
- Implement chronological folds, complexity penalty, correlated-family
  collapse, untouched final dates, deterministic bootstrap seeds, conservative
  depth, and cap-aware scoring.
- Produce the single Markdown/JSON/CSV report and the four-state outcome.

Exit: identical cache, code, cutoff, and configuration produce identical
content hashes and rankings; the holdout is inaccessible until representatives
are frozen; zero strategies is a successful report; failed analysis is visibly
different.

### D4: Existing-Candidate Forward Evaluation

- Reduce the current registry to the candidate identity and activation evidence
  needed by the command.
- Evaluate exact existing versions only after activation.
- Add aligned-date and incremental cap-aware comparisons without automatic
  research-role transitions.
- Preserve weather, venue, markout, fill-scenario, and actual-order provenance.

Exit: no pre-activation row can enter a score; changed definitions create new
versions; repeated evaluation is idempotent; missing venue settlement or
markouts prevents promotion claims while still allowing clearly labeled
diagnostics.

### D5: Operator Cutover And Optional Scheduling

- Make `scripts/run_discovery.py` the sole documented discovery command.
- Retire the old C3/C4/C5/C6 CLIs, service, and TUI controls from operator
  routing while keeping reusable internals until cleanup is separately proven.
- Update status reporting to point to the latest complete report and cache
  health.
- Run at least three consecutive production manual cycles: cold/resume, warm
  no-op, and new-data incremental.
- Only then consider a thin scheduler that invokes the same command. The
  scheduler adds timing and alerting, not a second analytical architecture.

Exit: “run discovery” means one command and returns one understandable answer;
manual acceptance passes on production data; any scheduler failure preserves a
complete prior report and cannot be confused with no strategies emerging.

### D6: Phase 4 Handoff

This remains a later gate, not part of the simplification build. Package an
exact candidate for controlled funded validation only after venue settlement,
valid markouts, causal coverage, conservative positive economics,
concentration limits, useful-size evidence, and explicit human approval pass.

## Verification Matrix

| Concern | Required proof |
| --- | --- |
| Correct grain | Many model rows sharing one decision produce one replay and retain every model mapping. |
| Causality | No book event after quote-ready time or outcome unavailable at the sealed cutoff enters a decision. |
| Gap safety | Any required non-`VALID` interval rejects the decision; no snapshot-price fallback exists. |
| Determinism | Same cache/code/config/cutoff yields the same manifest, rows, ranking, and report content hash. |
| Incrementality | Unchanged watermarks cause zero tape replay; new input touches only new/version-invalidated decisions. |
| Crash recovery | Killing a cache batch and rerunning produces the same final state as an uninterrupted build. |
| Holdout integrity | Rule generation and family selection cannot read final holdout outcomes. |
| Forward integrity | Existing candidate results exclude every pre-activation row. |
| Correlation | Nearby variants collapse before holdout and do not count as independent evidence. |
| Execution honesty | Public tape labels only declared taker counterfactuals; passive/actual fills remain unavailable without authoritative truth. |
| Settlement honesty | Weather scoring is labeled diagnostic; promotion uses venue-authoritative settlement only. |
| Operator clarity | Completed-none, emerged, incomplete-cache, and failed-analysis outcomes are unmistakable in one report. |
| Resource bounds | Production warm and incremental runtime/memory gates pass before scheduling. |
| Authority | Every result states `funded_authorization=false`; Phase 4 remains separately approved. |

## Reuse And Deprecation Map

Reuse:

- causal `CausalBookProvider` and ask-sweep primitives;
- coverage, checkpoint, partition, and reconstruction provenance;
- deterministic contracts and stable hashing;
- bounded grammar, chronological scoring, complexity penalty, and family
  collapse;
- candidate definition hashes and activation boundaries;
- append-only run/candidate evidence where it directly supports attribution.

Compatibility-only until cutover:

- `scripts/phase3d_continuous_discovery.py`;
- `scripts/phase3d_continuous_evaluation.py`;
- `scripts/phase3d_apply_transitions.py`;
- `scripts/run_phase3d_scheduler.py`;
- `deploy/systemd/roboweather-phase3d-discovery.service`;
- champion/challenger/probation transition logic and TUI controls.

Do not delete these during D1-D4. After D5 acceptance, remove or archive only
code proven unused by the single command and its evidence model.

## Kill And Pivot Rules

- If no family survives costs and chronological stability, publish
  `COMPLETED_NO_EMERGED_STRATEGIES` and continue collecting. Do not widen the
  grammar automatically.
- If the cache cannot reproduce sampled direct replay exactly, stop discovery
  work and repair cache identity/provenance first.
- If warm or incremental performance gates fail, optimize the decision cache
  before adding orchestration, scheduling, or lifecycle features.
- If a candidate fails forward evaluation, preserve that exact result. A
  revised rule is a new version and evidence clock.
- If venue settlement or markouts are absent, report weather diagnostics but do
  not imply promotion readiness.
- If repeated runs produce only highly correlated threshold variants, tighten
  family collapse before increasing grammar breadth.

## Acceptance Checklist

- [x] Corrected architecture and failure diagnosis are canonical.
- [x] Known-failing discovery scheduler is removed from active operation.
- [x] Production decision-grain benchmark is recorded.
- [x] Incremental decision-cache schema and deterministic identities pass.
- [x] Cache replay matches direct replay and survives interruption.
- [x] Historical backfill and warm/incremental performance gates pass.
- [ ] Historical grid consumes cached decisions only.
- [ ] Correlated representatives are frozen before holdout access.
- [ ] One command writes one complete human-readable report.
- [ ] Existing candidates are evaluated only after activation.
- [ ] Completed-none and failed-analysis states are distinct.
- [ ] Weather, venue, markout, and actual-fill provenance remain distinct.
- [x] Old multi-command workflow is removed from operator guidance.
- [ ] Three consecutive production manual acceptance cycles pass.
- [ ] Any optional scheduler invokes the exact accepted command.
- [ ] Phase 4 receives only an explicitly approved exact candidate version.

## Decision Log

- 2026-08-11: Accepted D0-D2 for the versioned first-post-ready-checkpoint
  taker contract after stopping the failed scheduler. Production cold, warm,
  incremental, resource, crash-resume, and 200-row exact replay gates passed.
  The initial exact-time raw-partition backfill was preserved as an interrupted
  compatibility contract after measured throughput proved it could not satisfy
  the daily update budget; no evidence was silently reinterpreted.
- 2026-08-11: Respecified Phase 3D after the production scheduler repeatedly
  timed out during full-history per-model-row tape reconstruction. Made an
  incremental executable-decision cache and one deterministic report command
  the critical path. Retained causal replay, sealed manifests, chronological
  validation, correlated-family collapse, immutable candidate versions, and
  post-activation evidence; demoted the C3-C6 multi-command lifecycle workflow
  to compatibility-only until the replacement passes production performance.
- 2026-08-07: C5-C6 role-transition, scheduler, and status code was completed
  before production-scale materialization viability was established.
- 2026-08-04: C2-C4 registry, recurring discovery, and forward evaluator code
  was completed as the prior continuous-versioned architecture.
- 2026-07-30: Policy-neutral causal tape-backed discovery became a required
  research gate; that scientific requirement remains in force.
