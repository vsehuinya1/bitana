# WICK-X-3D: the wick catcher on unseen liquid coins, held 1–7 days. Pre-registration (research loop run 13, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any price of the
universe coins was loaded for this idea. The A2 count is an estimate: the wick catcher's 2024 fill rate (233 fills on
20 coins, 0.032 per coin-day) × 46,003 liquid coin-days of this universe ≈ 1,470 fills. The split comes from liquid
coin-days in `trend/liq_mask`, not from prices.

## Mechanism
- WD's wick catcher (PREREG-WICK-CATCHER, live in the WD engine) provides liquidity to forced sellers. A resting bid
  5 ATR below the hourly close fills only inside liquidation wicks, and wicks revert.
- Its research basis: 20 coins plus 12 collapsed coins, positive in every year 2020–2026. Market-wide wicks paid
  +9.4% per fill, single-coin wicks +1.2%.
- Two things are new here:
  - **Unseen coins:** every liquid alt outside those 32.
  - **1–7 day holds (A3)** in place of "sell at the pre-wick close, else 24 h". Does the overshoot keep reverting for
    days, enough to pay funding?

## Universe
Every USDT perp in `trend/liq_mask` on the bid day, except the 20 coins (BTC ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH
ATOM NEAR UNI FIL ETC TRX BNB XLM AAVE) and the 12 collapsed coins (LUNA FTT SRM RAY ANC LUNA2 WAVES BTS HNT TOMO CVC
OMG). That is 327 coins and 46,003 liquid coin-days from 2020 to 2026-09.

## Signal and entry (the registered wick rule, on 1h bars)
- Every hour H: ATR1h = mean true range of the last 14 hourly bars. Skip if ATR1h / close < 0.1%.
- **Bid:** a resting limit buy at L = close(H) − 5 × ATR1h for hour H + 1 only.
- **Fill (strict):** the H + 1 bar's low ≤ L − 0.1 × ATR1h. Fill price = min(open of H + 1, L).
  - The registered rule reads 5m bars inside H + 1. On 1h bars, a gap below L can only be seen at the hour open, so
    the fill price is the same or slightly worse.
- **One position per coin:** no new bid while a position in that coin is open.

## Exit
- The open of the 1h bar at start(H + 1) + H_hold + 1 h, H_hold ∈ {24, 72, 168 h}. The hold is H_hold to H_hold + 1 h.
- No take-profit, since A3 needs ≥ 24 h. No stop; the worst fill is reported.
- **Costs:** 0.12% round trip (maker entry 2 bp, exit 5 bp + 5 bp slippage), as registered. Plus actual funding at
  every settlement held: the long pays positive rates. Cached `edge/funding_all` from 2023-12, archive fundingRate files
  before. A +10 bp stress is reported.

## Market-wide tag (a grid dimension)
- The bid hour H is market-wide when ≥ 50% of the 20 coins have hourly z ≤ −2, from `edge/klines_1h.pkl`.
- z = log return / trailing 720-bar sd, shifted by 1, with ≥ 16 coins valid. This is the reader's definition.

## Controls
3 random hourly entries per fill, same coin and calendar month (inside the split), long, same hold, same costs and
funding. Seeded with zlib.crc32 of coin and fill hour.

## Periods
- **Discovery:** bid hours before 2024-09-10 (60% of liquid coin-days).
- **Holdout:** 2024-09-10 → 2026-09-23, exits by 2026-09-30.
- The discovery code loads bars only before the cut. A discovery fill whose exit would pass the cut is dropped.

## Grid
H_hold ∈ {24, 72, 168 h} × {all fills, market-wide fills}: 6 cells. Reported only: funding paid, coin-specific fills,
per year, per coin, fills per day, positions open at once, and the stress.

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by fill week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in each calendar year of the holdout (2024 from Sep 10, 2025, 2026);
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
