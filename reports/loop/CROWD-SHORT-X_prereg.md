# CROWD-SHORT-X: long crowded shorts on unseen coins, 1–7 days. Pre-registration (research loop run 14, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any price around these
events was loaded. The A2 count used cached funding only: 704 events in 121 coins from 2024 on, before the archive
years. **Disclosure:**
- The 20-coin funding-extremes test (`reports/structural_edge_2026-09-25.md`) found that buying per-coin crowded shorts
  earned +0.64% per trade (t 2.98 by day), mostly in 2020–21; 2025–26 was about +0.9% (t ≈ 1.4).
- Those 20 coins are excluded; every coin here is unseen for this question.
- Run 12 (CROWD-MOM-X) used the same universe for the mirror question (crowded longs). It priced only events with
  positive funding, which are different events.

## Mechanism
- Funding far below a coin's own norm means shorts are crowded and pay the longs at every settlement.
- They face a steady cost and a squeeze: forced buy-ins when the price rises.
- The long collects the funding, so it is paid to wait.

## Universe
Every USDT perp in `trend/liq_mask` on the event day, except the 20 coins of the funding-extremes test (BTC ETH SOL XRP
ADA DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI FIL ETC TRX BNB XLM AAVE).

## Events
- **Funding:** cached `edge/funding_all` (2023-12 on), extended back with archive fundingRate files. Rates are scaled
  to 8 h (rate × 8 / interval hours).
- **z** = (rate − trailing 180-day mean) / trailing 180-day sd of the coin's scaled rates, excluding the current
  settlement. At least 300 prior settlements are required.
- **Event:** a settlement s with z ≤ −2.5 and rate < 0, at least 72 h after the coin's previous event.
- **Split:** the oldest 60% of events are discovery, the rest holdout. The cut date is computed from the event list
  (funding only) before any price is loaded, then printed and recorded.

## Data
data.binance.vision monthly 1h klines for the event coins and months; archive funding held in memory. Bars are sliced
to the split on load.

## Trade
- **Entry:** long at the open of the 1h bar at floor_hour(s) + 1 h, after C's 30-minute window. C on these events
  would be short.
- **Exit:** the open of the bar at entry + H, H ∈ {24, 72, 168 h}. No stop; the worst trade is reported.
- **Costs:** 20 bp round trip, plus actual funding at every settlement in (entry, exit]. The long receives negative
  rates. A +10 bp stress is reported.

## Controls
3 random hourly entries per trade, same coin and calendar month (inside the split), long, same hold. Seeded with
zlib.crc32 of coin and settlement time.

## Grid
z ≤ −2.5 or z ≤ −3.5 × H ∈ {24, 72, 168 h}: 6 cells. Reported only: funding received, BTC over the same hold, per
year, per coin, weekly clustering, overlap with market-wide crash days, and the stress.

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 4, t_req = Φ⁻¹(1 − 0.025/4) = 2.50):
  1. net > 0 with t ≥ 2.50 (clustered by week);
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
