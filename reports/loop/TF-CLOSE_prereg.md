# TF-CLOSE: stock perps into the US close, with the day's move. Pre-registration (research loop run 10)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any price of these perps
in 2026-02 → 2026-09 was loaded. The A2 count used listing dates only: 1,890 instrument-days.

## Mechanism
- **Leveraged ETFs** reset to their target leverage at every close. After a day return r, an L× fund must trade about
  L(L − 1) × r × AUM in the underlying, mostly in the last 15–30 minutes. That means buying on up days and selling on
  down days.
- **Short-gamma option dealers** hedge the same way.
- Studies of US stocks find "intraday momentum": the day's return predicts the last half hour.
- The perps track the stock while it trades, so the same flow should push them into 16:00 ET.
- Closed relative: B3 (crypto coins, 09:30–10:00 NY signal into 15:30–16:00 NY) failed. Crypto has no leveraged-ETF
  rebalancing; these underlyings do.

## Universe (fixed list)
- Index-ETF and stock perps whose underlying has large US-listed 2× / 3× leveraged ETFs.
- **Index ETFs:** SPY, QQQ, IWM, SMH.
- **Stocks:** TSLA, NVDA, MSTR, COIN, PLTR, AMD, AAPL, AMZN, MSFT, GOOGL, META.
- A perp enters 2 days after its listing (listings 2026-01-28 → 2026-07-29).

## Events
- Every NYSE full trading day from 2026-02-02 to 2026-09-30 (holidays excluded).
- **Day move:** r = close(5m bar ending 15:30 ET) / close(5m bar ending 16:00 ET on the previous trading day) − 1.
- **Side:** d = sign(r).
- **Discovery:** days before 2026-08-01 (1,260 instrument-days). **Holdout:** 2026-08-01 → 2026-09-30 (630).

## Data
data.binance.vision monthly 5m klines and TradFi archive funding (`ctradfi_funding.py`). Streamed, sliced to the period
on load, never stored.

## Trade
- **Entry:** the open of the 5m bar at E, E ∈ {15:30, 15:45} ET.
- **Exit:** the open of the 5m bar at 16:00 ET.
- **No stop.**
- **Costs:** 20 bp round trip, plus actual funding at settlements in (entry, exit]. A 4 h interval settles at 20:00
  UTC, which is 16:00 ET in summer. A +10 bp stress is reported.

## Controls
3 random 5m entries per trade, same instrument, calendar month (inside the period) and side, with the same hold length
(30 or 15 min), inside the regular session 09:30–15:30 ET. That isolates the close from intraday drift. Seeded with
zlib.crc32 of instrument and day.

## Grid
Entry E ∈ {15:30, 15:45} × threshold |r| ≥ 1% or ≥ 3%: 4 cells. Reported only: index ETFs vs stocks, per instrument,
per month, up vs down days, and the stress.

## Gate and holdout bar (protocol + A1 + A2)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in 2026-08 and in 2026-09 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
