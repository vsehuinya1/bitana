# CB-PREMIUM: BTC and ETH after a day of unusual Coinbase premium. Pre-registration (research loop run 24, A5)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A5), before any Coinbase price was
downloaded. **A2 estimate:**
- A |z| ≥ 1.5 day is about 7% of days per side.
- ~1,950 days from 2021-06 to 2026-09 → ~135 events per side before the 3-day spacing, ~100 after.
- × 2 coins × 2 sides ≈ 400 trades.

**Data sources (A5):** Coinbase Exchange public candles (`api.exchange.coinbase.com/products/{BTC,ETH,USDT}-USD/candles`,
hourly, no key), fetched during this run and cached in `research_cache/loop/ext/`; Binance 1h klines from
data.binance.vision.

## Mechanism
- US spot demand (ETFs, US institutions, Coinbase Prime clients) clears on Coinbase first. A day-long Coinbase premium
  over Binance means persistent US buying that offshore arbitrageurs have not yet closed, and a discount means US
  selling.
- Arbitrage moves that flow onto Binance and its perps over the next days, so price should follow the premium's sign.
- Watched widely as the "Coinbase premium index". Not in any closed list: BASIS-UP and WD used Binance's own spot.

## Signal (per coin: BTC, ETH)
- **Hourly premium** p = Coinbase c-USD close / (Binance cUSDT perp close × Coinbase USDT-USD close) − 1.
  - The USDT-USD factor removes USDT's own drift from $1.
  - Coinbase lists USDT-USD from 2021-05, so the test starts 2021-06-01.
- **Daily premium** P_d = mean of the hourly p in UTC day d (≥ 20 hours required).
- **z_d** = (P_d − trailing 90-day mean) / trailing 90-day sd, excluding d (≥ 60 days required).
- **Events:** **premium** z ≥ 1.5 → long; **discount** z ≤ −1.5 → short. At least 3 days after the same coin's
  previous event of that side.
- **Lag:** hourly market prices are public in real time, so day d's P is known at 24:00 UTC.

## Trade
- **Entry:** the open of the 1h bar at 00:00 UTC of d + 1 (Binance perp, the coin's own signal).
- **Exit:** the open at 00:00 of d + 1 + k, k ∈ {1, 3, 7} days. No stop.
- **Costs:** 12 bp round trip, plus actual funding (cache from 2023-12, archive before). A +10 bp stress is reported.

## Controls
3 random entry days per trade, same coin and calendar month (inside the split), same side and hold.

## Periods
The oldest 60% of events (both coins and sides, by date) are discovery. The cut is computed from the premium series
before any outcome, then printed and recorded.

## Grid
{premium → long, discount → short} × k ∈ {1, 3, 7}: 6 cells. Reported only: BTC and ETH separately, |z| ≥ 2, per year,
and the stress.

## Gate and holdout bar
- **Gate:** net > 0 with t ≥ 2 (clustered by entry week); edge vs control > 0 with t ≥ 1.5; n ≥ 100 trades.
- **Holdout, chosen cell** (m = 6, t_req = 2.64):
  1. net > 0 with t ≥ 2.64;
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
