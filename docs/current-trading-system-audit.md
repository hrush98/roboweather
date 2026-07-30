# Current Trading System Audit

This is the living financial and systems audit for RoboWeather. Update this document in place when new evidence changes the assessment. Do not create a new narrative audit report for each review.

Generated or ad hoc analysis may live under `reports/`, but durable conclusions, open risks, and decisions belong here. The current implementation sequence belongs in `docs/execution-rebuild-roadmap.md`; funded operating state belongs in `docs/live-trading-journal.md`.

Last reviewed: 2026-07-30

## Current Verdict

RoboWeather is an execution-first research system with funded trading paused. The immediate objective is to determine whether promising weather signals can be converted into positive fill-conditioned PnL at useful size. A parallel forecast-edge and full-market-lifecycle research track is now approved because the fresh configured portfolio failed before execution, broad recent model fairs were overconfident, and waiting until late intraday may forfeit earlier liquidity and price-discovery opportunities.

The shared weather market tape now contains retained data from July 23 onward and supports exact quote-ready taker-book replay. On July 29 the catalog held 1,662 partitions and 291.6 million raw events, while the bounded service was active. The strict lifecycle report still failed because sessions ended with errors, observed receipt lag exceeded 10 seconds, and no eligible closed market met the complete listing-to-close definition. The tape can remove policy-specific collection bias and now supports a first post-cutoff execution holdout, but it does not prove exact passive fills, settlement-aligned PnL, capacity, or funded readiness. The immediate pricing priority remains Price Sheet V2a; V2b may consume only tape windows that pass validity gates.

Current confidence by layer:

| Layer | Assessment | Decision |
| --- | --- | --- |
| Research collection | Broad snapshot collection is useful and operational. The previous memory-growth blocker is resolved. | Continue collection. |
| Forecast and settlement truth | Existing METAR/HRRR point features are rich, but the IEM maximum used for research truth has not been reconciled to Weather Underground/venue settlement, and no new ensemble/spatial source has passed incremental-skill gates. | Audit target fidelity first; then test WeatherNext/NBM, spatial residuals, and observed radiation on identical causal rows. |
| Full-market-lifecycle edge | Morning snapshots show more displayed depth than late afternoon, but current data do not establish D-1 traded volume, passive fills, round-trip capacity, or an early calibrated forecast. | Collect from first listing and test separate D-1/early arms against the frozen late control. |
| Current configured portfolio | Failed the fresh July 9-14 cap-aware replay. | Do not restart it. |
| New late HRRR signals | Promising but based on six correlated weather dates. | Freeze as forward shadow hypotheses, not funded strategies. |
| Fair-value/price sheet | Existing scoped price sheet failed its updated theoretical gate. V2a Slice 2 now proves leak-safe walk-forward calibration, but its first 98-row evaluation found the market baseline better than pooled or market-aware calibration. | Continue to conservative Slice 3 pricing without promoting a calibrator; allow the pilot signals to fail the quoted-EV gate. |
| Phase 3 shared tape | Slices 1-4 are implemented. Retained July 23-29 tape supports exact batch taker replay, but the lifecycle report fails on collector errors, receipt lag, and absent eligible closed markets; Slice 4 also lacks one real persisted-quote join. | Continue bounded collection and fail closed on gaps; repair lifecycle failures before claiming a pass. |
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
- The first V2a walk-forward read on 98 frozen July 16-29 decisions confirmed that overconfidence: raw-model Brier scores were `0.380` for HRRR-rich and `0.348` for HRRR-v2. Pooled and market-aware calibration improved those scores, but the causal market baseline remained better (`0.255` and `0.260`) than market-aware calibration (`0.288` for both). This is a build-validation result, not evidence to quote at the market price; the labels remain IEM weather outcomes rather than venue settlement.
- Static ask depth was useful for triage but did not establish actual or passive fillability.
- A deduplicated snapshot diagnostic since June 1 found mean displayed `$50` ask-sweep fillable notional of about `$46.94` at station-local 07, `$39.61` at 12, and `$27.84` at 17; corresponding full-`$50` rates were `26.7%`, `16.4%`, and `1.1%`. This supports earlier collection but is not actual volume or fill evidence, and current snapshots do not cover D-1 adequately.
- The METAR+HRRR tuned-dynamic artifact was behaviorally identical to its HRRR-rich counterpart in the fresh window and must not be counted as independent confirmation.
- The current US training/resolution path defines the daily high as the maximum IEM `tmpf` report while active markets identify Weather Underground station history as their resolution source. Source equivalence is plausible but unproven near one-degree bucket boundaries.

Evidence source: `reports/research-collection-analysis-2026-07-15.md`. That file is an ad hoc analysis artifact; the conclusions above are canonical here.

The July 29 rolling-discovery/tape holdout added the first execution evidence that was not limited to a previously materialized strategy:

- The research loop was still active with 1,465,280 snapshots through 2026-07-30 00:18 UTC, covering 36 model names and 18 stations. The shared-tape service was also active.
- A predeclared July 22 discovery cutoff selected three overlapping late US high-temperature families from the general raw-snapshot sweep: PM high regression 10m late, PM MVP late, and PM dynamic-tuned 10m late.
- Priority-order station/date deduplication produced 19 post-cutoff signals across six resolved July 23-28 market dates.
- Exact quote-ready tape reconstruction with 250 ms latency, 60 seconds of continuous valid pre-signal coverage, a `$0.50` cap, and a `$25` immediate ask sweep executed 12 positions. Three signals failed valid coverage and four had no asks at or below the cap.
- The 12 simulated taker executions cost `$205.51` and returned `+$93.22`, or `+0.454 R/R`, with six wins and average VWAP `$0.423`.
- This is a preliminary positive holdout, not promotion evidence: the sample is six correlated dates, partial taker fills are allowed, PnL uses weather outcomes rather than venue settlement, and passive queue fills and markouts are not modeled.

The result is reproducible with `scripts/tape_strategy_holdout_report.py`; the frozen economic hypothesis is `docs/hypotheses/2026-07-29-rolling-tape-portfolio-discovery.md`.

## What Is Working

- Broad causal prediction snapshots preserve the opportunity universe better than materialized policy tables.
- Raw-snapshot replay, cap-aware portfolio ordering, and whole-chain attribution have exposed several previously hidden selection and overlap errors.
- Funded trading is paused rather than allowing positive historical replay to override negative current or fill-conditioned evidence.
- The documentation already distinguishes selected replay, filled replay, actual PnL, and venue settlement.
- The new market-tape hypothesis correctly moves collection from model/policy-specific rows to a reusable token-level event stream.
- The current weather feature stack is a credible point-observation/HRRR baseline, which makes controlled source ablation possible; the next forecast gains should come from target fidelity, probabilistic ensembles, spatial residuals, and observed heating surprise rather than more model variants over the same inputs.
- The approved lifecycle design connects forecast revisions, first-listing tape, conservative price sheets, quote cancellation/repricing, and inventory/exit replay without pretending that the current late model can simply run a day earlier.

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
| 9 | Lifecycle stale-quote and inventory risk | D-1 quotes can be adversely selected around scheduled forecast releases, while filled inventory cannot be canceled and may be costly to exit. | Horizon-specific uncertainty/inventory reserves, release-aware cancel/reprice replay, exit-versus-hold evidence, and aggregate capacity caps. |

## Current Decisions

1. Keep funded trading paused.
2. Continue broad research snapshots and outcome resolution.
3. Stop adding model families merely to expand the leaderboard. New models need demonstrably different predictions or incremental information.
4. Continue the bounded shared market-tape lifecycle probe, diagnose its repeated session errors and receipt-lag breach, and require the strict lifecycle report to pass. Do not promote the probe into an unattended production service.
5. Treat the current candidate-token collector and shadow labeler as a prototype only.
6. Keep the late HRRR-rich tuned dynamic and HRRR-v2 dynamic policies as forward signal hypotheses. Add the frozen three-family late taker portfolio as a separate tape-backed forward-shadow hypothesis; do not fund or retune it on the July 23-28 holdout.
7. Implement `docs/implementation/price-sheet-v2.md`: V2a walk-forward outcome pricing now, then V2b execution reductions/skips on valid tape windows.
8. Evaluate passive price-making and a separately tagged stable-taker control from the same tape.
9. Run the approved research-only forecast-edge program in parallel without changing the execution critical path: target/sensor truth audit first, then identical-coverage WeatherNext/NBM benchmarks, high-frequency spatial residuals, and GOES radiation/cloud surprise.
10. Keep weather-only probability, settlement mapping, market-aware calibration, and execution adjustment separately versioned. No new forecast source enters funded pricing until it demonstrates causal incremental skill.
11. Extend research and collection to the full market lifecycle. Treat D-1 open, D-1 revision, D0 early, intraday, and late as separate horizons; add them one at a time behind forecast, tape, quote, inventory, exit, and portfolio gates.

## Promotion Standard

The promotable unit is:

```text
lifecycle horizon + signal/forecast version + fair-value version
+ quote/update/cancellation/exit rule + inventory cap + size
```

Normal funded sizing requires all of the following:

- current-window and all-loaded selected replay are positive;
- quoted-price replay is positive after calibration, costs, and haircuts;
- continuous valid tape coverage exists from before decision through quote termination;
- first-listing and D-1 claims use actual lifecycle coverage, not a later snapshot extrapolation;
- conservative/base shadow fills are positive; optimistic-only profitability is insufficient;
- actual filled and filled-at-quote PnL are positive for controlled canaries;
- filled rows do not materially underperform comparable missed rows;
- post-fill markouts are not persistently toxic;
- early filled inventory survives forecast-release markouts and its declared exit-versus-hold rule;
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

- 2026-07-29: Replayed a portfolio discovered only from pre-July-23 raw snapshots against later exact market tape. Twelve of 19 deduplicated signals executed for `+$93.22` on `$205.51` cost across six resolved dates; seven failed closed on coverage or capped liquidity. Recorded the result as preliminary forward-shadow evidence while the lifecycle report remains failed and funded trading remains paused.
- 2026-07-30: Completed Price Sheet V2a Slice 2 and a nonempty current-database smoke. All 98 frozen predictions used per-date calibrators trained strictly on earlier dates. Calibration reduced raw overconfidence but underperformed the decision-time market baseline on both pilots, so no pricing model was promoted and funded status did not change.
- 2026-07-23: Reconciled the Phase 3 build claims against the remote host. No recorder was active and retained evidence covered only approximately 18-second probes, so Slice 2 had not passed. Completed the missing future discovery, listing provenance, bounded-supervision, and executable lifecycle-gate paths while keeping the exit open for real elapsed coverage.
- 2026-07-16: Created the living audit from the July 15 research collection review and the market-tape systems audit. Confirmed the research memory-growth prerequisite is resolved and approved Phase 3 market-tape implementation while keeping funded trading paused.
- 2026-07-16: Recorded operator confirmation that Phase 3 is built and running, with exit evidence still accumulating. Made Price Sheet V2a the current implementation priority and approved the V2b tape-overlay plan.
- 2026-07-17: Added the station-specific forecast-edge research track after reviewing the existing rich METAR/HRRR feature baseline and identifying unresolved IEM-versus-Weather-Underground/venue target fidelity. Prioritized truth reconciliation, WeatherNext/NBM probabilistic baselines, spatial residuals, and observed radiation surprise without changing funded status or the execution critical path.
- 2026-07-17: Approved full-market-lifecycle research and collection from first listing through settlement. Added horizon-specific forecast, repricing, inventory, exit, tape, and promotion gates while preserving the late Price Sheet V2a pilot as the immediate critical path and keeping funded trading paused.
