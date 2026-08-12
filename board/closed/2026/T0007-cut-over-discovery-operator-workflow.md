---
id: T0007
title: Cut over discovery operator workflow
status: CLOSED
priority: normal
owner: Codex
opened: 2026-08-12
last_touched: 2026-08-12
facts_fingerprint: d013a0c1dab80137fb3c4c8b4540fb36f144611f7bb052b82e3be1b1169e9880
closed: 2026-08-12
---

# T0007 Cut over discovery operator workflow

## Question

Can scripts/run_discovery.py become the sole observable operator surface and pass cold/resume, warm no-op, and new-data incremental production cycles while obsolete discovery automation remains inactive?

## Outcome

scripts/run_discovery.py is the sole Phase 3D operator workflow; atomic latest-complete status, TUI observability, retired compatibility routing, and final cold/resume, warm, and new-watermark production cycles all pass while scheduling and funded authority remain disabled.

## Evidence

- 31 focused tests passed. Production final code: interrupted cold cache resumed in 40.05s at 817956 KiB with 156346 mappings/20254 decisions/0 pending; warm completed in 7.07s with 0 scan/mapping/decision/replay work; natural watermark advanced 2730640 to 2731040 and completed in 7.10s; latest status validated HEALTHY; deliberate FAILED_ANALYSIS exit 2 preserved latest_complete.json SHA-256 4aabce99549b0bea4418d010e5c3454eecfe731fe8767509fb4b1fcf5d9376b3 byte-identically; research/tape active and old scheduler inactive/disabled.

## Durable Output

scripts/run_discovery.py; weather_trader/discovery/operator_status.py; weather_trader/ui/textual_app.py; deploy/systemd/compatibility/roboweather-phase3d-discovery.service; tests/test_discovery_operator_status.py; docs/implementation/tape-strategy-discovery.md; AGENTS.md; learning/L0004-attempt-status-is-not-last-known-good-state.md; commits 590b4a2 and dbc77ee
