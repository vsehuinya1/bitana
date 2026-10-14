# XS-MOM-W: weekly cross-sectional momentum on liquid perps. Pre-registration (research loop run 17, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any portfolio return
was computed. The A2 count comes from the calendar and the liquid mask only: 351 Mondays from 2020-01-06 to
2026-09-21, a median of 33 liquid coins, so about 13 positions a week and over 4,000 in total. Category 3.

## Mechanism
- **Liu, Tsyvinski and Wu (2022, Journal of Finance, "Common Risk Factors in Cryptocurrency"):** coins with high past
  1–4 week returns keep outperforming those with low returns over the next weeks. That is one of three factors that
  price crypto returns.
- **Proposed drivers:** slow diffusion of information, and trend chasing by leveraged retail.
- **Closed relatives are different:**
  - Trend tests (`trend_step3`) were single-coin, 5m/15m.
  - Daily cross-sectional reversal (`mr_baseline`) was 1–7 day lookback with 1–3 day holds; losers kept losing, which
    is momentum.
  - This test is weekly, ranked across coins.

## Universe and data
- Every USDT perp in `trend/liq_mask` on the formation Monday, with ≥ 60 days of daily bars. At least 10 such coins,
  or the week is skipped.
- Daily bars from `edge/pdaily` / `pdaily_old` (on disk). Funding from cached `edge/funding_all` (2023-12 on), with
  archive fundingRate files before that.

## Portfolio
- **Formation** on Monday M (00:00 UTC): past return over L days, from the open of M − L to the open of M.
- **Long** the top 20% (rounded down, at least 2), **short** the bottom 20%, equal weight on each side.
- **Hold** from the open of M to the open of M + k days.
- **Weekly return** = (mean long return − mean short return) / 2. That is unit gross exposure, half long and half short.
- **Costs:** 20 bp round trip on every position every week (full turnover assumed), plus actual funding: longs pay
  positive rates, shorts receive them. A +10 bp stress is reported.

## Control
3 random portfolios per week from the same Monday's universe, with the same number of longs and shorts drawn at random.
Same hold, costs and funding. Seeded with zlib.crc32 of the Monday.

## Periods
- **Discovery:** Mondays before 2024-01-15 (210 weeks, 60%).
- **Holdout:** 2024-01-15 → 2026-09-21 (141 weeks).
- Bars are sliced to the split on load.

## Grid
L ∈ {7, 21 days} × k ∈ {3, 7 days}: 4 cells. Reported only: the long and short legs alone, funding, per year,
turnover, and the stress.

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery mean weekly net > 0, with t ≥ 2 (one observation per week, so it is already clustered by week);
  - edge vs random portfolios > 0, with t ≥ 1.5;
  - n ≥ 100 weeks.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 4, t_req = 2.50):
  1. mean weekly net > 0 with t ≥ 2.50;
  2. net > 0 in each calendar year of the holdout (2024, 2025, 2026);
  3. net > 0 without the best 5% of weeks;
  4. edge vs random portfolios > 0.
