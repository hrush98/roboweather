# Repository Agent Instructions

- After a large or multi-file change set, break the work into coherent commits and push the branch when verification passes.
- Keep generated runtime state out of commits unless explicitly requested. This includes live SQLite databases, logs, and bulky ad hoc research artifacts.
- Before committing, inspect `git status --short` and avoid staging unrelated user changes accidentally.
- When you figure out a useful repeatable workflow, command pattern, data source, or repo-specific practice, document it here so future agents do not have to rediscover it.
- Treat `docs/live-trading-journal.md` as the live trading state and rationale tracker. Update it when live policy mix, sizing, entry caps, risk caps, execution behavior, or material trading lessons change.

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
