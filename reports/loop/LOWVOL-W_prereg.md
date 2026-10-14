# LOWVOL-W: weekly low-volatility vs high-volatility perps. Pre-registration (research loop run 18, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any portfolio return
ranked by volatility was computed. The A2 count is the same calendar and mask count as XS-MOM-W: 351 Mondays, a median
of 33 liquid coins, over 4,000 positions. Run 17 used the same universe and weeks but ranked by past return. The
volatility ranking is a different portfolio and has not been computed. Category 3.

## Mechanism
- **Low-volatility / betting-against-beta anomaly** (Frazzini and Pedersen 2014; Ang et al. 2006): investors who want
  leverage or lottery-like payoffs overpay for high-volatility assets. Low-volatility assets then earn more per unit
  of risk, and often more in total.
- In crypto, perps make leverage cheap, which may weaken the effect. That is the question.

## Universe, data, periods
As XS-MOM-W (`reports/loop/XS-MOM-W_prereg.md`):
- **Universe:** `trend/liq_mask` on the formation Monday, ≥ 60 days of bars, ≥ 10 coins.
- **Data:** `edge/pdaily` / `pdaily_old` daily bars; funding from the cache, with archive files before 2023-12.
- **Periods:** discovery Mondays before 2024-01-15, holdout 2024-01-15 → 2026-09-21. Bars are sliced to the split on
  load.

## Portfolio
- **Formation** on Monday M (00:00 UTC): realized volatility = sd of daily close-to-close log returns over the V days
  ending at the close of M − 1, V ∈ {14, 30}. At least V − 2 returns are required.
- **Long** the 20% lowest-volatility coins (rounded down, at least 2), **short** the 20% highest, equal weight on each
  side.
- **Hold** from the open of M to the open of M + k, k ∈ {3, 7} days.
- **Weekly net** = (mean long net + mean short net) / 2. Each position pays 20 bp round trip and actual funding (longs
  pay positive rates, shorts receive them). A +10 bp stress is reported.
- This is dollar-neutral, not beta-neutral: the short side has the higher beta. That is disclosed, and BTC's weekly
  return is reported beside it.

## Control
3 random portfolios per week of the same sizes from the same universe, same hold, costs and funding. Seeded with
zlib.crc32 of the Monday and cell.

## Grid
V ∈ {14, 30} × k ∈ {3, 7}: 4 cells. Reported only: each leg alone, per year, and BTC over the same week.

## Gate and holdout bar
Same as XS-MOM-W:
- **Gate:** weekly net > 0 with t ≥ 2; edge vs random portfolios > 0 with t ≥ 1.5; ≥ 100 weeks.
- **Holdout** (m = 4, t_req = 2.50):
  1. mean weekly net > 0 with t ≥ 2.50;
  2. net > 0 in 2024, 2025 and 2026;
  3. net > 0 without the best 5% of weeks;
  4. edge vs random portfolios > 0.
