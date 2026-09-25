# Possible improvements — expectancy sweep (started 2026-09-24)

Owner brief: use the live DB, shadow DB, RESEARCH_PLAN.md and the live yaml to improve expectancy. Everything is in scope:
hours, deciles, pairs, sessions, regimes, exits and sizing.
Resume point after any interruption: the first unchecked box below.

## Ground rules for this sweep (owner-confirmed 2026-09-24)
- **Objective: net R per week after costs.** A cell is cut only if it is net-negative or makes drawdown worse.
  E per trade is reported alongside, but it doesn't decide anything.
- **Output: proposed prereg rows plus ready yaml `.patch` files.** Clear-cut items are flagged for early wiring at the
  owner's discretion. Nothing is applied: no edits to `config/*.yaml`, `RESEARCH_PLAN.md` or bot-loaded code, and
  no restarts.
- **Queued rows are left to their readers.** NY-VOLZ-OFF, NEUT-STOP (Row 4), FK1–3 (Rows 5–7), PREREG-OIGATE
  (+ amendment) and Rows 8–10 (LON-BULL-NARROW, NY-ZEC, LON-DECILE). Also left alone: the other registered rows
  (LON-TAIL, Rows 1–3, LON-H9, London 1h-exit swap, BEAR-*, TUEASIA, WKNDNY). Their populations are not re-read
  and their verdicts are not pre-empted; overlaps are only noted.
- **Killed ideas stay dead unless there is new data** (decision log): TSL, tight 2–5 ATR stops on Asia, confirmed or
  delayed entry, limit entry, scale-ins as edge, HMM gate, funding leg, OI-flush long rule, age gate, V-flip veto,
  weekend tradability, Monday risk bump, LATEFADE, LNC, BEAR-B h21.
- **Data access:** DBs are read only from scratchpad copies (CLAUDE.md).
- **Units and costs:** PnL in ATR, then R at the LIVE stop (london 6 ATR, ny 5 ATR). Costs are 12 bps round-trip
  (plan standard), converted per trade to ATR via `entry_atr_pct`, and cross-checked against live measured
  slippage plus fees.
- **Floors (plan §6):** n ≥ 15 cap-3 accepted and ≥ 5 days (regime-split cells ≥ 3 days); top day ≤ 40% of net.
  G1 bar: ≥ +0.5 ATR/trade, n ≥ 20. Volume-cutting filters need a larger Δ.
- **Anti-overfit:** discovery window = shadow entries ≤ 2026-09-10; holdout = 2026-09-11 → 2026-09-24. A candidate
  survives only if the holdout direction agrees and the floors hold. Live-real legs are a sanity check, not the basis
  (n≈240).

## Checklist

### Phase 0 — setup
- [x] Copy DBs to scratchpad (live, shadow, v5_forward_test); record copy timestamps
- [x] Map shadow schema essentials (`trade_r_path` units, pnl_atr ↔ R, symbol coverage, strategy specs)

### Phase 1 — baselines
- [x] B1 Live-real baseline by arm × regime × hour × weekday × symbol (R, n, days, top-day, PF)
- [x] B2 Cost model: live entry/exit slippage + fees in bps, and in ATR/R by symbol (vs the 12 bps plan standard)
- [x] B3 Shadow live-mirror populations under CURRENT gates (london burst_follow LONG h10/11/13 bull + bear base
      hours; ny_flush_buy_1h hour/weekday lattice), re-simulated at live exits (SL6/TP3/6 bars; SL5/12 bars) from
      `trade_r_path`
- [x] B4 Reconcile B1 vs B3 (does the mirror book match live-real by cell? if not, which one is binding)

### Phase 2 — lever sweeps (discovery window, then holdout)
- [x] L1 Cost efficiency: `entry_atr_pct` floor / per-symbol cost-in-ATR (trades where 12 bps eats the edge)
- [x] L2 Pairs: demote weak live symbols; promote shadow-only symbols with n ≥ 30 per symbol (exclude ZEC-ny = Row 9)
- [x] L3 Exits: NY arm TP variants and time 12 vs 18/24 bars; london TP 2/3/4 × time 6/9 (London 1h-swap row = leave)
- [x] L4 `min_imb` per arm (0.5 → 0.7/0.8/0.9)
- [x] L5 Burst intensity: `burst_vol_zscore`, 30m volume, event-count floors
- [x] L6 `cascade_strength` floor in bull/neutral (BEAR-C is tracking-only, bear excluded)
- [x] L7 Symbol's own trend (`symbol_trend_state`) alignment with BTC regime
- [x] L8 Cluster structure: E by leg order inside a 15-min bucket → is `max_cluster_risk_pct` 15% (effective cap-2)
      leaving R on the table, or protecting it?
- [x] L9 Weekday × arm residuals not already in the lattice (london Thu etc.)
- [x] L10 Session/regime gaps not already registered (only if new structure appears; most cells are killed or registered)
- [x] L11 Sizing tilts (risk by arm/regime/cost-efficiency) for the survivors, measured in base-R units

### Phase 3 — validation
- [x] V1 Holdout check for every survivor (direction, floors, top-day)
- [x] V2 Interaction check between survivors (stacked, not additive)
- [x] V3 Net R/week projection for the combined proposal vs current book

### Phase 4 — deliverables
- [x] D1 `reports/expectancy_sweep_2026-09.md`: findings, dead ends, numbers
- [x] D2 Proposed prereg rows (plan-format text, ready to paste; not pasted)
- [x] D3 yaml `.patch` files for clear-cut items (reports/*.patch), `git apply --check` clean
- [x] D4 Final summary to owner

## Findings log (append as items complete)

- **Setup (12:24Z copies):** `trade_r_path` = side-signed ATR from entry, `r_high` = favorable / `r_low` = adverse; `post` bar 1
  duplicates the exit bar, so bar N+k = post bar k+1. Paths exist from Aug (burst_follow 66% ≥12 bars, ny_1h 96%).
- **B1 live-real (Jul-22→Sep-24):** 238 legs, −4.65R, E −0.020, PF 0.87, 32 days. Current arms: london burst_follow n=110 −0.84R;
  ny_1h n=40 +0.87R. Retired ny_4h −5.60R/48, asia +0.92R/40.
- **B2 costs:** fees 10.0 bps round-trip (taker = 5 bps/side, not the yaml's 4); median total 11 bps (plan's 12 is right).
  In ATR: BTC ≈1.09, ETH ≈0.57, SOL/XRP ≈0.4, alts 0.15–0.25 per leg at 12 bps. ETH/BTC legs are cost-heavy.
- **B3 mirror (current gates, live exits, 12 bps):** london n=217/13d net +13.5R E+0.062 PF1.94 top 29%;
  ny n=325/15d net +11.5R E+0.035 PF1.24 top 57%.
- **B4 live vs shadow:** twin coverage 60%. Twinned live legs +0.133 ATR/leg net (n=90); UN-twinned −0.343 (n=60).
  London un-twinned −0.451 ATR/leg (n=45, −20.3 ATR) is the whole live-vs-shadow gap. Live and twin gross prices agree
  (median gap 0.00 ATR), so execution is not the problem; the shadow book is missing a class of live legs.
  So pre-parity shadow E overstates the live arms. Untwinned legs = sustained one-sided bursts (median burst age 5 bars vs 2,
  imb30 15m earlier 0.98 vs 0.76, vol_z 0.56 vs 1.55). A "recent imbalance flip" filter is NOT supported (london flip legs +0.02 vs −0.20).
- **L1 cost:** dead. Gated removed sets are positive (London cost>0.35 +2.09/+0.19R, NY +2.71/+3.18R).
- **L2 pairs:** watch row PREREG-NY-PAIRS (ENA n=15 +4.08R, AAVE n=11 +2.75R, both positive in both windows; WLD-ny n=11
  −1.63R). All are below n≥30, and `symbols.active` is global (London expansion lowered R in the sim).
- **L3 NY exits:** keep 12 bars/no-TP (best holdout ex-Sep-23). London exits are already covered by registered rows.
- **L4 NY min_imb 0.9:** SURVIVOR → PREREG-NY-IMB90.
  - Family removed n=279/24d −21.7R, top 28%, both windows and both regimes.
  - Gated removed n=54 −4.47R, but worst day 77% (Sep-23) → fails §6.
  - Live 1h: <0.9 n=10 −1.99R.
  - Sim: +0.45 R/wk, DD −2.69 → −1.87.
- **L5/L6/L7:** dead (removed winners or era-inconsistent).
- **L8 cluster:** later legs are not worse, so keep the 15% budget (it protects knife days).
- **L9 weekday:** noise (1–3 days per cell).
- **L10 scan:** 79 cells, none positive in both windows with top ≤40% after costs.
- **L11 sizing:** no tilt clears the floors; revisit after a parity-era month.
- **Extra dead ends:** fixed entry delay; recent-flip filter; London vol_z<0.5 (the bleed sat in hours already blocked).
- **V1–V3:** in the report (S0 1.81 R/wk → S3 2.26; with pairs ~2.44).
- **D1–D3:** `reports/expectancy_sweep_2026-09.md`, `reports/expectancy_ny_imb90_{yaml,mirror}.patch`. Both patches
  `git apply --check` clean; parity + cluster suites 21/21 with both applied.
- **STATUS 2026-09-24 12:4xZ: sweep COMPLETE. Nothing applied; the owner decides on NY-IMB90 (G0 dark vs early wire)
  and the pairs row.**
- **REVIEW 2026-09-24 ~13:15Z (external model critique, verified against data):**
  - **Withdrawn:** the untwinned-cost headline (era/unit-confounded), the NY-IMB90 early-wire option and +0.45 R/wk
    projection (live support is 1 day, Sep-23), and the PARITY-RECON row.
  - **Kept:** NY-IMB90 as a dark row (live-real binds, read after NY-VOLZ-OFF), the NY-PAIRS watch, the dead-end list,
    the cost measurement, and the mirror patch.
  - **Refuted from the critique:** "same bucket" (the gap persists in the SL6 era); "same legs counted twice"
    (cross-tab: 9/10 NY low-imb legs twinned); "vol_z window" (all low-imb legs have vol_z ≥ 0; the bucket starts after
    the Sep-12 D1 unlock).
  - **NEW:** the WLA mirror ignores `min_n_confirms`, and it's ACTIVE: 19.5% London / 29% NY WLA=1 rows have
    n_confirms=0, while live has 0 of 186. Latent too: `min_imb`, `min_cascade_strength`, `max_regime_age_bars`, and
    the burst floors. Infra row candidate.
- **2026-09-24 ~14:05Z — gate-complete mirror patch DRAFTED (not applied):** `reports/wla_gate_complete.patch` + `reports/wla_gate_complete_notes.md`.
  - **Binds:** `min_n_confirms` (active) and the 4 latent gates. `n_confirms` is written at insert (trades + pending).
  - **Tests:** a new test enforces that every engine gate is mirrored, using the engine `evaluate()` as oracle. Negative
    control: 10/11 fail on HEAD. Full suite 91 pass / 3 pre-existing fails.
  - **Pending:** the other agent's review, then owner apply + v5-paper restart = MIRROR CUT-OVER 3.
- **2026-09-24 ~18:10Z NY hold-length study (owner Q: "when to hold 4h vs book early"): DEAD END, keep the 1h exit.**
  - **Average:** gated NY n=325/15d: 1h +0.035 vs 4h −0.109 R/leg (Δ −0.144, both windows). 4h wins 7/15 days, but its
    losing days are 2–3× larger (Aug-28 −30.6R, Sep-23 −18.1R). Stop-outs double (7% → 14%) at SL5.
  - **Pre-entry predictors:** none of the 13 fixed features favours 4h in both windows and both books. The only positive
    pocket (BTC rv24 > 0.148) is ONE day (Aug-21).
  - **In-trade "extend winners at 1h":** loses at every threshold (+0 to +2 ATR), with or without a BE stop, both windows,
    both books.
  - **Earlier booking:** TP +1/+1.5/+2 ATR and 30/45m exits are no better than 60m.
  - **Consistent with the Sep-6 G1 read** (1h beats 4h by +0.115/leg paired) and the retired live 4h arm (−5.60R/48).
- **2026-09-24 ~18:40Z "3 biggest levers" pass: two more dead ends, plus a logging gotcha.**
  - **Day-stop dead.** "No new entries after −1/−1.5/−2R": the first test (+7R) was LOOK-AHEAD (counted open legs).
    Realized-only: current arms +3.23 → +2.82/+3.30/+3.30R, i.e. no gain. Loss days are simultaneous cluster legs.
  - **Resting limit TP dead.** `exit_slippage_bps` is logged as abs() (position_manager.py:362). Signed, close-checked
    exits fill BETTER than trigger: TP 73% (+15 bps), stops 92% (+0.17R/stop leg). A resting TP or stop would be worse.
    **Never use the logged slippage fields; recompute signed from prices.**
  - **Note:** `place_stop_order` (STOP_MARKET) is defined but never called, so there's no exchange-resident stop. That's
    tail risk if the bot is down, not an E item.
- **2026-09-25 ~04:5xZ weekend + late-session setup search (owner Q):** ~91 tests. The scan found 0 passing of 77 cells
  (53 weekend, 24 late). Then the existing arms were cloned into each window (7 setups × 2 exits).
  - **REGISTERED 2026-09-25 04:4xZ (owner order) as PREREG-LATE-BULL-FLUSH in RESEARCH_PLAN.md (committed 9d6d2ef).** burst_follow LONG, Mon–Fri 22–24Z, bull, live gates, SL5 / 60m /
    no TP. n=31/11d, E +0.123 net, halves +0.095/+0.161, 9/11 days positive, both hours positive, +0.081 at 20 bps.
    **FAILS concentration** (Aug-24 = 59%; ex-top-day +0.054/29).
  - **Weekend London-hours flush-buy (60m): fragile, no row.** n=120/9d E +0.052 but h13 = 106% of net, top-3 symbols 114%,
    break-even at 20 bps.
  - **Avoid:** late squeeze-shorts (−0.06/−0.08 R/leg); weekend 22–24Z LONG (−0.19/−0.21).
  - **Registered/killed, left alone:** WKNDNY (Oct-4), weekend tradability (killed), LATEFADE (killed).

## 2026-09-25 — Neutral-regime fresh look (owner order: all sessions; retest kills with own metrics; bring theses + yaml suggestions)
- [x] N0 fresh shadow copy; neutral occupancy by day/session; live-real neutral legs by arm
- [x] N1 retest kills: LATEFADE, NY neutral h14-15 widen, NY neutral h18-20, London neutral exclusion, asia_burst_fade,
      age gate (neutral 24-48h), Tue-neutral exclusion, NY Monday blackout (neutral), weekend (neutral)
- [x] N2 neutral scan: session × hour-band × side × strategy family (own exits + live-exit re-sims), halves + concentration
- [x] N3 exits in neutral (hold/stop/TP) for surviving cells; pending NEUT-STOP not pre-empted
- [x] N4 portfolio check under live caps for any candidate; yaml patch(es) + prereg rows; report
- Pending rows NOT pre-empted: NY-NEUT-KEEP, LON-NEUT-H13, ASIA-PUMP-NEUTRAL, NEUT-STOP, FK1-3, TUEASIA
- **Neutral fresh look DONE (report: reports/neutral_regime_fresh_look_2026-09-25.md):**
  - **Kills:** every killed or invalidated neutral theory's kill is JUSTIFIED on my metrics.
  - **Live cell endorsed:** NY h16–17 Wed–Fri, E +0.473 (28 legs/5d); 60m is the best hold.
  - **Scan:** 0 of 120 cells pass. The 24h near-miss is mostly beta (corr +0.63, β-adjusted ≈ +0.15 R/leg).
  - **Yaml:** no neutral change recommended. The next neutral arm = Asia pump-short (Row 3 pending; live-real +1.14R/37).
  - N4 portfolio check was not needed (no candidate).

## 2026-09-25 — London loss review → PREREG-LON-BULL-FADE (Row 11) registered (owner order "Register.")
- **Sep-25 London live:** 8 legs −1.47R (1 TP, then 5 time-exit losers, no stop hit). Account drawdown 9.6% → 24.4% (1R ≈ 10% of equity).
- **Fade condition** (bull, 4h ADX down ≥ 4 over 3 closes): shadow basis n=39/3d E −0.072 @20bps vs control +0.074 (n=158/11d).
  Top-day 76% → watch row only, no cut. Live-real: fade −0.056/leg (n=43/4d) vs control +0.001 (n=75/10d).
- **Reader:** `research/lon_bull_fade_reader.py` (`--validate` PASS); research-board row added. Forward from 2026-09-25T14:40Z.
- **Live ops:** Claude Code live risk watch (session-scoped) sends pre-London/pre-NY briefs at 08:40/13:40 UTC with the fade flag, plus intra-session alerts.

## 2026-09-25 ~16:xxZ — Cross-arm market-state scan ("gems", owner ask)
Paper legs at current gates + live exits, 20 bps (London bull 203, NY bull 329), 25 features × 2 arms. A pattern had to
hold in both bull runs (Aug 19–29, Sep 18–25) and be checked against live. Post hoc: ~50 comparisons, so leads only; nothing wired.
- **NY breadth:** flushes with >20 symbols liquidating at once make +0.085/leg (96 legs, 7/8 days, both runs); ≤20 lose −0.025 (233 legs).
  Not FK2 in disguise (corr 0.67): within FK2's pass zone broad +0.077 vs narrow −0.036. Live twin-matched (thin): broad(13+) +0.20/11 vs narrow −0.08/21.
  London points the same way (>12 symbols +0.076, 7/9 days, vs ≤6 −0.003).
- **London is front-loaded in a bull:** bull days 1–3 paper +0.113 (44 legs, 5/5 days), live at current hours +0.130 (SL6 era +0.204).
  Fading day 4+ loses in both books (Row 11). Non-fading day 4+ is disputed (paper +0.065 6/6d vs live −0.044). NY is the reverse (fine late).
- **NY and BTC's 24h move:** paper rises steadily −0.021 (<−1.5%) → +0.074 (>+1%); live the same way (−0.004 → +0.274, thin).
  Red day + narrow flush −0.053 (1/4 days); non-red day + broad flush +0.249 (5/5 days).
- **Dropped:** vol_z (books contradict; NY-VOLZ-OFF reads Sunday); ADX level (no floor or ceiling holds).
- **REGISTERED 2026-09-25 16:xxZ (owner order "Register everything"):**
  - Row 12 NY-BREADTH (dark, bull only; veto or sizing-tilt fallback) and Row 13 NY-KNIFE (dark, bull), reader `research/ny_flush_quality_reader.py`, forward from 16:30Z.
  - Row 11 amendment: report-only age split in `research/lon_bull_fade_reader.py`.
  - Registration caveats: the narrow-flush loss is Sep-23-driven (ex-Sep-23 narrow +0.041 vs broad +0.219), the knife cell is essentially one day (Aug-28), and the neutral cell's narrow flushes make +0.458/leg, hence the bull-only scope.
