# Expectancy sweep: 2026-09-24

Owner brief: improve net R/week using the live DB, shadow DB, RESEARCH_PLAN.md and the live yaml (hours, deciles, pairs,
sessions, regimes, exits, sizing). Output: proposed rows and patches only. Nothing is applied, and rows already queued
for Sun Sep-27 are not re-read. Checklist and resume point: `possible_improvements.md`.

## Corrections after review (2026-09-24 ~13:15Z): these supersede the sections below
- **Headline "untwinned legs are what's costing money": WITHDRAWN.** The −20.3 ATR figure mixed stop eras and units;
  most of it (−15.5 ATR) is the closed SL10 era.
  - London live by stop era: SL10 n=68 −1.97R PF 0.66; **SL6 (Sep-7+) n=42 +1.13R PF 1.25**.
  - The twinned/untwinned gap does persist inside SL6 (untwinned −0.042 vs twinned +0.084 R/leg, n=19/23), but it's
    small and thin. It becomes a calibration note only; the PARITY-RECON row is dropped.
- **Untwinned vs low-imbalance overlap, now cross-tabbed.** These are not the same legs. 9 of the 10 NY live
  imb<0.9 legs were twinned. London untwinned losses are in high-imbalance legs (−2.07R/24).
- **NY-IMB90 live-real "support" was one day.** The live imb<0.9 bucket is n=10, −1.99R, of which Sep-23 is 8 legs,
  −2.07R (104%). Ex-Sep-23 it's 2 legs, +0.08R. That's below every floor in the binding book.
  - **Early-wire option: WITHDRAWN.** The +0.45 R/week sim (shadow book, Sep-23-driven) is **withdrawn** as a
    projection.
  - The row stays **dark only**, promote on parity-era **live-real** n≥30/5d, top ≤40%, read after NY-VOLZ-OFF.
  - The first live low-imbalance leg is Sep-18, after the Sep-12 D1 unlock. All 10 legs have vol_z ≥ 0.
- **Multiple comparisons (undisclosed before):** about 36 filters × 2 arms × 2 populations were scanned. The
  "both windows agree" guard is weak at that breadth; NY-IMB90 was selected from that scan.
- **Labels:** the live book excluded COMPRESSION (4 legs, −2.98R); all engines = 242 legs, −7.63R. Bear cells ARE
  wired and trade at the flip: NY h14/15/20 at SL10, London base hours [9,10,11,13] LONG at SL6. There's no forward
  data yet.
- **NEW, found while verifying the review: the WLA mirror does not apply `min_n_confirms`, and the gap is ACTIVE.**
  - Since Aug-21, n_confirms=0 is on 19.5% of London WLA=1 rows (275) and 29% of NY WLA=1 rows (108), via the
    burst_snapshots join. Live has 0 of 186 legs with n_confirms=0 (live yaml `min_n_confirms: 1`).
  - This contradicts audit G10e ("near-vacuous, unverified").
  - Book E barely differs (NY +0.033 vs +0.052), but WLA-based reads over-count legs live rejects.
  - Also unbound but latent (values equal today): `min_imb`, `min_cascade_strength`, `max_regime_age_bars`, and the
    burst floors (20k/3 hard-coded in the ShadowStrategy specs).

## Bottom line (numbers first; superseded where the corrections above say so)
- **Live-real book:** 238 legs, −4.65R, PF 0.87. The current arms are roughly flat: London −0.84R/110, NY 1h +0.87R/40.
  - **Where the losses sit:** live legs **without a shadow twin** (40% of legs): −0.343 ATR/leg, against +0.133 for
    twinned legs. London untwinned alone is −20.3 ATR (n=45).
  - **Consequence:** pre-parity shadow estimates of the live arms are upper bounds.
- **One lever survives:** NY `min_imb` 0.5 → 0.9 (**NY-IMB90**).
  - Portfolio sim under live caps: +0.45 R/week (1.81 → 2.26) and max drawdown −2.69 → −1.87R.
  - Better in both windows and both regimes.
  - It fails the §6 concentration bar on the live-gated book (Sep-23 = 77%), so it's a G0 row with an **early-wire
    option at owner discretion, not a clear-cut wire**.
- **One watch row:** NY pairs. Promote ENA and AAVE, demote WLD-ny. The combined upside is about +0.6 R/week, but it's
  below the per-symbol n≥30 floor and `symbols.active` is global (see below).
- **Everything else is a dead end**, recorded below so it isn't re-mined: costs, burst intensity, cascade, symbol trend,
  NY exits, cluster budget, weekday, fixed delay, flips, and a 79-cell session×regime scan after costs.

## Method
- **Books:**
  - London = shadow `burst_follow` LONG, NY = `ny_flush_buy_1h` LONG.
  - Current live gates are applied through `config.loader` (`hour_gate_reason`, weekday, regime, side, vol_z, decile).
  - Exits are re-simulated at the live settings from `trade_r_path`: London SL6/TP3/6 bars, NY SL5/12 bars (bear SL10),
    stop-first.
  - `post` bar 1 duplicates the exit bar. That was verified on 400/400 legs.
- **Costs:** 12 bps round-trip (plan §2), converted per leg to ATR via `entry_atr_pct`.
  - Measured live costs: fees 10.0 bps (5/side), median total 11 bps.
  - In ATR per leg: BTC ≈1.09, ETH ≈0.57, SOL/XRP ≈0.4, alts 0.15–0.25.
- **Anti-overfit:** discovery ≤ Sep-10, holdout Sep-11 → Sep-24. Survivors must agree in both windows and pass §6 floors.
- **Portfolio sim:** live caps (8 concurrent, 1/symbol, cluster cap 3, 15% cluster risk budget at 10%/leg giving sizes
  1.0/0.5/0), averaged over 20 random same-bar orderings; 4.86 weeks (Aug-21 → Sep-24).
- **Limitations:**
  - The bear regime has zero rows since Aug-17.
  - Only ~1 day of parity-era data exists.
  - Shadow-based levels are optimistic (B4), so compare scenario **deltas**, not levels.

## Baselines
| Book | n | days | net R | E (net R/leg) | PF | top-day |
|---|---|---|---|---|---|---|
| Live-real all arms (Jul-22→Sep-24) | 238 | 32 | −4.65 | −0.020 | 0.87 | — |
| Live-real London burst_follow | 110 | 12 | −0.84 | −0.008 | 0.92 | — |
| Live-real NY 1h | 40 | 6 | +0.87 | +0.022 | 1.15 | — |
| Shadow London, current gates, live exits | 217 | 13 | +13.46 | +0.062 | 1.94 | 29% |
| Shadow NY, current gates, live exits | 325 | 15 | +11.46 | +0.035 | 1.24 | 57% |

- **Live vs shadow (B4).** Twin coverage is 60%.
  - Twinned live legs: +0.133 ATR/leg net (n=90). Untwinned: −0.343 (n=60). London untwinned −0.451 (n=45).
  - Execution is not the gap: live-vs-twin gross price gap median 0.00 ATR.
  - Features agree: same-bar twins have identical vol_z, imbalance and decile (corr 1.000). Only `cascade_strength`
    differs (corr 0.75, daily-liq-cache source).
  - The gap is **population**: live fires on bars the pre-parity shadow grid skipped. The detector-parity fix
    (2026-09-23T12:36:54Z) now captures them, so parity-era reads will be lower and truer.
- **Current config already removed most live London bleed.** Live London h9 (n=22, −1.50R) is blocked by LON-H9. The
  live low-vol_z bleed (vol_z<0.5, −17.1 ATR/36) sits in h9/h8/h12. In current hours the same bucket is +2.8 ATR/23.

## Lever results (removed-set net R: discovery / holdout, live-gated books)
| Lever | London gated | NY gated | Verdict |
|---|---|---|---|
| Cost filter (cost>0.35 ATR) | +2.09 / +0.19 | +2.71 / +3.18 | **dead** (removes winners) |
| **min_imb <0.9** | +0.70 / +1.44 | **−0.75 / −3.72** | **NY candidate** (London: no) |
| 30m burst volume/events floors | positive | inconsistent | dead |
| burst_vol_zscore <0 / <1 | +0.70 / +4.47 | +8.44 / −3.87 | dead |
| Burst age ≥3 / ≥6 / =0 | positive | inconsistent | dead |
| Cascade floor 0.1/0.2/0.4 | positive | inconsistent | dead |
| Symbol's own trend ≠ bull | +1.57 / +0.97 | +10.68 / +8.03 | dead (removes winners) |
| Recent imbalance flip | −0.45 / +2.39 | −0.78 / −7.53 (top 113%) | dead (London); NY = Sep-23 |
| Depth/spread floors | positive | inconsistent | dead |
| Weekday cells | 1–3 days each | 1–3 days each | noise |
| Fixed entry delay 1–3 bars | +1 bar: +0.025/+0.006 R/leg | inconsistent | dead (delayed-entry family killed Aug-22) |
| NY exits (TP 2–6 × hold 12/18/24) | — | current 12 bars/no-TP best in holdout ex-Sep-23 (+9.99R); TP4 +2.4 disc / −2.3 hold | **keep current** |
| Cluster budget (leg rank 4+ E) | +0.075 / +0.110 | +0.191 / −0.085 (Sep-23) | keep 15% (protects knife days) |
| 79-cell strategy×session×side×regime scan (net 12 bps) | — | — | **no untapped cell**; best near-miss Asia bull LONG +0.010 R/leg |

## Proposed rows (paste-ready; NOT pasted into RESEARCH_PLAN.md)

### PREREG-NY-IMB90: NY arm liquidation-imbalance floor 0.5 → 0.9 (proposed 2026-09-24, expectancy sweep)
- **Claim:** NY flush-buys on a mixed liquidation tape (0.5 ≤ imb30 < 0.9, meaning ≥5% of 30m liquidated notional was
  shorts) have negative net expectancy at the live exit. One-sided flushes (≥0.9) carry the arm.
- **Mechanism:** the arm buys forced-seller exhaustion. On a two-sided tape both sides are being stopped out (chop), so
  there is no one-sided seller exhaustion to buy.
- **Basis (disclosed; this data is pre-registration):**
  - **NY family** (`ny_flush_buy_1h` LONG, imb≥0.5, Mon–Fri, all NY hours and regimes, live exit, 12 bps). Removed lane:
    n=279, 24 days, ΣR −21.7, E −0.078, PF 0.58, worst day 28%.
    - By window: discovery n=165 E −0.067, holdout n=114 E −0.093.
    - By regime: bull −0.023/−0.073, neutral −0.109/−0.126.
    - Thresholds 0.7–0.9 are all negative (plateau 0.85–0.9).
  - **Live-gated book:** removed n=54/12d, ΣR −4.47, E −0.083, PF 0.57. **Worst day 77% (Sep-23 −3.45R): fails §6.**
    Ex-Sep-23: n=36 E −0.028.
  - **Live-real 1h arm:** imb<0.9 n=10 −1.99R vs ≥0.9 n=30 +2.86R.
  - **Portfolio sim:** +0.45 R/week, max DD −2.69 → −1.87R, both windows up (disc +5.93→+6.77, hold +2.87→+4.23,
    hold ex-Sep-23 +5.56→+6.09).
- **Prior-decision check:** Aug-24 "keep 0.5" was London-bull only (n=193). Audit G10a kept 0.5 as the signal
  definition, not on performance. Not a relitigation.
- **Overlap disclosure:** Sep-23 is the FK1–3 / NEUT-STOP knife day. FK3 also reads `liq_imb`, at the opposite ≥0.97
  end. **Do not read before the Sep-27 FK / NEUT-STOP / NY-VOLZ-OFF verdicts land.**
- **Dark read (no wiring needed):** parity-era (≥2026-09-23T12:36:54Z) raw `ny_flush_buy_1h` rows under current live
  gates. Compare lane 0.5≤imb<0.9 against ≥0.9 at live exits, 12 bps. At ~3.6 lane legs per NY day, n≥30 takes about
  2 weeks.
- **Promote bar:** lane E_net < 0 at n≥30 over ≥5 distinct days with top-day ≤40%, AND kept lane E_net ≥ the
  lane-inclusive baseline.
- **Kill:** lane E_net ≥ +0.03R at n≥30. The cut would be removing winners, so close the row and keep 0.5.
- **Wire (on promote, or owner early-wire):** `ny.min_imb: 0.9` plus the WLA-mirror `min_imb` patch, in the same dual
  restart (live + v5-paper). Patches: `reports/expectancy_ny_imb90_yaml.patch`, `reports/expectancy_ny_imb90_mirror.patch`.
  Without the mirror patch, NY WLA=1 rows would include legs live rejects (the vol_z-bug class).

### PREREG-NY-PAIRS: watch (dark): ENA/AAVE promote, WLD-ny demote (proposed 2026-09-24)
- **Basis:** NY live-gated book per symbol, with live gates applied to every shadow-tracked symbol.
  - **ENAUSDT:** n=15/10d, ΣR +4.08, E +0.272 (disc +0.407/7, hold +0.154/8), top 33%.
  - **AAVEUSDT:** n=11/7d, ΣR +2.75, E +0.250 (+0.400/6, +0.071/5), top 36%.
  - **WLDUSDT (live):** n=11/8d, ΣR −1.63, E −0.149 (−0.104/6, −0.202/5).
  - All are below the backlog's per-symbol n≥30 floor.
  - Adding every shadow symbol to NY: +0.65 R/week in the sim, but discovery-heavy (non-live fills disc +4.72 / hold
    +0.52R). So only symbols positive in both windows qualify.
- **Constraint:** `symbols.active` is global. Adding ENA/AAVE also arms London, where universe expansion *lowered* net R
  in the sim (8.79 → 8.36R, cluster-slot competition). Either build a per-arm symbol scope (the loader has none) or
  accept London exposure (London ENA n=11 E +0.033, AAVE n=3 E −0.045).
- **Promote bar (per symbol):** parity-era NY gated n≥30, ≥5 days, E_net ≥ +0.05, top-day ≤40%.
- **Demote bar (WLD-ny):** n≥30, E_net ≤ −0.02, top ≤40% → per-symbol veto (same knob question as Row 9 NY-ZEC; prereg
  the exact knob before wiring).
- **Kill:** promote candidates E_net ≤ 0 at n≥30.

### PARITY-RECON: measurement item (no wiring)
- All pre-parity shadow estimates of the live arms are upper bounds (B4: untwinned live legs −0.343 ATR/leg).
- At the Oct-4 loop, compare parity-era shadow arm E against live-real per arm (n≥30/arm). Any arm whose parity-era
  shadow E ≤ 0 goes to its own row before new cuts are layered on it. This complements LON-TAIL (parity-era only).

## Session × regime answer (how to trade each)
- **Bull:**
  - London h10/11/13 LONG: gated shadow +0.062R/leg; live since LON-H9 ≈ breakeven-positive in the kept hours.
  - NY h14–20 lattice minus the weekday exclusions, plus the NY-IMB90 candidate.
- **Neutral:** NY h16–17 only (Row 1 endorsed). The NY-IMB90 removed lane is worst in neutral (−0.109 / −0.126 R/leg).
  London, Late and Asia have no cell that is positive after costs (Asia = Row 3 conditional).
- **Bear:** no data since Aug-17. The pre-registered dormant cells stand; nothing new is registrable.
- **Sizing:** no tilt clears the floors on current data. Revisit after a parity-era month (plan: MDP sizing only after
  ≥2 months of live PnL).

## Notes
- **`fees.taker_bps: 4.0`** vs a measured 5.0/side. This only affects the paper executor and replay tools (not live), so
  replays understate costs by about 2 bps round-trip. Correct it in the tools when they're next used; no live change.
- **Weeks per result:** everything here spans 4.86 weeks and 13–15 arm-days. Treat R/week deltas as ±50% estimates.

## Artifacts
Scratchpad scripts (reproducible from the 12:24Z DB copies): `build.py`, `lib.py`, `fofeat.py`, `b1`–`b5*.py`,
`prep.py`, `sweep*.py`, `pairs.py`, `exits.py`, `cluster.py`, `delay.py`, `scan.py`, `port*.py`, `imbval.py`, `v1.py`.
Patches: `reports/expectancy_ny_imb90_yaml.patch`, `reports/expectancy_ny_imb90_mirror.patch`. Both pass
`git apply --check`, and the parity + cluster suites pass 21/21 with both patches applied.
