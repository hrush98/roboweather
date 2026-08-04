# Tape-Backed Strategy Discovery And Freeze

Status: D0-D3 repository path implemented; D4 stable-taker path implemented but operational acceptance blocked on a prospective manifest, venue settlement, and markouts

Last updated: 2026-08-04

## Feature Goal

Build a policy-neutral research path in which simple weather-market strategies
emerge from the causal combination of forecast snapshots, model outputs, market
tape, execution labels, and venue-authoritative settlement rather than being
chosen before the measurement data are inspected.

The deployed strategy must remain simple even though the offline discovery
substrate is rich:

```text
policy-independent market tape
+ causal forecast/observation/model snapshots
+ valid execution and settlement labels
                         |
                         v
             constrained strategy discovery
                         |
                         v
                  one simple winner
                         |
                         v
             immutable strategy manifest
                         |
                         v
               untouched future tape
                         |
                         v
             controlled real-order test
```

This contract owns discovery, selection, and freezing. Phase 3 owns market-tape
validity and replay semantics; Price Sheet V2 owns calibrated economic pricing
and execution reductions; Phase 4 owns controlled funded validation.

## Non-Goals

- Do not predeclare a named MVP, HRRR, consensus, station, or side as the winner.
- Do not search the same market dates used for forward confirmation.
- Do not treat tape events, prediction snapshots, stations, or buckets as
  independent when they share a market date or weather regime.
- Do not build an unconstrained learned live quote policy in this phase.
- Do not materialize a permanent quote grid for every candidate combination.
- Do not duplicate raw market tape or bulky forecast artifacts in another
  database.
- Do not use weather-outcome scoring for promotion when venue settlement is
  available or the two sources disagree.
- Do not let an attractive diagnostic slice silently become a trading filter.

## Relationship To Existing Work

- Broad `prediction_snapshots` provide causally timestamped forecasts,
  observations, model probabilities, selected tokens, and reconstruction IDs.
- Phase 3 Slices 2-4 provide policy-independent market events, deterministic
  books, validity intervals, and causal token/time joins.
- Phase 3 Slice 5 provides conservative fill bounds, markouts, and
  coverage-invalidated labels.
- Price Sheet V2 Slice 5 provides a reusable tape-feature materializer with a
  broad discovery view and a frozen V2 view.
- Phase 3 Slice 6 consumes the immutable winning manifest on tape strictly
  after its activation time.
- Price Sheet V2 Slice 7 and Phase 4 compare public replay with actual order
  truth and validate useful-size capacity.

The two current late V2a pilots remain valuable end-to-end controls and
plumbing fixtures. They receive no presumption that one must be the discovered
winner.

## Research Substrate Contract

The substrate is a derived, reproducible view over existing immutable sources,
not a new raw recorder.

Every eligible row must identify:

- source prediction snapshot IDs and stable candidate/decision ID;
- model artifact and probability-output versions;
- forecast and observation source timestamps plus local receipt timestamps;
- station, market date, family, bucket, side, and token;
- lifecycle horizon and observation/forecast freshness;
- quote-ready timestamp and configured latency arm;
- tape session, partitions, coverage interval, and reconstruction hashes;
- decision-time bid, ask, spread, depth, flow, and capacity features;
- calibration and conservative-price versions when available;
- passive and stable-taker execution inputs under identical economic ceilings;
- conservative, base, and optimistic fill labels kept separate;
- post-decision and post-fill markouts;
- venue settlement, provenance, revision state, and any research-truth
  disagreement;
- source/build/config fingerprints and discovery eligibility reason.

Rows with missing causal timestamps, invalid tape coverage, unresolved token
identity, or unresolved settlement disagreement remain retained with explicit
ineligibility reasons. They may not receive an executable or profitable label.

The materializer must expose two views:

1. `broad_discovery_view`: every eligible causal snapshot/token decision,
   independent of current policies or V2 pilots.
2. `frozen_evaluation_view`: only rows selected by an immutable strategy
   manifest, using identical source and feature semantics.

Repeated materialization against the same source watermarks and configuration
must produce the same row IDs, fields, eligibility decisions, and hashes.

## Constrained Discovery Protocol

### Immutable Discovery Run

Before a discovery run starts, persist:

- discovery-run ID and implementation/build hash;
- inclusive source start and end plus exclusive discovery cutoff;
- exact tape sessions/partitions and research-database watermark;
- outcome and venue-settlement versions;
- candidate-rule grammar and complexity budget;
- walk-forward folds, minimum effective samples, and cluster definitions;
- scoring metrics, costs, fill scenario, latency, size, and portfolio caps;
- stability, concentration, and rejection thresholds;
- correlated-variant collapse and winner-selection rules;
- maximum number of winners;
- earliest permitted forward activation timestamp.

Changing any item creates a new discovery-run version. It cannot revise the
already declared holdout.

### Initial Simplicity Budget

The initial rule grammar should permit breadth in the offline search while
keeping every resulting strategy auditable:

- one primary forecast/model/consensus score;
- at most two eligibility filters;
- one declared lifecycle horizon or continuous information-state region;
- one side-selection rule;
- one first-entry/deduplication scope;
- one passive or stable-taker execution arm;
- one price ceiling and size/cap rule;
- no station-specific exception list;
- no learned online resizing or quote-policy model.

Broader grammars require a separately versioned discovery run and stronger
complexity penalty. The grammar may search model identity, agreement,
forecast/observation freshness, lifecycle state, side, price band, spread,
depth, and simple flow/toxicity state only when those inputs are causal and
available across the declared discovery window.

### Walk-Forward Selection

- Score with date-ordered walk-forward folds.
- Fit calibration, uncertainty, or thresholds only on dates before each
  evaluation fold.
- Cluster primary uncertainty by market date and report station/date counts
  separately.
- Require minimum effective resolved dates and executable station/dates before
  ranking a family.
- Compare all-loaded, recent, and regime/date-fold stability without selecting
  a filter solely because one diagnostic slice won.
- Collapse nearby delay, clock-window, price-cap, and scope variants into one
  correlated family.
- Apply an explicit complexity penalty before ranking.
- Prefer the simplest stable candidate to the highest in-sample R/R.
- Replay portfolio caps and priority before declaring candidates additive.
- Select at most one primary winner initially. A second candidate must show
  independent incremental value and receives a separate manifest.

Discovery output is hypothesis evidence only. It cannot authorize funding.

## Immutable Strategy Manifest

The selected winner must be serialized and hashed before the forward window
begins. Required fields include:

- strategy ID, version, discovery-run ID, and source cutoff;
- exact model/forecast inputs and versions;
- causal feature definitions and allowed missing-data behavior;
- side, bucket, horizon, time, entry, and deduplication rules;
- calibration, uncertainty, cost, and maximum-price versions;
- execution arm, latency, TTL/cancellation behavior, size, and capacity limits;
- station/date, side, bucket, portfolio, and daily risk caps;
- settlement source and required provenance;
- activation timestamp and untouched holdout start;
- all source, code, configuration, and manifest hashes.

No manifest field may be retuned during the holdout. Any change creates a new
candidate with a new future activation time; the earlier holdout remains
reported.

## Untouched Forward Evaluation

Forward evidence may use only decisions whose causal availability and tape
events occur after the manifest activation boundary.

The report must include:

- raw, eligible, deduplicated, valid, postable, filled, partial, missed, and
  invalid counts;
- effective resolved market dates and station/dates;
- cost, fees, PnL, R/R, drawdown, wins, VWAP, and capacity;
- results by date, station, regime, side, and execution arm as diagnostics;
- conservative/base/optimistic fills and actual fills kept separate;
- selected-versus-executed and filled-versus-missed comparisons;
- markouts at predeclared horizons;
- concentration and common-weather-date stress;
- venue settlement and any label disagreement;
- all rejected rows and fail-closed reasons.

Passing requires positive base-case, fill-conditioned, settlement-aligned
economics across enough independent dates/regimes; positive optimistic-only
results fail. The report may recommend rejection, continued collection, or a
Phase 4 request. It may not modify the strategy.

## Build Slices

### Slice D0: Freeze Discovery Contracts

- Define source, row, grammar, fold, scoring, complexity, manifest, and report
  schemas.
- Freeze initial simplicity and effective-sample rules.
- Add deterministic IDs and hashes.

Exit: the same inputs always define the same discovery run and candidate space.

### Slice D1: Broad Joined Substrate

- Generalize Price Sheet V2 Slice 5 beyond selected pilots.
- Join all eligible causal snapshots to valid quote-ready tape state.
- Add Phase 3 fill/markout fields and settlement provenance.
- Emit broad and frozen views without copying raw tape.

Exit: sampled rows reconstruct exactly to snapshots, tape, coverage, and
settlement; invalid intervals fail closed.

### Slice D2: Constrained Walk-Forward Discovery

- Generate the predeclared simple-rule grammar.
- Run date-clustered walk-forward scoring and complexity penalties.
- Collapse correlated variants and apply portfolio caps.
- Produce a reproducible ranked family report.

Exit: repeated runs over identical watermarks return the same candidates,
scores, family collapse, and winner.

### Slice D3: Winner Freeze

- Select at most one primary winner under the predeclared rule.
- Persist the immutable strategy manifest and activation boundary.
- Publish rejected and runner-up families without granting them holdout access.

Exit: manifest and source hashes are frozen before the first holdout decision.

### Slice D4: Untouched Forward Tape

- Evaluate only post-activation decisions through Phase 3 Slice 6.
- Preserve fail-closed coverage and fill scenarios.
- Use venue-authoritative settlement and date-clustered evidence.

Exit: the report produces a pass, continue-collecting, or reject decision
without retrospective retuning.

### Slice D5: Controlled Real-Order Validation

- Request Phase 4 only for a passing exact manifest.
- Reconcile public replay against private order truth.
- Advance separately through plumbing, `$50`, and `$100` evidence.

Exit: the exact strategy/tactic/size passes Phase 4 or is rejected.

## Repository Implementation

The initial deterministic Phase 3D path is implemented in
`weather_trader/discovery/` and two read-only CLIs:

- `scripts/phase3d_strategy_discovery.py` freezes the complete run contract
  before ranking, materializes every selected snapshot/token decision in the
  declared source window, reconstructs its exact quote-ready book from valid
  tape, searches the fixed simple-rule grammar, collapses correlated variants,
  and writes either one immutable winner manifest or an immutable no-winner
  result.
- `scripts/phase3d_forward_report.py` verifies the manifest hash, rejects
  pre-activation rows, rematerializes only later source dates with the same
  causal book semantics, applies the exact frozen rule, and produces a
  `PASS_TO_PHASE4_REQUEST`, `CONTINUE_COLLECTING`, or `REJECT` disposition.
- The broad view retains tape failures and settlement disagreement as explicit
  ineligibility reasons. Discovery may use research weather truth when venue
  truth is absent, but forward evaluation counts only venue-authoritative
  labels toward a pass.
- The initial grammar has no fitted thresholds. Its fixed model, side, delay,
  local-window, and entry-band variants are frozen in the run spec; three or
  more contiguous date folds measure stability without training on a future
  date.

No production discovery run or prospective manifest has been frozen yet.
Current `resolutions` tables contain no venue rows, and the shared-tape stack
does not yet provide fill-conditioned markouts. Those are operational D4 exit
gates, not reasons to substitute IEM weather outcomes or optimistic fills.

## Acceptance Checklist

- [x] Broad discovery rows are independent of current policy and V2 pilot
      selection.
- [x] Every implemented discovery feature is causally available by quote readiness.
- [x] Invalid or incomplete coverage fails closed.
- [x] Venue settlement and research-truth disagreement are explicit.
- [x] Discovery cutoff, source watermarks, grammar, folds, metrics, costs, and
      complexity rules are frozen before ranking.
- [x] Initial fixed-rule folds fit no thresholds on evaluation dates; future
      fitted grammar must train only on earlier dates.
- [x] Effective sample and stability are market-date clustered.
- [x] Correlated variants are collapsed before winner selection.
- [x] The initial winner respects the simplicity budget.
- [ ] One immutable manifest is persisted before holdout activation.
- [x] The forward evaluator accepts only strictly post-activation rows and
      cannot retune the manifest.
- [ ] Conservative/base/optimistic/actual fills remain separate.
- [ ] Positive optimistic-only evidence cannot pass.
- [ ] Phase 4 receives only the exact passing manifest, tactic, and size.

## Kill And Pivot Rules

- If no simple family survives walk-forward stability and costs, record no
  winner and continue collection or reject the current signal universe.
- If the winner fails untouched forward tape, reject that manifest; do not
  repair it with a holdout-derived filter.
- If many near-identical variants alternate at the top, treat them as one
  unstable family rather than independent confirmation.
- If strategy quality depends on station exceptions, tiny fills, one date, or
  weather rather than venue settlement, reject it.
- If V2 pricing is positive but execution-aware discovery is negative, kill
  the tactic rather than expanding the search grammar automatically.
- If no current forecast/model feature adds value over the causal market
  baseline, prioritize forecast and target-truth research instead of execution
  complexity.

## Decision Log

- 2026-08-04: Implemented the deterministic D0-D3 repository path plus a
  fail-closed stable-taker D4 evaluator. Kept the operational manifest,
  venue-settlement, fill-scenario, and markout gates open; no strategy was
  selected or promoted by the build.

- 2026-07-30: Made policy-neutral constrained strategy discovery a first-class
  Phase 3D gate. Required a broad joined substrate, predeclared simple-rule
  grammar, date-clustered walk-forward selection, one immutable winner, and
  untouched post-activation tape before any controlled real-order request.
