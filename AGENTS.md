# Repository Agent Instructions

- After a large or multi-file change set, break the work into coherent commits and push the branch when verification passes.
- Keep generated runtime state out of commits unless explicitly requested. This includes live SQLite databases, logs, and bulky ad hoc research artifacts.
- Before committing, inspect `git status --short` and avoid staging unrelated user changes accidentally.
- When you figure out a useful repeatable workflow, command pattern, data source, or repo-specific practice, document it here so future agents do not have to rediscover it.

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
