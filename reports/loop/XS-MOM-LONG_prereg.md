# XS-MOM-LONG: weekly momentum winners vs the liquid universe. Pre-registration (research loop run 19, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before this portfolio was
computed. **Disclosure, post-hoc:**
- Run 17 (XS-MOM-W) found the momentum in the long leg (+1.2–1.4% a week); the short leg was about 0 after funding.
- This variant was chosen after seeing that. Its discovery period (2021-01 → 2024-01) is the one run 17 used, so the
  discovery gate is weak evidence.
- **The untouched 2024-01-15 → 2026-09-21 holdout decides** (as C-TF-MAKER in run 7). No portfolio from those weeks
  has ever been computed.

## Mechanism
- Cross-sectional crypto momentum (Liu, Tsyvinski and Wu 2022).
- Winners keep attracting trend-following and leveraged buyers. Losers are cheap to short in theory, but they squeeze
  and pay little funding, so only the winners side pays.
- The hedge is the equal-weight liquid universe, not the losers. That removes the market's move without betting on the
  losers.

## Universe, data, periods
As XS-MOM-W:
- **Universe:** `trend/liq_mask` on the formation Monday, ≥ 60 days of bars, ≥ 10 coins.
- **Data:** daily bars and funding (archive before 2023-12).
- **Periods:** discovery Mondays before 2024-01-15, holdout 2024-01-15 → 2026-09-21. Bars are sliced to the split on
  load.

## Portfolio
- **Formation** on Monday M: past 21-day return, from the open of M − 21 to the open of M.
- **Long:** the top 20% (rounded down, at least 2), equal weight, half the gross.
- **Hedge:** short every coin in the week's universe, equal weight, the other half.
- **Hold** from the open of M to the open of M + k, k ∈ {3, 7} days.
- **Weekly net** = (mean long net + mean hedge net) / 2. Each position pays 20 bp round trip and actual funding (longs
  pay, shorts receive). A +10 bp stress is reported.

## Control
3 random portfolios per week: q random coins long against the same universe hedge, same hold, costs and funding.
Seeded with zlib.crc32 of the Monday and cell.

## Grid
k ∈ {3, 7}: 2 cells. Reported only: the long leg alone, the hedge alone, per year, and the stress.

## Gate and holdout bar
- **Gate:** weekly net > 0 with t ≥ 2; edge vs random portfolios > 0 with t ≥ 1.5; ≥ 100 weeks.
- **Holdout, chosen cell** (m = 4, t_req = Φ⁻¹(1 − 0.025/4) = 2.50):
  1. mean weekly net > 0 with t ≥ 2.50;
  2. net > 0 in 2024, 2025 and 2026;
  3. net > 0 without the best 5% of weeks;
  4. edge vs random portfolios > 0.
