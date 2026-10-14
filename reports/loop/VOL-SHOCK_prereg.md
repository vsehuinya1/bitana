# VOL-SHOCK: long liquid perps after a volume shock, 1–7 days. Pre-registration (research loop run 15, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any return after these
events was computed. The A2 count used daily volumes only: 993 events in 176 coins, 2020 → 2026-09. Category 3 (A2:
"anything else"): no forced-flow source left that the archive can test at 1–7 days was untested or above the event
floor (queue rows 17–28).

## Mechanism
- **High-volume return premium** (Gervais, Kaniel and Mingelgrin 2001, US stocks; similar results reported for crypto):
  an unusual volume spike makes an asset visible.
- The new attention brings buyers over the following days and weeks. Short-sale limits keep the sellers' side quieter,
  so the price drifts up.
- Splitting off shocks with a small price move separates attention from news.
- Not in any closed list. This is volume on daily bars, not an indicator combination.

## Universe and data
- Every USDT perp in `trend/liq_mask` on the event day.
- Daily bars from `edge/pdaily` / `pdaily_old` (on disk). Funding from cached `edge/funding_all` (2023-12 on), with
  archive fundingRate files before that.

## Events
- **Day d (UTC):** base volume ≥ 3 × its mean over the previous 30 days (30 full days required), the coin is in the
  liquid mask on d, and ≥ 7 days have passed since the coin's previous event.
- **Split:** the oldest 60% of events by date are discovery, the rest holdout. The cut is computed from the
  volume-only event list before any return is computed, then printed and recorded.

## Trade
- **Entry:** long at the open of day d + 1 (00:00 UTC, after the shock day closes).
- **Exit:** the open of day d + 1 + k, k ∈ {1, 3, 7} days. No stop; the worst trade is reported.
- **Costs:** 20 bp round trip, plus actual funding at every settlement held. A +10 bp stress is reported.

## Controls
3 random entry days per event, same coin and calendar month (inside the split), long, same hold. Seeded with
zlib.crc32 of coin and day.

## Grid
{all shocks, shocks with |return on d| < 3%} × k ∈ {1, 3, 7}: 6 cells. Reported only: up-day vs down-day shocks, BTC
over the same hold, per year, per coin, and the stress.

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
