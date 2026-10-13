# BASIS-UP: short the perp when it trades well above spot. Pre-registration (research loop run 2)

**Status.** Registered 2026-10-13 under `research/loop/PROTOCOL.md` (amendment A1 applies), before any premium-side
number was computed. The 2026-09-27 test (`research_cache/edge/basis_dislocation.py`) computed only the discount side:
perp below spot, the signal now in the WD engine.

## Mechanism
- When leveraged longs chase a move, the perp trades above spot. The premium shows up as funding the longs pay.
- Arbitrageurs short the perp and buy spot, which pulls the perp back down to spot.
- This is the mirror of the discount side, where the profit came from the bounce, not the convergence.

## Data
- `research_cache/edge/k5m/{SYM}.npy` (perp) and `edge/spot5m/{SYM}.npy` (spot), 5m OHLC, 20 coins
  (`capitulation_reader.UNIVERSE`), 2020-01 → 2026-09-24.
- `edge/funding.pkl`: actual funding. A short receives positive funding.

## Signal and trade
- Basis b = perp close / spot close − 1 at each 5m close where both bars exist.
- **Signal:** b crosses above +T (the previous bar < T), T ∈ {30 bp, 50 bp}. Signals with b > 5% are skipped as
  probable bad prints.
- **Entry:** short the perp at the next 5m open. One position per coin per cell; signals during a position are ignored.
- **Exits** (4 rules):
  - **C24:** at the next open after the first 5m close with b ≤ +5 bp (converged), or at 24 h.
  - **C4:** the same convergence exit, capped at 4 h.
  - **F4:** a fixed 4 h hold.
  - **F24:** a fixed 24 h hold.
- No stop. A 5% disaster stop is reported (5m highs; fills at the worse of the stop and the bar open).
- **Costs:** 12 bp round trip (BTC, ETH), 14 bp (the other 18). Actual funding at settlements inside (entry, exit].
  A +10 bp stress is reported.
- **Controls:** 3 random 5m bars per trade, same coin and calendar month. Each is a short held for the same number of
  bars as the trade. Hold-matched because a convergence exit on a random bar would close it at once.

## Grid
T 30 / 50 × exit C24 / C4 / F4 / F24 = 8 cells. Short only, pooled over the 20 coins.

## Periods
- **Discovery:** entries 2020-01-01 → 2023-12-31.
- **Holdout:** entries 2024-01-01 → 2026-09-24. Never loaded before the gate.

## Gate and holdout bar (protocol + A1)
- **Gate:** all four must hold:
  - discovery net mean > 0, with t ≥ 2 (clustered by entry week);
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 2, t_req = 2.24):
  1. net > 0 with t ≥ 2.24;
  2. net > 0 in 2024, 2025 and 2026 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.

## Results
