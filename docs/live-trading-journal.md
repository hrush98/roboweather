# Live Trading Journal

This is the operating journal for live RoboWeather trading. Use it to record what is live, why it is live, and what should be revisited as more fills settle.

Git history remains the source of truth for code changes. This journal is the source of truth for trading rationale and current operating assumptions.

## Current Live State

Updated: 2026-06-12

### Active policies

| Policy | Side | Target notional | Entry cap | Notes |
| --- | --- | ---: | ---: | --- |
| Consensus no-tiny | mixed | $100 BUY_NO; $50 BUY_YES | <= $0.50 | Canonical promoted US high-temp core. Selected by the raw-snapshot promotion report and mapped to `pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first`. |
| Moonshot | BUY_NO | $2 | <= $0.50 | Small US high-temperature tail allocation. Original tiny moonshot remains constrained by its tighter policy price rules. |
| Global low-temp canary | BUY_NO | $100 | $0.05-$0.75 | Primary global low-temperature BUY_NO consensus canary for EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD, station-local 00:30-05:00. |
| Global low-temp MVP add-on | BUY_NO | $50 | $0.05-$0.50 | Additive single-model MVP sleeve, mapped to `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first`; runs after the consensus canary and uses live caps/depth sizing. |
| Global low-temp tiny tail | BUY_NO | $5 | <= $0.05 | Asymmetric global low-temperature tail sleeve on the same station/window set, mapped to `global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first`. |

### Risk caps

| Cap | Current value |
| --- | ---: |
| Max order | $100 |
| Station/date | $300 |
| Station/date/side | $200 |
| Exact bucket/side | $100 |
| Total open risk | $1,125 |
| Daily new risk | $750 |

### Execution rules

- US high-temperature live entries are capped at `<= 0.50` because historical replay showed materially better return on risk below this price. The global low-temp canary is BUY_NO-only with a `0.05-0.75` entry band and station-local `00:30-05:00` decision window; the global low tiny-tail sleeve covers `<= 0.05`.
- Live execution is entry-anchored: the scored `entry_price` is the execution contract, and `selected_sweep_price_cap` is retained only as research/liquidity diagnostics.
- Initial FAK uses `entry_price + $0.01` only. Retry FAK uses the same entry-anchored cap and is skipped when the refreshed ask is above that cap; raw model fair no longer permits chasing up to `best_ask + $0.05`.
- Any live strategy may place a resting fallback ladder after eligible FAK failure paths. Candidates blocked only by insufficient `ask + $0.01` depth skip FAK and route directly into the same ladder while keeping risk/exposure caps intact.
- Resting fallback TTL is 420 seconds. The ladder allocates remaining notional across deterministic bands `entry + $0.01`, `entry`, `entry - $0.01`, and `entry - $0.02` with weights `30% / 40% / 20% / 10%`, skipping children below the live minimum order size.
- The `entry + $0.01` resting child is normal GTC because immediate fills at that price are acceptable; `entry` and lower children are exchange-enforced post-only GTC where the CLOB client supports it.
- The resting fallback is intentionally narrow: it is for improving fill odds at replay-compatible prices without adding a broad passive market-making system.
- Live settlement in the live DB updates only when the Polymarket live resolver runs. Polymarket UI may show resolution before `live_policy_positions` is marked `SETTLED`.

### Known operator caveats

- The TUI strategy and performance tables are the primary place to inspect per-policy target caps, entry bands, market family, and recent/live/historical performance. The Config tab is intentionally limited to operational execution, sizing, and risk caps.
- Research policy scoring uses official weather outcomes from IEM ASOS. Live PnL settlement uses Polymarket resolution.
- Same-day weather snapshots are useful for preliminary reads, but official Polymarket resolution is what determines live settlement.
- Polymarket's portfolio P/L is account-level. The live SQLite ledger tracks the bot's positions, fills, and settlements, so any mismatch means the bot ledger is missing some mark, settlement, or timing state relative to the exchange view.

## Rationale
### 2026-06-15 entry-anchored execution upgrade

Live execution now treats replay EV as an entry-price claim, not a broad directional permission to chase. The old FAK/retry path could inherit `selected_sweep_price_cap` and submit several cents above the scored entry, which made live fills a different trade than replay. The new contract caps immediate FAK/retry at `entry + 1c`, then rests a 7-minute ladder at `entry+1c`, `entry`, `entry-1c`, and `entry-2c`, with entry-and-lower children post-only.

The operating lesson is that missed fills are acceptable evidence, while adverse fills above the replay-compatible entry contaminate policy evaluation. Live reviews should compare replay at scored entry, theoretical PnL at actual average fill, and final settlement PnL before promoting or sizing policies.


### Entry cap at 50 cents

Historical replay of the current live strategy family showed that entries above $0.50 were much less efficient than cheaper entries. They were positive historically, but contributed far less return per dollar of risk than capped entries.

The working assumption is that higher-priced bucket NO entries often have less attractive convexity: downside remains near full loss, while upside is compressed. The cap reduces volume, but improves risk efficiency.

### Promotion strategy

Policy promotion now runs through `scripts/live_policy_promotion_report.py`. It replays from raw `prediction_snapshots`, reconstructs the live opportunity scope, scores against resolved outcomes, and emits `PROMOTE`, `CANARY`, `DEACTIVATE`, or `RESEARCH_ONLY`.

This replaces any workflow that relies only on materialized `research_policy_positions`. New live policy changes should be checked against the promotion report first, then mapped to the live registry names in this file.

### Current sizing

The system uses fixed per-policy targets selected by the raw-snapshot promotion gatekeeper:

- Consensus no-tiny: $100 BUY_NO and $50 BUY_YES for the canonical US high-temp live core.
- Moonshot: $2 because US high-temperature tail entries are high variance and should not drive daily risk.
- Global low-temp canary: $100 BUY_NO-only with entry band `0.05-0.75` and station-local `00:30-05:00`, using the existing live FAK, retry, and 360-second resting fallback ladder with no added depth gate.
- Global low-temp MVP add-on: $50 BUY_NO-only with entry band `0.05-0.50`; cap-aware portfolio replay showed the move from $25 to $50 remained positive after the current stack, caps, and recorded depth, but it still competes for the same station/date/bucket/side caps.
- Global low-temp tiny tail: $5 BUY_NO-only with entry cap `<= 0.05` and the same station-local `00:30-05:00` window.

The max order cap is set to $100 so the largest intended order can fit the current primary-policy size. Exact bucket/side is also $100, station/date/side is $200, station/date is $300, daily new risk is $750, and total open risk is $1,125.


### Potential low-temp expansion shortlist

Deep raw-snapshot replay showed low-temperature markets as the strongest expansion candidate so far. The broad global low BUY_NO overlay is now a $100 live canary for the `0.05-0.75` band, and the tiny-tail BUY_NO slice is live at $5 for `<= 0.05`. Other low-temp variants stay research-only until they pass de-duplicated replay, liquidity checks, and live-market availability review.

Candidate shortlist:

- `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`: promoted to live canary and now sized at $100, BUY_NO-only, `$0.05-$0.75`, station-local `00:30-05:00`, using existing execution and risk controls. Earlier all-day replay showed 36 resolved, 91.7% win rate, about +11.44 PnL, R/R about 0.53, Sharpe about 0.99 across 6 stations from 2026-05-30 through 2026-06-05. A later raw-snapshot window replay favored `00:30-05:00` with 36 resolved, 36-0, about +15.19 PnL, R/R about 0.73.
- `global_low_dynamic_mvp_hc_buy_no_10m_entry_50_75_by_bucket_side_delay_first`: more constrained high-conviction BUY_NO candidate; 11 resolved, 100% win rate, about +4.25 PnL, R/R about 0.63, Sharpe about 5.22. Needs more sample and execution validation.
- `global_low_dynamic_mvp_tail_buy_no_entry_00_05_by_bucket_side_delay_first`: strongest tail niche; 24 resolved, 100% win rate, about +23.33 PnL, very high R/R due to sub-5-cent entries. Now live as a $5 tiny-entry tail allocation, not a core sizing candidate.
- `low_pm_us12_consensus_hc_buy_no_entry_50_75_by_bucket_side_delay_first`: US low-temp consensus BUY_NO candidate; 23 resolved, 100% win rate, about +8.78 PnL, R/R about 0.62. Current replay flags execution/liquidity weakness, so do not promote without book-depth confirmation.

Working interpretation: low-temp BUY_NO overlays appear to be finding overpriced YES buckets. Global high-temp consensus did not transfer cleanly; keep global high research-only while prioritizing low-temp replay.

### Resting fallback

FAK retries address temporary book/depth/order-version issues. When those still fail for an eligible live strategy, a short-lived passive order can capture fills inside or near the intended risk price without leaving stale exposure in the market.

The 360-second TTL is a deliberate compromise: weather does not normally reprice enough in six minutes to invalidate the original edge, but the order should not remain open after the cycle context has aged. The fallback now ladders the leftover notional into $25 chunks, stepped down by one cent per child order, so a $60 remainder posts as $25, $25, and $10 rather than one large passive order.

## Journal

### 2026-06-15

- Implemented the Layer 1 calibration gate from `docs/calibration-layer-1-design-2026-06-15.md` as an opt-in live execution guard. When `--calibration-path` is configured, mapped live policies are checked by model family, station, side, and entry band before sizing: `BLOCK` rejects with `CALIBRATION_BLOCK`, `CANARY` caps target notional to the configured calibration canary size, and `WATCH`/`TRADE`/missing buckets pass through with metadata. This is a defensive filter only, not evidence for size-ups.
- Live/replay audit lesson: positive raw-snapshot replay is not sufficient evidence of tradable edge unless it can be reconciled to the exact live candidate and fill path. The live DB stores selected/reserved live positions and raw candidate payloads in `live_policy_positions`, but the live loop currently leaves `prediction_snapshots` empty; live execution builds snapshots in memory with temporary IDs and does not persist the full candidate universe the way the research collector does. This means current reports can compare actual fills to selected/reserved live candidates, but cannot yet audit every unselected candidate the live scanner considered.
- Current selected-live-candidate audit showed the core failure mode clearly: actual filled rows were negative while selected-candidate replay was positive. On persisted selected/reserved rows, filled candidates replayed worse than unfilled reserved candidates, consistent with adverse fill selection. Actual average fill prices also materially exceeded recorded entry prices in several policies; submitted live limit prices were often much wider than the intended one-or-two-cent tolerance, so replay at `entry_price` can overstate executable edge.
- Directional model confidence is not enough for bucket trading. Bucket `BUY_NO` can be directionally plausible and still lose if the final temperature lands inside the exact selected bucket, and live filled fair bands showed bad calibration in the traded subset. Treat raw model fair as uncalibrated until live/resolved fair-band diagnostics support it.
- Global low-temperature settlement needs extra scrutiny. Several live global-low rows had official weather-outcome scoring that implied `BUY_NO` should win, while live settlement recorded `BUY_YES`. Do not use global-low replay for promotion or size-up until resolver-vs-Polymarket-settlement semantics are reconciled.
- New promotion standard: before any policy size-up or new live sleeve, require positive actual filled R/R when available, positive replay on persisted live-selected candidates, filled-subset R/R not materially worse than unfilled/reserved selected candidates, no inverted fair-band calibration, and no unresolved settlement-source mismatch. Raw-snapshot portfolio replay remains useful for hypothesis generation, but not sufficient for live sizing without this live-candidate/fill audit.
- Engineering follow-up: persist live prediction snapshots or a dedicated live candidate snapshot table before policy filtering, with stable candidate IDs linked from `live_policy_positions`. Also add a hard execution guard or alert when submitted limit price exceeds recorded entry by more than the intended tolerance.

### 2026-06-12

- Raised the global low-temperature MVP add-on from $25 to $50 after the new cap-aware portfolio replay showed the size-up stayed positive behind the current stack with recorded ask-sweep depth. All-loaded replay moved the MVP sleeve from about $672.85 risk / +$1,645.78 PnL / 2.45 R/R to about $884.32 risk / +$2,111.15 PnL / 2.39 R/R; recent replay from 2026-06-04 moved it from about $366.35 risk / +$514.91 PnL / 1.41 R/R to about $445.23 risk / +$667.65 PnL / 1.50 R/R. This is a controlled size-up, not a broad cap increase; US consensus no-tiny remains unchanged until post-fix live settlement confirms replay quality.
- HRRR-rich dynamic-tuned inland late-day disagreement replay looks promising as a future additive US high-temperature overlay, not a standalone/core strategy. In the initial resolved overlap window from 2026-06-04 through 2026-06-10, `dynamic_bucket_tuned_hrrr_rich_pm_active_us12_obs_2022_2025` additive inland-only rows showed 10 resolved, 60.0% win rate, about +1.80 PnL, and R/R about 0.43 when HRRR found late local `12:00-15:00` `HIGH_CONVICTION` `<= 0.50` opportunities the obs bucket consensus did not already own or saw much more weakly. Coastal HRRR disagreement was negative, and HRRR CatBoost was poor in this overlay shape, so revisit only as a tiny canary/research sleeve after more resolved inland sample, likely around `hrrr_dynamic_tuned_inland_late_disagreement_entry_00_50_by_bucket_side_delay_first`.

### 2026-06-11

- Live global low-temperature review showed the `$0.50-$0.75` canary band has been selected, but realized live exposure is uneven: filled cost is far below intended target notional, and some parent rows overstated completion after partial resting fills. Live execution now keeps parent positions `PARTIAL` until cumulative cost reaches the parent target.

### 2026-06-10

- Live order review showed recent LFPB resting fallback ladders were posted and cancelled after TTL with no fills. Parent position summaries now report that as `RESTING_TTL_EXPIRED`, while truly skipped ladders remain separate from accepted-but-unhit passive orders.
- Fixed the FAK/retry/GTC execution handoff after KSEA HIGH_TEMP only filled the sweep-depth-sized `$13.50` child against a `$100` target and stopped. Sweep depth now limits the initial FAK amount only; the position keeps the full risk-capped target and routes remaining notional through retry and the resting GTC ladder.
- Fixed the live v2 resting fallback order path after recent RKSI LOW_TEMP showed FAK partial fills followed by rejected GTC ladder children with invalid tick-size prices. Passive GTC/GTD children now use explicit limit-order submission with price and share size instead of market-order amount reconstruction.
- Fixed the global low-temperature MVP add-on station filter after live KLGA LOW_TEMP BUY_NO fills showed it was admitting US low-temperature markets. The MVP add-on now uses the same global station allow-list as the global low consensus and tiny-tail sleeves: EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD.

### 2026-06-09

- Recent live cycles showed repeated global canary weather errors: Unknown station for EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD. Live execution now routes non-US station IDs to the Celsius/global weather feature service, matching the research collector behavior, so global low-temperature strategies can build station-local low-temperature signals instead of skipping those stations.
- Deactivated NGBoost BUY_YES after the standardized raw-snapshot replay showed weak overall and poor recent performance. Reallocated the live risk slot to global low-temperature BUY_NO: the broad canary is now $50 for `$0.05-$0.75` entries and the tiny-tail `<= $0.05` sleeve is live at $5.
- Deactivated the US high-temperature 15m consensus overlay after cap-aware live-style replay showed it was negative incrementally behind the no-tiny consensus core. The earlier standalone promotion read did not account for plan order, same station/date bucket/side caps, and live depth sizing, which let the core consume the overlapping good rows first.
- Added `global_low_mvp_high_conviction_buy_no_entry_05_50_by_bucket_side_delay_first` as a $25 BUY_NO MVP add-on after live-style portfolio replay showed it was additive behind the current stack: 41 incremental entries, about $523.50 risk, about +$1,187.55 PnL, and about 2.27x ROI before future live fill slippage.
- Raised the main US consensus and global low consensus canary to $100 targets and raised live caps to max order/exact bucket-side $100, station/date/side $200, station/date $300, daily new risk $750, and total open risk $1,125. Resting fallback now posts $25 penny-stepped GTC child orders with one shared 360-second TTL before refresh/cancel.

### 2026-06-08

- Raw-snapshot promotion is now the standard gate: `scripts/live_policy_promotion_report.py` replays raw snapshots, reconstructs the live scope, and classifies candidates as PROMOTE, CANARY, DEACTIVATE, or RESEARCH_ONLY. Live policy changes should be decided from that report, not from stale materialized policy rows.
- The canonical US high-temp live core is now `pm_us12_bucket_consensus_hc_late_no_tiny_by_bucket_side_delay_first`. The 15m bucket-consensus policy `pm_us12_bucket_consensus_hc_15m_late_entry_00_50_by_bucket_side_delay_first` was later deactivated on 2026-06-09 after live-style cap/depth replay showed negative incremental value behind the core. The old dynamic core has been deactivated in the live registry.
- NGBoost BUY_YES was later deactivated on 2026-06-09 after raw-snapshot replay weakened. Moonshot remains at $2. Global low-temp BUY_NO was later raised to a $50 canary on EGLC, LFPB, RJTT, RKSI, VHHH, and ZSPD with station-local `00:30-05:00` timing and `$0.05-$0.75` entry band, plus a $5 tiny-tail sleeve for `<= $0.05`.
- Deep raw-snapshot niche replay across US high, global high, global low, and US low identified low-temperature BUY_NO overlays as the strongest expansion candidates so far. The leading broad candidate, `global_low_dynamic_mvp_high_conviction_by_bucket_side_delay_first`, was promoted as a BUY_NO-only canary with `<= $0.75` entry cap and later raised to $50 with the `<= $0.05` tail slice split out. It uses the existing FAK, retry, and resting fallback path with no added depth gate.
- Global high-temperature consensus did not transfer cleanly from US high temp; global high remains research-only.

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
