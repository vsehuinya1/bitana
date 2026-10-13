# CROWD: trade against extreme retail positioning. Pre-registration (research loop run 1)

**Status.** Registered 2026-10-13 under `research/loop/PROTOCOL.md`, before any of the positioning data was computed on
for this test. Positioning ratios were never tested before. "Funding / OI context" (2026-09-30) was a filter on the
breakout, not this.

## Mechanism
- Binance publishes the all-accounts long/short ratio (mostly small accounts) and the top traders' position ratio every
  5 min.
- When small accounts pile into one side far beyond their norm for the coin, that side pays funding and holds the
  nearest liquidation levels. A move against it can cascade.
- If top traders lean the other way at the same time, the crowd is likely wrong.

## Data
- `research_cache/edge/metrics/{SYM}.pkl`: 5m Binance metrics for 20 coins (BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX,
  LINK, DOT, LTC, BCH, ETC, TRX, XLM, ATOM, NEAR, UNI, FIL, AAVE). From 2021-09 (BTC) or 2021-12 (most others).
- `research_cache/edge/k5m/{SYM}.npy`: 5m perp OHLC.
- `research_cache/edge/funding.pkl`: actual funding.

## Signal (per coin, on each 5m metrics snapshot t)
- A = log(all-accounts long/short ratio), T = log(top-trader position ratio).
- z = (value − trailing 30-day mean) / trailing 30-day SD. The window includes t and needs ≥ 70% of its bars. The first
  30 days are warm-up only.
- **Crowded long:** z_A crosses up through +2 (previous snapshot < 2, this one ≥ 2). That is a **short** signal.
- **Crowded short:** z_A crosses down through −2. That is a **long** signal.
- **S1:** z_A alone. **S2:** also top traders on the other side: z_T ≤ 0 for a short, z_T ≥ 0 for a long.

## Trade
- Entry at the open of the 5m price bar that starts 5 min after t (one bar skipped for the publication delay).
- Exit at the open of the bar H after entry, H ∈ {4 h, 24 h}. No stop. A 5% disaster stop is reported only.
- One position per coin per cell. Signals during a position are ignored.
- Costs: 12 bp round trip (BTC, ETH), 14 bp (the other 18). Actual funding at settlements inside (entry, exit].
  A +10 bp stress is reported.
- **Controls:** 3 random 5m bars per trade, same coin, same calendar month, same side, same hold.
  Edge = trade net − the mean of its controls' net.

## Grid
Side (long / short) × S1 / S2 × H 4 h / 24 h = 8 cells, pooled over the 20 coins.

## Periods
- **Discovery:** data start → 2024-06-30 (entry time).
- **Holdout:** 2024-07-01 → 2026-09-24. Never loaded before the gate.

## Gate and holdout bar (protocol)
- **Gate:** discovery net mean > 0, t ≥ 2 (clustered by entry week), edge vs control > 0, n ≥ 100. The qualifying cell
  with the highest t goes on.
- **Holdout, chosen cell** (m = 1, t_req = 1.96):
  1. net > 0 with t ≥ 1.96;
  2. net > 0 in 2024 H2, 2025 and 2026 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.

## Results
