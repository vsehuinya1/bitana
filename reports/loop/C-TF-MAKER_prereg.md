# C-TF-MAKER: rule C on TradFi perps with a maker entry. Pre-registration (research loop run 7)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any maker-entry outcome
was computed. **Disclosure:** the discovery period (2025-12 → 2026-07) was already seen in run 5 with a taker entry
(C-TradFi: +13.9 bp gross, +3.9 net, t 1.47, edge t 4.54). This variant was chosen after that result, so the discovery
gate here is weak evidence. **The untouched 2026-08/09 holdout decides**; its ticks have never been loaded.

## Mechanism
- C-TradFi found the settlement flow on TradFi perps (gross +14 bp vs about 0 at random times) but too small for 10 bp
  of taker fees.
- A maker entry pays 2 bp instead of 5 and earns the spread instead of paying it.
- Against that, a resting limit fills only when the price trades through it, which is when the price moves against
  the trade (adverse selection). It misses the fastest winners. This test measures whether the saving beats the
  selection.

## Events (as C-TradFi)
- **Universe:** every `TRADIFI_PERPETUAL` in the 2026-10-06 exchangeInfo snapshot with archive funding
  (`research/loop/ctradfi_funding.py`, re-run to rebuild the funding file).
- **Settlement s:** each archive fundingRate row's calc_time, rounded to the second. f_prev is the rate settled at the
  coin's previous settlement.
- **Event:** |f_prev| ≥ 0.10%. Direction d = sign(f_prev).
- **Discovery:** s from 2025-12-01 to 2026-07-31 (1,794 events). **Holdout:** s from 2026-08-01 to 2026-09-30
  (1,038 events). Together 2,832, above A2's 300.

## Data
data.binance.vision daily aggTrades files, one per event coin-day plus the next day when s + 61 min crosses midnight.
Streamed in memory; nothing is stored but results.

## Trade
- **Order:** at t_a = s + 250 ms, a post-only limit at the touch on the trade's own side.
  - Short: a sell limit at the ask. The ask is the price of the last buy-aggressor aggTrade at or before t_a.
  - Long: a buy limit at the bid. The bid is the price of the last sell-aggressor aggTrade at or before t_a.
  - If that reference trade is older than s − 5 min, or there is none, the event is dropped and counted.
- **Fill:** only when a later trade (time > t_a) trades through the limit: above it for a short, below it for a long,
  within W seconds of t_a. Fill time t_f; entry price = the limit. Otherwise the order is cancelled and there is no
  trade (the fill rate is reported).
- **Stop:** when any trade after t_f is ≥ 200 bp against entry, exit at the first exit-side trade at or after it
  (taker).
- **Exit:** otherwise at the first exit-side trade at or after s + 30 min (taker; a short buys the ask, a long sells
  the bid). A filled trade with no exit trade by s + 60 min is dropped and counted.
- **Costs:** net = gross − 7 bp (2 bp maker + 5 bp taker; the exit spread is inside the prices). A +10 bp stress is
  reported.
- **No funding:** entry is after s and exit is before the next settlement.

## Controls
3 random times c per event on the same coin and day, with no settlement of the coin inside [c − 5 min, c + 35 min]
(as C-TradFi, same seeds). The same order, fill, stop and exit rules, measured from c. A control that does not fill
is left out; the event's control is the mean of its filled controls.

## Grid
θ 0.10%, 30 min, 2% stop, t_a = s + 250 ms. Fill window W = 60 s (the rule) or W = 10 s, so 2 cells. Reported only:
fill rate, no stop, the stress, and splits by month, underlying type, US cash session and side.

## Gate and holdout bar (protocol + A1 + A2)
- **Gate** (all must hold), on filled trades:
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = Φ⁻¹(1 − 0.025/3) = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in 2026-08 and in 2026-09 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
