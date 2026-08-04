# Continuous Versioned Strategy Discovery

Status: C0-C2 implemented; recurring orchestration, continuous evaluation, and role transitions remain open

Last updated: 2026-08-04

## Goal

Build a continuous research system in which simple weather-market strategies
emerge from causal prediction snapshots, market tape, execution evidence, and
settlement; receive stable version identities; accumulate honest forward
evidence; and are promoted, retained, revised, or retired as new dates resolve.

The strategy program is adaptive. Individual candidate versions are immutable
only so their evidence remains attributable. A change to a rule creates a new
version; it does not stop discovery or permanently freeze the family.

```text
continuous snapshots + tape + outcomes
                    |
                    v
        policy-neutral causal materializer
                    |
                    v
          recurring discovery runs
                    |
                    v
       versioned candidate registry
                    |
                    v
      forward evaluation by cohort
                    |
                    v
       champion/challenger scorecards
                    |
                    v
   retain / revise / retire / Phase 4 request
                    |
                    +----------> repeat
```

This contract owns discovery, candidate versioning, forward shadow evaluation,
and research promotion state. Phase 3 owns tape validity and replay semantics;
Price Sheet V2 owns calibrated economic pricing and execution reductions;
Phase 4 owns controlled funded validation. No Phase 3D state change can itself
authorize funded trading.

## Why Versioning Is Required

"Versioned" does not mean a strategy is chosen forever. It means the system
cannot edit yesterday's candidate after seeing today's outcome and continue to
report the combined record as though the rule never changed.

Every candidate version therefore has:

- an immutable definition hash;
- the discovery run and source cutoff that created it;
- an activation timestamp after that cutoff;
- a forward-evidence cohort beginning at activation;
- an append-only lifecycle and score history.

If a discovery run changes a threshold, model, filter, price cap, execution
arm, or risk rule, the registry creates a new candidate version. The old
version keeps its original results and may remain active as a comparator.

## Operating Principles

1. Collection is continuous and independent of strategies.
2. Discovery recurs whenever enough newly resolved data exist; it is not a
   one-time winner-selection ceremony.
3. Candidate definitions are generated from a bounded, versioned grammar, not
   from an expanding list of hand-named policies.
4. Multiple challengers may coexist. Zero candidates is also a valid result.
5. A champion is a reporting/allocation role, not a claim of truth and not
   permission to trade real money.
6. Every candidate version is scored on data that became available after its
   activation as well as on causal walk-forward discovery folds.
7. Nearby variants share family-level evidence and are not counted as
   independent discoveries.
8. Failed versions remain visible. A replacement version cannot erase family
   drawdowns or reset the evidence clock without disclosure.
9. Tape gaps, unavailable books, unresolved settlement, and missing markouts
   remain explicit rejections; no layer fills them from snapshot prices.
10. Automation may nominate and shadow candidates, but funded promotion always
    requires the separate Phase 4 authorization path.

## Definitions

### Strategy family

A stable economic idea, such as a model/consensus source, side rule, lifecycle
region, and execution arm. Nearby delay, clock, price-band, or scope variants
belong to the same family unless they demonstrate independent incremental
portfolio value.

### Candidate version

One exact, executable rule definition within a family. It includes signal,
pricing, execution, sizing, deduplication, and risk behavior. Candidate versions
are immutable and content-addressed.

### Discovery run

One reproducible search over a declared source window and grammar version. A
run may produce zero, one, or several registry nominations. It never rewrites
previous runs.

### Evaluation cohort

The rows causally available after a candidate version's activation and before
an optional retirement boundary. Cohorts are append-only and candidate-specific.

### Champion and challenger

Research roles assigned from comparable evidence. A champion is the current
best-supported candidate for a declared family/portfolio slot. Challengers
continue shadow evaluation on the same new dates. There may be no champion.

## System Boundaries

### Existing immutable inputs

- `prediction_snapshots` and station/date outcomes from the research database;
- policy-independent raw tape, coverage intervals, checkpoints, and catalog;
- venue-authoritative resolution when available;
- calibrated price-sheet versions and execution features when available;
- private order/user-channel truth only after a separately approved canary.

### Derived discovery state

Store compact registry and score state outside the repository, by default at:

`~/.local/state/roboweather/discovery/catalog.sqlite`

This database contains definitions, hashes, watermarks, lifecycle events,
evaluation references, and compact scorecards. It must not duplicate raw tape,
snapshot payloads, model artifacts, or bulky materialized datasets. Generated
row exports and reports remain uncommitted under `reports/`.

### Read/write ownership

- Collectors remain the only writers to the research and tape sources.
- Materialization and discovery open source databases read-only.
- One discovery scheduler owns registry writes through a database-scoped lock.
- TUI and reports observe registry state read-only.

## Policy-Neutral Causal Materializer

The materializer is a reproducible derived view over source watermarks. It must
cover every eligible causal snapshot/token decision, not only named policies,
existing candidates, V2 pilots, or currently active families.

Every row identifies:

- source snapshot IDs and stable payload hashes;
- model/forecast/observation versions and availability timestamps;
- station, market date, market family, bucket, side, and token;
- lifecycle horizon and local decision state;
- decision and quote-ready timestamps plus latency arm;
- tape session, partitions, validity interval, and reconstruction hash;
- quote-ready bid, ask, spread, depth, flow, and capacity;
- economic fair, maximum price, cost, and sizing versions when available;
- execution scenario and conservative/base/optimistic labels kept separate;
- predeclared markouts and actual fill state when available;
- venue settlement provenance and research-truth disagreement;
- explicit eligibility or rejection reasons.

Rows with missing causal timestamps, invalid coverage, unresolved token
identity, unavailable required asks, or settlement disagreement remain in the
view but cannot receive an executable/profitable label.

Repeated materialization against identical watermarks and configuration must
produce identical row IDs, values, eligibility decisions, and hashes.

## Strategy Grammar

The grammar defines the dimensions from which strategies may emerge. It is
versioned independently from any candidate and begins deliberately small:

- one forecast/model or declared consensus score;
- market family and lifecycle information-state region;
- side selection;
- observation/forecast freshness band;
- local-time or lifecycle-state window;
- high-conviction/edge requirement;
- entry-price band and economic ceiling;
- first-entry and portfolio deduplication scope;
- stable-taker or separately modeled passive execution arm;
- fixed size plus station/date and daily portfolio caps.

The initial grammar excludes station exception lists, arbitrary date filters,
deep rule trees, learned online resizing, and unconstrained quote-policy models.
New dimensions require a grammar version, causal coverage audit, stronger
complexity control, and explicit family mapping.

## Recurring Discovery Run

### Trigger

Run discovery after new venue/research outcomes resolve and at least one new
effective market date is available. Weekly is the default operating cadence;
the scheduler may skip a run when source watermarks have no meaningful change.
Collection and forward evaluation continue between discovery runs.

### Immutable run record

Before ranking, persist:

- run ID, creation time, code/build hash, and grammar version;
- inclusive source start and exclusive cutoff;
- research/tape/settlement watermarks and selected partitions/sessions;
- walk-forward folds and clustered sample definitions;
- costs, execution scenarios, latency, size, and portfolio caps;
- complexity penalty, family-collapse rules, nomination thresholds, and the
  maximum number of new challengers;
- comparison baselines and currently registered champion/challengers.

The run record is immutable historical evidence. The next scheduled run may
use a later cutoff or a new grammar version without changing prior results.

### Search and nomination

1. Generate all rules allowed by the declared grammar.
2. Score them using date-ordered walk-forward folds.
3. Fit calibration or thresholds only on dates preceding each evaluation fold.
4. Cluster primary uncertainty by market date and report station/date counts.
5. Apply costs, quote availability, tape validity, size, and portfolio caps.
6. Collapse correlated variants into families.
7. Compare candidates with market-only and current-registry baselines.
8. Nominate zero or more challengers that clear minimum stability, sample,
   complexity, concentration, and incremental-value gates.
9. Reuse an existing candidate version when its definition hash is unchanged;
   otherwise create a new version with a new activation boundary.

Discovery ranking is hypothesis evidence. It cannot authorize funding.

## Candidate Registry

The registry is append-only at the definition and lifecycle-event level.

Required entities:

### `discovery_runs`

Run specification, source watermarks, hashes, status, diagnostics, and output
references.

### `strategy_families`

Stable family identity, economic rationale, grammar provenance, correlation
group, and cumulative family-level evidence.

### `candidate_versions`

Candidate ID/version, exact rule payload, content hash, source run, activation,
pricing/execution/risk versions, and current research role.

### `evaluation_cohorts`

Candidate version, activation boundary, eligible source interval, watermarks,
coverage/fill/settlement requirements, and current completeness.

### `candidate_scorecards`

As-of watermark, discovery and forward statistics, effective dates,
station/dates, execution counts, cost, PnL, R/R, uncertainty, drawdown,
markouts, capacity, concentration, and rejection counts.

### `candidate_lifecycle_events`

Generated, nominated, shadow-activated, champion-assigned, challenger-assigned,
degraded, retired, rejected, and Phase-4-requested events with reason and time.

No status update deletes an earlier event or scorecard.

## Continuous Forward Evaluation

Each active candidate version is evaluated whenever new tape/outcomes become
available. Only rows at or after its activation may enter forward evidence.

The evaluator reports:

- raw, eligible, deduplicated, valid, postable, executed, partial, missed, and
  invalid counts;
- effective resolved market dates and station/dates;
- cost, fees, PnL, R/R, uncertainty, drawdown, wins, VWAP, and capacity;
- conservative/base/optimistic/actual scenarios kept separate;
- selected-versus-executed and filled-versus-missed comparisons;
- markouts at declared horizons;
- settlement provenance/disagreement and all fail-closed reasons;
- common-date comparison with its family champion and relevant baselines.

Forward evaluation never changes a candidate definition. A revised rule is a
new candidate version whose evidence starts at its own activation.

## Champion/Challenger Policy

Champion assignment is conservative and scoped by correlated family or
portfolio slot. It is not necessary to identify one global winner.

- Maintain zero or one research champion per declared slot and a small bounded
  set of challengers.
- Compare champion and challengers on common eligible post-activation dates;
  do not compare one candidate's long history with another's short favorable
  interval without an aligned-date report.
- Require incremental portfolio value after the existing champion consumes
  shared station/date and daily caps.
- Prefer the simpler candidate when evidence is statistically/economically
  indistinguishable.
- A challenger may replace a champion only after predeclared forward sample,
  economics, uncertainty, concentration, execution, and settlement gates pass.
- A champion that degrades may move to probation or retirement. The system may
  return to no champion.
- Multiple versions from one family do not count as independent confirmation.

Registry role changes affect research/shadow reporting only. Phase 4 remains a
separate explicit request for an exact candidate version, tactic, and size.

## Continuous Improvement Loop

The default loop is:

1. Collect snapshots, tape, and outcomes continuously.
2. Resolve new market dates and update active candidate scorecards.
3. Run discovery on the declared cadence when meaningful new data exist.
4. Register new challenger versions and preserve unchanged identities.
5. Evaluate all active candidates prospectively.
6. Update champion/challenger/retired roles under fixed transition rules.
7. Publish a compact operator report and TUI health/status view.
8. Review grammar/model/source changes separately; version them before use.
9. Repeat without rewriting prior candidate evidence.

The loop should initially be scheduled manually or by a dry-run service. Fully
automatic research scheduling is allowed only after idempotency, locking,
bounded candidate counts, runtime/storage limits, and failure visibility pass.
Automatic funded promotion is out of scope.

## Candidate State Machine

```text
GENERATED
   | discovery gates pass
   v
NOMINATED
   | activation recorded
   v
SHADOW_ACTIVE <--------------------------+
   |                                     |
   +--> CHALLENGER --+                   |
   |                 | common-date pass  |
   |                 v                   |
   +-------------> CHAMPION              |
   |                 |                   |
   |                 +--> PROBATION -----+
   |
   +--> REJECTED
   +--> RETIRED
   +--> PHASE4_REQUESTED (separate approval)
```

Every transition is an append-only event. A new candidate version starts at
`GENERATED`; it never inherits the forward evidence of the version it replaces.

## Build Plan

### Slice C0: Respecification And Migration

- Replace one-winner terminology with recurring runs and candidate versions.
- Map the committed batch implementation to reusable versus transitional code.
- Define registry schema, state machine, cadence, and migration compatibility.
- Keep existing CLI artifacts readable as `batch_v1` evidence.

Exit: canonical docs and tests describe one continuous architecture; no code
path claims that a one-winner batch is the completed Phase 3D system.

### Slice C1: Causal Materializer And Replay Primitives

- Retain the implemented broad row contract, read-only source access, exact
  quote-ready reconstruction, validity checks, and ask-sweep primitives.
- Separate row materialization from winner/manifest assumptions.
- Add deterministic materializer fixtures across multiple tape sessions/gaps.

Exit: identical sources/configuration produce identical policy-neutral rows,
and invalid intervals fail closed.

### Slice C2: Durable Candidate Registry

- Add migrations and repositories for runs, families, candidate versions,
  cohorts, scorecards, and lifecycle events.
- Enforce unique content hashes, immutable definitions, append-only events,
  one scheduler writer, and read-only observers.
- Import optional `batch_v1` outputs without treating them as forward evidence.

Exit: repeated registration is idempotent; changed rules create new versions;
historical evidence cannot be overwritten.

Implementation: complete. `weather_trader/discovery/registry.py` owns schema
version 1, database-level append-only guards, content-addressed family and
candidate registration, activation-bounded cohorts, watermark-addressed
scorecards, lifecycle events, a nonblocking one-writer lock, and query-only
observers. `scripts/phase3d_registry.py` initializes/inspects the external
catalog and can import `batch_v1` identity without importing forward evidence.

### Slice C3: Recurring Discovery Orchestrator

- Generalize the implemented grammar/scoring into a run that nominates a
  bounded set of challengers rather than one winner.
- Trigger from meaningful resolved-data watermarks.
- Persist run diagnostics and no-nomination outcomes.
- Collapse families and enforce candidate-count/runtime budgets.

Exit: repeated runs over unchanged watermarks are no-ops; later runs append
new evidence/candidates without changing earlier runs.

### Slice C4: Continuous Cohort Evaluator

- Evaluate every active candidate from its own activation boundary.
- Persist scorecards by as-of watermark and common-date comparisons.
- Separate discovery, post-activation shadow, and actual-order evidence.
- Preserve venue settlement, markout, and fill-scenario fail-closed gates.

Exit: candidate results update idempotently as new dates resolve, and no row is
credited to a version before activation.

### Slice C5: Champion/Challenger Transitions

- Implement predeclared nomination, champion, probation, retirement, and
  rejection rules.
- Require aligned-date and incremental-portfolio comparisons.
- Retain family-level failure history across candidate versions.
- Keep every transition research-only.

Exit: deterministic inputs produce deterministic role transitions; the system
can select no champion and cannot erase failed evidence through version churn.

### Slice C6: Scheduling And Operator Visibility

- Add a bounded dry-run scheduler/service after manual idempotency passes.
- Expose last run, source watermarks, active candidates, stale evaluation,
  errors, and scorecard summaries in reports/TUI.
- Add restart locks, runtime/storage budgets, and alertable failures.

Exit: continuous discovery/evaluation survives restart without duplicate runs,
unbounded candidates, silent failure, or TUI lifetime ownership.

### Slice C7: Phase 4 Handoff

- Produce an exact candidate-version package only after settlement-aligned,
  fill-conditioned forward gates pass.
- Require explicit operator approval for controlled real-order validation.
- Reconcile public replay with private order truth at the tested tactic/size.

Exit: Phase 4 receives one exact approved candidate version without stopping
or constraining the broader discovery loop.

## Current Repository Mapping

Commits `97baafb` and `a5de0c8` implemented/documented a deterministic batch
vertical slice. Preserve them as history; modify forward instead of reverting.

Reusable now:

- `weather_trader/discovery/materializer.py`: causal broad-row construction;
- `weather_trader/tape/replay.py`: read-only exact-book replay and ask sweep;
- `CandidateRule`, row hashes, source watermarks, run hashes, and immutable JSON
  primitives in `weather_trader/discovery/contracts.py`;
- fixed grammar generation, walk-forward scoring, complexity penalty, and
  correlated-family collapse in `weather_trader/discovery/engine.py`;
- fail-closed activation and venue-settlement checks;
- deterministic unit fixtures in `tests/test_phase3d_discovery.py`.
- `weather_trader/discovery/registry.py`: durable append-only runs, families,
  versions, cohorts, scorecards, lifecycle history, and writer ownership;
- `scripts/phase3d_registry.py`: registry initialization, read-only status, and
  identity-only `batch_v1` import.

Transitional and to be refactored:

- `DiscoveryRunSpec.maximum_winners == 1`;
- `freeze_winner_manifest` and one-winner/no-winner output semantics;
- the single-manifest `phase3d_forward_report.py` workflow;
- tests and docs that treat one global winner as the Phase 3D exit;
- repository status claims that D0-D3 complete the intended operating system.

Until C3-C5 are implemented, the existing discovery/forward CLIs are read-only batch research
tools. Do not run them as the production discovery scheduler, create a funded
strategy from their output, or treat their single manifest as the intended
steady-state architecture.

## Acceptance Checklist

- [x] Broad rows are independent of current policies and named V2 pilots.
- [x] Implemented tape joins are causal and fail closed on invalid coverage.
- [x] Existing row/run identities are deterministic for fixed inputs.
- [x] Registry schema and append-only lifecycle events are implemented.
- [ ] Discovery runs recur idempotently from resolved-data watermarks.
- [ ] Runs may nominate zero or a bounded number of challengers.
- [x] Unchanged candidate definitions reuse their existing version identity.
- [x] Changed definitions create a new version identity and require a new
  post-activation cohort.
- [ ] Active candidates receive continuous scorecard updates.
- [ ] Common-date champion/challenger comparisons are deterministic.
- [ ] Family-level evidence survives version replacement and retirement.
- [ ] Candidate and runtime/storage budgets prevent unbounded growth.
- [ ] Venue settlement and research-truth disagreement remain explicit.
- [ ] Conservative/base/optimistic/actual fills remain separate.
- [ ] Positive optimistic-only evidence cannot trigger a role promotion.
- [ ] No discovery or registry transition can authorize funded trading.
- [ ] Phase 4 receives only an explicitly approved exact candidate version.

## Kill And Pivot Rules

- If no family survives costs and walk-forward stability, register no
  challengers and continue collecting; do not widen the grammar automatically.
- If a candidate fails forward evaluation, retain the failure and reject or
  retire that version; do not repair its existing cohort retrospectively.
- If version churn repeatedly resets the same family, suspend new versions for
  that family until a materially new causal input or economic rationale exists.
- If nearby variants alternate at the top, collapse them into one unstable
  family rather than reporting independent confirmation.
- If quality depends on station exceptions, tiny fills, one weather date, or
  research truth that disagrees with venue settlement, reject it.
- If V2 pricing is positive but execution-aware evidence is negative, kill the
  tactic rather than expanding the search space automatically.
- If no forecast/model feature adds value over the causal market baseline,
  prioritize forecast and target-truth research instead of execution complexity.
- If continuous scheduling creates duplicate runs, unbounded state, or silent
  stale evaluation, disable the scheduler and retain manual idempotent runs.

## Decision Log

- 2026-08-04: Respecified Phase 3D as a continuous versioned discovery system.
  Discovery runs now recur, may nominate multiple bounded challengers, and feed
  append-only candidate cohorts plus champion/challenger scorecards. Candidate
  versions remain immutable for attribution, but the strategy program never
  freezes. Preserved the committed batch implementation as reusable vertical
  infrastructure and marked its one-winner orchestration transitional.
- 2026-08-04: Completed C2 with a durable external registry, schema migration,
  append-only database guards, single-writer locking, read-only observation,
  idempotent content-addressed versions/cohorts/scorecards/events, and an
  identity-only `batch_v1` importer. C3-C5 remain open and no funded state
  changed.
- 2026-08-04: Implemented the deterministic batch materializer, fixed grammar,
  one-winner selection, and fail-closed stable-taker evaluator. No production
  discovery run or candidate was activated.
- 2026-07-30: Introduced policy-neutral constrained discovery, causal source
  cutoffs, complexity control, correlated-family collapse, and post-activation
  evidence as requirements before controlled funded validation.
