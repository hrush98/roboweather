# Current Trading System Audit

This is the living financial and systems audit for RoboWeather. Update this document in place when new evidence changes the assessment. Do not create a new narrative audit report for each review.

Generated or ad hoc analysis may live under `reports/`, but durable conclusions, open risks, and decisions belong here. The current implementation sequence belongs in `docs/execution-rebuild-roadmap.md`; funded operating state belongs in `docs/live-trading-journal.md`.

Last reviewed: 2026-08-13

## Current Verdict

RoboWeather is a four-pillar net-edge research system with funded trading paused. The project develops information, settlement, execution, and costs/adverse-selection evidence as distinct verticals, then requires them to pass together for one exact signal, rule, and size. The immediate research emphasis is settlement truth and information edge while accepted tape, replay, pricing, and cost-measurement substrates continue collecting evidence. A parallel full-market-lifecycle track remains approved because the fresh configured portfolio failed before execution, broad recent model fairs were overconfident, and waiting until late intraday may forfeit earlier liquidity and price-discovery opportunities.

The shared weather market tape contains retained data from July 23 onward and supports exact quote-ready taker-book replay. Bounded validation run `tape-validation-20260730T183750Z-69556cbf` completed its 72-hour limit with 224.6 million events, 1,273 complete discovery refreshes, zero reconstruction errors, queue high-water 1/10,000, about 234 MiB peak recorder RSS, and projected raw growth of about 12.0 GB/day. Eligible tokens were `VALID` for about 96.7% of their observed lifetimes. The remaining gaps and late initial discovery are accepted as research-infrastructure limitations rather than a strategy-discovery blocker. Exact decision replay still rejects any signal whose pre-signal-through-execution window crosses a gap, so missing tape is never silently bridged. The likely missing application heartbeat, reconnect-path rediscovery delay, and incomplete per-event discovery reporting remain non-critical technical debt; another lifecycle run is not required before strategy discovery proceeds. The tape can remove policy-specific collection bias, but it does not prove exact passive fills, settlement-aligned PnL, capacity, or funded readiness.

Continuous research collection resumed on August 4 under enabled user service `roboweather-market-tape.service`. Fresh session `tape-20260804T152718Z-b341a291` reached `VALID` for all 1,474 subscribed tokens, completed discovery, rotated and strictly verified its first five-minute partition, kept queue high-water at 1/10,000 and RSS near 142 MiB, and recorded zero reconstruction errors. The August 2-to-August 4 outage remains an explicit unusable gap. The TUI now observes and controls the systemd-owned service without stopping it on TUI exit.

Policy-neutral causal strategy discovery remains a required Phase 3D gate. The implemented C3-C6 operating workflow failed its production-scale viability test: the scheduler repeatedly spent its full 900-second budget reconstructing historical tape per model row, then terminated recurring discovery with zero completed runs and zero candidate versions. That scheduler is now stopped and disabled while research and tape collection remain active; its historical failures are unhealthy analysis rather than valid no-nomination results.

The corrected Phase 3D path has now passed D0-D5 for a versioned delayed-checkpoint taker counterfactual. The cache passed production cold, 200/200 direct-replay, crash-resume, warm, relevant-increment, and resource gates. The bounded grid runs only from cached decisions, freezes correlated families before holdout access, and distinguishes every complete/incomplete/failed state. Exact candidate evaluation preserves registry identity and activation, uses registered execution caps, applies aligned/shared-cap comparisons, and keeps weather diagnostics distinct from unavailable venue/markout/order evidence. A same-day audit found that the production zero-survivor result had hidden a missing emerged-strategy bridge: historical wildcard-bucket, edge, and spread predicates could not be frozen losslessly and the command never wrote candidates. That bridge now seals an exact future-activated identity in the report and atomically appends the completed run and surviving candidates only after report success; end-to-end tests prove exact post-activation evaluation and all-or-nothing failure. `scripts/run_discovery.py` remains the sole operator command. Its final production acceptance passed interrupted cold/resume in 40.05 seconds at about 799 MiB RSS, a 7.07-second explicit zero-work warm cycle, and a 7.10-second natural new-watermark cycle. It atomically publishes latest-complete report/cache status to the TUI; a failed invocation left the prior record byte-identical. Production has zero registered versions and the latest historical holdout still reports no emerged strategy, so no candidate passed. The old scheduler remains inactive/disabled, its unit is archived, and no replacement scheduler is enabled.

Causal coverage, gap rejection, immutable candidate definitions, chronological validation, weather-versus-venue provenance, markout requirements, and separate Phase 4 approval remain unchanged. Current venue-resolution and fill-conditioned markout evidence is still insufficient, so no result can pass to Phase 4.

## Four-Pillar Edge Scorecard

```text
net trading edge = information advantage + settlement advantage
                 + execution advantage - costs and adverse selection
```

| Pillar | Current assessment | Next proof |
| --- | --- | --- |
| Information | Research-level advantage established for one forecast version, not yet tradable edge. F3's exact-cutoff remaining-heating ensemble improved corrected historical, untouched forward, and recent probability scores versus HRRR-rich; market-relative intervals remain uncertain. | Carry the frozen F3 version into F6 quoted-price/tape evaluation; run F4 spatial residuals next; evaluate WeatherNext only when approved ingestion-time history exists. |
| Settlement | Baseline mapping established, not an edge. F0 found the IEM routine/special report maximum in the venue-winning bucket on 220/220 comparable June station-dates; alternative physical/display sources materially disagreed. | Backfill venue bucket and versioned source provenance, preserve fail-closed unresolved rows, and validate the mapping out of cohort before any integrated promotion. |
| Execution | The causal tape, decision cache, and replay substrate work; an execution advantage is not proven. | Show non-toxic markouts and positive actual or conservative fill-conditioned economics for one exact quote/cancel/inventory rule. |
| Costs and adverse selection | Partially measurable but not cleared. Price reserves and depth diagnostics exist; useful-size fills, toxicity, capacity, and concentration remain open. | Demonstrate positive net EV after all explicit costs and reserves at the intended size. |

Pillar status is evidence status, not implementation status. Completion of a collector, model, replay, or order path does not by itself improve this scorecard.

Current confidence by layer:

| Layer | Assessment | Decision |
| --- | --- | --- |
| Research collection | Broad snapshot collection is useful and operational. The previous memory-growth blocker is resolved, and collection now runs under a restartable 4 GiB-bounded user service rather than TUI child ownership. | Continue collection and investigate any service restart or memory-limit event. |
| Forecast and settlement truth | F0 reconciled the current IEM report maximum to 220/220 venue-winning buckets; F0B/F3 corrected the evaluation cutoff; F1 added causal source vintages; F2 rejected NBM; and F3 accepted a coherent remaining-heating/HRRR ensemble for pricing research. WeatherNext remains unscored. | Backfill provenance/venue labels, evaluate the frozen F3 version through F6, run F4 spatial residuals, and add WeatherNext only after approved access supplies provider ingestion timestamps. |
| Full-market-lifecycle edge | Morning snapshots show more displayed depth than late afternoon, but current data do not establish D-1 traded volume, passive fills, round-trip capacity, or an early calibrated forecast. | Collect from first listing and test separate D-1/early arms against the frozen late control. |
| Current configured portfolio | Failed the fresh July 9-14 cap-aware replay. | Do not restart it. |
| New late HRRR signals | Promising but based on six correlated weather dates. | Freeze as forward shadow hypotheses, not funded strategies. |
| Fair-value/price sheet | V2a Slices 0-3 now produce leak-safe calibration, causal uncertainty reserves, conservative fairs, maximum quotes, and economic gates. The first 98-row Slice 3 read leaves both pilots research-only because no calibrator or untouched forward window was frozen. | Freeze a baseline and forward start before new outcomes; do not promote from the July 16-29 comparison. |
| Phase 3 shared tape | Slices 1-4 are implemented. Continuous collection resumed August 4 after the accepted 72-hour evidence; its first new session reached 1,474/1,474 valid tokens and strictly verified its first partition. Periodic reconnect gaps and late initial discovery remain known limitations; Slice 4 still lacks one real persisted-quote join. | Continue collection, monitor health/storage through the TUI, use retained tape for strategy work, and reject every affected decision window. |
| Phase 3D strategy discovery | D0-D5 are accepted. The cache, cache-only historical/holdout report, lossless atomic emerged-candidate registration, exact forward evaluation, sole command, atomic latest-complete status, and three production manual modes pass. No candidate emerged or exists in the production registry; no scheduler is enabled. | Preserve the accepted command and collection. Keep D6/Phase 4 blocked until an exact candidate passes venue, markout, economics, concentration, useful-size, and explicit approval gates. |
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
- Slice 3 priced the same rows with an 80th-percentile prior-date overprediction reserve, a `0.02` minimum uncertainty reserve, `0.05` profit reserve, and `0.01` known-cost reserve. HRRR-rich market-aware was negative (`-1.000 R/R`, one eligible quote); pooled was `+0.081` across only two dates and depended on extreme raw fairs. HRRR-v2 was theoretically positive for pooled (`+1.315 R/R`, six eligible market dates) and market-aware (`+0.224`, five dates), including positive non-extreme diagnostics. These are maximum-quote, weather-outcome economics with no fill assumption; neither calibrator was selected and no untouched forward window was declared, so both signals remain research-only.
- Static ask depth was useful for triage but did not establish actual or passive fillability.
- A deduplicated snapshot diagnostic since June 1 found mean displayed `$50` ask-sweep fillable notional of about `$46.94` at station-local 07, `$39.61` at 12, and `$27.84` at 17; corresponding full-`$50` rates were `26.7%`, `16.4%`, and `1.1%`. This supports earlier collection but is not actual volume or fill evidence, and current snapshots do not cover D-1 adequately.
- F0B proved that historical grouped model metrics were scored on synthetic ladders centered on the realized high and are invalid for promotion comparison. F3 then found that F0B's hour-bucket selector admitted post-cutoff reports on 4,338/4,364 rows. `forecast_fixed_support_exact_cutoff_weather_date_v2` now enforces the full timezone-aware local cutoff while retaining the fixed support, weather-date clustering, and three distinct control roles.
- The METAR+HRRR tuned-dynamic artifact was behaviorally identical to its HRRR-rich counterpart in the fresh window and must not be counted as independent confirmation.
- F0's `us_high_temperature_truth_v1` audit covered 230 June 1-23 station-dates across all 10 US stations. Polymarket exposed 220 fully resolved venue buckets; the other 10 were all unresolved June 18 chains and failed closed. The IEM routine/special report maximum matched 220/220 winning buckets. CLI conflicted on 56/176, NCEI one-minute ASOS on 115/196, and interval-aware localized Weather Underground on 21/220. Venue bucket is authoritative; IEM report maximum is the versioned numeric proxy/report-stream high-so-far, and matched-cohort numeric relabeling is unnecessary. Provenance and venue-bucket backfill remain required.
- F1's `forecast_source_vintage_v1` catalog separates initialization, valid, provider-ingestion, first-observed, and modification clocks; raw revisions are content-addressed and replay is fail-closed before causal availability. A bounded host probe captured and decoded three NBM station subsets, three HRRR station subsets, and one IEM observation vintage. WeatherNext remains access-gated and requires provider `ingestion_time`; RRFS remains unfrozen. This validates collection/replay plumbing only, not incremental probability skill.
- F2 materialized `nbm_v5_archive_cycle_plus_2h_v1` at three horizons and scored 541 complete same-snapshot D0 station/date rows across 55 weather dates. The untouched 22-date holdout assigned NBM 2.09% weight over HRRR-rich: RPS improved by `0.01135`, but log loss worsened by `0.00340`; the market assigned effectively zero NBM weight. Raw NBM was materially worse overall and recently. The exact transform is rejected; WeatherNext is unavailable rather than rejected, and neither enters pricing research.
- F3 accepted `remaining_heating_hurdle_multinomial_exact_cutoff_v3` after replacing crossed independent ordinal curves that could create zero-probability learned outcomes. A coherent 43.99% remaining-heating / 56.01% conditioned-HRRR ensemble improved untouched 22-date holdout log loss by `0.17879` and RPS by `0.29365`, with both clustered intervals below zero; the recent 14-date slice also improved both. A 94.53% weather / 5.47% market combination improved holdout and recent point estimates, but its intervals cross zero. This authorizes F6 pricing research only, not funded trading.

Evidence source: `reports/research-collection-analysis-2026-07-15.md`. That file is an ad hoc analysis artifact; the conclusions above are canonical here.

The July 29 rolling-discovery/tape holdout is retained as retired historical evidence. Its hard-coded three-sleeve report was removed on August 9 because named frozen portfolios must not route current Phase 3D discovery or analysis:

- The research loop was still active with 1,465,280 snapshots through 2026-07-30 00:18 UTC, covering 36 model names and 18 stations. The shared-tape service was also active.
- A predeclared July 22 discovery cutoff selected three overlapping late US high-temperature families from the general raw-snapshot sweep: PM high regression 10m late, PM MVP late, and PM dynamic-tuned 10m late.
- Priority-order station/date deduplication produced 19 post-cutoff signals across six resolved July 23-28 market dates.
- Exact quote-ready tape reconstruction with 250 ms latency, 60 seconds of continuous valid pre-signal coverage, a `$0.50` cap, and a `$25` immediate ask sweep executed 12 positions. Three signals failed valid coverage and four had no asks at or below the cap.
- The 12 simulated taker executions cost `$205.51` and returned `+$93.22`, or `+0.454 R/R`, with six wins and average VWAP `$0.423`.
- This is a preliminary positive holdout, not promotion evidence: the sample is six correlated dates, partial taker fills are allowed, PnL uses weather outcomes rather than venue settlement, and passive queue fills and markouts are not modeled.

The historical economic hypothesis remains in `docs/hypotheses/2026-07-29-rolling-tape-portfolio-discovery.md`, but its standalone replay CLI is intentionally no longer available.

The July 30-August 2 extension materially weakened that initial result. The frozen three-sleeve portfolio executed 13 of 22 signals and earned only `+$5.91` on `$297.43` cost (`+0.020 R/R`). Evaluated alone, high regression lost `$22.40` (`-0.321 R/R`, three executions), dynamic tuned lost `$13.33` (`-0.062`, ten executions), and MVP late earned `+$27.76` (`+0.104`, 12 executions). MVP late is therefore the only recent positive candidate, but it was selected after viewing these four dates and requires a newly frozen untouched forward window.

## What Is Working

- Broad causal prediction snapshots preserve the opportunity universe better than materialized policy tables.
- Raw-snapshot replay, cap-aware portfolio ordering, and whole-chain attribution have exposed several previously hidden selection and overlap errors.
- Funded trading is paused rather than allowing positive historical replay to override negative current or fill-conditioned evidence.
- The documentation already distinguishes selected replay, filled replay, actual PnL, and venue settlement.
- The new market-tape hypothesis correctly moves collection from model/policy-specific rows to a reusable token-level event stream.
- The Phase 3D contract now keeps strategy generation downstream of measurement:
  recurring discovery may search broadly, while every exact candidate version
  remains simple, attributable, and tested only on its later cohort.
- The current weather feature stack is a credible point-observation/HRRR baseline, which makes controlled source ablation possible; the next forecast gains should come from target fidelity, probabilistic ensembles, spatial residuals, and observed heating surprise rather than more model variants over the same inputs.
- The approved lifecycle design connects forecast revisions, first-listing tape, conservative price sheets, quote cancellation/repricing, and inventory/exit replay without pretending that the current late model can simply run a day earlier.

## Open Risks And Required Gates

| Priority | Risk | Why it matters | Required gate |
| ---: | --- | --- | --- |
| 1 | False or incomplete fill labels | Price changes, book touches, or gapped feeds can be mistaken for fills. | Trade-direction tests, deterministic replay fixtures, book reconstruction, and gap-invalidated coverage intervals. |
| 2 | Signal miscalibration | A fillable negative-EV quote still loses money. | Positive recent walk-forward quoted-price EV using calibrated or shrunk fairs. |
| 3 | Adverse fill selection | Filled rows have historically underperformed missed rows. | Positive filled-subset EV and non-toxic markouts for the exact quote rule. |
| 4 | Small correlated samples | Stations, models, and sleeves often express the same weather-date risk. | Evaluate effective sample by market date/regime and use uncertainty bounds, not raw snapshot counts. |
| 5 | Discovery overfit and hidden policy selection | A rich tape can generate many correlated winners, especially when the feature materializer is limited to already favored pilots or version churn resets failures. | Policy-neutral broad rows, sealed run contracts, correlated-family collapse, bounded challengers, append-only candidate cohorts, aligned-date comparisons, and retained family-level failure history. |
| 6 | Settlement mapping durability | F0 established a perfect 220-row cohort mapping from the IEM routine/special report maximum to venue bucket, while CLI and one-minute ASOS materially disagreed. The mapping may still change by venue/source revision or outside the audited cohort. | Backfill immutable venue buckets and source provenance, retain explicit unresolved/revision states, and monitor out-of-cohort disagreement before integrated promotion. |
| 7 | Capacity | Positive tiny fills do not prove `$50-$100` tradability. | Direct fill/miss evidence at the intended size. |
| 8 | Portfolio concentration | Regional or model-common errors can hit several positions together. | Market-date/regime stress limits and incremental portfolio replay. |
| 9 | Operational integrity | Gaps, clock drift, lag, backpressure, or storage growth can invalidate replay. | Collector health budget and invalid-data rules that fail closed. |
| 10 | Lifecycle stale-quote and inventory risk | D-1 quotes can be adversely selected around scheduled forecast releases, while filled inventory cannot be canceled and may be costly to exit. | Horizon-specific uncertainty/inventory reserves, release-aware cancel/reprice replay, exit-versus-hold evidence, and aggregate capacity caps. |

## Current Decisions

1. Keep funded trading paused.
2. Continue broad research snapshots and outcome resolution.
3. Stop adding model families merely to expand the leaderboard. New models need demonstrably different predictions or incremental information.
4. Accept bounded shared-tape validation run `tape-validation-20260730T183750Z-69556cbf` as sufficient infrastructure evidence for strategy discovery. Its approximately 96.7% valid coverage is operationally adequate for this slow market, provided every replay continues to reject decision windows that cross a gap. Keep heartbeat, reconnect, and late-discovery improvements as non-critical technical debt; do not require another lifecycle run before moving to Phase 3D.
5. Treat the current candidate-token collector and shadow labeler as a prototype only.
6. Keep the late HRRR-rich tuned dynamic and HRRR-v2 dynamic policies as V2 vertical controls and forward signal hypotheses. Retain the July three-family taker result only as historical evidence; do not replay or route it into Phase 3D discovery, champion selection, or funding decisions.
7. Implement `docs/implementation/price-sheet-v2.md`: V2a walk-forward outcome pricing now, then V2b execution reductions/skips on valid tape windows.
8. Implement `docs/implementation/tape-strategy-discovery.md`: preserve the broad causal materializer, add an append-only candidate registry, run recurring constrained discovery from resolved-data watermarks, and continuously compare post-activation champion/challenger cohorts.
9. Evaluate passive price-making and a separately tagged stable-taker control from the same tape and economic price ceiling.
10. Preserve the accepted F3 exact-cutoff remaining-heating version and carry it into F6 pricing/tape research; run F4's spatial residual ablation next, then F5's GOES radiation/cloud surprise; revisit WeatherNext only with approved causal history.
11. Keep weather-only probability, settlement mapping, market-aware calibration, and execution adjustment separately versioned. No new forecast source enters funded pricing until it demonstrates causal incremental skill.
12. Extend research and collection to the full market lifecycle. Treat D-1 open, D-1 revision, D0 early, intraday, and late as separate horizons; add them one at a time behind forecast, tape, quote, inventory, exit, and portfolio gates.
13. For V2a, freeze one calibrator and untouched forward start before additional outcomes are inspected. Do not use the July 16-29 candidate comparison as its own promotion window.
14. Prioritize finding and forward-validating profitable strategy families over further recorder robustness work. Revisit recorder hardening only when observed gaps materially reduce a candidate's evaluable sample or before production operation requires higher availability.
15. Keep the retired three-sleeve portfolio only as historical audit evidence. Its executable report has been removed; do not recreate it, manually install its sleeves, or use named frozen portfolios as a fallback when adaptive discovery is unhealthy.

## Promotion Standard

The promotable unit is:

```text
lifecycle horizon + signal/forecast version + fair-value version
+ quote/update/cancellation/exit rule + inventory cap + size
```

Normal funded sizing requires all of the following:

- the candidate came from a versioned policy-neutral discovery run whose
  sources, cutoff, grammar, folds, costs, complexity, and nomination rules were
  sealed before ranking;
- its immutable candidate definition and activation timestamp predate every forward row;
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

- 2026-08-13: Accepted F3 for Price Sheet V2 research. Exact local cutoff
  selection removed 4,338 post-cutoff rows from the legacy selection, and the
  coherent hurdle/multinomial remaining-heating model improved corrected
  historical, untouched 22-date forward, and recent 14-date probability scores
  versus conditioned HRRR-rich. Market-relative point estimates improved but
  remain statistically uncertain. F6 and funded authority remain gated.

- 2026-08-13: Completed F2's identical-coverage source benchmark. The exact
  NBM v5 archive transform covered all 541 scored D0 rows but failed the joint
  held-out log-loss/RPS and market-relative gates; raw NBM was materially worse
  overall and recently. WeatherNext remains unavailable rather than rejected.
  The information pillar remains unproven and funded authority remains paused.

- 2026-08-12: Completed F1 causal forecast-source collection. Froze source-specific availability rules, a separate content-addressed runtime catalog, listing-bounded collectors, format validation, retry/size bounds, immutable revision/failure telemetry, and replay queries. Bounded host evidence decoded NBM and HRRR subsets and captured IEM observations; WeatherNext access and RRFS versioning remain explicit. The information pillar remains unproven and funded authority remains paused.

- 2026-08-12: Completed F0B forecast baseline/evaluation repair. Retired outcome-centered synthetic-ladder scores, froze fixed-support weather-date-clustered full-distribution evaluation, collapsed model-count inflation to three controls, and left the market-relative gate explicitly unavailable. The information pillar and funded status remain unproven/paused.
- 2026-08-12: Completed F0 settlement/sensor truth audit. The reproducible 230-row June cohort found 220/220 venue-bucket matches for the existing IEM routine/special report maximum and material conflicts for CLI, one-minute ASOS, and localized Weather Underground. Froze venue bucket as authoritative and IEM report maximum as a versioned numeric proxy/report-stream high-so-far; required provenance/venue backfill and out-of-cohort monitoring. This establishes mapping substrate, not settlement advantage, information edge, or funded authority.

- 2026-08-12: Adopted four-pillar net-edge governance across information,
  settlement, execution, and costs/adverse selection. Existing plans remain in
  place, new work is pillar-tagged, and promotion remains a combined gate. This
  changes project prioritization and attribution, not funded authority or the
  underlying evidence verdict.

- 2026-08-12: Corrected the high-severity emerged-path gap found by the D0-D5
  audit. D3 wildcard-bucket, edge, and spread predicates now map losslessly to
  immutable candidate versions; the sole command seals future activation and
  atomically appends successful outcomes/candidates after report artifacts
  exist. Tests prove idempotent identity, exact post-activation evaluation, and
  zero partial registry rows on invalid identity. Funded authority is unchanged.

- 2026-08-12: Accepted Phase 3D D5 after the sole command passed interrupted
  cold/resume (40.05 seconds), explicit warm no-op (7.07 seconds), and natural
  new-watermark (7.10 seconds) production modes. The TUI now validates an
  atomic latest-complete report/cache pointer; a failed run preserved it
  byte-for-byte. Archived the failed service unit and enabled no scheduler.

- 2026-08-12: Accepted Phase 3D D4 from tested immutable synthetic candidates
  plus an honest zero-candidate production report. The evaluator excludes all
  pre-activation decisions, uses registered execution price/size, reports
  aligned and shared-cap diagnostics, and cannot promote without venue
  settlement and markouts. Repeated complete reports were byte-identical.

- 2026-08-11: Accepted Phase 3D D3 after the production cache-only grid ranked
  20,736 rules, collapsed 252 passing variants to 12 representatives before
  the untouched five-date holdout, returned a healthy completed-none result,
  and repeated with byte-identical JSON, Markdown, CSV, and content hashes.
  D4-D6 and funded authority remain open.

- 2026-08-11: Accepted Phase 3D D0-D2 for the versioned causal checkpoint
  taker cache. Stopped and disabled the failed scheduler, completed the
  146,937-row/19,032-decision cold backfill in 32.43 seconds, matched 200/200
  stratified direct replays, completed a warm no-op in 0.026 seconds and a
  9,216-row increment in 2.52 seconds, and retained 30-second-delay, gap, ask,
  and token failures as explicit rejections. D3-D6 and funded authority remain
  open.

- 2026-08-11: Reclassified Phase 3D C3-C6 from an implemented operating path to compatibility-only after repeated production cycles timed out at approximately 900 seconds during per-model-row historical tape reconstruction, leaving zero completed runs and zero candidates. Approved an incremental executable-decision cache and one deterministic discovery/report command as the replacement critical path. Causal replay, chronological holdout, correlated-family collapse, immutable activation-bounded candidates, venue settlement, markout, and Phase 4 authority gates remain unchanged.

- 2026-08-09: Removed the hard-coded three-sleeve tape holdout report and its
  operator routing. Retained the July results only as retired historical
  evidence. Current tape-backed strategy research must flow through recurring
  policy-neutral Phase 3D discovery; timeouts and zero completed runs are an
  unhealthy discovery system, not permission to fall back to named sleeves.

- 2026-08-07: Completed Phase 3D C5-C6 repository implementation. Research
  roles now require aligned replacement evidence plus conservative/base
  uncertainty, concentration, settlement, and markout gates; failed versions
  remain in family history and no role can authorize funding. Added a bounded,
  restart-safe scheduler, append-only cycle failures, CLI/TUI status, and a
  durable systemd unit. The unit was not enabled and funded status did not
  change.

- 2026-08-06: Completed Phase 3D C4 with activation-bounded cohorts and
  immutable forward-shadow/common-date scorecards for every active candidate.
  Venue-only economics, invalid-tape rejection, markout gates, displayed-depth
  scenarios, and separate actual-order evidence now fail closed. C5 role
  transitions remain open; funded status did not change.

- 2026-08-06: Completed Phase 3D C3 with resolved-watermark-triggered recurring
  discovery, bounded multi-challenger nomination, interrupted-run resume,
  unchanged-version reuse, append-only no-nomination/budget outcomes, and
  explicit candidate/rule/runtime/diagnostic budgets. C4 forward evaluation
  and C5 role transitions remained open; funded status did not change.

- 2026-08-04: Completed Phase 3D C2 with a durable append-only external
  registry, content-addressed version reuse, activation-bounded cohort and
  watermark scorecard records, lifecycle history, single-writer locking,
  query-only observers, and identity-only batch compatibility import. C3-C5
  and all forward evidence gates remain open; funded status did not change.
- 2026-08-04: Respecified Phase 3D from one-shot winner freezing to a continuous
  versioned discovery system. Kept the committed materializer, replay, grammar,
  scoring, and hash primitives; marked the one-winner orchestration transitional
  and opened registry, recurring-run, cohort-scorecard, and champion/challenger
  slices. Funded status did not change.
- 2026-08-04: Implemented the Phase 3D D0-D3 repository path and the
  activation-gated stable-taker D4 evaluator. No production discovery run or
  manifest was frozen; venue settlement and markouts remain open, so funded
  status did not change.
- 2026-08-04: Migrated the active prediction-snapshot loop from its July 16 TUI-owned process group to enabled user service `roboweather-research.service`. The controlled handoff left the TUI running, installed restart-on-failure and a 4 GiB memory ceiling, added journal/file logs plus a DB-scoped writer lock, and made TUI exit independent of research collection. Funded status did not change.
- 2026-08-04: Enabled continuous user service `roboweather-market-tape.service` and resumed collection after the August 2 bounded-run stop. Session `tape-20260804T152718Z-b341a291` reached 1,474/1,474 valid tokens, completed discovery, and strictly verified its first partition with queue high-water 1/10,000, about 142 MiB RSS, and zero reconstruction errors. The intervening outage remains an explicit invalid gap; funded status did not change.
- 2026-08-03: Evaluated completed 72-hour tape run `tape-validation-20260730T183750Z-69556cbf` and separated operational availability from execution validity. Adopted a 30-second lifecycle recovery budget, individual late/fallback market exclusion, and receipt-lag warnings while preserving uninterrupted 60-second pre-signal-through-execution coverage for every replay. The run still failed: only 4/33 eligible markets were complete and the remaining maximum recovery gaps were 36.34-41.32 seconds. Slice 2 remains open.
- 2026-07-30: Corrected lifecycle right-censoring and narrowed recorder
  fingerprinting, then passed a two-refresh 1,364-token short probe with 60,401
  events, zero health failures, queue high-water 1/10,000, and no reconstruction
  errors. Started disabled bounded validation run
  `tape-validation-20260730T183750Z-69556cbf`, due 2026-08-02 18:37:50 UTC;
  complete-lifecycle acceptance remains open.
- 2026-07-30: Made constrained policy-neutral strategy discovery a required
  Phase 3D gate. Added a broad snapshot/tape/settlement substrate, predeclared
  walk-forward and complexity rules, at most one immutable primary winner, and
  untouched post-activation tape before Phase 4; current V2a pilots remain
  vertical controls rather than predetermined strategies.
- 2026-07-30: Closed the validation-cohort integrity gaps by persisting restart-stable run/build/config fingerprints and discovery-refresh membership/health, and by retaining late, fallback, open, and incomplete within-window markets in the acceptance denominator. A 200-second probe crossed two refresh boundaries and passed strict health; the full lifecycle gate remains open.
- 2026-07-30: Repaired the Phase 3 recorder failures exposed by the July 23-30 probe: direct D+1/D+2 discovery, in-process disconnect recovery, stale incremental-frame resync, restart-stable runtime bounds, chunked subscriptions, strict full-book health, expected pre-seed delta accounting, and scoped lifecycle acceptance. A clean 1,364-token short live probe passed strict health with all tokens `VALID`; the complete lifecycle gate remains open.
- 2026-07-29: Replayed a portfolio discovered only from pre-July-23 raw snapshots against later exact market tape. Twelve of 19 deduplicated signals executed for `+$93.22` on `$205.51` cost across six resolved dates; seven failed closed on coverage or capped liquidity. Recorded the result as preliminary forward-shadow evidence while the lifecycle report remains failed and funded trading remains paused.
- 2026-07-30: Completed Price Sheet V2a Slice 2 and a nonempty current-database smoke. All 98 frozen predictions used per-date calibrators trained strictly on earlier dates. Calibration reduced raw overconfidence but underperformed the decision-time market baseline on both pilots, so no pricing model was promoted and funded status did not change.
- 2026-07-30: Completed Price Sheet V2a Slice 3 and its 98-row current-database smoke. Added causal prior-OOF uncertainty, separate profit/cost reserves, tick-rounded maximum quotes, explicit skips, economic/probability reports, and fail-closed calibrator/forward gates. HRRR-v2 produced promising theoretical quote-cap diagnostics, but no calibrator or untouched window was frozen, so both pilots remain research-only.
- 2026-07-23: Reconciled the Phase 3 build claims against the remote host. No recorder was active and retained evidence covered only approximately 18-second probes, so Slice 2 had not passed. Completed the missing future discovery, listing provenance, bounded-supervision, and executable lifecycle-gate paths while keeping the exit open for real elapsed coverage.
- 2026-07-16: Created the living audit from the July 15 research collection review and the market-tape systems audit. Confirmed the research memory-growth prerequisite is resolved and approved Phase 3 market-tape implementation while keeping funded trading paused.
- 2026-07-16: Recorded operator confirmation that Phase 3 is built and running, with exit evidence still accumulating. Made Price Sheet V2a the current implementation priority and approved the V2b tape-overlay plan.
- 2026-07-17: Added the station-specific forecast-edge research track after reviewing the existing rich METAR/HRRR feature baseline and identifying unresolved IEM-versus-Weather-Underground/venue target fidelity. Prioritized truth reconciliation, WeatherNext/NBM probabilistic baselines, spatial residuals, and observed radiation surprise without changing funded status or the execution critical path.
- 2026-07-17: Approved full-market-lifecycle research and collection from first listing through settlement. Added horizon-specific forecast, repricing, inventory, exit, tape, and promotion gates while preserving the late Price Sheet V2a pilot as the immediate critical path and keeping funded trading paused.
