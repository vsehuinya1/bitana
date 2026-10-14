# UNL-SMALL: short ahead of small team unlocks (0.25–1% of supply). Pre-registration (research loop run 11, A3)

**Status.** Registered 2026-10-14 under `research/loop/PROTOCOL.md` (amendments A1–A3: 1–7 day holds), before any price
outcome around these events was computed. The A2 count used DefiLlama schedules and perp-file existence only (429
events). The passed UNL test (`reports/unl_prereg.md`) traded only team cliffs ≥ 1%, so these events are unseen.

## Mechanism
- This is UNL's mechanism, which passed its holdout on 2025–26 (+2.52% per trade, t 2.83). Locked team tokens become
  sellable at a public, scheduled date, and holders sell into the final week.
- Smaller unlocks are a new, unseen sample of the same flow. Their effect should be smaller (Keyrock: bigger unlocks hit
  harder), but they are much more frequent: monthly vesting chunks.

## Events
- **Unlock day U:** the UTC date where DefiLlama `insiders` cliffs sum to ≥ 0.25% and < 1% of the supply unlocked before
  U (UNL's `extract_unlocks.py` data in `research_cache/unl/cliffs.pkl`).
- **Exclusions:**
  - any team (`insiders`) or investor (`privateSale`) cliff ≥ 1% of the same coin within ±10 days, which would be a
    passed or reported UNL event;
  - a second small event of the same coin < 10 days after the first.
- **Perp data:** the Binance USDT perp needs ≥ 30 days of daily bars before entry and complete bars through exit
  (`edge/pdaily`, `pdaily_old`).
- **Count:** 429 events, 32 coins (CELO, LINK, APT, MANTA, GMT, OP, HFT, LQTY each 21–31); 270 are 0.5–1%.
- **Discovery:** U ≤ 2025-09-30 (≈ 59%). **Holdout:** U from 2025-10-01, exit ≤ 2026-09-30 (≈ 175 events).

## Trade (UNL's registered trade, shortened to fit A3's 7 days)
- **Short the coin's perp** from the open of U + a to the close of U + b.
  - **Window A:** [U − 6, U], 7 days ending on the unlock day.
  - **Window B:** [U − 5, U + 1], 7 days ending the day after.
- **Hedge:** long an equal-weight basket of established liquid alts over the same dates (UNL / NL definition:
  `trend/liq_mask` on the day before entry, ≥ 180 days of bars, excluding BTC and the coin).
- **Stop:** +50% on the short leg on daily highs; if the day opens above the stop, the fill is that open. The basket
  closes the same day.
- **Costs:** 30 bp round trip on the short leg, 14 bp on the basket, and actual funding on both legs. A +10 bp stress
  is reported.
- **Return per trade** = the registered UNL "edge": −short return + basket return − costs + funding.

## Control
UNL's placebo, which the protocol allows because it is the passed mechanism's own control. 5 same-coin dates per event,
drawn at random (default_rng(20261014)) among days with no team or investor cliff ≥ 0.25% within ±30 days. Same window,
hedge, stop and costs. Edge vs control = event − mean placebo.

## Grid
Window {A, B} × size {all 0.25–1%, 0.5–1% only}: 4 cells. Reported only: outright short, ETH-hedged, per year, per coin,
the stress, and positions open at once (mean, maximum).

## Gate and holdout bar (protocol + A1 + A2 + A3)
- **Gate** (all must hold):
  - discovery mean > 0, with t ≥ 2 clustered by the ISO week of U;
  - edge vs placebo > 0, with t ≥ 1.5;
  - n ≥ 100.

  The qualifying cell with the highest t goes on.
- **Holdout, chosen cell** (m = 3, t_req = Φ⁻¹(1 − 0.025/3) = 2.39):
  1. mean > 0 with t ≥ 2.39 (clustered by week);
  2. mean > 0 in 2025 Q4 and in 2026 separately;
  3. mean > 0 without the best 5%;
  4. event − placebo > 0.
