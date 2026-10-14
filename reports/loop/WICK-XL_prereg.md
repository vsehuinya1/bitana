# WICK-XL: run 13's wick rule, unchanged, on less liquid coins. Pre-registration (research loop run 22, A4)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A4; A4.2 near-miss replication),
before any price of these coins was loaded for this idea. The split and the A2 count come from volumes and the mask
only.

## Why
- Run 13 (WICK-X-3D) passed its gate: market-wide fills held 24 h, +14.7% per fill, t 3.05.
- It failed its holdout on t alone: +5.72%, t 1.53, positive in 2024, 2025 and 2026, but only 81 fills on 12 days.
- A4.2 allows the rule to be re-tested unchanged on coins it never used, with a fresh discovery and holdout.

## Universe
- Every USDT perp **not** in `trend/liq_mask` on the bid day, with trailing 30-day median daily quote volume (base
  volume × close, through the day before) ≥ $10M.
- Excluded: the wick catcher's 20 coins and 12 collapsed coins.
- That is 543 coins and 142,348 coin-days from 2020 to 2026-09, about 3× run 13's.
- None of these coins were in run 13 (liquid mask only) or in the WD research.

## Rule (run 13's chosen cell, unchanged)
- **Bid:** every hour H, ATR1h = mean true range of the last 14 hourly bars (skip if < 0.1% of close). Limit buy at
  L = close(H) − 5 × ATR1h for hour H + 1.
- **Fill (strict):** low(H + 1) ≤ L − 0.1 × ATR1h. Fill price = min(open(H + 1), L).
- **One position per coin.**
- **Market-wide filter:** the bid hour has ≥ 50% of the 20 coins at hourly z ≤ −2 (z = log return / trailing
  720-bar sd, shifted 1; ≥ 16 coins valid).
- **Exit:** the open of the 1h bar at start(H + 1) + 25 h (24 to 25 h held).
- **Costs:** 0.12% round trip plus actual funding (cache from 2023-12, archive before). A +10 bp stress is reported;
  it matters more for thin coins.

## Controls
3 random hourly entries per fill, same coin and calendar month (inside the split), long, 24 h, same costs and funding.

## Periods
- **Discovery:** bid hours before 2024-11-22 (60% of the universe's coin-days).
- **Holdout:** 2024-11-22 → 2026-09-23.
- The discovery code loads bars only before the cut.

## Grid
1 cell: 24 h, market-wide fills. Reported only: all fills (single-coin wicks included), fills per day, per year, per
coin, and the stress.

## Gate and holdout bar
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by fill week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.
- **Holdout** (m = 5, t_req = Φ⁻¹(1 − 0.025/5) = 2.58):
  1. net > 0 with t ≥ 2.58;
  2. net > 0 in each calendar year of the holdout (2024 from Nov 22, 2025, 2026);
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
