# Repository Agent Instructions

- After a large or multi-file change set, break the work into coherent commits and push the branch when verification passes.
- Keep generated runtime state out of commits unless explicitly requested. This includes live SQLite databases, logs, and bulky ad hoc research artifacts.
- Commit source code, tests, scripts, and documentation changes when requested, but do not commit model artifacts or generated research outputs unless explicitly requested. In particular, leave `data/models/`, `data/reports/`, ad hoc `reports/`, generated raw/processed datasets, logs, and runtime SQLite state out of commits.
- Before committing, inspect `git status --short` and avoid staging unrelated user changes accidentally.
- When the user asks a question, expresses uncertainty, or wants to discuss strategy, answer with an overview and recommendation first; do not jump into code changes unless they explicitly ask for implementation or approve a proposed change.
- When you figure out a useful repeatable workflow, command pattern, data source, or repo-specific practice, document it here so future agents do not have to rediscover it.
- Treat `docs/live-trading-journal.md` as the live trading state and rationale tracker. Update it when live policy mix, sizing, entry caps, risk caps, execution behavior, or material trading lessons change.
- Update docs/changelog.md for meaningful system changes, including data source changes, model activation/deactivation, research-loop defaults, live execution behavior, sizing/risk policy, and operator workflow changes.
- Use the `roboweather` conda environment for Python commands in this repo. Codex may run on either host, so choose the path that exists in the current checkout:
  - Remote runtime/development host: `/home/maxrush/miniconda3/envs/roboweather/bin/python`
  - Local checkout host: `/home/hmrush/Desktop/personal/roboweather/.conda/roboweather/bin/python`
  - Remote example: `/home/maxrush/miniconda3/envs/roboweather/bin/python -m pytest tests/test_live_execution.py`
  - Local example: `/home/hmrush/Desktop/personal/roboweather/.conda/roboweather/bin/python -m pytest tests/test_live_execution.py`
- Paths under `/home/maxrush/...` are valid on the remote host where the operator may run Codex, collectors, live/research databases, and production-style commands. A missing `/home/maxrush` path on the local host means use the local equivalent; it is not a repository or runtime failure.

## Continuous research-loop operation

- Use `deploy/systemd/roboweather-research.service` for continuous prediction-snapshot and outcome collection. Install it under `~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then enable/start it with `systemctl --user enable --now roboweather-research.service`.
- Inspect it with `systemctl --user status roboweather-research.service --no-pager` and `journalctl --user -u roboweather-research.service`. The TUI controls and observes this durable service but does not own its lifetime; closing the TUI must not stop research collection.
- The research runner takes a nonblocking writer lock at `<DB>.research-loop.lock`. Do not bypass the lock or run a second manual/TUI-owned research loop against the same database. Stop the durable service before deliberately running a one-off loop against its DB.
- The service has a 4 GiB memory ceiling and restart-on-failure. Treat a memory-limit restart or repeated restart loop as an operational incident; inspect the journal and recent `engine_state.raw_json.runtime_metrics` before restarting manually.

## Documentation architecture

- Prefer a small set of living canonical documents over new one-off narrative reports:
  - `docs/current-trading-system-audit.md`: current financial/systems verdict, durable evidence, open risks, and promotion blockers. Update the body in place and append a short audit-log entry.
  - `docs/execution-rebuild-roadmap.md`: current phase sequence, status, and exit gates.
  - `docs/implementation/`: active feature/sprint implementation contracts and acceptance checklists.
  - `docs/hypotheses/`: dated economic or trading ideas, evidence requirements, falsification, and decision history. Keep the hypothesis separate from its implementation plan.
  - `docs/live-trading-journal.md`: current funded state, sizing, risk, execution rules, and material live lessons only.
  - `docs/changelog.md`: chronological completed system and workflow changes only.
  - `reports/`: generated or ad hoc evidence. Reports are not canonical conclusions and remain uncommitted unless explicitly requested.
- When analysis changes the system assessment, update `docs/current-trading-system-audit.md`. Update the roadmap only if phase status, sequencing, or gates changed; update the live journal only if funded state or live operating assumptions changed.
- Do not create a new dated narrative audit when the conclusion can be incorporated into the living audit. Preserve detailed tables in a reproducible report or script and copy only durable conclusions into the canonical documents.

## Research SQLite analysis workflow

- Prefer the active local research database for current analysis:
  - `/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
  - The repo copy at `data/paper/research_2026-05-08_multimodel.sqlite` may be stale because live SQLite runtime state is intentionally kept out of commits.
- Before analyzing policy results, verify which DB is current and which market dates have official weather outcomes:
  - `ls -lh ~/.local/state/roboweather/*.sqlite data/paper/*.sqlite`
  - `sqlite3 ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite "select market_date, count(*) outcomes from station_date_outcomes group by market_date order by market_date;"`
  - `sqlite3 ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite "select market_date, count(*) positions, sum(case when sdo.final_high_tmpf is not null then 1 else 0 end) with_high from research_policy_positions rpp left join station_date_outcomes sdo using(station, market_date) group by market_date order by market_date;"`
- Research policy scoring is weather-outcome based, not Polymarket-settlement based. The resolver writes official max temps to `station_date_outcomes.final_high_tmpf` from IEM ASOS. Same-day rows are normally unresolved until the next day after the configured station-local resolve hour.
- For retrospective policy-bucket experiments, prefer replaying first-eligible rows from `prediction_snapshots` instead of only filtering already-materialized `research_policy_positions`. This avoids bias when the existing first policy row fails a new filter but a later snapshot on the same station/date would have qualified.
- To replay PM-active US12 consensus high-conviction policies, pair snapshots from:
  - `dynamic_bucket_pm_active_us12_obs_2022_2025`
  - `mvp_pm_active_us12_obs_2022_2025`
- Match the two models on station, market date, observation delay bucket, strategy bucket, selected side, selected market id, and selected bucket. Use the mean of the two selected edges/fairs as the consensus edge/fair, then sort by timestamp/id and select the first row per `(station, market_date)` for `*_first` style policies.
- Existing helper code worth reading before custom analysis:
  - `weather_trader/research/policies.py` for policy construction and consensus pairing mechanics.
  - `weather_trader/research/resolver.py` for official max-temperature resolution.
  - `scripts/policy_leaderboard.py` for bucket parsing, scoring, return/risk, Sharpe, and station regime labels.

## Ask sweep and bid ladder research analysis

- The research loop records two separate hypothetical execution modes:
  - Ask sweep: immediate take from existing ask depth, stored in `selected_ask_sweep_json`.
  - Bid ladder: passive post-only bids we would place, stored in `selected_bid_ladder_json`.
- Do not interpret bid ladder rows as fill simulations. They describe posted bid geometry, book-relative aggressiveness, preserved edge, and reserved notional only.
- Useful scalar columns on both `prediction_snapshots` and `research_policy_positions`:
  - Sweep: `selected_sweep_price_cap`, `selected_sweep_depth_to_cap`, `selected_sweep_fillable_25_usd`, `selected_sweep_fillable_50_usd`, `selected_sweep_fillable_100_usd`, `selected_sweep_vwap_25`, `selected_sweep_vwap_50`, `selected_sweep_vwap_100`.
  - Bid ladder: `selected_bid_ladder_top_price`, `selected_bid_ladder_low_price`, `selected_bid_ladder_levels`, `selected_bid_ladder_total_notional_usd`, `selected_bid_ladder_top_distance_from_ask`, `selected_bid_ladder_top_improvement_over_best_bid`, `selected_bid_ladder_min_edge`, `selected_bid_ladder_max_edge`.
- Repeatable SQLite checks:
  - `sqlite3 ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite "pragma table_info(prediction_snapshots);"`
  - `sqlite3 ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite "pragma table_info(research_policy_positions);"`
  - `sqlite3 ~/.local/state/roboweather/research_2026-05-08_multimodel.sqlite "select policy_name, count(*) rows, avg(selected_sweep_fillable_50_usd >= 50.0) sweep_50_rate, avg(selected_bid_ladder_total_notional_usd) avg_ladder_notional, avg(selected_bid_ladder_min_edge) avg_min_ladder_edge from research_policy_positions where selected_ask_sweep_json is not null group by policy_name order by rows desc;"`
- Full implementation report and query examples are in `docs/ask-sweep-bid-ladder-research-capture-2026-05-18.md`.

## Raw snapshot policy replay workflow

- Current research collection is snapshot-first. Do not assume `research_policy_positions` is current; the research loop intentionally avoids materializing/clogging policy rows unless `EVALUATE_POLICIES=1` or a specific backfill is run. Treat policy tables as historical/stale after the latest materialized timestamp and replay directly from raw `prediction_snapshots` for current analysis.
- For post-analysis after snapshot-only research collection, use `scripts/snapshot_opportunity_sweep.py` instead of filtering `research_policy_positions`. The script replays policy-like constraints from raw `prediction_snapshots`, builds consensus rows in memory, scores directly from `station_date_outcomes` when `prediction_results` is sparse, and does not write policy rows.
- Current US high-temperature rolling check:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/snapshot_opportunity_sweep.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --market-family HIGH_TEMP --us-high-temp-only --rolling-summary --min-policy-n 20 --top-n 8`
- Phase 1 adverse-selection price-sheet sanity check for the scoped US high-temperature consensus no-tiny BUY_NO sleeve:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase1_price_sheet_report.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
- Use `--min-policy-n 6` for a very fresh read on newly activated model families; use `--min-policy-n 20` or higher before treating a pattern as actionable.
- The rolling summary uses a compact live-style overlay: high-conviction only, entry caps `0.00-0.50` and `0.05-0.50`, all-day and local late windows, no fixed delay plus `10m`/`15m`, and `station_date` / `station_date_bucket_side_obs_delay` scopes.
- Add `--start-date YYYY-MM-DD` when you want a deliberately recent-only report; without it the script compares last 7 days, last 30 days, and all loaded resolved history.
- For live promotion decisions, do a portfolio replay, not just an isolated policy leaderboard. Start from the current live stack in live plan order, rebuild consensus rows from raw `prediction_snapshots`, then apply the candidate after existing policies using current sizing, entry caps, station/date caps, station/date/side caps, exact bucket/side caps, and recorded depth/VWAP fields. Classify apparent winners as duplicate size-ups, overlapping delay/window variants, low-sample niches, or genuinely additive policies before recommending promotion. This is the workflow that caught the NGBoost and 15m-overlay issues.
- For current live-stack portfolio replay and sizing/promotion checks, use `scripts/portfolio_promotion_report.py`. It replays from raw `prediction_snapshots`, rebuilds consensus rows in memory, applies the current live plan order, live-style risk caps, and recorded ask-sweep depth, then reports each sleeve's incremental risk/PnL/RR after earlier live sleeves consume capacity. Default current-stack check:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/portfolio_promotion_report.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
  - Add `--start-date YYYY-MM-DD` for recent-only reviews. Use `--no-depth` only for an upper-bound capacity diagnostic, not promotion evidence.

## Tape-backed rolling portfolio discovery workflow

- Strategies do not need to be defined before snapshot or shared-tape collection. The canonical Phase 3D contract is `docs/implementation/tape-strategy-discovery.md`: build a policy-neutral causal snapshot + tape + settlement view, run recurring constrained searches from resolved-data watermarks, register a bounded set of versioned challengers, and continuously evaluate each candidate only on decisions after its own activation.
- The broad discovery materializer must cover every eligible causal snapshot/token decision, not only current policies or frozen V2a pilots. It is a derived view over existing snapshots and tape, not another raw recorder.
- Keep discovery and execution holdout separate. Run the rolling snapshot sweep through the day before tape activation:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/snapshot_opportunity_sweep.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --end-date YYYY-MM-DD --market-family HIGH_TEMP --us-high-temp-only --rolling-summary --min-policy-n 20 --top-n 8`
- Before ranking, seal that discovery run's sources/watermarks, grammar, folds, costs, fill scenario, size, caps, effective-sample rules, complexity penalty, correlated-family collapse, nomination rule, and earliest activation. This makes the run reproducible without freezing the discovery program. Later resolved data create another append-only run. Prefer stable first-eligible families across date-ordered folds, collapse nearby delay/scope/entry variants, and prefer the simplest stable family to the highest exploratory return.
- Replay the frozen portfolio against quote-ready books with:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/tape_strategy_holdout_report.py --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --discovery-cutoff YYYY-MM-DD --holdout-start YYYY-MM-DD --end-date YYYY-MM-DD`
- The initial built-in `pm_high_regression_10m_late`, `pm_mvp_late`, then `pm_dynamic_tuned_10m_late` family is a reproducible historical bridge/fixture, not the required Phase 3D champion or a template that constrains broad discovery.
- The report is read-only and fail-closed. It requires continuous `VALID` pre-signal coverage, reconstructs the exact token book after configured latency, and simulates only an immediate capped ask sweep. Missing coverage and unavailable capped asks are rejections, not snapshot-price fills.
- Interpret the existing output as post-cutoff taker-execution hypothesis evidence. It does not infer passive fills, uses weather outcomes rather than venue settlement, and cannot by itself authorize funding. Phase 3D forward confirmation additionally requires an immutable candidate version created before its evaluation cohort, venue-authoritative settlement, fill/markout evidence, and later tape.

## Phase 3D batch compatibility workflow

- The discovery and forward-report Phase 3D CLIs are a committed read-only batch vertical slice,
  not the intended continuous scheduler/registry. Use them only for development
  fixtures while Slice C5 in `docs/implementation/tape-strategy-discovery.md`
  remains open. Do not treat their one-winner output as the steady-state design.
- Create a generated artifact directory under `reports/` and declare a future
  activation timestamp before running a compatibility discovery. The CLI seals source
  watermarks, tape sessions/partitions, model universe, grammar, folds, costs,
  caps, and winner rule in `discovery_run.json` before ranking:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_strategy_discovery.py --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --source-start-date YYYY-MM-DD --discovery-cutoff-exclusive YYYY-MM-DD --activation-timestamp ISO_UTC --out reports/phase3d/RUN_NAME`
- A compatibility run writes exactly one immutable `strategy_manifest.json` or
  `no_winner.json`. Do not edit or overwrite either result. In the continuous
  architecture, this becomes zero or more content-addressed candidate versions
  in an append-only registry; a changed rule starts a new version/cohort rather
  than modifying earlier evidence.
- After activation, evaluate the exact manifest only through an exclusive end
  date:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_forward_report.py --manifest reports/phase3d/RUN_NAME/strategy_manifest.json --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --end-date-exclusive YYYY-MM-DD --out reports/phase3d-forward/RUN_NAME`
- The forward report counts only venue-authoritative `resolutions` toward a
  pass. Empty venue truth, settlement disagreement, insufficient independent
  dates, nonpositive base economics, missing markouts, or invalid tape cannot
  be replaced with snapshot prices or IEM-only promotion claims. Leave all
  generated Phase 3D artifacts uncommitted.
- The C2 registry lives outside the repository (default
  `~/.local/state/roboweather/discovery/catalog.sqlite`). Initialize it with
  `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_registry.py init`
  and inspect it query-only with the `status` subcommand. Use
  `import-batch-v1 ARTIFACT_DIR` only to preserve a compatibility run and
  candidate identity; it intentionally imports no cohort or scorecard evidence.
  One writer holds `<REGISTRY>.writer.lock`; do not bypass it. C3 supplies
  recurring nominations, C4 appends activation-bounded scorecards, C5 applies
  research roles, and C6 schedules the loop. No registry transition may
  authorize funded trading.

## Phase 3D continuous discovery workflow

- C3-C6 are repository-complete. Manual idempotent surfaces remain available
  for diagnostics. Run one discovery cycle with a future activation boundary:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_continuous_discovery.py --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --registry /home/maxrush/.local/state/roboweather/discovery/catalog.sqlite --source-start-date YYYY-MM-DD --discovery-cutoff-exclusive YYYY-MM-DD --activation-timestamp ISO_UTC`
- The command holds the registry's single-writer lock, skips before
  materialization when resolved outcomes and the sealed grammar/build/model
  inputs are unchanged, resumes a sealed interrupted run, and writes no
  repository artifact. Completion, no-nomination, and budget outcomes are
  immutable; an elapsed activation boundary fails closed. Registered
  candidates are research-only `NOMINATED` versions; C3 creates no forward scorecard, champion, Phase 4 request, or funded authority.
- Append one idempotent C4 scorecard watermark for every active candidate after
  the exclusive evaluation end date has elapsed:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_continuous_evaluation.py --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --registry /home/maxrush/.local/state/roboweather/discovery/catalog.sqlite --end-date-exclusive YYYY-MM-DD --as-of-timestamp ISO_UTC`
- C4 reuses one open cohort per exact candidate activation and appends
  `FORWARD_SHADOW` plus family-peer `COMMON_DATE` scorecards addressed by the
  complete as-of watermark. It excludes every pre-activation row, rejects
  invalid tape, credits only venue-authoritative settlement, requires valid
  markouts before review readiness, never infers `ACTUAL_ORDER` fills from
  public tape, and applies no role transition or funded authority.
- Apply one deterministic research-only C5 role review after C4:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_apply_transitions.py --registry /home/maxrush/.local/state/roboweather/discovery/catalog.sqlite --effective-at-timestamp ISO_UTC`
- C5 requires positive conservative and base uncertainty lower bounds, bounded
  station/date concentration, venue settlement, valid markouts, and aligned
  replacement evidence. It may assign no champion. Lifecycle history rejects
  role jumps/backdating and explicitly denies funded authority; C5 creates no
  Phase 4 request.
- For continuous dry-run operation, install
  `deploy/systemd/roboweather-phase3d-discovery.service` under
  `~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then enable it
  with `systemctl --user enable --now roboweather-phase3d-discovery.service`.
  The service owns a separate scheduler lock, runs C4/C5 every six hours and C3
  at most weekly, and enforces task, whole-cycle, registry-size, candidate, and
  diagnostic budgets. Do not bypass either scheduler or registry writer lock.
- Inspect it with
  `systemctl --user status roboweather-phase3d-discovery.service --no-pager`,
  `journalctl --user -u roboweather-phase3d-discovery.service`, and
  `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/phase3d_status.py`.
  The TUI Processes tab reads the same registry status but does not own the
  service lifetime. Disable the service and return to manual idempotent runs if
  duplicate cycles, unbounded state, or stale/silent evaluation appears.

## Price Sheet V2a dataset workflow

- Build generated V2a fit/evaluation artifacts read-only from the current remote research database. The default frozen evaluation starts on the pilot activation date and the fit corpus ends strictly before it:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/build_price_sheet_v2_dataset.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --out reports/price-sheet-v2a-datasets/current`
- Build one signal's generated Slice 2/3 calibration, conservative-price, and economic artifacts from that dataset directory:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/price_sheet_v2_report.py --dataset-dir reports/price-sheet-v2a-datasets/current/late_hrrr_rich_tuned_dynamic_buy_no_v1 --out reports/price-sheet-v2a-slice3/current/late_hrrr_rich_tuned_dynamic_buy_no_v1`
- The report expands with resolved prior evaluation dates only, persists an exclusive cutoff and hash for every fitted fold, and compares pooled-Platt and market-aware pricing candidates. It fails closed with `NO_CALIBRATOR_SELECTED` unless `--selected-calibration-baseline` is supplied. Do not supply that option retrospectively to promote the same rows; freeze the baseline and `--untouched-forward-start-date` before evaluating a promotion window.
- Slice 3 uncertainty uses only resolved out-of-fold residuals from market dates strictly before each priced row. Early rows without the configured minimum prior dates are expected to be ineligible. A market-aware result that fails to beat the causal market baseline is a negative result, not permission to quote at the market price.
- Generated manifests and JSONL rows are research outputs. Inspect them for nonzero fit/evaluation counts and label/reference diagnostics, but leave the output directory uncommitted.
- On the local `/home/hmrush` checkout, use `/home/hmrush/Desktop/personal/roboweather/.conda/roboweather/bin/python` with a locally available DB. The checked-in May DB predates the pilot HRRR-rich/HRRR-v2 families and is useful for schema compatibility only; zero pilot rows there are expected.

## Legacy candidate-scoped shadow collection workflow

- The current candidate-scoped collector is a plumbing prototype, not the Phase 3 shared market tape. It begins from policy-candidate tokens after candidate generation and its existing shadow labels are not promotion evidence.
- Phase 3 design and acceptance are canonical in `docs/implementation/phase-3-market-tape-replay.md`. New collection work should follow that plan rather than expanding the candidate-scoped collector into another policy-specific data path.
- Do not register the bounded quote specs as live funded strategies, and do not infer live promotion from quote intent or existing shadow outcome rows.
- Current collection sequence for a dry-run session:
  - Run the live dry-run cycle so current candidate rows, price sheets, and `$50/$100` shadow quote intents are persisted.
  - Run CLOB candidate-token collection: `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/collect_candidate_clob_events.py --db /home/maxrush/.local/state/roboweather/live_trading.sqlite --max-seconds 900`
  - Persist batch labels: `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/label_shadow_quote_outcomes.py --db /home/maxrush/.local/state/roboweather/live_trading.sqlite`
  - Run the strict health report: `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/shadow_collection_report.py --db /home/maxrush/.local/state/roboweather/live_trading.sqlite`
- Use the live ledger health report to verify that current dry-run/live cycles can reconstruct candidates through price sheets, emitted useful-size quote specs, lifecycle states, book/feed coverage, persisted labels, and pending/available markout windows:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/shadow_collection_report.py --db /home/maxrush/.local/state/roboweather/live_trading.sqlite`
  - Add `--since-timestamp ISO_TS` for a specific dry-run session, and `--candidate-id LIVE_CANDIDATE_ID` to inspect a known row.
- A passing legacy health report means only that candidate-scoped plumbing rows can be reconstructed. It does not prove pre-signal coverage, continuous book validity, correct trade direction, hypothetical fills, or profitability.

## Phase 3 market-tape development workflow

- The shared recorder is opt-in and separate from both research and live ledgers. Do not point it at either production SQLite database.
- After accepting the bounded lifecycle evidence, use `deploy/systemd/roboweather-market-tape.service` for continuous policy-independent research collection. Install it under `~/.config/systemd/user/`, enable/start it with `systemctl --user enable --now roboweather-market-tape.service`, and keep the bounded `roboweather-market-tape-lifecycle.service` disabled; the latter exists only to reproduce a time-limited validation cohort.
- Check the continuous collector with `systemctl --user status roboweather-market-tape.service --no-pager`, then run `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/market_tape_health.py --catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite`. Continuous collection does not relax replay validity: every decision window crossing a non-`VALID` interval remains rejected.
- The TUI Processes tab controls the continuous tape unit through user systemd and shows lightweight catalog health plus journal output. Closing the TUI never stops the tape service. Stop and restart require confirmation because they create invalid coverage intervals. Override the displayed catalog for local/testing use with `ROBOWEATHER_TAPE_CATALOG=/path/to/catalog.sqlite`.
- Slice 2 repository implementation and the bounded 72-hour host evidence are accepted as sufficient research infrastructure. Another lifecycle run is not required before Phase 3D; exact decision windows must still fail closed on gaps.
- For recorder changes or bounded diagnostics, use a temporary probe:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/run_market_tape.py --catalog /tmp/roboweather_market_tape_catalog.sqlite --raw-dir /tmp/roboweather_market_tape_raw --max-messages 50 --max-seconds 60 --refresh-seconds 30`
- Follow every probe with the strict catalog/segment check:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/market_tape_health.py --catalog /tmp/roboweather_market_tape_catalog.sqlite`
- For the bounded complete-lifecycle evidence run on the remote host, install and start (but do not enable) `deploy/systemd/roboweather-market-tape-lifecycle.service`. It runs for at most 72 hours and writes only under `~/.local/state/roboweather/market_tape/`.
- Inspect the bounded service with `systemctl --user status roboweather-market-tape-lifecycle.service --no-pager` and `journalctl --user -u roboweather-market-tape-lifecycle.service`. Stop it explicitly with `systemctl --user stop roboweather-market-tape-lifecycle.service` if the probe must be abandoned.
- After the bounded run, evaluate the complete Slice 2 evidence:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/market_tape_lifecycle_report.py --catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite`
- Scope acceptance to the declared clean validation cohort so historical probes do not poison the result:
  - use `--validation-start ISO_UTC --validation-end ISO_UTC` for a bounded session-start window;
  - or repeat `--session-id TAPE_SESSION_ID` for the exact restart cohort;
  - preferably use `--validation-run-id VALIDATION_RUN_ID` to select every supervised restart in the persisted run; the lifecycle deadline state creates and preserves this ID automatically;
  - errors, lag, gaps, reconstruction failures, and incomplete lifecycles inside the selected cohort still fail closed.
- Validation sessions persist their run, recorder-scoped build, and collector-config fingerprints. The build fingerprint covers recorder-transitive source/data files, the service unit, Python, and relevant dependency versions; unrelated repository commits are intentionally excluded. Acceptance fails when selected sessions mix or omit any fingerprint. Every initial/scheduled discovery attempt also persists its status and returned token/market membership; failed or incomplete refreshes fail acceptance.
- The lifecycle report includes every refresh-member market whose listing timestamp falls inside the selected observation window. Its matured cohort contains markets whose scheduled close is at or before the validation end. Late or fallback-listed markets remain explicit per-market exclusions and warnings but do not poison otherwise eligible markets; coverage-incomplete eligible markets still fail the run. Later-closing listings are reported as `RIGHT_CENSORED`; authoritative markets listed before the observation window remain background seed markets.
- Tape discovery directly polls current, D+1, and D+2 event slugs. The expanded universe is subscribed in 500-token WebSocket batches; strict health must show every latest-generation member received a full-book `VALID` transition.
- Reinstall `deploy/systemd/roboweather-market-tape-lifecycle.service` and run `systemctl --user daemon-reload` after recorder-unit changes. Its preserved runtime-directory deadline prevents automatic restarts from resetting the 72-hour bound.
- The lifecycle report requires at least 12 recorded hours, authoritative Gamma listing timestamps, discovery within 300 seconds, no operational coverage gap over 30 seconds, RSS at or below 1 GiB, queue high-water below capacity, projected raw growth at or below 25 GiB/day, and complete eligible markets for every eligible station/family. Peak receipt lag over 10 seconds is a lifecycle warning rather than a whole-run failure; missing lag telemetry remains a failure, and current-session strict health may still fail on stale lag.
- The 30-second lifecycle budget is an availability classification, not permission to bridge missing events. Decision joins and tape holdouts must continue to reject any signal whose configured pre-signal-through-execution window crosses a non-`VALID` interval; the default pre-signal requirement is 60 seconds.
- The recorder now fails closed on zero token events. Require nonzero messages/events, a subscription generation, `RESYNCING` followed by `VALID` coverage for full-book tokens, cataloged raw partitions, fresh telemetry, and exact segment replay.
- Unexpected disconnects must appear as `GAPPED -> RECONNECTING -> RESYNCING`; replay must not bridge those intervals, and a token returns to `VALID` only after a new full book.
- Rebuild deterministic L2 checkpoints only from verified raw segments:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/rebuild_market_tape_books.py --catalog /path/to/tape_catalog.sqlite /path/to/segment.jsonl`
- Inspect one token at an inclusive causal receipt boundary with:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/reconstruct_market_tape_book.py --token-id TOKEN --at ISO_UTC /path/to/segment-1.jsonl [/path/to/segment-2.jsonl ...]`
- Join an immutable decision-timing JSON record with explicit latency and pre-signal coverage using:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/join_market_tape_decision.py --catalog /path/to/tape_catalog.sqlite --decision-json /path/to/decision.json --pre-signal-seconds 60 /path/to/segment.jsonl`
- Join a persisted postable price-sheet quote directly from the read-only execution ledger through its observed cancel/GTD termination boundary using:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/join_market_tape_decision.py --catalog /path/to/tape_catalog.sqlite --execution-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --quote-id QUOTE_ID --activation-timestamp ISO_UTC --latency-ms 0 --pre-signal-seconds 60 /path/to/segment-1.jsonl [/path/to/segment-2.jsonl ...]`
  - Omit `--activation-timestamp` only when the persisted price sheet already embeds the frozen signal activation. A successful join requires the supplied raw segments and one continuous `VALID` interval to reach the recorded cancel time or GTD expiry.
- Continuous unattended research collection is approved with the measured queue, memory, and approximately 12 GB/day raw-growth profile. Keep disk and strict health visible, retain the 1 GiB service memory cap, and do not treat the recorder as a production trading dependency; retention remains an explicit operator responsibility and no replay may bridge missing coverage.

## Continuous improvement workflow

- Use `docs/continuous-improvement-loop.md` for the recursive improvement process. New strategy, sizing, model, risk, or execution hypotheses should get a record under `docs/hypotheses/` when they may affect live behavior or future promotion decisions.
- Convert durable lessons into gates where they are enforceable: tests in `tests/`, replay/report checks in `scripts/`, operator workflow requirements in `AGENTS.md` or `docs/live-trading-journal.md`, and live risk/strategy constraints in source modules.
- For weekly live-performance review, run `scripts/trading_retrospective_report.py` manually on Sunday after markets resolve or Monday before sizing/promotion decisions. It reads the live SQLite ledger, optionally compares the same window to current-stack research replay, and emits a Markdown/JSON retrospective covering uncalibrated model-implied EV, empirical replay EV/PnL, realized PnL, fills vs intended notional, rejects by reason, and policies crossing review/kill thresholds. Default weekly command:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/trading_retrospective_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date YYYY-MM-DD --end-date YYYY-MM-DD --out reports/trading-retrospectives/weekly-YYYY-Www.md`
  - Add `--start-timestamp ISO_TS` for post-deployment reviews that should exclude pre-fix live ledger noise.
