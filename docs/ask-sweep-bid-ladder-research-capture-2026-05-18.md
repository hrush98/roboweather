# Ask Sweep and Bid Ladder Research Capture

Date: 2026-05-18

## Summary

The research loop now records two separate hypothetical execution modes for each selected signal:

- **Ask sweep**: immediate liquidity that could be bought from the existing ask book within a price/edge cap.
- **Bid ladder**: passive post-only bids that would be placed into the book while preserving a minimum modeled edge.

This is research-only metadata. It does not place orders and does not attempt to simulate passive fills.

## Why This Exists

Weather markets can be thin. The old liquidity fields answered only "how much is available on the ask side right now?" That is useful for immediate entry, but it does not describe the larger sizing approach we would use live: take a small starter immediately, then post a short-lived ladder of bids to accumulate passively.

The new capture separates those two execution questions:

```text
Ask sweep = what can I buy immediately from existing asks?
Bid ladder = what bids would I post into the book?
```

Passive ladder rows intentionally do not say whether the orders would have filled. That has to be measured in a live execution engine or with future order/trade-level data.

## Defaults Captured

The defaults live in `weather_trader/execution/liquidity.py`.

```text
signal_min_edge = 0.25
post_fill_min_edge = 0.15
sweep_max_slippage = 0.05
sweep_targets_usd = 25, 50, 100
bid_ladder_order_notional_usd = 50
bid_ladder_total_notional_usd = 500
bid_ladder_step_cents = 0.01
bid_ladder_range_cents = 0.10
bid_ladder_ttl_seconds = 180
```

Ask sweep cap:

```text
sweep_price_cap = min(best_ask + 0.05, fair - 0.15)
```

Bid ladder top price:

```text
edge_max_bid = fair - 0.15
post_only_top_bid = min(edge_max_bid, best_ask - 0.01)
```

## Data Written

The research collector writes full JSON plus queryable scalar columns on `prediction_snapshots`. Research policy materialization carries the same metadata into `research_policy_positions`.

Important JSON columns:

```text
selected_ask_sweep_json
selected_bid_ladder_json
```

Important ask sweep scalar columns:

```text
selected_sweep_price_cap
selected_sweep_depth_to_cap
selected_sweep_fillable_25_usd
selected_sweep_fillable_50_usd
selected_sweep_fillable_100_usd
selected_sweep_vwap_25
selected_sweep_vwap_50
selected_sweep_vwap_100
```

Important bid ladder scalar columns:

```text
selected_bid_ladder_top_price
selected_bid_ladder_low_price
selected_bid_ladder_levels
selected_bid_ladder_total_notional_usd
selected_bid_ladder_top_distance_from_ask
selected_bid_ladder_top_improvement_over_best_bid
selected_bid_ladder_min_edge
selected_bid_ladder_max_edge
```

The JSON objects also include eligibility and reason fields. Common ineligible reasons are:

```text
SKIP
MISSING_BOOK
MISSING_ASK
MISSING_FAIR
EDGE_BELOW_SIGNAL_GATE
NO_POST_ONLY_PRICE
```

## Useful Analysis Queries

Use the active local DB unless intentionally analyzing a committed paper DB:

```bash
DB=/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Check whether new columns are present:

```bash
sqlite3 "$DB" "pragma table_info(prediction_snapshots);"
sqlite3 "$DB" "pragma table_info(research_policy_positions);"
```

Summarize ask sweep availability by policy:

```sql
select
  policy_name,
  count(*) rows,
  avg(selected_sweep_depth_to_cap) avg_depth_to_cap,
  avg(selected_sweep_fillable_25_usd >= 25.0) fillable_25_rate,
  avg(selected_sweep_fillable_50_usd >= 50.0) fillable_50_rate,
  avg(selected_sweep_fillable_100_usd >= 100.0) fillable_100_rate,
  avg(selected_sweep_vwap_50) avg_vwap_50
from research_policy_positions
where selected_ask_sweep_json is not null
group by policy_name
order by rows desc;
```

Summarize bid ladder geometry by policy:

```sql
select
  policy_name,
  count(*) rows,
  avg(selected_bid_ladder_top_price) avg_top_bid,
  avg(selected_bid_ladder_total_notional_usd) avg_reserved_notional,
  avg(selected_bid_ladder_top_distance_from_ask) avg_top_distance_from_ask,
  avg(selected_bid_ladder_top_improvement_over_best_bid) avg_top_bid_improvement,
  avg(selected_bid_ladder_min_edge) avg_min_preserved_edge
from research_policy_positions
where selected_bid_ladder_json is not null
group by policy_name
order by rows desc;
```

Inspect ineligible execution-mode reasons:

```sql
select
  json_extract(selected_ask_sweep_json, '$.reason') reason,
  count(*) rows
from prediction_snapshots
where selected_ask_sweep_json is not null
  and json_extract(selected_ask_sweep_json, '$.eligible') = 0
group by reason
order by rows desc;
```

Inspect the current best generalist consensus policy slice:

```sql
select
  station,
  count(*) rows,
  avg(selected_sweep_fillable_50_usd >= 50.0) sweep_50_rate,
  avg(selected_bid_ladder_total_notional_usd) avg_bid_ladder_notional,
  avg(selected_bid_ladder_min_edge) avg_min_ladder_edge
from research_policy_positions
where policy_name = 'pm_us12_consensus_hc_by_bucket_side_delay_first'
  and selected_side = 'BUY_NO'
  and entry_edge >= 0.25
  and entry_price >= 0.10
group by station
order by rows desc;
```

## Implementation Notes

- Builder functions are in `weather_trader/execution/liquidity.py`.
- Snapshot wiring is in `weather_trader/research/collector.py`.
- Policy propagation is in `weather_trader/research/policies.py`.
- SQLite schema and inserts are in `weather_trader/execution/store.py`.
- Tests cover the builder, snapshot persistence, and policy propagation.

Verification run after implementation:

```bash
pytest
```

Result:

```text
129 passed
```
