# SPOTOPEN: trade the perp when Binance opens spot trading in its coin. Pre-registration (research loop run 4)

**Status.** Registered 2026-10-13 under `research/loop/PROTOCOL.md` (amendment A1 applies), before any price around a
spot opening was loaded. The event list was built from data.binance.vision S3 file listings only (first archive month
and day of each spot pair, `research/loop/spotopen_events.py`), and event counts by quarter were looked at to set the
split. No prices.

## Mechanism
- Many coins get a Binance perp first and Binance spot later, at a minute announced days ahead.
- At that minute a new group of traders can buy: spot-only Binance users, who cannot use futures, plus listing-chasers.
  Spot market makers start quoting and arbitrage links spot to the perp.
- If the new spot demand is larger than perp traders priced in, the perp drifts up after the open. If the
  announcement already pumped it, "sell the news" pulls it down. Direction is not assumed: long and short are both
  tested.

## Events
- Every USDT-M perp in `research_cache/edge/perp_universe.json` (delisted included) whose coin has Binance spot pairs
  in the archive.
  - The coin is the perp's base, with or without a leading 1000 / 1000000 / 1M.
  - The spot pairs searched quote in USDT, BUSD, BTC, BNB, FDUSD, TRY, USDC, ETH, EUR or TUSD.
- **Spot open S:** the open time of the first spot 1m bar with volume, across the coin's pairs with the earliest
  archive day.
- **The event counts only if** the perp has a 1m bar with volume at or before S − 24 h, and one within 2 min of S.
  Spot and perp opening together are not events.
- About 75 events, 2021 → 2026-09, most from 2025 on.
  - **Discovery:** S before 2025-10-01 (~50).
  - **Holdout:** S from 2025-10-01 to 2026-09-30 (~32).

## Data
- Perp 1m klines from data.binance.vision archives: monthly files, or daily files where no monthly file exists yet.
  The span is S − 8 d to S + 8 d, kept only for that window.
- Spot 1m daily archive file for the first spot day, to find S.
- Funding: `edge/funding_all/{SYM}.pkl` if it covers the window, else archive fundingRate monthly files.

## Trade
- **Windows** (entry → exit, perp 1m opens):
  - **W15:** S → S + 15 min;
  - **W60:** S → S + 60 min;
  - **W240:** S → S + 240 min;
  - **L240:** S + 15 min → S + 240 min, which skips the opening spike.
- Long or short. One trade per event per cell. No stop; a 10% disaster stop on 1m highs and lows is reported.
- **Costs:** 20 bp round trip, the protocol's rate for new or less liquid coins. Actual funding at settlements inside
  (entry, exit]. A +10 bp stress is reported. Entries at S hit the busiest minute, so the stress matters more here.

## Controls
- 3 random perp 1m bars per event, from S − 7 d to S − 1 d and S + 1 d to S + 7 d, only where the perp traded.
  Same side, same hold.
- This replaces the protocol's "same calendar month". It keeps the coin's own age and drift around the event and
  leaves out the open itself.

## Grid
Side (long / short) × window (W15 / W60 / W240 / L240) = 8 cells.

## Gate and holdout bar (protocol + A1)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2. t is clustered by entry week, because Binance often opens several coins
    together;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 40. That is relaxed from 100 because history fixes the event count: about 75 in total. With ~32 holdout
    events, the holdout can only pass on a large effect.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 2, t_req = 2.24):
  1. net > 0 with t ≥ 2.24;
  2. net > 0 in 2025 Q4 and in 2026 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
