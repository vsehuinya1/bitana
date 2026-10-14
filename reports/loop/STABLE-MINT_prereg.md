# STABLE-MINT: BTC and ETH after big stablecoin mints or burns. Pre-registration (research loop run 23, A5)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A5; the first C idea), before any
price after these events was computed. The A2 count used stablecoin supply only.

At z ≥ 1.5 there are 138 mint and 87 burn events from 2020 to 2026-09, and 450 trades on BTC and ETH. That clears A2's
300 on trades; events per side are thin, which is disclosed.

**Data source (A5):**
- `https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=1` (USDT) and `…=2` (USDC). DefiLlama, public, no
  key.
- Fetched 2026-10-14 08:43Z into `research_cache/loop/ext/`.

## Mechanism
- USDT and USDC are minted when large buyers (OTC desks, funds, exchanges) wire in dollars, and the new coins are
  mostly spent on crypto within days: forced deployment of new money.
- Burns are redemptions, money leaving. A big net mint should precede buying, and a big burn selling.
- Prior work is mixed (Griffin and Shams 2020 on Tether; later studies find weak effects at daily horizons), so this
  is a real test.

## Signal
- S_d = USDT + USDC total circulating (DefiLlama daily point for UTC day d). ΔS_d = S_d − S_{d−1}.
- z_d = (ΔS_d − trailing 90-day mean) / trailing 90-day sd of ΔS, excluding d (≥ 60 days required).
- **Mint event:** z ≥ 1.5. **Burn event:** z ≤ −1.5. At least 3 days after the previous event of the same side.
- **Publication lag (A5.4):** DefiLlama's point for d is a snapshot taken around 00:00 UTC of d. Unsure of the exact
  time, so one full day: the trade enters at the open of d + 1.

## Trade
- **Mint:** long BTCUSDT and ETHUSDT perps, each a separate trade. **Burn:** short both.
- **Entry:** the open of day d + 1 (00:00 UTC). **Exit:** the open of d + 1 + k, k ∈ {1, 3, 7} days. No stop.
- **Costs:** 12 bp round trip (BTC / ETH), plus actual funding (cache from 2023-12, archive before). A +10 bp stress is
  reported.

## Controls
3 random entry days per trade, same coin and calendar month (inside the split), same side and hold. Seeded with
zlib.crc32 of coin and day.

## Periods
The oldest 60% of events (both sides, by date) are discovery, the rest holdout. The cut is computed from the supply
series before any price is loaded, then printed and recorded.

## Grid
{mint → long, burn → short} × k ∈ {1, 3, 7}: 6 cells. Reported only: z ≥ 2.0, BTC and ETH separately, USDT-only and
USDC-only signals, per year, and the stress.

## Gate and holdout bar (protocol + A1–A5)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100 trades.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 6, t_req = Φ⁻¹(1 − 0.025/6) = 2.64):
  1. net > 0 with t ≥ 2.64;
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
