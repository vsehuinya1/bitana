# CROWD-MOM-X: long crowded longs on unseen coins, 1–7 days. Pre-registration (research loop run 12, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any price around these
events was loaded. The A2 count used cached funding only: 332 events from 2024 on, before the archive years.
**Disclosure:** the idea comes from a seen result. The 20-coin funding-extremes test
(`reports/structural_edge_2026-09-25.md`) found that fading per-coin funding z ≥ 2.5 lost −2.03% per trade (t −2.65),
so this is its mirror. Those 20 coins are excluded; every coin here is unseen for this question.

## Mechanism
- Funding far above a coin's own norm means leveraged longs are crowded and paying.
- The 20-coin test says such crowding is momentum, not a top. Shorts fading the crowd get squeezed, and trend followers
  keep buying.
- The long pays the funding, which is large at these extremes, so the price move must beat it.

## Universe
Every USDT perp in `trend/liq_mask` (established liquid alts), except the 20 coins of the funding-extremes test: BTC
ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI FIL ETC TRX BNB XLM AAVE. The coin must be in the mask on the
event day.

## Events
- **Funding:** cached `edge/funding_all` (2023-12 on), extended back with archive fundingRate files.
  - Each rate is scaled to an 8-hour equivalent (rate × 8 / interval hours), so 4 h and 1 h intervals are comparable.
- **z** = (rate − trailing 180-day mean) / trailing 180-day sd of the coin's own scaled rates, excluding the current
  settlement. At least 300 prior settlements are required.
- **Event:** a settlement s with z ≥ 2.5 and rate > 0, at least 72 h after the coin's previous event.
- **Split:** the oldest 60% of events by settlement time are discovery, the rest holdout. The cut date is computed from
  the event list (funding only) before any price is loaded, then printed and recorded in Results.

## Data
data.binance.vision 1h klines (monthly files) for the event coins and months, plus the control months. Archive funding
held in memory. Bars are sliced to the split on load.

## Trade
- **Entry:** long at the open of the 1h bar starting at s + 1 h, after C's 30-minute settlement window.
- **Exit:** the open of the bar at entry + H, H ∈ {24 h, 72 h, 168 h}. No stop; the worst trade is reported.
- **Costs:** 20 bp round trip, plus actual funding at every settlement in (entry, exit]; the long pays positive rates.
  A +10 bp stress is reported.

## Controls
3 random hourly entries per trade, same coin and calendar month (inside the split), long, same hold. Seeded with
zlib.crc32 of coin and settlement time.

## Grid
z ≥ 2.5 or z ≥ 3.5 (for the 3.5 cells, only events that also reach 3.5) × H ∈ {24, 72, 168 h}: 6 cells. Reported
only: funding paid, BTC's return over the same hold, per year, per coin, episodes (events per week), and positions open
at once.

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
