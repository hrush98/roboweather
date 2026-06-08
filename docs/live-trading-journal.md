# Live Trading Journal

This is the operating journal for live RoboWeather trading. Use it to record what is live, why it is live, and what should be revisited as more fills settle.

Git history remains the source of truth for code changes. This journal is the source of truth for trading rationale and current operating assumptions.

## Current Live State

Updated: 2026-06-08

### Active policies

| Policy | Side | Target notional | Entry cap | Notes |
| --- | --- | ---: | ---: | --- |
| Consensus HC late | mixed | $50 BUY_NO; $25 BUY_YES | <= $0.50 | Primary live strategy. Uses the consensus high-conviction late-window policy. |
| Consensus 15m late core | mixed | $50 BUY_NO; $25 BUY_YES | <= $0.50 | Replaces weak single-model dynamic core with bucket-consensus high-conviction 15m late overlay. |
| NGBoost BUY_YES | BUY_YES | $10 | <= $0.50 | Dedicated BUY_YES allocation. Keep smaller than core NO until live evidence improves. |
| Moonshot | BUY_NO | $2 | <= $0.50 | Small tail allocation. Original tiny moonshot remains constrained by its tighter policy price rules. |
| Global low-temp canary | BUY_NO | $25 | <= $0.75 | Live canary only for EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD low-temperature markets, station-local 00:30-05:00. Not core sizing. |

### Risk caps

| Cap | Current value |
| --- | ---: |
| Max order | $50 |
| Station/date | $125 |
| Station/date/side | $85 |
| Exact bucket/side | $50 |
| Total open risk | $450 |
| Daily new risk | $300 |

### Execution rules

- US high-temperature live entries are capped at `<= 0.50` because historical replay showed materially better return on risk below this price. The global low-temp canary is BUY_NO-only with a separate `<= 0.75` cap and station-local `00:30-05:00` decision window.
- Orders use FAK first, with retry handling for transient depth/order-version failures. Explicit partial fills continue into a 120-second resting remainder for the leftover notional. Matched/filled responses with returned fill amounts are treated as partial only when the unfilled remainder exceeds $3.
- Any live strategy may place a single resting fallback limit order after eligible FAK failure paths.
- Resting fallback TTL is 120 seconds and targets the remaining notional after the FAK retry path; keep whatever fills before the cancel.
- The resting fallback is intentionally narrow: it is for improving fill odds without adding a broad passive market-making system.
- Live settlement in the live DB updates only when the Polymarket live resolver runs. Polymarket UI may show resolution before `live_policy_positions` is marked `SETTLED`.

### Known operator caveats

- The TUI config page still shows legacy base notional from bankroll and fixed fraction. Actual live policy sizing now comes from fixed per-policy targets.
- Research policy scoring uses official weather outcomes from IEM ASOS. Live PnL settlement uses Polymarket resolution.
- Same-day weather snapshots are useful for preliminary reads, but official Polymarket resolution is what determines live settlement.
- Polymarket's portfolio P/L is account-level. The live SQLite ledger tracks the bot's positions, fills, and settlements, so any mismatch means the bot ledger is missing some mark, settlement, or timing state relative to the exchange view.

## Rationale

### Entry cap at 50 cents

Historical replay of the current live strategy family showed that entries above $0.50 were much less efficient than cheaper entries. They were positive historically, but contributed far less return per dollar of risk than capped entries.

The working assumption is that higher-priced bucket NO entries often have less attractive convexity: downside remains near full loss, while upside is compressed. The cap reduces volume, but improves risk efficiency.

### Current sizing

The system moved from small exploratory sizing to policy-specific fixed sizing after the capped replay looked stronger:

- Consensus HC: $50 for BUY_NO and $25 for BUY_YES because consensus BUY_YES remains weaker historically.
- Consensus 15m late core: $50 BUY_NO / $25 BUY_YES, replacing Core capped dynamic BUY_NO after raw-snapshot replay showed dynamic core was barely positive and dominated by consensus overlays.
- NGBoost BUY_YES: $10 because it is the dedicated BUY_YES strategy but remains smaller than core NO sizing.
- Moonshot: $2 because tail entries are high variance and should not drive daily risk.
- Global low-temp canary: $25 BUY_NO-only with entry cap `<= 0.75` and station-local `00:30-05:00`, using the existing live FAK, retry, and 120-second resting fallback engine with no added depth gate. Promotion toward $50 depends on live fills and resolved PnL.

The max order cap is set to $50 so the largest intended order cannot exceed the current primary-policy size.


### Potential low-temp expansion shortlist

Deep raw-snapshot replay on 2026-06-08 showed low-temperature markets as the strongest non-live expansion candidate so far. The broad global low BUY_NO overlay is now a $25 live canary; the remaining low-temp variants stay research-only until they pass de-duplicated replay, liquidity checks, and live-market availability review.

Candidate shortlist:

- `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`: promoted to live canary at $25, BUY_NO-only, `<= $0.75`, station-local `00:30-05:00`, using existing execution and risk controls. Earlier all-day replay showed 36 resolved, 91.7% win rate, about +11.44 PnL, R/R about 0.53, Sharpe about 0.99 across 6 stations from 2026-05-30 through 2026-06-05. A later raw-snapshot window replay favored `00:30-05:00` with 36 resolved, 36-0, about +15.19 PnL, R/R about 0.73.
- `global_low_dynamic_mvp_hc_buy_no_10m_entry_50_75_by_bucket_side_delay_first`: more constrained high-conviction BUY_NO candidate; 11 resolved, 100% win rate, about +4.25 PnL, R/R about 0.63, Sharpe about 5.22. Needs more sample and execution validation.
- `global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first`: strongest tail niche; 24 resolved, 100% win rate, about +23.33 PnL, very high R/R due to sub-5-cent entries. This is a tiny-entry tail allocation candidate, not a core sizing candidate.
- `low_pm_us12_consensus_hc_buy_no_entry_50_75_by_bucket_side_delay_first`: US low-temp consensus BUY_NO candidate; 23 resolved, 100% win rate, about +8.78 PnL, R/R about 0.62. Current replay flags execution/liquidity weakness, so do not promote without book-depth confirmation.

Working interpretation: low-temp BUY_NO overlays appear to be finding overpriced YES buckets. Global high-temp consensus did not transfer cleanly; keep global high research-only while prioritizing low-temp replay.

### Resting fallback

FAK retries address temporary book/depth/order-version issues. When those still fail for an eligible live strategy, a short-lived passive order can capture fills inside or near the intended risk price without leaving stale exposure in the market.

The 120-second TTL is a deliberate compromise: weather does not normally reprice enough in two minutes to invalidate the original edge, but the order should not remain open after the cycle context has aged.

## Journal

### 2026-06-08

- Deep raw-snapshot niche replay across US high, global high, global low, and US low identified low-temperature BUY_NO overlays as the strongest expansion candidates so far. The leading broad candidate, `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`, is now live as a $25 BUY_NO-only canary with `<= $0.75` entry cap on EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD low-temp markets. It uses the existing FAK, retry, and resting fallback path with no added depth gate; promotion toward $50 depends on live fills and resolved PnL.
- Global high-temperature consensus did not transfer cleanly from US high temp; global high remains research-only.
- Replaced live Core capped dynamic-tuned BUY_NO policy with `pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first` in the core notional slot. Raw snapshot replay through 2026-06-06 showed the old dynamic core at roughly R/R 0.03 versus the replacement at roughly R/R 1.32 over 20 resolved rows, while staying inside the consensus high-conviction, late-window, <= $0.50 entry family.
- Current core sizing remains $50 target notional, with consensus BUY_YES halved by sizing policy. Keep HRRR-rich consensus research-only until it reaches a larger resolved sample. A targeted replay of the global low canary local decision time favored station-local `00:30-05:00`: 46 selected rows, 36 resolved, 36-0, about +15.19 PnL, R/R about 0.73, while `00:00-05:00` included two ZSPD local-midnight losses.

### 2026-06-03

- Research loop default model set now includes US high-temperature HRRR v2 equivalents as extra models for `MARKET_SCOPE=us` and `MARKET_SCOPE=all`: dynamic bucket, tuned dynamic bucket, CatBoost bucket, MVP, high regression, and NGBoost. This is research collection only; live execution remains on the existing obs-family strategy stack until HRRR research-policy replay is reviewed.
- Observability lesson: active research/live model names must be surfaced in status/TUI so trained-but-unloaded model families are visible before sizing decisions.

### 2026-06-02

- Live fixed targets moved to Consensus HC $50 BUY_NO / $25 BUY_YES and Core capped $50. NGBoost BUY_YES remains $10 and Moonshot remains $2.
- Risk caps moved to max order $50, station/date $125, station/date/side $85, exact bucket/side $50, total open risk $450, daily new risk $300.
- Rationale: keep the tighter `<= 0.50` eligibility box for R/R and risk efficiency, then size up inside the lower-frequency trade set rather than loosening into weaker high-price rows.

### 2026-06-01

- TUI-launched live loop default polling cadence changed from 360 seconds to 90 seconds when `INTERVAL_SECONDS` is unset.
- Live loop now writes a separate JSONL debug log for candidate-funnel diagnostics without adding detail to TUI stdout.
- Live accounting now treats `live_order_attempts` as the source of truth for filled shares/cost after FAK partials; June 1 KATL BUY_YES rows were reconciled from $24.50 / 120.535896 shares to $12.29 / 68.414614 shares.

### 2026-05-29

- Research loop default market scope is now `all`, so a normal restart captures both US and global markets.
- Live sizing moved to Consensus HC $40 BUY_NO / $20 BUY_YES, Core capped $35, NGBoost BUY_YES $10, and Moonshot $2.
- Current risk caps are max order $40, station/date $100, station/date/side $70, exact bucket/side $40, total open risk $450, daily new risk $300.
- Resting fallback now applies to all live strategies after eligible retry/partial paths, not only Consensus HC.
- Exchange `matched`/`filled` responses with returned fill amounts now respect the returned notional: unfilled remainder `<= $3` is treated as filled dust; larger remainders stay partial and can continue into retry/resting fallback.

### 2026-05-28

- Added this journal as the live trading state and rationale tracker.
- Current live strategy stack:
  - Consensus HC at $30.
  - Core capped at $25.
  - NGBoost BUY_YES at $7.50.
  - Moonshot at $2.
- Current live entry cap is `<= 0.50`.
- Current risk caps are max order $30, station/date $75, station/date/side $55, exact bucket/side $30, total open risk $450, daily new risk $300.
- Operator note: May 27 looked rough intraday, but Polymarket UI later showed Seattle, Los Angeles, and the smaller New York NO positions resolving favorably while several other positions lost. The live DB still requires the resolver to mark final settled PnL.
- Live candidate generation now builds every strategy bucket required by the active policy stack, including `BEST_BUCKET` for NGBoost BUY_YES, instead of only `HIGH_CONVICTION`.

## Update Protocol

Update this journal when any of these change:

- live policy set
- policy sizing
- entry caps or filters
- station or side restrictions
- execution behavior
- risk caps
- live-vs-research interpretation
- material lessons from resolved live trading days

Keep entries short and factual. Link to deeper reports when the reasoning depends on a longer analysis.
