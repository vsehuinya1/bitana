# C-ALT: rule C on Binance's COIN-M and USDC-margined perps. Pre-registration (research loop run 6)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any COIN-M or USDC perp
price or trade was loaded. The event list and counts come from archive funding rates only
(`research/loop/calt_events.py`).

## Mechanism
- C passed its holdout on USDT perps: after a settlement with large funding, receivers and payers re-trade within
  seconds to minutes, and the perp drifts in the funding's sign for about 30 min.
- COIN-M perps (inverse, settled in the coin) and USDC-margined perps have their own funding and their own funding
  farmers.
- **Independent events only.** At these settlements the coin's USDT perp was *not* also at |f_prev| ≥ 0.10%. Those are
  new information. When both are extreme, the price path is mostly C's own, which has already been tested.

## Events
- **Contracts:** every COIN-M `*USD_PERP` (49) and every USDC-margined `*USDC` perp (40) with archive funding.
- **Settlement s:** each archive fundingRate row's calc_time. f_prev is the rate settled at the contract's previous
  settlement. Event when |f_prev| ≥ 0.10%; direction d = sign(f_prev).
- **Independent:** the coin's USDT perp (`edge/funding_all`, else the archive) had no settlement within 1 min of s
  with |f_prev| ≥ 0.10%.
- **Counts:** 1,584 events, 578 of them independent (476 COIN-M, 102 USDC; 80% shorts; 483 coin-days).
  - **Discovery:** independent events with s from 2022-07-01 to 2025-06-30 (335).
  - **Holdout:** independent events with s from 2025-07-01 to 2026-09-30 (243).
  - The 1,006 overlap events are not loaded.

## Data
data.binance.vision daily aggTrades files (`futures/cm` for COIN-M, `futures/um` for USDC), one per event coin-day plus
the next day when s + 61 min crosses midnight. Each file is streamed in memory; nothing is stored but results.

## Trade: rule C-X-tick, unchanged (as in C-TradFi)
- **Entry:** the first entry-side aggTrade at or after s + L (a short sells to the bid; a long buys the ask).
- **Stop:** when any trade is ≥ 200 bp against entry, exit at the first exit-side trade at or after it.
- **Exit:** otherwise at the first exit-side trade at or after s + 30 min. A trade with no entry or exit trade inside
  [s + L, s + 60 min] is dropped and counted.
- **Return:** the price return. For an inverse contract, PnL in USD differs only at second order over 30 min.
- **Costs:** net = gross − 10 bp taker fees, with the spread inside the prices. A +10 bp stress is reported.
- **No funding:** entry is after s and exit is before the next settlement.

## Controls
3 random times c per event on the same contract and day, with no settlement of the contract inside
[c − 5 min, c + 35 min]. Same side, entry, stop and exit rules.

## Grid
θ 0.10%, 30 min, 2% stop. Entry L = 250 ms (the rule) or L = 1 s, so 2 cells. Reported only: no stop, the stress,
and splits by market (COIN-M / USDC), side and year.

## Gate and holdout bar (protocol + A1 + A2)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in 2025 H2 and in 2026 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
