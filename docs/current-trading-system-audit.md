# Current Trading System Audit

This is the living financial and systems audit for RoboWeather. Update this document in place when new evidence changes the assessment. Do not create a new narrative audit report for each review.

Generated or ad hoc analysis may live under `reports/`, but durable conclusions, open risks, and decisions belong here. The current implementation sequence belongs in `docs/execution-rebuild-roadmap.md`; funded operating state belongs in `docs/live-trading-journal.md`.

Last reviewed: 2026-07-17

## Current Verdict

RoboWeather is an execution-first research system with funded trading paused. The immediate objective is to determine whether promising weather signals can be converted into positive fill-conditioned PnL at useful size. A parallel forecast-edge research track is now warranted because the fresh configured portfolio failed before execution and broad recent model fairs were overconfident.

The shared weather market tape is reported built and running, with acceptance and replay evidence accumulating. It can remove policy-specific collection bias and make causal taker/passive replay possible, but it does not itself prove forecast alpha, exact passive fills, capacity, or profitability. The immediate implementation priority is Price Sheet V2a; V2b will consume valid tape windows.

Current confidence by layer:

| Layer | Assessment | Decision |
| --- | --- | --- |
| Research collection | Broad snapshot collection is useful and operational. The previous memory-growth blocker is resolved. | Continue collection. |
| Forecast and settlement truth | Existing METAR/HRRR point features are rich, but the IEM maximum used for research truth has not been reconciled to Weather Underground/venue settlement, and no new ensemble/spatial source has passed incremental-skill gates. | Audit target fidelity first; then test WeatherNext/NBM, spatial residuals, and observed radiation on identical causal rows. |
| Current configured portfolio | Failed the fresh July 9-14 cap-aware replay. | Do not restart it. |
| New late HRRR signals | Promising but based on six correlated weather dates. | Freeze as forward shadow hypotheses, not funded strategies. |
| Fair-value/price sheet | Existing scoped price sheet failed its updated theoretical gate. | Redesign around walk-forward calibration and market-aware shrinkage. |
| Phase 3 shared tape | Collector reported operational; replay validity and evidence gates are still accumulating. | Keep collecting and validate coverage/replay contracts. |
| Existing candidate-token shadow collector | Useful plumbing prototype, but candidate-scoped and not a continuous causal market tape. | Do not use its fill labels as promotion evidence. |
| Fillability and adverse selection | Still unresolved. Public L2 data can provide bounds, not exact hypothetical queue position. | Validate shared-tape replay, then run controlled real-order canaries. |
| Funded readiness | No exact signal + quote policy + size has passed current fill-conditioned gates. | Keep funded trading paused. |

## Evidence Snapshot

The July 15 collection review used a read-only backup containing more than one million prediction snapshots and evaluated the fresh July 9-14 window.

Durable conclusions from that review:

- The fresh cap-aware portfolio lost `$428.45` on `$2,089.05` risk, or `-0.205 R/R`. The no-depth upper bound was also negative, so displayed depth was not the primary explanation.
- The existing Phase 1 price sheet fell to `-0.007 R/R` all-history, `-0.020` over the last 30 days, and `-1.000` on its only fresh-window row.
- HRRR-rich tuned dynamic late and HRRR-v2 dynamic late were the most stable new standardized signal candidates, but their strongest fresh evidence covers only six weather dates.
- Broad recent model fairs remained overconfident. New price sheets should use walk-forward empirical calibration or market-aware shrinkage rather than raw model fairs.
- Static ask depth was useful for triage but did not establish actual or passive fillability.
- The METAR+HRRR tuned-dynamic artifact was behaviorally identical to its HRRR-rich counterpart in the fresh window and must not be counted as independent confirmation.
- The current US training/resolution path defines the daily high as the maximum IEM `tmpf` report while active markets identify Weather Underground station history as their resolution source. Source equivalence is plausible but unproven near one-degree bucket boundaries.

Evidence source: `reports/research-collection-analysis-2026-07-15.md`. That file is an ad hoc analysis artifact; the conclusions above are canonical here.

## What Is Working

- Broad causal prediction snapshots preserve the opportunity universe better than materialized policy tables.
- Raw-snapshot replay, cap-aware portfolio ordering, and whole-chain attribution have exposed several previously hidden selection and overlap errors.
- Funded trading is paused rather than allowing positive historical replay to override negative current or fill-conditioned evidence.
- The documentation already distinguishes selected replay, filled replay, actual PnL, and venue settlement.
- The new market-tape hypothesis correctly moves collection from model/policy-specific rows to a reusable token-level event stream.
- The current weather feature stack is a credible point-observation/HRRR baseline, which makes controlled source ablation possible; the next forecast gains should come from target fidelity, probabilistic ensembles, spatial residuals, and observed heating surprise rather than more model variants over the same inputs.

## Open Risks And Required Gates

| Priority | Risk | Why it matters | Required gate |
| ---: | --- | --- | --- |
| 1 | False or incomplete fill labels | Price changes, book touches, or gapped feeds can be mistaken for fills. | Trade-direction tests, deterministic replay fixtures, book reconstruction, and gap-invalidated coverage intervals. |
| 2 | Signal miscalibration | A fillable negative-EV quote still loses money. | Positive recent walk-forward quoted-price EV using calibrated or shrunk fairs. |
| 3 | Adverse fill selection | Filled rows have historically underperformed missed rows. | Positive filled-subset EV and non-toxic markouts for the exact quote rule. |
| 4 | Small correlated samples | Stations, models, and sleeves often express the same weather-date risk. | Evaluate effective sample by market date/regime and use uncertainty bounds, not raw snapshot counts. |
| 5 | Settlement and sensor mismatch | Research truth currently uses the maximum IEM report, while active US markets reference Weather Underground and official ASOS maxima follow rolling-average/reporting rules. A one-degree mismatch can change the winning bucket. | Station/date comparison of venue, Weather Underground, CLI, routine METAR, and high-frequency ASOS outcomes; then venue-authoritative linkage or a versioned settlement mapping. |
| 6 | Capacity | Positive tiny fills do not prove `$50-$100` tradability. | Direct fill/miss evidence at the intended size. |
| 7 | Portfolio concentration | Regional or model-common errors can hit several positions together. | Market-date/regime stress limits and incremental portfolio replay. |
| 8 | Operational integrity | Gaps, clock drift, lag, backpressure, or storage growth can invalidate replay. | Collector health budget and invalid-data rules that fail closed. |

## Current Decisions

1. Keep funded trading paused.
2. Continue broad research snapshots and outcome resolution.
3. Stop adding model families merely to expand the leaderboard. New models need demonstrably different predictions or incremental information.
4. Keep the shared active-universe market tape running and complete its acceptance/replay evidence gates.
5. Treat the current candidate-token collector and shadow labeler as a prototype only.
6. Keep the late HRRR-rich tuned dynamic and HRRR-v2 dynamic policies as the primary forward signal hypotheses. Keep the two-of-four agreement rule exploratory until frozen and replayed behind portfolio caps.
7. Implement `docs/implementation/price-sheet-v2.md`: V2a walk-forward outcome pricing now, then V2b execution reductions/skips on valid tape windows.
8. Evaluate passive price-making and a separately tagged stable-taker control from the same tape.
9. Run the proposed forecast-edge program in parallel without changing the execution critical path: target/sensor truth audit first, then identical-coverage WeatherNext/NBM benchmarks, high-frequency spatial residuals, and GOES radiation/cloud surprise.
10. Keep weather-only probability, settlement mapping, market-aware calibration, and execution adjustment separately versioned. No new forecast source enters funded pricing until it demonstrates causal incremental skill.

## Promotion Standard

The promotable unit is:

```text
signal policy + fair-value version + quote rule + cancellation rule + size
```

Normal funded sizing requires all of the following:

- current-window and all-loaded selected replay are positive;
- quoted-price replay is positive after calibration, costs, and haircuts;
- continuous valid tape coverage exists from before decision through quote termination;
- conservative/base shadow fills are positive; optimistic-only profitability is insufficient;
- actual filled and filled-at-quote PnL are positive for controlled canaries;
- filled rows do not materially underperform comparable missed rows;
- post-fill markouts are not persistently toxic;
- Polymarket settlement is authoritative and linked;
- the result survives portfolio caps and market-date/regime concentration checks;
- the tested size has direct evidence.

Small funded orders may validate plumbing and replay fidelity, but they confer no capacity or promotion authority. `$50` and `$100` capacity claims require evidence at those sizes.

## Kill And Pivot Rules

- Kill a quote tactic when base-case filled EV is negative even though selected replay is positive.
- Kill a signal sleeve when recent selected replay is negative before execution.
- If passive execution is negative but the stable-taker control is positive, retain only the taker mechanism that passed.
- If passive and stable-taker execution are both negative, stop that sleeve or venue instead of adding more retrospective filters.
- Do not scale a result that depends on optimistic queue assumptions, extreme raw fairs, settlement mismatches, or a few correlated weather dates.

## Audit Update Protocol

Update the body of this document when the current assessment changes. Append one short entry below describing the evidence that caused the change; keep detailed generated tables in reproducible reports or scripts.

## Audit Log

- 2026-07-16: Created the living audit from the July 15 research collection review and the market-tape systems audit. Confirmed the research memory-growth prerequisite is resolved and approved Phase 3 market-tape implementation while keeping funded trading paused.
- 2026-07-16: Recorded operator confirmation that Phase 3 is built and running, with exit evidence still accumulating. Made Price Sheet V2a the current implementation priority and approved the V2b tape-overlay plan.
- 2026-07-17: Added the station-specific forecast-edge research track after reviewing the existing rich METAR/HRRR feature baseline and identifying unresolved IEM-versus-Weather-Underground/venue target fidelity. Prioritized truth reconciliation, WeatherNext/NBM probabilistic baselines, spatial residuals, and observed radiation surprise without changing funded status or the execution critical path.
