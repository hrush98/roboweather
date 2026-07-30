# Changelog

Keep this file up to date for notable data, model, and trading changes.

## 2026-07-30

- Corrected the Phase 3 lifecycle acceptance cohort to include only in-window listings whose scheduled close is at or before the validation end. Later-closing listings are now reported explicitly as right-censored and do not fail a clean matured cohort; late, fallback-listed, and coverage-incomplete matured markets still fail closed. Replaced whole-repository Git fingerprinting with a recorder-scoped source/data/service-unit/runtime-version fingerprint while retaining the separate collector-config identity, so unrelated commits no longer split a supervised run. A two-refresh 1,364-token short probe passed strict health, and disabled bounded validation run `tape-validation-20260730T183750Z-69556cbf` is active through 2026-08-02 18:37:50 UTC.
- Made policy-neutral strategy discovery a first-class Phase 3D gate between
  measurement and funded validation. Added a dedicated implementation contract
  for a broad causal snapshot + tape + settlement substrate, predeclared
  simple-rule grammar, date-clustered walk-forward search, complexity penalty,
  correlated-family collapse, at most one immutable primary winner, and
  untouched post-activation tape. Generalized the planned V2b Slice 5
  materializer into broad discovery and frozen evaluation views, reclassified
  current V2a pilots as vertical controls rather than predetermined winners,
  and blocked Phase 4 on a passing exact manifest. Funded behavior is
  unchanged.
- Hardened Phase 3 validation-cohort integrity. Collector sessions now persist restart-stable validation-run, exact tracked-build, and config fingerprints; every discovery attempt persists status, warnings, and exact membership; strict health reports refresh status; and lifecycle acceptance fails on identity/config drift, unhealthy refreshes, or late/fallback/open/coverage-incomplete within-window cohort markets instead of filtering them out of the denominator. A 200-second, three-refresh live probe recorded 157,807 events across 1,364 tokens and passed exact strict health; complete lifecycle acceptance remains open.
- Completed Price Sheet V2a Slice 3. Added versioned causal uncertainty, profit, and known-cost reserves; conservative outcome fairs; tick-rounded maximum quotes; explicit no-selection, insufficient-history, missing-market, and no-positive-quote skips; reconstructable pricing artifacts; and broad/current/forward probability and theoretical-economic reports. The default report compares pooled and market-aware candidates but fails closed until a calibrator and untouched forward start are frozen.
- Ran the Slice 3 current-database smoke on the 98 July 16-29 pilot decisions. HRRR-rich did not clear the robust gate; HRRR-v2 had positive theoretical maximum-quote diagnostics for both fitted candidates, but those results make no fill claim and no baseline or untouched forward window was predeclared. Both pilots remain research-only and funded behavior is unchanged.
- Repaired the Phase 3 market-tape recorder after the long lifecycle probe exposed late future discovery, process-ending disconnects, stale-frame handling, restart-reset deadlines, oversized subscription seeding, weak partial-coverage health, and catalog-wide acceptance poisoning. Added direct D+1/D+2 event discovery, in-process gap/reconnect/resync, incremental-event lag enforcement, restart-stable bounded supervision, 500-token subscription batches, strict latest-generation full-book health, scoped lifecycle reports, and fail-closed pre-seed delta handling. A clean 1,364-token short live probe reached `VALID` for every token and passed strict health; complete lifecycle evidence remains pending.
- Clarified the Price Sheet V2 and full-market-lifecycle architecture: the destination is one continuously operating, event-driven pricing and inventory engine from market listing through settlement. Named horizons remain progressively validated calibration, uncertainty, reporting, risk, activation, and rollback states rather than permanent isolated time-of-day strategies.
- Completed Price Sheet V2a Slice 2. Added deterministic expanding-window pooled-Platt and regularized market-aware calibration, raw/market baselines, stable per-fold hashes and exclusive training cutoffs, explicit sparse and missing-market fallbacks, cluster-weighted probability/reliability diagnostics, generated artifact I/O, and leakage-focused tests.
- Ran the read-only current-research-database smoke for both frozen pilot signals. It produced 98 July 16-29 predictions with no fitted-fold fallback; calibration improved materially over raw model fairs but did not beat the causal market baseline, so no calibrator, quote, or funded behavior was promoted.

## 2026-07-29

- Added `scripts/tape_strategy_holdout_report.py` and focused tests for causal quote-ready taker replay of a portfolio discovered from raw snapshots before an immutable cutoff. The report fails closed on broken coverage or unavailable capped asks, applies sleeve-priority station/date deduplication, and leaves research and tape databases read-only.
- Froze the initial late US high-temperature three-family shadow portfolio and reproduced its July 23-28 holdout: 12 of 19 deduplicated signals executed, producing `+$93.22` weather-outcome PnL on `$205.51` cost across six resolved dates. Recorded this as preliminary hypothesis evidence only; passive fills, markouts, venue settlement, lifecycle acceptance, and funded promotion remain open.
- Documented the repeatable snapshot-discovery-to-tape-holdout workflow in `AGENTS.md`, the living audit, roadmap, Phase 3 implementation plan, and hypothesis records. Added the personal `roboweather-tape-discovery` Codex skill. Funded trading policy and sizing did not change.

## 2026-07-23

- Completed the repository work needed to evaluate Phase 3 Slice 2 honestly: market-tape discovery now merges current and future active weather markets, records authoritative Gamma creation/listing provenance, migrates existing catalogs, refreshes subscriptions dynamically without scheduled reconnect gaps, and provides a strict multi-session lifecycle/resource report plus a bounded 72-hour user-systemd probe unit. A remote audit found no active recorder and only approximately 18-second historical probes, so the Slice 2 evidence gate remains open until a real first-listing-through-close run passes.
- A dynamic-subscription host probe preserved existing coverage across refresh but showed that optional custom feed events would exceed the raw-disk budget, reaching 405 MB in four minutes. Removed that optional stream, added atomic gzip finalization and exact replay for five-minute partitions, and started final compressed core-L2 session `tape-20260723T173900Z-b9392349`. Its first 65,196-event partition compressed to 9.95 MB and passed strict replay; post-rotation health remained clean and the early retained-growth projection was 9.03 GB/day against the 25 GiB/day gate. The complete-lifecycle gate remains pending elapsed evidence.

## 2026-07-22

- Completed the repository implementation for Price Sheet V2a Slices 0 and 1. Added immutable/versioned pilot signal and V2 sheet contracts, deterministic decision/spec hashes, explicit V1 rollback, and a read-only dataset builder that emits separate leak-safe calibration-fit and frozen-policy evaluation artifacts. The materializer enforces prior-date fit cutoffs, timestamp causality, first-entry dedupe, market-date/station-date cluster weighting, typed stale/crossed market references, reconstructable source IDs, and explicit IEM-versus-venue label diagnostics. Focused tests pass; a nonempty artifact smoke against the current remote research database remains before real-data evidence closes.
- Documented that Codex and operator commands may run on either the `/home/maxrush` remote host or the `/home/hmrush` local checkout host, preserving both valid `roboweather` Python environment paths.

## 2026-07-17

- Approved and documented the full-market-lifecycle research program. Added a standalone strategy report, falsifiable hypothesis, and implementation contract covering first-listing/D-1 data, horizon-specific distributions, forecast-release cancellation/repricing, inventory and exits, frozen lifecycle arms, and useful-size gates. Integrated the program into the forecast-data, Price Sheet V2, shared-tape, roadmap, and living-audit documents. The late V2a pilot remains the immediate critical path and funded trading remains paused.
- Documented the proposed station-specific forecast-edge program. Preserved the full data-source strategy in `reports/`, added a falsifiable hypothesis and proposed implementation contract, and updated the living audit to require settlement/sensor truth reconciliation plus causal identical-coverage skill gates before WeatherNext, NBM, MADIS/upwind, or GOES features can affect pricing. No live strategy, funded state, or execution sequencing changed.
- Completed the repository side of Phase 3 Slice 4: persisted execution-ledger price-sheet quotes now export read-only into immutable decision timing, quote-ready reconstruction excludes future events, coverage must remain continuously valid through observed cancel/GTD termination, and joins persist source/coverage/event/watermark/termination references. One real host quote/tape reconstruction remains before the slice evidence gate closes.

## 2026-07-16

- Started the Phase 3 causal decision join with immutable observation/decision timing, hypothesis activation, configurable latency, first-visible-event selection, continuous pre-signal coverage proof, persisted reconstruction references, and a strict JSON decision join command. Real research/Price Sheet V2 decision mapping remains open.
- Completed the Phase 3 deterministic-book repository gate: the collector now validates books online, schedules initial/periodic checkpoints, catalogs reconstruction errors, invalidates malformed coverage, and supports arbitrary receipt-time/event reconstruction across ordered partitions. A 616-token live probe persisted 616 checkpoints with zero reconstruction errors and passed strict health.
- Started Phase 3 deterministic book reconstruction with full-book baselines, absolute-size L2 deltas, gap-safe invalidation, canonical state hashes, cataloged checkpoints, and a raw-segment rebuild command. The bounded live segment reproducibly rebuilt 616 token checkpoints.
- Hardened the Phase 3 active-token recorder with hourly UTC raw-segment rotation and partition cataloging, bounded exponential reconnects, explicit gap/resync coverage, persisted lag/queue/RSS/disk telemetry, zero-event failure, and a strict catalog/segment health command. A bounded 616-token host probe captured 674 events and passed health verification; complete-lifecycle capacity remains open.
- Added the first repository-backed Phase 3 active-token recorder vertical slice: separate tape catalog, policy-independent all-weather token registry, immutable subscription generations, conservative token retirement, bounded WebSocket queue/frame limits, raw segment writes, and `RESYNCING`/`VALID`/`CLOSED` coverage transitions. A bounded host probe subscribed to 616 tokens and captured 714 token events with exact replay; complete-lifecycle supervision and retention gates remain open.
- Added the Price Sheet V2 implementation contract. V2a builds walk-forward, market-aware conservative outcome pricing; V2b can only reduce price/size or skip using valid Phase 3 tape features, then compares passive and stable-taker arms before Phase 4.
- Recorded operator confirmation that the Phase 3 collector is built and running while keeping its resource, coverage, deterministic replay, and fill-label acceptance evidence open. Price Sheet V2a is now the current implementation critical path.
- Started Phase 3 Slice 1 with policy-independent market-tape contracts, checksummed append-only raw JSONL segments, stable byte-offset event IDs, strict truncated/corrupt-record rejection, deterministic round-trip fixtures, and a representative-message storage/compression benchmark CLI. Format and retention defaults remain provisional until captured live samples establish resource budgets.
- Fixed unbounded native memory growth in long-running HRRR collection by explicitly closing every `pygrib`/ecCodes file handle after decoding. Added a bounded 512-entry station/cycle/forecast-hour cache so six-minute research cycles reuse immutable HRRR point forecasts instead of repeatedly downloading and decoding the same files.
- Added research-cycle runtime telemetry to `engine_state.raw_json`, including RSS after discovery, books, weather, and model persistence plus HRRR point-cache entries/hits/misses. Cycle logs now print the same metrics for post-restart validation.
- Confirmed the research-loop memory prerequisite is resolved and approved the shared token-level weather market tape as Phase 3 of the execution rebuild.
- Added living canonical documents for the current trading-system audit and execution roadmap, plus a separate Phase 3 market-tape implementation contract. Dated hypotheses now remain the economic `why`, while implementation plans own architecture, sprint slices, and acceptance tests.
- Reclassified the existing candidate-token collector and shadow fill labels as plumbing prototypes only, reconciled the failed current price-sheet gate, and separated minimum-risk canary plumbing validation from `$50/$100` capacity evidence.

## 2026-07-06

- Closed the steady-state shadow collection implementation gaps: the shadow grid now uses `$50/$100` intended notional instead of tiny quote sizes, quote intents persist initial depth/queue context, `scripts/collect_candidate_clob_events.py` subscribes the CLOB market feed to current candidate tokens, `scripts/label_shadow_quote_outcomes.py` persists conservative/base/optimistic fill labels and markouts, and `scripts/shadow_collection_report.py` now fails on missing token/feed/useful-size/outcome coverage.
- Tightened the adverse-selection rebuild report around useful-size evidence: the steady-state shadow milestone is now partial until CLOB event collection, fill/toxicity labels, and `$50-$100` shadow coverage exist; `$5` and `$10` quote tests no longer count as representative evidence for this phase.
- Implemented the steady-state shadow collection build target: Phase 1 price sheets now fan out a bounded 24-spec post-only shadow grid with stable `quote_spec_id` values, persisted spec/rule metadata, `would_post` flags, pending markout hooks, more specific shadow lifecycle states, store-level reconstruction helpers, and `scripts/shadow_collection_report.py` for live-ledger health checks. This remains non-funded shadow infrastructure.
- Added a steady-state shadow collection milestone to the adverse-selection rebuild report, specifying the non-funded build target of candidate persistence, bounded shadow quote specs, intent fanout, CLOB event coverage, lifecycle reconciliation, and markout hooks before Phase 3 profitability reporting.
- Added desk-style execution intuition and hard no-promote gates to the adverse-selection rebuild report, including post-fill markouts, winner/loser fill-rate checks, target-size evidence requirements, and a clearer distinction between capped/haircut fair values and true calibrated fairs.
- Updated the adverse-selection rebuild plan to require useful-size capacity evidence: `$50` funded quote validation is the minimum useful read, and `$100` validation is required for target-capacity sizing.
- Implemented Phase 2 adverse-selection shadow quote support: scoped Phase 1 sheets now generate persisted post-only GTD `live_quote_intents`, linked to live candidates and positions, with quote-price clamping below the ask and shadow expiry/cancel reconciliation. This adds no funded quote placement.
- Implemented Phase 1 adverse-selection price-maker source support: scoped US high-temperature consensus no-tiny BUY_NO candidates now get persisted `live_price_sheets` with raw/capped fair, market reference, uncertainty/adverse-selection haircuts, minimum edge, max quote price, size cap, validity, and cancel triggers. Added `scripts/phase1_price_sheet_report.py` to replay the generated sheet on raw research snapshots.
- Clarified the ML framing in `docs/hypotheses/2026-07-06-adverse-selection-execution-rebuild.md`: keep the existing regression/tree/HRRR/METAR forecast stack, but route outputs through calibration, market-aware shrinkage, uncertainty haircuts, quoteable fair prices, and filled-subset quote-PnL evaluation.
- Refined `docs/hypotheses/2026-07-06-adverse-selection-execution-rebuild.md` so the primary next-phase build is a conservative price-maker / post-only quoting engine with calibrated quote prices, cancellation rules, shadow quote replay, and useful-size funded validation rather than a broad taker/resting execution-variant search.
- Implemented Phase 0 adverse-selection instrumentation: live runs now persist pre-policy model candidates and policy candidates with stable `live_candidate_id` values, durable prediction snapshot IDs, candidate-to-position/order/event links, CLOB feed event storage, feed/local timestamps, and decision-time quote lifecycle feature payloads.
- Added `weather_trader/execution/clob_feed.py` to normalize Polymarket market/user WebSocket messages into `clob_feed_events` rows, including `price_change`, `best_bid_ask`, `last_trade_price`, `tick_size_change`, and generic user-channel order/trade events.
- Added `docs/hypotheses/2026-07-06-adverse-selection-execution-rebuild.md`, a deep-dive execution rebuild report covering adverse selection, market microstructure lessons, local whole-chain evidence, and the recommended WebSocket/fill-conditioned execution roadmap.
- Documented the execution-first phase in `docs/hypotheses/2026-07-06-execution-first-phase.md` after whole-chain analysis showed persistent replay-to-live divergence, adverse fill selection, and recent global low MVP signal degradation.
- Updated `docs/live-trading-journal.md` to pause funded live trading pending fill-conditioned gates and to require actual filled R/R, filled-at-entry replay, filled-vs-unfilled selected comparison, settlement alignment, and recent-window checks before future promotion or sizing.
- Updated `docs/continuous-improvement-loop.md` with adverse-selection and fill-conditioned evidence gates so raw snapshot replay is treated as hypothesis generation rather than live funding approval.

## 2026-06-18

- Extended `scripts/portfolio_promotion_report.py` with `10:00-12:00` replay variants for the live US consensus core and the two promoted high-temp consensus sleeves, so portfolio-grade liquidity and capacity can be compared against the existing `12:00-15:00` window.
- Lowered the live US high-temperature consensus core from $100 to $50 and promoted two additional $50 `0.05-0.50` live sleeves: `metar_hrrr_rich_catboost_mvp_entry_05_50` and `hrrr_v2_three_model_consensus_entry_05_50`. `weather_trader/live/execution.py` now loads the paired METAR/HRRR v2 model artifacts and registers both sleeves in `live_strategy_plans()`.
- Deactivated the live global low-temperature consensus canary `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first` by removing it from `live_strategy_plans()` and dropping the global-low dynamic model from default live model loading. The single-model global low MVP BUY_NO add-on remains active.
- Extended `scripts/portfolio_promotion_report.py` with no-tiny `0.05-0.50` variants of the broad US high-temperature consensus candidates from `docs/hypotheses/2026-06-18-us-high-temp-consensus-candidates.md`: METAR+HRRR-rich CatBoost+MVP, HRRR v2 three-model consensus, HRRR v2 bucket consensus, and PM US12 CatBoost+MVP. These now run through the same current-stack, cap-aware, recorded-depth promotion gate as existing candidate sleeves.
- Added `scripts/top_bucket_cluster_calibration_replay.py` for top-2/top-3 YES cluster research. It builds raw top-N clusters from candidate distributions, reports baseline raw cluster calibration, trains walk-forward Platt/context calibrators on prior resolved dates, and replays dutched cluster policies using raw or calibrated hit probabilities.
- Added `scripts/top_bucket_basket_replay.py`, a raw-candidate-distribution replay for dutched top-N YES bucket baskets. It supports first-eligible, cheapest-intraday, and best-edge-intraday selection modes, ask-sum/edge grids, model and consensus distributions, and alive-bucket filtering so low-temperature buckets already impossible from `low_so_far` are excluded by default.

## 2026-06-17

- Integrated the bucket-YES Platt calibration artifact into live/research candidate selection for US high-temperature obs bucket models. `FairValueEngine.price_markets` now applies calibrated YES probabilities after dynamic bucket normalization, recomputes NO probabilities/edges from the calibrated fair, and carries raw fair plus fit metadata in candidate/snapshot audit JSON.
- Added `--bucket-calibration-path` and `--bucket-calibration-mode off|apply` to research and live loop commands. Default mode is `apply` against `~/.local/state/roboweather/bucket_calibration_pm_us12_high_temp.json` when present; `off` is the rollback switch.
- Disabled the legacy Layer 1 live calibration gate whenever bucket calibration mode is `apply`. `scripts/run_research.sh live-loop` no longer passes the old `CALIBRATION_PATH` by default, and legacy gate metadata now records `disabled_by_bucket_calibration`.
- Added `scripts/bucket_probability_calibration.py`, a probability-only walk-forward diagnostic for US high-temperature bucket models. It trains bucket-YES Platt calibration from raw candidate distributions, reports binary Brier/log-loss and reliability bands, and compares raw-normalized, Platt-normalized, and temperature-scaled multiclass bucket distributions without using execution depth or portfolio caps.
- Added `scripts/build_bucket_calibration_artifact.py` to generate the bucket-YES Platt calibration JSON artifact for the US high-temperature obs bucket models, including model/station fits, model-global fallbacks, source coverage, and in-sample diagnostics.
- Added `scripts/calibrated_candidate_replay.py`, a walk-forward model-level calibration replay that trains only on prior resolved dates, recalculates each model's selected candidate from raw `candidate_distribution` payloads, rebuilds obs bucket consensus from calibrated selections, and compares raw live selection with selected-row and candidate-universe calibration variants. The report includes Brier/log-loss, fair-band reliability tables, and a compact parameter-grid mode for feature/sample/edge/side-awareness sweeps.

## 2026-06-16

- Changed global low-temperature research resolution for `RJTT`, `RKSI`, `VHHH`, and `ZSPD` to use Polymarket Gamma's settled winning low-temperature bucket when scoring `LOW_TEMP` snapshots, aligning replay labels with Polymarket settlement for Tokyo, Seoul, Hong Kong, and Shanghai. High-temperature fields still come from the existing weather source because `station_date_outcomes` stores one station/date row. Backfilled the active research DB for 68 existing closed affected station/date outcomes and rewrote the corresponding low-temp prediction results.
- Replaced live calibration CANARY sizing with gate mode. Normal mapped live policies now execute only on `TRADE`; `BLOCK`, `WATCH`, `CANARY`, `INSUFFICIENT_DATA`, missing, and unmapped states reject with `CALIBRATION_BLOCK`. The two HRRR inland late disagreement execution experiments are tagged `hrrr_execution_experiment` and execute unless calibration explicitly says `BLOCK`.
- Defaulted `scripts/run_research.sh live-loop` to use `~/.local/state/roboweather/calibration.json` when present, passing it as `--calibration-path` with the configured canary notional so TUI-started live loops enable the Layer 1 calibration gate without custom arguments.
- Documented the execution-experiment doctrine in the live trading journal and `$1000/day` EV roadmap: replay and shadow selection are signal evidence only, while live scaling requires controlled tests of the combined signal, execution, and sizing policy with filled-versus-missed diagnostics, slippage checks, and Polymarket-settled PnL.
- Retired the three global low-temperature live strategies (canary $100, MVP add-on $50, tiny tail $5). Global low research collection continues; only the live execution sleeves are deactivated.
- Added two HRRR inland late disagreement execution experiments at $25 each: HRRR-rich and METAR+HRRR-rich dynamic-tuned models, inland-only stations (KATL, KDAL, KORD), edge >= 0.25, HRRR-versus-obs dispute >= 0.15, obs edge < 0.10, entry $0.00-$0.50, local 12:00-15:00.
- Extended `ResearchPolicySpec` with `hrrr_disagreement_min` and `obs_edge_max` fields so live execution can gate HRRR model candidates against the obs bucket consensus. The filter is applied in `ResearchPolicyEvaluator._candidates_for_policy` via `_passes_hrrr_disagreement`, mirroring the research replay disagreement logic from `portfolio_promotion_report.py`.
- Updated `LIVE_MODEL_PATHS` to load HRRR-rich and METAR+HRRR-rich dynamic-tuned `.joblib` artifacts alongside existing obs models; removed global low model paths from live loading.

## 2026-06-15

- Added `scripts/whole_chain_truth_report.py`, a generated sleeve-by-sleeve reconciliation of raw snapshot replay, live-selected candidate replay, filled replay at entry and actual fill prices, actual settled/mark PnL, fill-selection bias, slippage, settlement mismatches, lost capacity, and Layer 1 calibration allow/canary/block outcomes.
- Implemented Layer 1 live calibration as an optional defensive gate: generated calibration JSON can now block known bad station/side/entry-band/model-family buckets or cap them to canary notional, with per-candidate metadata and cycle-debug counters. Added `--calibration-path` and `--calibration-canary-notional-usd` to live commands.
- Extended `scripts/calibration_table.py` with `--out` JSON output and `--family all` while preserving the existing human-readable table output.
- Hardened `docs/calibration-layer-1-design-2026-06-15.md` so Layer 1 calibration is explicitly a defensive gate, with copy-based live candidate handling, required raw metadata, unknown-bucket behavior, tests, and non-goals.
- Updated `docs/roadmap-to-1000-ev-day.md` to make the whole-chain truth report the next scaling gate: raw replay, live-selected replay, actual fills, and Polymarket settlement must reconcile before calibration-driven sizing, sleeve promotion, or cap increases.
- Upgraded live execution to anchor FAK, retry, and resting orders to the scored `entry_price`: initial/retry FAK are capped at `entry + $0.01`, the resting TTL is now 420 seconds, and the fallback ladder uses deterministic `entry+1c/entry/entry-1c/entry-2c` bands with `30/40/20/10` weights.
- Added exchange-enforced post-only support for GTC child orders and apply it to the `entry` and lower resting ladder bands, while keeping the `entry+1c` child as normal GTC because immediate fills there are acceptable.
- Extended live order attempt payloads with execution-contract metadata including scored entry, max immediate price, slippage versus entry, and execution-price violation flags; updated focused live execution/CLOB tests for the new order contract.

- Documented the live/replay audit lesson in `docs/live-trading-journal.md`: raw-snapshot replay is hypothesis evidence, not sufficient live sizing evidence, until it reconciles to persisted live-selected candidates, actual fill prices, fair-band calibration, filled-vs-unfilled selected subsets, and settlement-source semantics.
- Identified two operator follow-ups from the audit: persist the full live candidate snapshot universe with stable IDs, and add an execution guard or alert when submitted live limit price exceeds recorded entry by more than the intended tolerance.

## 2026-06-12

- Integrated current-stack promotion/candidate replay into `scripts/trading_retrospective_report.py` so the weekly review packet shows live sleeves, candidate sleeves, empirical PnL/R/R, fill/sample counts, and watch/review status in one run.
- Renamed weekly retrospective EV labels so entry-edge EV is explicitly treated as uncalibrated model-implied EV, while current-stack resolved replay PnL is presented as the empirical EV proxy until calibration improves.
- Enhanced `scripts/trading_retrospective_report.py` to separate uncalibrated model-implied intended EV from filled model-implied EV, estimate missed model-implied EV from unfilled exposure, classify execution outcomes as terminal rejects vs child FAK misses/resting TTL/order-construction issues, and support `--start-timestamp`/`--end-timestamp` for post-deployment reviews.
- Added `scripts/trading_retrospective_report.py` for manual Sunday/Monday weekly live retrospectives covering uncalibrated model-implied EV, empirical replay EV/PnL, realized PnL, fills vs intended notional, rejects by reason, current-stack replay comparison, and policy review/kill threshold flags.
- Added `docs/continuous-improvement-loop.md` and `docs/hypotheses/README.md` to formalize the hypothesis-to-replay-to-live-canary-to-gate workflow for recursive trading-system improvement.
- Raised the live global low-temperature MVP add-on default target from $25 to $50 after cap-aware portfolio replay showed the size-up remained positive behind the current live stack using current caps and recorded ask-sweep depth.
- Added `scripts/portfolio_promotion_report.py` as the cap-aware current-stack replay gate for live sizing and promotion decisions; it reports each sleeve's incremental risk/PnL/RR after current live plan order, risk caps, and recorded ask-sweep depth.
- Added `docs/roadmap-to-1000-ev-day.md` as the strategic scaling roadmap, including portfolio-promotion requirements, calibration/regime sizing, HRRR specialist overlay replay results, breadth expansion, and execution-attribution milestones.

## 2026-06-11

- Fixed live resting fallback accounting so a parent position is marked `PARTIAL`, not `FILLED`, when GTC child orders fill only part of the parent target; filled shares, cost, and average entry are now persisted from cumulative parent-level execution before returning from the fallback path.

## 2026-06-10

- Raised the live resting fallback TTL from 180 seconds to 360 seconds so accepted passive GTC ladder children have more time to fill before refresh/cancel.
- Changed live resting fallback parent summaries so GTC ladders that are posted, left unfilled for the TTL, and then cancelled are reported as `RESTING_TTL_EXPIRED` instead of the misleading `RESTING_LADDER_SKIPPED_AFTER_INSUFFICIENT_DEPTH`.
- Fixed depth-limited live entries so selected sweep depth caps only the initial FAK child, not the full intended risk target; filled sweep children now continue through retry and resting GTC ladder for the remaining risk-capped notional.
- Fixed live Polymarket v2 resting fallback submission so passive GTC/GTD child orders use explicit limit-order args with tick-resolved price and share size, avoiding invalid signed prices from market-order amount/price reconstruction.
- Routed live entries blocked only by insufficient ask-sweep depth directly into the resting $25 GTC ladder, preserving risk caps while avoiding no-op FAK rejects when passive fills are acceptable.
- Restricted the live `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first` add-on to the global low-temperature station allow-list after KLGA LOW_TEMP fills showed the MVP sleeve was missing the station filter used by the other global low sleeves.

## 2026-06-09

- Updated the Textual live dashboard for the US plus global live stack: Live exposure now aggregates all open positions across market dates instead of filtering to only the newest date, station/contract/strategy rows show market family, strategy rows show caps and decision windows, the Performance tab adds per-strategy recent/live/historical R/R and Sharpe columns, and the Config tab was trimmed to operational execution/sizing/risk settings.
- Raised main live sizing to $100 targets for the US no-tiny consensus core and global low-temperature consensus canary, with risk caps raised to max order/exact bucket-side $100, station/date/side $200, station/date $300, daily new risk $750, and total open risk $1,125. Resting fallback now ladders leftover notional into $25 penny-stepped GTC child orders under one shared 180-second TTL before refresh/cancel.
- Added a $25 live `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first` BUY_NO add-on after live-style portfolio replay showed positive incremental value behind the current stack; exposed `--global-low-mvp-notional-usd` for independent sizing.
- Deactivated the live US high-temperature 15m consensus overlay after cap-aware live-style replay showed the policy was negative incrementally behind the no-tiny consensus core; the active US high-temperature stack now keeps the canonical no-tiny consensus core plus the small moonshot sleeve.
- Deactivated live NGBoost BUY_YES after raw-snapshot replay showed weak overall and poor recent performance; raised the global low-temperature BUY_NO canary from $25 to $50 for the $0.05-$0.75 band and added a $5 `global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first` tiny-tail BUY_NO sleeve.
- Fixed live global low-temperature weather collection by routing non-US station IDs through the Celsius/global weather feature service. Recent live cycles were admitting EGLC/LFPB/RJTT/RKSI/VHHH/ZSPD markets but logging Unknown station errors from the US-only weather service before this change.

## 2026-06-08

- Added `scripts/live_policy_promotion_report.py` as the standardized raw-snapshot gatekeeper for US high-temperature live policy promotion. It replays from `prediction_snapshots`, ignores stale `research_policy_positions`, scores exact live-like opportunity scopes, and classifies policies as PROMOTE/CANARY/DEACTIVATE/RESEARCH_ONLY using predictive profitability/stability gates; fillability remains diagnostic-only because live execution uses FAK retries plus resting fallback.
- Replaced the live core dynamic-tuned BUY_NO policy with the bucket-consensus high-conviction 15m late `<= 0.50` replay winner (`pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first`) while preserving the existing core notional slot.
- Added a $25 live canary for global low-temperature `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`: BUY_NO-only, EGLC/LFPB/RJTT/RKSI/VHHH/ZSPD only, station-local `00:30-05:00`, `<= 0.75` entry cap, existing FAK/retry/GTC resting fallback execution, and no added depth gate. Live market discovery now runs with `market_scope=all` by default and admits markets by active strategy plan rather than a hard-coded US high-temp filter.

## 2026-06-07

- Extended `scripts/snapshot_opportunity_sweep.py` for raw `prediction_snapshots` policy replay: direct weather-outcome scoring fallback, HRRR-rich and METAR+HRRR-rich PM-active US12 model/consensus aliases, US high-temperature filtering, and a compact rolling 7-day/30-day/all-time summary mode for live-style high-conviction overlays.

## 2026-06-04

- Changed the research runner default to snapshot-only policy capture (`EVALUATE_POLICIES=0`) so TUI-started research loops collect broad `prediction_snapshots` without expanding `research_policy_positions`; set `EVALUATE_POLICIES=1` to materialize fixed policies.
- Added HRRR-rich and METAR+HRRR-rich PM-active US12 high-temperature model artifacts to the default US/all research-loop model set.
- Extended live/research HRRR inference feature assembly for the rich model families: remaining min/max, next-3h temperature summaries, dewpoint, relative humidity, wind/gust, cloud cover, shortwave, and forecast count now flow into model feature rows and snapshot raw JSON.

## 2026-06-03

- Added METAR-rich ASOS enrichment fields to dataset construction, model feature rows, and live/research fair-value feature assembly: relative humidity, wet-bulb approximation, pressure, pressure tendency, visibility, hourly precipitation, altimeter, feels-like temperature, min-so-far, range-so-far, and threshold/bucket distances from min-so-far.
- Retrained PM-active US12 high-temperature model families for METAR-rich, HRRR-rich, and combined METAR+HRRR-rich feature sets; saved model artifacts and report directories under `data/models` and `data/reports`.
- Added `scripts/model_registry.py`, `data/reports/model_registry.csv`, and `docs/model-performance-log.md` as the canonical model-performance registry/log, including a focused PM-active US12 enrichment comparison.

- Added the US high-temperature HRRR v2 model family to the default research-loop model set for MARKET_SCOPE=us and MARKET_SCOPE=all: dynamic bucket, tuned dynamic bucket, CatBoost bucket, MVP, high regression, and NGBoost. This activates HRRR research snapshot collection while leaving live execution on the existing obs-family strategy stack until HRRR replay is reviewed.

## 2026-05-28

- Added `docs/live-trading-journal.md` as the current live trading state and rationale tracker.
- Linked the journal from `AGENTS.md` so future agents update it when live strategy, sizing, risk, execution, or material trading assumptions change.

## 2026-05-06

- Added backfilled changelog and a modeling/trading decision reference doc.
- Rebuilt same-day datasets so `temp_change_1h` and `temp_change_3h` use timestamp-based lookbacks instead of row shifts.
- Retrained the dynamic bucket classifier, regression residual model, and NGBoost baseline on the rebuilt dataset.
- Fixed synthetic bucket semantics so bounded buckets are half-open and no longer overlap at integer boundaries.
- Added bucket-distribution experiment reports and stratified window comparisons.

## 2026-05-05

- Added the dynamic bucket candidate model and grouped ladder evaluation.
- Added the headless research collector and training-data diagnostics.
- Added HRRR-enriched model reporting and live scanner support improvements.

## 2026-05-04

- Added the baseline paper trader, station/date grouping, and initial same-day threshold model work.

## Notes

- Treat this as the running history for meaningful user-facing changes in the repo.
- Prefer adding a short entry here when a change affects modeling, trading, data generation, or operator workflow.
