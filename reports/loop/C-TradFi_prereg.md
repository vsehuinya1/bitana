# C-TradFi: funding-settlement rule C on Binance's TradFi perps. Pre-registration (research loop run 5)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1, A2), before any TradFi price or
trade was loaded. The A2 count used funding rates and S3 file sizes only (`research/loop/ctradfi_funding.py`).
Nobody had computed a TradFi outcome before. The paper tracker logs TradFi events from 2026-10-06 on, after both
periods.

## Mechanism
- This is the C mechanism, which passed its holdout on crypto perps (C, C-X, C-X-tick in `reports/scalping_prereg.md`).
- After a settlement with large funding, the side that just collected funding (mostly shorts on negative funding) or
  the side that just paid it re-trades within seconds to minutes. The perp drifts in the funding's sign for about 30 min.
- Binance's 217 TradFi perps (US, HK, KR and CN equities, commodities, FX; listed 2025-12 → 2026-10) have the same
  settlement mechanics but different traders. The question is whether the same forced flow pays there.

## Events
- **Universe:** every `TRADIFI_PERPETUAL` in the 2026-10-06 exchangeInfo snapshot (217; 213 with archive funding).
- **Settlement s:** each archive fundingRate row's calc_time, rounded to the nearest second. f_prev is the rate
  settled at the coin's previous settlement, known before s.
- **Event:** |f_prev| ≥ 0.10% (rule C's θ). Direction d = sign(f_prev): short on negative funding.
- **Counts (no prices):**
  - **Discovery:** s from 2025-12-01 to 2026-07-31: 1,794 events, 1,214 coin-days.
  - **Holdout:** s from 2026-08-01 to 2026-09-30: 1,038 events, 858 coin-days.

## Data
- data.binance.vision daily aggTrades files, one per event coin-day plus the next day when s + 31 min crosses
  midnight.
- Each file is streamed in memory. Only the trades in the windows used are kept; no zip is stored.

## Trade: rule C-X-tick, unchanged
- **Entry** at s + L: the first entry-side aggTrade. A short sells at the first sell-aggressor trade (the bid); a long
  buys at the first buy-aggressor trade (the ask).
- **Stop:** when any trade is ≥ 200 bp against entry, exit at the first exit-side trade at or after it.
- **Exit:** otherwise at the first exit-side trade at or after s + 30 min. A trade with no entry or exit trade inside
  [s + L, s + 60 min] is dropped and counted.
- **Costs:** net = gross − 10 bp taker fees. The spread is inside the prices. A +10 bp stress is reported.
- **No funding:** entry is after s and exit is before the next settlement (the shortest interval is 1 h).

## Controls
- 3 random times c per event on the same coin and day, with the same side, entry, stop and exit rules, measured
  from c.
- c must be at least 5 min before and 35 min after every settlement of the coin, so no settlement falls inside
  [c − 5 min, c + 35 min].
- This replaces "same calendar month", which would need about 30 times more tick files. It keeps the coin, the day
  and its trading hours.

## Grid
θ 0.10%, 30 min, 2% stop. Entry L = 250 ms (the rule) or L = 1 s, so 2 cells.
- Reported only: no stop, the +10 bp stress, and splits by underlying type (equity / commodity / HK / KR / CN) and by
  US cash session (13:30–20:00 UTC on weekdays, or outside it).

## Gate and holdout bar (protocol + A1 + A2)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week (day-clustered t also reported);
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = Φ⁻¹(1 − 0.025/3) = 2.39):
  1. net > 0 with t ≥ 2.39 (clustered by week);
  2. net > 0 in 2026-08 and in 2026-09 separately;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
