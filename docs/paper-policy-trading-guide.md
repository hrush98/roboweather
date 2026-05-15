# Paper Policy Trading Guide

Date added: 2026-05-15

## What Changed

RoboWeather now has two separate live loops:

- `research-loop`: continues to collect markets, books, weather snapshots, model predictions, research policy positions, and resolved outcomes.
- `paper-policy-loop`: promotes only allowlisted research policy positions into a separate paper execution ledger.

Restarting the existing `research-loop` does not start paper execution and does not change data collection behavior. The paper layer only runs when explicitly invoked with `paper-policy-cycle`, `paper-policy-loop`, or `scripts/run_policy_paper.sh`.

The research ledger remains the source of all policy hypotheses. Paper trading records link back to `research_policy_positions.id` and are stored in separate tables. Each promoted policy is treated as its own paper book for sizing and duplicate-exposure control; the default three policies are not traded as one aggregate portfolio.

## Default Promoted Policies

The default paper allowlist is intentionally narrow:

- `pm_us12_consensus_hc_15m_entry_50_75_first`
- `pm_us12_consensus_hc_late_entry_50_75_first`
- `pm_us12_consensus_hc_15m_entry_25_75_first`

These are defaults, not hardcoded trading logic. Override them with repeated `--promoted-policy` flags:

```bash
python -m weather_trader.cli paper-policy-cycle \
  --promoted-policy pm_us12_consensus_hc_15m_entry_50_75_first \
  --promoted-policy pm_us12_consensus_hc_late_entry_50_75_first
```

## Running It

The default database for paper policy trading is the live research database:

```text
/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite
```

Run one cycle:

```bash
python -m weather_trader.cli paper-policy-cycle
```

Run continuously:

```bash
python -m weather_trader.cli paper-policy-loop --interval-seconds 360
```

Or use the wrapper:

```bash
scripts/run_policy_paper.sh
```

To point at another database:

```bash
DB=/path/to/research.sqlite scripts/run_policy_paper.sh
```

## Data Model

Research remains unchanged:

- `prediction_snapshots`
- `research_policy_positions`
- `station_date_outcomes`
- `prediction_results`

Paper execution adds:

- `paper_policy_positions`: one durable paper position per promoted `research_policy_positions.id`.
- `paper_policy_order_attempts`: one row per simulated submit attempt, including lifecycle state and raw payload.
- `paper_policy_trade_events`: append-only event tape.
- `paper_policy_risk_snapshots`: portfolio exposure snapshots after each cycle.

The key join is:

```text
paper_policy_positions.research_policy_position_id -> research_policy_positions.id
```

This keeps the research policy backtest ledger separate from the paper execution ledger.

## Execution Design Philosophy

The paper layer is deliberately pessimistic. It is designed to answer: "What might have happened if this research policy had been submitted through a real CLOB-style execution path?"

The design follows these principles:

- Research signals are not fills. A policy entry price in `research_policy_positions` is a signal-time ask, not an execution guarantee.
- Every promoted position is reserved durably before a submit attempt. This prevents duplicate open exposure if a process restarts mid-cycle.
- Every submit gets an attempt row. Attempts carry `attempt_seq`, `external_status`, `not_found_count`, `final_state`, `final_reason`, consumed book levels, and raw payload.
- Every meaningful lifecycle transition is appended to the event tape.
- Live orderbooks are refetched immediately before simulated submit.
- Stale or missing books are rejected instead of silently using old data.
- Fills walk the ask ladder up to the limit price. The simulator records each consumed level.
- FOK is the default. FAK-compatible partial fill simulation exists, but default paper behavior is stricter.
- Unknown, delayed, stale, and phantom-style failures are first-class states, not exceptions hidden in logs.
- Settlement is based on resolved station highs, not intraday marks.

This is closer to a paper execution harness than a backtest. The goal is auditability under live-market uncertainty, not optimistic replay.

## Order States

Paper attempts can end in:

- `FILLED`: target shares fully filled.
- `PARTIAL`: partial fill under FAK-compatible simulation.
- `REJECTED`: generic rejection, including missing book or insufficient depth.
- `DELAYED`: simulated delayed exchange/client response.
- `UNKNOWN`: simulated unknown order ID or phantom attempt.
- `STALE_BOOK`: book was too old or failed stale-book adversity.
- `FOK_NOT_FILLED`: FOK order could not fully fill at the limit.

Positions can also become:

- `RESERVED`: durable reservation created before submit.
- `SUBMITTED`: reserved and in submit flow.
- `SETTLED`: final station outcome has been applied.

## Event Tape

Events are append-only and intended for audit/debug review:

- `ENTRY_RESERVED`
- `ENTRY_SUBMIT`
- `ENTRY_CONFIRMED`
- `ENTRY_REJECTED`
- `ENTRY_RETRY`
- `MARK`
- `RESOLVED`

The event tape should be treated as the operational narrative. Position rows summarize current state; attempt rows summarize submit outcomes; event rows explain what happened and when.

## Sizing And Risk

Sizing v1 uses fixed fractional bankroll sizing with caps:

- `--bankroll`
- `--fixed-fraction`
- `--max-usd-per-order`
- `--max-exposure-per-station-date`
- `--max-total-open-risk`

Risk caps are applied per `policy_name`, so one promoted policy does not consume another promoted policy's paper bankroll or station/date cap. The aggregate risk snapshot still records total exposure across paper policy positions for monitoring, and its raw payload includes a per-policy breakdown.

By default, duplicate exposure is blocked inside the same policy book for the same:

```text
station / market_date / selected_bucket / selected_side
```

Allow same-policy duplicate bucket/side exposure explicitly:

```bash
python -m weather_trader.cli paper-policy-cycle --allow-duplicate-bucket-side
```

The code also defines a `SizingModel` interface for a later Kelly-style sizing model. The intended v2 inputs are fair probability, entry price, calibration haircut, liquidity cap, station confidence, and policy confidence. V1 keeps this simple and capped.

## Adversity Profiles

Use `--adversity-profile` to make paper execution less idealized:

```bash
python -m weather_trader.cli paper-policy-cycle --adversity-profile mild
```

Profiles:

- `off`: deterministic default, no injected failure.
- `mild`: small probabilities of FOK miss, stale book, delayed response, and unknown order.
- `stress`: higher failure probabilities and FAK partial-fill pressure.

These knobs are not market forecasts. They are operational stress tests for the paper ledger and retry/reconciliation behavior.

## Marking And Settlement

Open filled positions are marked intraday to current bid when a fresh book is available. Missing mark books produce `MARK` events but do not settle the position.

Settlement uses `station_date_outcomes`:

- If the selected side wins, payout is one dollar per filled share.
- If it loses, payout is zero.
- Realized PnL is payout minus cost.
- Realized R/R is realized PnL divided by cost.

This means intraday marks are diagnostic, while resolved station highs are authoritative.

## Operational Pattern

A typical morning/evening setup is:

1. Run or restart `research-loop` to collect all policy data.
2. Run `paper-policy-loop` separately when ready to promote the allowlisted policies.
3. Let resolver populate `station_date_outcomes`.
4. Paper loop marks open positions and settles resolved ones on later cycles.

The important separation is:

```text
research-loop creates hypotheses
paper-policy-loop simulates executable orders for selected hypotheses
resolver creates authoritative outcomes
```

## Quick Inspection Queries

Recent attempts:

```sql
select id, timestamp, research_policy_position_id, final_state, final_reason, cost_usd
from paper_policy_order_attempts
order by id desc
limit 20;
```

Open paper positions:

```sql
select id, policy_name, station, market_date, selected_bucket, selected_side, state, cost_usd, unrealized_pnl
from paper_policy_positions
where state in ('FILLED', 'PARTIAL', 'DELAYED', 'UNKNOWN', 'RESERVED', 'SUBMITTED')
order by id desc;
```

Event tape:

```sql
select id, timestamp, paper_position_id, event_type, message
from paper_policy_trade_events
order by id desc
limit 50;
```

Policy attribution after settlement:

```sql
select policy_name, count(*) positions, sum(cost_usd) risk, sum(realized_pnl) pnl
from paper_policy_positions
where state = 'SETTLED'
group by policy_name
order by pnl desc;
```
