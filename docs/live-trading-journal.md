# Live Trading Journal

This is the operating journal for live RoboWeather trading. Use it to record what is live, why it is live, and what should be revisited as more fills settle.

Git history remains the source of truth for code changes. This journal is the source of truth for trading rationale and current operating assumptions.

## Current Live State

Updated: 2026-05-28

### Active policies

| Policy | Side | Target notional | Entry cap | Notes |
| --- | --- | ---: | ---: | --- |
| Consensus HC late | mixed | $25 | <= $0.50 | Primary live strategy. Uses the consensus high-conviction late-window policy. |
| Core capped | BUY_NO | $20 | <= $0.50 | Core dynamic policy after removing higher-priced entries. |
| NGBoost BUY_YES | BUY_YES | $5 | <= $0.50 | Small exploratory allocation. Keep size low until live evidence improves. |
| Moonshot | BUY_NO | $1 | <= $0.50 | Small tail allocation. Original tiny moonshot remains constrained by its tighter policy price rules. |

### Risk caps

| Cap | Current value |
| --- | ---: |
| Max order | $25 |
| Station/date | $60 |
| Station/date/side | $40 |
| Exact bucket/side | $25 |
| Total open risk | $375 |
| Daily new risk | $250 |

### Execution rules

- Live entries are capped at `<= 0.50` because historical replay showed materially better return on risk below this price.
- Orders use FAK first, with retry handling for transient depth/order-version failures.
- Consensus HC may place a single resting fallback limit order after eligible FAK failure paths.
- Resting fallback TTL is 60 seconds and targets 100% of remaining notional.
- The resting fallback is intentionally narrow: it is for improving fill odds without adding a broad passive market-making system.
- Live settlement in the live DB updates only when the Polymarket live resolver runs. Polymarket UI may show resolution before `live_policy_positions` is marked `SETTLED`.

### Known operator caveats

- The TUI config page still shows legacy base notional from bankroll and fixed fraction. Actual live policy sizing now comes from fixed per-policy targets.
- Research policy scoring uses official weather outcomes from IEM ASOS. Live PnL settlement uses Polymarket resolution.
- Same-day weather snapshots are useful for preliminary reads, but official Polymarket resolution is what determines live settlement.

## Rationale

### Entry cap at 50 cents

Historical replay of the current live strategy family showed that entries above $0.50 were much less efficient than cheaper entries. They were positive historically, but contributed far less return per dollar of risk than capped entries.

The working assumption is that higher-priced bucket NO entries often have less attractive convexity: downside remains near full loss, while upside is compressed. The cap reduces volume, but improves risk efficiency.

### Current sizing

The system moved from small exploratory sizing to policy-specific fixed sizing after the capped replay looked stronger:

- Consensus HC: $25 because it is the highest-conviction promoted strategy.
- Core capped: $20 because it remains strong but is slightly secondary to consensus.
- NGBoost BUY_YES: $5 because BUY_YES has been weaker historically and remains exploratory.
- Moonshot: $1 because tail entries are high variance and should not drive daily risk.

The max order cap is set to $25 so the largest intended order cannot exceed the current primary-policy size.

### Resting fallback

FAK retries address temporary book/depth/order-version issues. When those still fail for Consensus HC, a short-lived passive order can capture fills inside or near the intended risk price without leaving stale exposure in the market.

The 60-second TTL is a deliberate compromise: weather does not normally reprice enough in one minute to invalidate the original edge, but the order should not remain open after the cycle context has aged.

## Journal

### 2026-05-28

- Added this journal as the live trading state and rationale tracker.
- Current live strategy stack:
  - Consensus HC at $25.
  - Core capped at $20.
  - NGBoost BUY_YES at $5.
  - Moonshot at $1.
- Current live entry cap is `<= 0.50`.
- Current risk caps are max order $25, station/date $60, station/date/side $40, exact bucket/side $25, total open risk $375, daily new risk $250.
- Operator note: May 27 looked rough intraday, but Polymarket UI later showed Seattle, Los Angeles, and the smaller New York NO positions resolving favorably while several other positions lost. The live DB still requires the resolver to mark final settled PnL.

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
