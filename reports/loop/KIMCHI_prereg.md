# KIMCHI: BTC and ETH against extreme Korean premium days. Pre-registration (research loop run 25, A5)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A5), before any Upbit price or FX
rate was downloaded. **A2 estimate** (as CB-PREMIUM): |z| ≥ 1.5 days are about 7% per side, so 2020 → 2026-09 gives
roughly 120 events per coin per side after the 3-day spacing. × 2 coins × 2 sides ≈ 480 trades.

**Data sources (A5), fetched during this run into `research_cache/loop/ext/`:**
- Upbit public daily candles (`api.upbit.com/v1/candles/days`, KRW-BTC and KRW-ETH, no key).
- The ECB euro reference rates (`ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip`, public), for USD/KRW.
- Binance daily bars from `edge/pdaily(_old)`.

## Mechanism
- The Korean premium (Upbit's KRW price over the global price) rises when Korean retail buys faster than capital
  controls let arbitrage close the gap. Spikes have marked local euphoria (2017–18, 2021, 2024).
- Discounts mark Korean panic selling. If retail extremes mark turning points, fading them pays over the following
  days. This is a sentiment (category 3) idea; CB-PREMIUM, the US version, just failed in the momentum direction.

## Signal (per coin: BTC, ETH)
- **USD/KRW** = ECB KRW per EUR / ECB USD per EUR, carried over weekends and holidays from the last fixing. Published
  around 15:00 UTC, before the UTC day closes.
- **Daily premium** P_d = Upbit KRW close(d) / (Binance cUSDT close(d) × USD/KRW(d)) − 1. Both are UTC daily closes;
  USDT is taken as USD.
- **z_d** = (P_d − trailing 90-day mean) / trailing 90-day sd, excluding d (≥ 60 days).
- **Events:** **premium** z ≥ 1.5 → short; **discount** z ≤ −1.5 → long. At least 3 days after the same coin's
  previous event of that side. From 2020-01-01.

## Trade
- **Entry:** the open of d + 1 (00:00 UTC, Binance perp, the coin's own signal).
- **Exit:** the open of d + 1 + k, k ∈ {1, 3, 7}. No stop.
- **Costs:** 12 bp round trip, plus actual funding (cache from 2023-12, archive before). A +10 bp stress is reported.

## Controls, periods, grid
- **Controls:** 3 random entry days per trade, same coin and calendar month (inside the split), same side and hold.
- **Periods:** the oldest 60% of events by date are discovery. The cut is computed from the premium series before any
  outcome.
- **Grid:** {premium → short, discount → long} × k ∈ {1, 3, 7}: 6 cells. Reported only: BTC and ETH separately,
  |z| ≥ 2, per year, and the stress.

## Gate and holdout bar
- **Gate:** net > 0 with t ≥ 2 (clustered by entry week); edge vs control > 0 with t ≥ 1.5; n ≥ 100 trades.
- **Holdout, chosen cell** (m = 6, t_req = 2.64):
  1. net > 0 with t ≥ 2.64;
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
