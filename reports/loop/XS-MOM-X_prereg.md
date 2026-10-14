# XS-MOM-X: weekly momentum winners on unseen, less liquid perps. Pre-registration (research loop run 20, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3), before any return of this
universe's portfolios was computed. The A2 count comes from volumes and the mask only: a median of 61 coins per Monday,
200 discovery weeks and 141 holdout weeks with ≥ 10 coins, over 2,000 long positions.

**Disclosure:**
- The construction is run 19's (XS-MOM-LONG), which was chosen after run 17. Both narrowly failed on the liquid
  universe (t 1.94, 1.98).
- These coins were never in those runs' universe, so the discovery here is a new sample. The construction is fixed
  exactly as registered in run 19; nothing is tuned.
- This is the loop's last run before the A3 stop rule (9 FAILs since A3).

## Mechanism
- Cross-sectional crypto momentum (Liu, Tsyvinski and Wu 2022).
- Thinner, less-followed coins should diffuse news more slowly and attract more trend chasing, so momentum should be
  stronger there.

## Universe
- Every USDT perp **not** in `trend/liq_mask` on the formation Monday M.
- Trailing 30-day median daily quote volume (base volume × close, through M − 1) ≥ $10M.
- ≥ 60 days of daily bars. At least 10 such coins, or the week is skipped.

## Portfolio (run 19's, unchanged)
- **Formation:** past 21-day return, from the open of M − 21 to the open of M.
- **Long:** the top 20% (rounded down, at least 2), equal weight, half the gross.
- **Hedge:** short every coin in the week's universe, equal weight, the other half.
- **Hold** from the open of M to the open of M + k, k ∈ {3, 7}.
- **Weekly net** = (mean long net + mean hedge net) / 2.
- **Costs:** 20 bp round trip per position, plus actual funding (cached; archive before 2023-12). The +10 bp stress
  is reported, and matters more for thinner coins.

## Control
3 random portfolios per week: q random coins long against the same universe hedge. Seeded with zlib.crc32 of the
Monday and cell.

## Periods
- **Discovery:** Mondays before 2024-01-15.
- **Holdout:** 2024-01-15 → 2026-09-21.
- Bars are sliced to the split on load.

## Grid
k ∈ {3, 7}: 2 cells. Reported only: the long leg alone, the hedge alone, per year, and the stress.

## Gate and holdout bar
- **Gate:** weekly net > 0 with t ≥ 2; edge vs random portfolios > 0 with t ≥ 1.5; ≥ 100 weeks.
- **Holdout, chosen cell** (m = 4, t_req = 2.50):
  1. mean weekly net > 0 with t ≥ 2.50;
  2. net > 0 in 2024, 2025 and 2026;
  3. net > 0 without the best 5% of weeks;
  4. edge vs random portfolios > 0.
