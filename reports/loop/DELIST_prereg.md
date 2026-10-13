# DELIST: trade a perp's final hours before Binance delists it. Pre-registration (research loop run 3)

**Status.** Registered 2026-10-13 under `research/loop/PROTOCOL.md` (amendment A1 applies), before any price data around
a delisting was loaded. Event counts by quarter were looked at to set the split, but no prices.

## Mechanism
- Binance announces a perp's delisting about a week ahead and settles every open position at a set minute.
- In the final hours, all remaining open interest must close, market makers pull out and the book thins.
- If one side dominates the forced closing, the price should drift that way into settlement. Direction is not assumed:
  long and short are both tested.

## Events
- Every non-TRADING perp in `research_cache/edge/perp_universe.json` (SETTLING or DELISTED, 161).
- **Delisting minute D:** the open time of the last 5m archive bar with volume, plus 5 min. Perps with no archive bars
  are dropped and counted.
- **Discovery:** D before 2026-01-01 (~95 events). **Holdout:** D from 2026-01-01 to 2026-09-30 (~63 events).

## Data
- 5m perp klines from data.binance.vision monthly archives: the delisting month and the month before.
- Funding: `edge/funding_all` (2023-12 on); before that, archive fundingRate files.

## Trade
- **Exit:** the open of the 5m bar starting at X = D − 10 min, ahead of the settlement mechanics.
- **Entry:** the open at X − H, H ∈ {1 h, 4 h, 12 h, 24 h}. Long or short. No stop; a 10% disaster stop is reported.
- **Costs:** 20 bp round trip (the protocol's "less liquid" rate). Actual funding at settlements inside (entry, exit].
  A +10 bp stress is reported. So is the median quote volume in the final hour.
- **Controls:** 3 random 5m bars per event from the same coin, between D − 31 days and D − 25 h − H. Same side, same hold.
  - The prior month replaces the protocol's "same calendar month", because the delisting month is the event itself.
  - The window still contains the post-announcement days.

## Grid
Side (long / short) × H (1 / 4 / 12 / 24 h) = 8 cells. One trade per event per cell.

## Gate and holdout bar (protocol + A1)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2. t is clustered by delisting week, because Binance often delists several perps at
    once;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100, relaxed to n ≥ 80 here because the event count is fixed by history.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 2, t_req = 2.24):
  1. net > 0 with t ≥ 2.24;
  2. net > 0 in 2026 H1 and in 2026 Q3 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.

## Results
