# TF-LAG: crypto-proxy stock perps after a fast BTC move outside US cash hours. Pre-registration (research loop run 9)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any BTC or proxy price in
2026-02 → 2026-09 was loaded. The proxy list and the split come from listing dates only. The event count is an estimate
from BTC's typical 15-minute volatility, not from prices.

## Mechanism
- Binance lists perps on US stocks whose value is mostly crypto: bitcoin and ether treasury companies, miners, crypto
  exchanges, the USDC issuer and a bitcoin futures ETF.
- Outside the regular session (09:30–16:00 ET on NYSE days), the stock itself is closed or thin. Nobody can hedge the
  perp against the stock, and the perp's market makers are crypto firms.
- A fast BTC move changes these companies' value at once. If their perps adjust slowly, because of thin books and no
  stock arbitrage, a trade in BTC's direction right after the move catches the lag.
- Lead-lag on crypto alts closed fast (test A: alts move with BTC within 10 s). These proxies are not crypto tokens and
  have far thinner books.

## Universe (fixed list)
MSTR, COIN, CRCL, BMNR, IREN, HUT, MARA, BITO, FWDI, BNC: the `EQUITY` perps in the 2026-10-06 exchangeInfo snapshot whose
main business is holding, mining, trading or issuing crypto, plus the bitcoin futures fund.
- Excluded: HOOD (a broad broker), APLD (AI data centres) and STRC (a preferred share, low beta).
- A proxy enters 24 h after its listing (listings from 2026-02-09 to 2026-09-18).

## Events
- **BTC move:** r15(t) = close(t) / close(t − 15 min) − 1 on BTCUSDT perp 1m closes. An event is the first minute t
  with |r15| ≥ 1%, at least 60 min after the previous event.
- **Outside cash hours:** t is not inside the NYSE regular session (09:30–16:00 ET on NYSE trading days). Weekends and
  holidays count as outside.
- **Trades:** for each listed proxy with 1m bars at the entry and the exit, one trade in BTC's direction d = sign(r15).
- **Estimated count:** about 150 events in 2026-02 → 09, times 5 proxies on average, ≈ 750 trades. That clears A2's
  300 with room.
- **Discovery:** events before 2026-08-01 (about 57% of proxy-days). **Holdout:** 2026-08-01 → 2026-09-30.

## Data
data.binance.vision monthly 1m klines (BTCUSDT and the proxies) and TradFi archive funding. Streamed, sliced to the
period on load, never stored.

## Trade
- **Entry:** the open of the 1m bar starting at t (the bar after the signal close).
- **Exit:** the open of the bar at t + H, H ∈ {15, 60} min. No stop.
- **Costs:** 20 bp round trip, plus actual funding at settlements in (entry, exit]. A +10 bp stress is reported.

## Controls
3 random 1m entries per trade, same proxy and calendar month (inside the period and outside cash hours), same side and
holds. Seeded with zlib.crc32 of proxy and event time.

## Grid
H ∈ {15, 60 min} × {all proxies, lagging only}: 4 cells. **Lagging:** the proxy's own 15-min move in BTC's direction
is less than half of BTC's, d × r15_proxy < 0.5 × |r15_BTC|. Reported only: the stress, per proxy, per month, weekend
vs weekday nights, and |r15| ≥ 2%.

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
