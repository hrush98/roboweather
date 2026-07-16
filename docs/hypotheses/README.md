# Hypothesis And Decision Records

Use this directory for structured trading, model, execution, sizing, and risk hypotheses.

A hypothesis record is the place to write down what we believe, why we believe it, what would prove it wrong, and which gates must pass before it changes live behavior.

A hypothesis is the economic or behavioral **why**, not the feature implementation plan. When a hypothesis is approved for a non-trivial build, create or update a living plan under `docs/implementation/` for architecture, module boundaries, sprint slices, and acceptance tests. Link the two documents rather than expanding the hypothesis into a project plan.

Current system-wide conclusions belong in `docs/current-trading-system-audit.md`; phase sequencing belongs in `docs/execution-rebuild-roadmap.md`.

## Filename Format

```text
YYYY-MM-DD-short-slug.md
```

Examples:

```text
2026-06-12-global-low-mvp-size-up.md
2026-06-12-hrrr-inland-late-overlay.md
```

## Template

```markdown
# YYYY-MM-DD Short Title

## Status

Proposed | Research | Canary | Live | Rejected | Superseded | Closed

## Hypothesis

State the expected EV mechanism in one or two sentences.

## Expected Mechanism

Why should this work? Include the market inefficiency, model behavior, execution behavior, station/regime condition, or risk-allocation logic.

## Scope

- Market family:
- Stations/regimes:
- Side:
- Entry band:
- Local window:
- Model/source:
- Policy/sleeve name:

## Evidence Required

- Replay gate:
- Recent-window requirement:
- Minimum resolved sample:
- Fillability/depth requirement:
- Live canary requirement:

## Current Evidence

Summarize replay/live evidence with dates, DB path, script command, risk, PnL, R/R, sample size, and caveats.

## Risks And Failure Modes

List known ways this can be wrong.

## Kill Conditions

Define objective conditions that deactivate or reject the hypothesis.

## Gates Added Or Required

List tests, scripts, checklist items, or code rules that enforce the lesson.

## Review Trigger

Examples: after 20 resolved trades, after 7 live trading days, after next settlement batch, before any size-up.

## Decision Log

- YYYY-MM-DD: Initial hypothesis created.
```

## Where Gates Go

Do not store gates only in this directory. Put them where they are enforceable:

- Tests: `tests/`
- Replay/report gates: `scripts/`
- Operator workflow gates: `AGENTS.md` or `docs/live-trading-journal.md`
- Live strategy/risk gates: source modules under `weather_trader/`

Reference the gate from the hypothesis record so future reviews know what changed.
