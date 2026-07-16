# Repository Agent Instructions

- After a large or multi-file change set, break the work into coherent commits and push the branch when verification passes.
- Keep generated runtime state out of commits unless explicitly requested. This includes live SQLite databases, logs, and bulky ad hoc research artifacts.
- Commit source code, tests, scripts, and documentation changes when requested, but do not commit model artifacts or generated research outputs unless explicitly requested. In particular, leave `data/models/`, `data/reports/`, ad hoc `reports/`, generated raw/processed datasets, logs, and runtime SQLite state out of commits.
- Before committing, inspect `git status --short` and avoid staging unrelated user changes accidentally.
- When the user asks a question, expresses uncertainty, or wants to discuss strategy, answer with an overview and recommendation first; do not jump into code changes unless they explicitly ask for implementation or approve a proposed change.
- When you figure out a useful repeatable workflow, command pattern, data source, or repo-specific practice, document it here so future agents do not have to rediscover it.
- Treat `docs/live-trading-journal.md` as the live trading state and rationale tracker. Update it when live policy mix, sizing, entry caps, risk caps, execution behavior, or material trading lessons change.
- Update docs/changelog.md for meaningful system changes, including data source changes, model activation/deactivation, research-loop defaults, live execution behavior, sizing/risk policy, and operator workflow changes.
- Use the `roboweather` conda environment for Python commands in this repo:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python`
  - Example: `/home/maxrush/miniconda3/envs/roboweather/bin/python -m pytest tests/test_live_execution.py`

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
- While lifecycle gates remain open, use a bounded temporary probe:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/run_market_tape.py --catalog /tmp/roboweather_market_tape_catalog.sqlite --raw-dir /tmp/roboweather_market_tape_raw --max-messages 50 --max-seconds 60 --refresh-seconds 30`
- Follow every probe with the strict catalog/segment check:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/market_tape_health.py --catalog /tmp/roboweather_market_tape_catalog.sqlite`
- The recorder now fails closed on zero token events. Require nonzero messages/events, a subscription generation, `RESYNCING` followed by `VALID` coverage for full-book tokens, cataloged raw partitions, fresh telemetry, and exact segment replay.
- Unexpected disconnects must appear as `GAPPED -> RECONNECTING -> RESYNCING`; replay must not bridge those intervals, and a token returns to `VALID` only after a new full book.
- Rebuild deterministic L2 checkpoints only from verified raw segments:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/rebuild_market_tape_books.py --catalog /path/to/tape_catalog.sqlite /path/to/segment.jsonl`
- Inspect one token at an inclusive causal receipt boundary with:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/reconstruct_market_tape_book.py --token-id TOKEN --at ISO_UTC /path/to/segment-1.jsonl [/path/to/segment-2.jsonl ...]`
- Queue, frame, partition, and retention values remain provisional until complete-lifecycle resource measurement passes. Do not run this recorder as an unattended production service yet.

## Continuous improvement workflow

- Use `docs/continuous-improvement-loop.md` for the recursive improvement process. New strategy, sizing, model, risk, or execution hypotheses should get a record under `docs/hypotheses/` when they may affect live behavior or future promotion decisions.
- Convert durable lessons into gates where they are enforceable: tests in `tests/`, replay/report checks in `scripts/`, operator workflow requirements in `AGENTS.md` or `docs/live-trading-journal.md`, and live risk/strategy constraints in source modules.
- For weekly live-performance review, run `scripts/trading_retrospective_report.py` manually on Sunday after markets resolve or Monday before sizing/promotion decisions. It reads the live SQLite ledger, optionally compares the same window to current-stack research replay, and emits a Markdown/JSON retrospective covering uncalibrated model-implied EV, empirical replay EV/PnL, realized PnL, fills vs intended notional, rejects by reason, and policies crossing review/kill thresholds. Default weekly command:
  - `/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/trading_retrospective_report.py --live-db /home/maxrush/.local/state/roboweather/live_trading.sqlite --research-db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite --start-date YYYY-MM-DD --end-date YYYY-MM-DD --out reports/trading-retrospectives/weekly-YYYY-Www.md`
  - Add `--start-timestamp ISO_TS` for post-deployment reviews that should exclude pre-fix live ledger noise.
