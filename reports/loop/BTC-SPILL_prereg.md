# BTC-SPILL: lagging alts after a big BTC day, 1–3 days. Pre-registration (research loop run 16, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any alt return after
these days was computed. A2 estimate: BTC days with |return| ≥ 4%, about 220 since 2020 (from BTC's daily volatility of
2.5–3.5%), times several lagging liquid alts per day: well over 300 trades. Category 3 (see VOL-SHOCK's note).

## Mechanism
- Big BTC days reprice the whole market, but attention and liquidity reach less-followed alts more slowly.
- Alts that moved less than half of BTC's move by the daily close may catch up over the next days.
- Test A (2026-10-06) closed this lag at seconds (alts follow BTC within 10 s). A lag over days is a different
  question: slow diffusion, not latency.

## Universe and data
- Every USDT perp in `trend/liq_mask` on day d except BTCUSDT.
- Daily bars from `edge/pdaily` / `pdaily_old` (on disk).
- Funding from cached `edge/funding_all` (2023-12 on), with archive fundingRate files before that, for both legs.

## Events
- **BTC day d:** R = BTC close(d) / close(d − 1) − 1, with |R| ≥ 4%. D = sign(R).
- **Lagging alt:** its own return on d, r, satisfies D × r < 0.5 × |R|.
- **Split:** the oldest 60% of BTC event days are discovery, the rest holdout. The cut is computed from BTC's daily
  returns (the signal) before any alt outcome is computed, then printed and recorded.

## Trade
- **Directional:** the alt in BTC's direction (long if D > 0) from the open of d + 1 to the open of d + 1 + k,
  k ∈ {1, 3} days.
- **BTC-hedged:** the same alt leg plus an equal-notional BTC perp leg the other way. That isolates the catch-up from
  BTC's own next-day move.
- **No stop.** The worst trade is reported.
- **Costs:** 20 bp round trip on the alt and 12 bp on BTC, plus actual funding on each leg at every settlement held.
  A +10 bp stress is reported.

## Controls
3 random entry days per trade, same alt and calendar month (inside the split), same side, hedge and hold. Seeded with
zlib.crc32 of coin and day.

## Grid
{directional, BTC-hedged} × k ∈ {1, 3}: 4 cells, on lagging alts. Reported only: all alts (not just laggards), up vs
down BTC days, |R| ≥ 6%, per year, the stress, and BTC's own next-day return after event days.

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery net mean > 0, with t ≥ 2 clustered by entry week;
  - edge vs control > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 4, t_req = 2.50):
  1. net > 0 with t ≥ 2.50 (clustered by week);
  2. net > 0 in each calendar year of the holdout;
  3. net > 0 without the best 5%;
  4. edge vs control > 0.
