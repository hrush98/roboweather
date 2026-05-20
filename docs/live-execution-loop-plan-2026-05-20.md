# Live Execution Loop Plan - 2026-05-20

## Goal

Move Roboweather from research-only policy capture to a small live execution loop for selected weather policies. The live loop should reuse the proven SleeperService CLOB execution wrapper, wallet, key material, kill switch pattern, allowance checks, and order lifecycle handling, while keeping Roboweather's research collector running as the broad opportunity tape.

The live loop should not be a thin wrapper around the current paper policy promotion code. Paper policy trading is useful as an audit rehearsal, but it promotes persisted `research_policy_positions` rows after the fact. Real execution should price the current market, select a candidate from fresh model output, pass risk and dedupe checks, and submit through a live adapter immediately.

## High-Level Decision

Run live execution as a separate process from the research loop.

Reasons:

- Research should stay broad and durable. It can continue collecting every model/strategy snapshot without being constrained by production trading policy gates.
- Execution needs different failure semantics. A trading process needs kill switch checks, order cooldowns, allowance failures, explicit idempotency, and order reconciliation. Those should not risk wedging the research collector.
- Both processes can share the same SQLite database for markets, books, prediction snapshots, and audit state. SQLite is acceptable if each process keeps transactions short and the live process does not hold long write locks.
- The live process can be restarted or paused independently without interrupting the research evidence stream.

## Source Of Truth Boundaries

`prediction_snapshots`
: Broad research tape. The live process may write the same snapshot shape for audit, but it should not wait for the research process to materialize rows before trading.

`research_policy_positions`
: Research scorecard and replay ledger. Useful for choosing promoted policy rules, but not the live order queue.

New live tables
: Durable execution ledger for idempotency, order attempts, reconciliation, fills, marks, and settlement. These should be separate from `paper_policy_*` tables so real money state is unmistakable.

In-memory cycle state
: The live process should discover markets, fetch books, fetch weather, run active model(s), build policy candidates, select trades, and submit orders within one cycle.

## Components

### 1. Settings And Secrets

Add a Roboweather settings module modeled after `sleeperservice/services/shared/config.py`.

Required live settings:

- `polymarket_clob_url`, default `https://clob.polymarket.com`
- `polymarket_chain_id`, default `137`
- `polymarket_signature_type`, default `0`
- `polymarket_funder_address`, optional
- `polymarket_keyfile_path`, default should point to the existing SleeperService key path unless overridden
- `polymarket_token_decimals`, default `6`
- `live_kill_switch_path`, default should match the existing SleeperService stop file unless overridden
- `live_max_usd_per_order`
- `live_max_total_open_risk`
- `live_max_exposure_per_station_date`
- `live_min_seconds_between_orders`
- `live_require_allowance_check`
- `live_dry_run`, default `true`

The initial implementation can read plaintext env vars or the existing decrypted key helper if it can be copied cleanly. Do not commit key material or generated runtime state.

### 2. CLOB Executor Adapter

Copy or vendor the SleeperService `ClobExecutor` design into Roboweather under a narrow module such as `weather_trader/execution/clob_executor.py`.

Keep these proven behaviors:

- derive CLOB API creds from private key with `py-clob-client`;
- use the same funder, signature type, and chain id settings;
- check the kill switch before every submit;
- support `check_allowance_buy`;
- support `place_fak_order`, `place_fok_order`, `place_gtc_order`, `place_fok_batch`;
- support `get_order` and `cancel_order`;
- quantize prices by tick size and round USDC amounts down to cents;
- return typed result objects instead of throwing venue exceptions into strategy code.

Roboweather-specific changes:

- keep the public interface small and strategy-agnostic;
- do not import SleeperService settings directly;
- make `py-clob-client` an optional live dependency so tests and research can run without it.

### 3. Live Execution Store

Add live execution tables to `ExecutionStore`.

Minimum tables:

- `live_policy_positions`
- `live_order_attempts`
- `live_trade_events`
- `live_risk_snapshots`

Important columns:

- unique idempotency key: `policy_name`, `station`, `market_date`, `market_family`, `selected_side`, `selected_bucket`
- source snapshot ids or raw candidate payload
- token id, market id, side, entry fair, target price, submitted price, filled shares, average fill price
- external order id, venue status, raw response JSON
- lifecycle state: `RESERVED`, `SUBMITTED`, `FILLED`, `PARTIAL`, `REJECTED`, `DELAYED`, `UNKNOWN`, `CANCELLED`, `SETTLED`
- timestamps for reservation, submission, confirmation, finalization, marks, settlement

The live tables should not reuse `paper_policy_*` because real and simulated state need a hard boundary.

### 4. Candidate Builder

Build live candidates from fresh cycle data, not from a DB queue.

Cycle flow:

1. Discover same-day active Polymarket weather markets.
2. Fetch CLOB books for all yes/no token ids.
3. Fetch station weather state once per station.
4. Run promoted model artifacts through `FairValueEngine`.
5. Group by `(station, market_date, market_family)`.
6. Use the existing `StationDateDecisionEngine` and policy-selection mechanics to produce selected candidates.
7. Apply only allowlisted live policies.
8. Attach live execution metadata from `selected_side_execution_modes`.

For the first live version, use the same consensus policy family that research found strongest. Avoid expanding to every paper policy just because the paper ledger can represent it.

### 5. Risk And Idempotency

Risk checks should happen before order submission and again immediately before venue submit.

Initial hard gates:

- live mode must default to dry run unless explicitly passed `--mode live`;
- kill switch file blocks order submission;
- require configured promoted policies;
- require selected side to be BUY_NO for the first live rollout unless intentionally widened;
- require book age under the configured max;
- require post-fill edge above the configured floor;
- require min ask depth for the target stake;
- block duplicate station/date/family exposure unless explicitly allowed;
- cap per-order notional;
- cap station/date exposure;
- cap total open risk;
- enforce min seconds between submitted live orders;
- optionally require allowance check before order placement.

The unique live position key should be inserted before submit. If another process or previous cycle already reserved the same key, skip.

### 6. Submit Modes

Use one strategy path with swappable final submit behavior.

`dry_run`
: Build candidate, run risk, calculate order payload, write live audit rows, but do not call CLOB.

`paper_submit`
: Use the same live candidate/risk path, but fill through a simulated adapter. This replaces the old DB-promotion paper loop for launch rehearsal.

`live`
: Submit through `ClobExecutor`.

Initial live order type should be conservative. Use FAK for ask sweeps when liquidity is visible and the price cap preserves the required edge. Save passive bid ladder geometry for later unless there is a clear operational reason to post live GTC bids on day one.

### 7. Reconciliation

The live loop needs a reconciliation pass each cycle:

- inspect submitted or unknown attempts with `get_order`;
- update final state from venue status;
- record delayed or unknown responses as events;
- retry only when configured and only while the original intent is still fresh;
- cancel stale GTC orders if passive ladder support is enabled later;
- mark open positions from current bid and same-day weather state;
- settle resolved positions from `station_date_outcomes` when official outcomes are available.

For first live deployment, avoid complex exit management unless there is a specific weather-market reason to sell before settlement. The first position lifecycle can be entry plus mark plus weather settlement.

### 8. Process Shape

Add CLI commands:

- `live-cycle`: one cycle, useful for tests and cron/systemd probes.
- `live-loop`: repeated cycles with interval, max cycles, and mode.

Suggested defaults:

- `--mode dry-run`
- `--db /home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite`
- `--interval-seconds 60`
- `--max-book-age-seconds 10`
- `--max-obs-age-minutes 30`
- `--max-usd-per-order 5`

The research loop can keep running in its existing process. The live loop should use the same DB path by default but should not depend on the research loop being up.

## What To Retire Or De-Emphasize

- Do not use `paper_policy_positions` as a live queue.
- Do not build live execution around stale `research_policy_positions` promotion.
- Do not carry random simulated failure knobs into live execution.
- Do not mix real orders into paper tables.
- Do not launch passive bid ladders until immediate FAK/FOK behavior is stable and reconciled.

## Implementation Sequence

1. Add live settings and optional CLOB executor adapter copied from SleeperService.
2. Add live execution contracts and store tables.
3. Add dry-run live candidate loop using fresh market/book/weather/model state.
4. Add tests for idempotency, risk gates, dry-run audit rows, and kill-switch blocking.
5. Add paper-submit adapter on the same code path.
6. Add live submit adapter and tests with a fake `ClobExecutor`.
7. Run dry-run beside the research loop for at least one live session.
8. Enable tiny live order caps only after dry-run rows match the intended policy gates.

## Open Questions Before Live Orders

- Exact first promoted policy name or consensus rule to launch.
- Whether the first live policy should be BUY_NO only.
- Initial stake size and total daily risk cap.
- Whether to decrypt the existing SleeperService keyfile in-process or rely on an already-exported private key env var.
- Whether live execution tables should live in the current research SQLite DB or in a separate live SQLite DB attached to the same runtime directory.
