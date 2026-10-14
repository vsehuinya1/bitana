# TF-WKND: fade US equity perps' weekend move after stock venues reopen. Pre-registration (research loop run 8)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any equity perp price in
2026-01 → 2026-09 was loaded. The A2 count used listing dates only (2,382 coin-weekends). A feasibility look at
2026-10-03/05/06 index and kline files (outside both periods) showed that Binance's equity index changes every minute,
weekends included, and the perp sits within about ±10 bp of it. Queue rows 9 and 11 were blocked on that look.

## Mechanism
- US stocks trade on weekdays: the regular session, pre-market, after-hours and the overnight venues
  (Sun–Thu 20:00 → 04:00 ET). From Friday 20:00 ET to Sunday 20:00 ET no US venue trades them.
- Binance's equity perps trade through the weekend. Then price discovery is left to leveraged crypto traders, with no
  stock to arbitrage against, and crypto moves spill into them.
- When the overnight venues reopen on Sunday 20:00 ET, arbitrageurs can hedge against the real stock again. If weekend
  moves overreact, the perp should revert in the hours after the reopening, before the cash open.
- A close relative failed: crypto's own weekend-gap reversion (`reports/structural_edge_2026-09-25.md`; the sign flips
  by period). Crypto has no reopening anchor; these perps do.

## Events
- **Universe:** every `EQUITY` (US) `TRADIFI_PERPETUAL` in the 2026-10-06 exchangeInfo snapshot (179). Perps delisted
  before 2026-10-06 are missing (survivorship, disclosed).
- **Weekend:** T0 = Friday 20:00 ET, T1 = Sunday 20:00 ET (UTC by the New York clock, so DST applies). The perp must be
  listed by T0 − 24 h.
- **Holiday weekends are dropped,** because the venues do not reopen on Sunday 20:00 ET, or the Friday session is
  missing. These are the weekends of Jan 17, Feb 14, Apr 3 (Good Friday), May 23, Jun 19, Jul 3 and Sep 5.
- **Weekend move:** W = close(5m bar ending at T1) / close(5m bar ending at T0) − 1. A perp with no 5m bar at T0 or T1 is
  dropped and counted.
- **Discovery:** weekends with T1 on or before Sunday 2026-08-16 (about 1,210 coin-weekends after the holiday drop).
  **Holdout:** Sundays 2026-08-23 → 2026-09-27 (about 770). The holdout covers only 5 weekends (Sep 6 is a holiday
  weekend), so its week-clustered t has 5 clusters.

## Data
data.binance.vision monthly 5m klines (daily files where no monthly exists yet), one per perp and month, streamed. Only
the bars used are kept in memory. TradFi funding from archive fundingRate files (`ctradfi_funding.py`). The discovery
code keeps nothing after 2026-08-17 14:00 UTC.

## Trade
- **Side:** fade the move: d = −sign(W). Entry at the open of the 5m bar starting at T1.
- **Exit:** the open of the 5m bar at T1 + H, H ∈ {1 h, 4 h, 8 h, 13.5 h}. 13.5 h is the 09:30 ET cash open.
- **No stop.** The worst trade is reported.
- **Costs:** 20 bp round trip (the protocol's rate for less liquid instruments), actual funding at every settlement in
  (entry, exit]. A +10 bp stress is reported.

## Controls
3 random 5m-bar entries per trade, same coin, calendar month and side, same hold, as the protocol says. They are
seeded with zlib.crc32 of coin and weekend. Control times are restricted to the period being tested: none falls after
the discovery cutoff in discovery.

## Grid
Threshold |W| ≥ 0 (every weekend) or |W| ≥ 1%, × H ∈ {1, 4, 8, 13.5 h}: 8 cells. Reported only: the stress, per
month, per coin, a split by |W| size, and the market weekend (median W across perps) vs each perp's own move.

## Gate and holdout bar (protocol + A1 + A2)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by weekend;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by weekend);
  2. net > 0 in both halves: Sundays Aug 23 and 30, and Sep 13 – 27;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
