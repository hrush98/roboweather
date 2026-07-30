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

- Strategies do not need to be defined before snapshot or shared-tape collection. Generate candidate policy families from raw `prediction_snapshots`, choose an immutable cutoff, freeze the exact family/order/parameters, and evaluate only later decisions against the shared tape.
- Keep discovery and execution holdout separate. Run the rolling snapshot sweep through the day before tape activation:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/snapshot_opportunity_sweep.py --db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --end-date YYYY-MM-DD --market-family HIGH_TEMP --us-high-temp-only --rolling-summary --min-policy-n 20 --top-n 8`
- Prefer stable first-eligible families across all/30-day/7-day windows, collapse nearby delay/scope/entry variants, and freeze priority order before inspecting the holdout.
- Replay the frozen portfolio against quote-ready books with:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/tape_strategy_holdout_report.py --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --tape-catalog /home/maxrush/.local/state/roboweather/market_tape/catalog.sqlite --discovery-cutoff YYYY-MM-DD --holdout-start YYYY-MM-DD --end-date YYYY-MM-DD`
- The initial built-in family is `pm_high_regression_10m_late`, `pm_mvp_late`, then `pm_dynamic_tuned_10m_late`. Repeat `--sleeve` to declare a different priority.
- The report is read-only and fail-closed. It requires continuous `VALID` pre-signal coverage, reconstructs the exact token book after configured latency, and simulates only an immediate capped ask sweep. Missing coverage and unavailable capped asks are rejections, not snapshot-price fills.
- Interpret the output as post-cutoff taker-execution hypothesis evidence. It does not infer passive fills, uses weather outcomes rather than venue settlement, and cannot by itself authorize funding.

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
- Slice 2 repository implementation is complete, but the acceptance gate requires elapsed host evidence. The retained July 16 probes are too short and must not be described as a complete lifecycle pass.
- While lifecycle gates remain open, use a bounded temporary probe:
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
  - errors, lag, gaps, reconstruction failures, and incomplete lifecycles inside the selected cohort still fail closed.
- Tape discovery directly polls current, D+1, and D+2 event slugs. The expanded universe is subscribed in 500-token WebSocket batches; strict health must show every latest-generation member received a full-book `VALID` transition.
- Reinstall `deploy/systemd/roboweather-market-tape-lifecycle.service` and run `systemctl --user daemon-reload` after recorder-unit changes. Its preserved runtime-directory deadline prevents automatic restarts from resetting the 72-hour bound.
- The lifecycle report requires at least 12 recorded hours, authoritative Gamma listing timestamps, discovery within 300 seconds, no coverage gap over 5 seconds, receipt lag at or below 10 seconds, RSS at or below 1 GiB, queue high-water below capacity, projected raw growth at or below 25 GiB/day, and at least one complete eligible market per discovered station/family.
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
- Queue, frame, partition, and retention values remain provisional until complete-lifecycle resource measurement passes. Do not run this recorder as an unattended production service yet.

## Continuous improvement workflow

- Use `docs/continuous-improvement-loop.md` for the recursive improvement process. New strategy, sizing, model, risk, or execution hypotheses should get a record under `docs/hypotheses/` when they may affect live behavior or future promotion decisions.
- Convert durable lessons into gates where they are enforceable: tests in `tests/`, replay/report checks in `scripts/`, operator workflow requirements in `AGENTS.md` or `docs/live-trading-journal.md`, and live risk/strategy constraints in source modules.
- For weekly live-performance review, run `scripts/trading_retrospective_report.py` manually on Sunday after markets resolve or Monday before sizing/promotion decisions. It reads the live SQLite ledger, optionally compares the same window to current-stack research replay, and emits a Markdown/JSON retrospective covering uncalibrated model-implied EV, empirical replay EV/PnL, realized PnL, fills vs intended notional, rejects by reason, and policies crossing review/kill thresholds. Default weekly command:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/trading_retrospective_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date YYYY-MM-DD --end-date YYYY-MM-DD --out reports/trading-retrospectives/weekly-YYYY-Www.md`
  - Add `--start-timestamp ISO_TS` for post-deployment reviews that should exclude pre-fix live ledger noise.
