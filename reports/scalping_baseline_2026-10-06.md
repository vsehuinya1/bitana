# Scalping research, step 1: cost-reality baseline (2026-10-06)

Data: Binance USDT-M aggTrades, 14 days (2026-09-22 → 10-05), 9 perps, from data.binance.vision
(`research_cache/aggtrades/`), plus a live public bookTicker capture on 10-06 (quoted spread, top-of-book size).
Scripts: `research_cache/scalp/cost_baseline.py`, `bt_stats.py`. Size assumed: ~$500–5k notional ($104 × 5x and up).
Fees: VIP0, maker 2.0 bp, taker 5.0 bp (BNB payment: −10%).

## 1. Execution costs per round trip (bps)

| coin | spread | slip beyond touch, $1–10k | maker markout 60s / fill | **TT** | **MT** | **MM** |
|---|---|---|---|---|---|---|
| BTC | 0.01 | 0.02 | −0.77 | 10.1 | 7.8 | 5.6 |
| ETH | 0.04 | 0.02 | −0.63 | 10.1 | 7.7 | 5.3 |
| BNB | 0.13 | 0.05 | −0.80 | 10.2 | 7.9 | 5.6 |
| SOL | 0.84 | 0.01 | −0.68 | 10.9 | 8.1 | 5.4 |
| XRP | 0.66 | 0.03 | −0.63 | 10.7 | 8.0 | 5.3 |
| DOGE | 1.04 | 0.05 | −0.72 | 11.2 | 8.3 | 5.4 |
| LINK | 0.71 | 0.15 | −0.69 | 11.0 | 8.2 | 5.4 |
| 1000PEPE | 0.23 | 0.34 | −0.48 | 10.9 | 7.9 | 5.0 |
| WIF | 4.01 | 0.47 | −1.19 | 15.0 | 10.7 | 6.4 |

TT = taker in and out: 10 + spread + slippage. MT = maker in, taker out: 7 + ½ spread + slippage + adverse selection.
MM = maker both sides: 4 + 2 × adverse selection.
- Spreads are 1 tick 97–100% of the time on all 9 coins. The live quoted spread matches the aggTrades estimate.
- **Fees are ~99% of taker cost on majors.** The BTC spread is 0.01 bp, against 5 bp of taker fee per side.
- Maker markout: a passive fill is worth −0.5 to −0.8 bp against the mid 10s–5m later, because adverse selection beats the
  half-spread captured. Large aggressor orders are worse: −1.2 to −26 bp per fill on orders of $1M+ (BTC −1.7, LINK −26).
  These are averages over all fills; a real resting quote fills more often when price trades through it, so its markout is worse.
- **Spread capture at VIP0 is impossible.** The maker fee (2 bp per side) is 50–200× the BTC/ETH spread.
  Only large-tick coins (WIF, 4 bp tick) have a spread near the round-trip maker fee, and adverse selection still exceeds it.

## 2. Typical move per horizon (σ of log return, bps, this 14-day window)

| coin | 10s | 1m | 5m | 15m | 1h |
|---|---|---|---|---|---|
| BTC | 1.8 | 4.6 | 10.8 | 17.6 | 33.5 |
| ETH | 2.3 | 5.7 | 13.1 | 20.9 | 37.8 |
| SOL | 3.2 | 7.8 | 18.0 | 29.7 | 54.8 |
| DOGE | 4.4 | 10.7 | 24.6 | 40.4 | 75.3 |
| 1000PEPE | 5.8 | 14.1 | 32.3 | 53.4 | 103.1 |
| WIF | 7.3 | 17.6 | 40.5 | 66.1 | 132.4 |

The window was quieter than the past 12 months: BTC 5m σ was 10.8 here, against 14.3 for the 12 months and 12.1 on the
median day. In normal volatility the ratios below are 10–25% lower (better).

## 3. Cost ÷ σ: how strong a signal must be just to break even

| coin | TT 10s | TT 1m | TT 5m | TT 15m | TT 1h | MM 1m | MM 5m | MM 15m | MM 1h |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 5.5 | 2.2 | 0.93 | 0.57 | 0.30 | 1.20 | 0.51 | 0.32 | 0.17 |
| ETH | 4.4 | 1.8 | 0.77 | 0.48 | 0.27 | 0.93 | 0.40 | 0.25 | 0.14 |
| SOL | 3.4 | 1.4 | 0.60 | 0.37 | 0.20 | 0.69 | 0.30 | 0.18 | 0.10 |
| DOGE | 2.6 | 1.0 | 0.45 | 0.28 | 0.15 | 0.51 | 0.22 | 0.13 | 0.07 |
| 1000PEPE | 1.9 | 0.78 | 0.34 | 0.20 | 0.11 | 0.35 | 0.15 | 0.09 | 0.05 |

For scale: the academic short-horizon reversal edge (~1.3 bp gross at ~15m) is a conditional drift of ~0.03–0.05σ.
Bitana's liquidation trigger had ~0σ. Ordinary always-on signals live in the 0.01–0.05σ range.

## Where an edge could survive
- **Dead:** anything under ~1 minute at VIP0, and any taker scalp on BTC/ETH/BNB under 1 hour. The signal would need a
  drift of 1–5σ (taker) or more than 1σ (maker).
- **Dead:** classic market making and spread capture at VIP0. It needs maker rebates or ~0 fees plus queue priority.
- **Narrow:** maker-entry trades on volatile alts at 15m–1h (needs 0.05–0.15σ drift), but only with event-conditioned
  signals (tails, forced flow). These are rare situations where conditional drift is large, the same family as the wick
  catcher. Always-on signals are too weak.
- **Ruled out:** a 10% BNB fee discount does not move any cell from dead to alive. Higher VIP tiers need volume that is not reachable at this size.

## Data availability (for step 3)
- aggTrades: available up to yesterday for all perps. This is enough for flow, imbalance and markout tests on any coin.
- Futures bookTicker archive: **ends 2024-03-30** (it covers 2023-05 → 2024-03). There is no recent top-of-book history;
  queue modelling needs either that period or a forward live recorder.
- bookDepth archive: ±1–5% bands only, too coarse for queue position. Tardis free days give full L2 on the 1st of each month.
