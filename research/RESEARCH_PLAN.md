# Bitana Research Plan

_Last updated: Sun 2026-08-16. Live config: v1.1.1 (`bf434ed`; Asia neutral-only + NY h16-17 pilot uncommitted). Register expanded to max 10._
_Companion prompt for deep research sessions: `research/QUANT_RESEARCH_PROMPT.md`._
_Weekend block artifacts: `research/output/reports/stop_variant_first_read_2026-08-01.csv`, `hmm_oos_validation_2026-08-01.csv`, `hmm_oos_decision_2026-08-01.json`, `weekend_aug1_bundle.json`. Runner: `research/weekend_aug1_analysis.py`._

Single source of truth for what we test, when, and how results get promoted into
(or killed out of) the live book.

## Ground rules

1. Objective: understanding > backtest performance. Every apparent edge is presumed
   false until it survives out-of-sample.
2. Standard evaluation: **candidate-specific** cap-3 portfolio sim, 1 per symbol,
   12 bps round-trip costs, PnL in ATR (1 ATR ≈ 0.8% equity at 8% risk / 10 ATR stop).
3. No lookahead: regime and HMM labels from completed 4h bars only; models frozen
   before their evaluation window (HMM walk-forward: train ≤ month-end, filter forward).
4. Pre-registration: hypothesis, mechanism, test window, and pass/fail criteria are
   written in this file BEFORE outcomes are computed.
5. Selection windows never overlap evaluation windows.
6. Sample floors: no conclusion on < 15 cap-3 accepted trades or < 5 distinct days
   (regime-split cells: ≥ 3 days). Top day may contribute ≤ 40% of net.
7. No hard cap on active hypotheses, but each must be pre-registered with a kill
   criterion. Live config changes ship at week boundaries (Mondays), except
   safety cuts.
8. **Research integrity:** do not use the shared historical `would_live_accept`
   column for promotion decisions on trades opened before the Jul 23 fix. From
   Jul 23 onward, `would_live_accept` is strategy-scoped (only that strategy's
   previously accepted opens count). Offline analyses must still recompute
   candidate-specific acceptance when mixing pre/post windows.

## Promotion ladder

| Gate | Meaning | Objective criteria |
|---|---|---|
| G0 | Idea | Pre-registered: claim, mechanism, window, criteria |
| G1 | In-sample candidate | cap-3 net/trade ≥ +0.5 ATR, n ≥ 20, ≥ 5 days |
| G2 | OOS-validated | ≥ 1 week AND ≥ 15 accepted OOS; net/trade ≥ +0.5 (volume-cutting filters: ≥ +1.0); concentration + cost checks pass |
| G3 | Live pilot | 1–2 weeks at 4% risk; slippage ≤ 1.5× modeled; live/shadow fill agreement ≥ 90% |
| G4 | Full size | 8% risk |

Kill at any gate: OOS net/trade < +0.2 ATR after 30 accepted, mechanism disproven,
or day-concentration fails. Killed ideas go to the decision log below.

## Dated timeline

### Week of Jul 20 (go-live week)

- **Mon Jul 20 — go-live day 1.** Fund check, first-fill reconciliation, live vs
  shadow `would_live_accept` agreement, age throttle armed (4% if neutral > 64h).
- **Sun Jul 19 —** 4/6/8 ATR stop variants deployed to `SHADOW_STRATEGIES`
  (logging only): `asia_pump_short_4h_s{4,6,8}`, `ny_flush_buy_4h_open_s{4,6,8}`.
- **Wed Jul 22 — live session retune shipped.** Asia remains
  `asia_pump_short_4h` at 48 bars, neutral+bull only, weekends off.
  NY switches to full-session `ny_flush_buy_4h`, neutral+bear only, Mon/Sat/Sun off.
  Blind Asia 36-bar cut rejected (MFE peak ≠ executable exit).
- **Thu Jul 23 — live fill accounting fix + weekly cooldown removed.** Binance
  market fills without `avgPrice`/`cumQuote` now resolve via `/fapi/v1/userTrades`.
  Corrected Asia closes: ETH −0.148R, SOL +0.094R (near flat). Weekly loss/cooldown
  path deleted from runtime. Live/shadow fill integrity returns to daily monitoring.
- **Thu Jul 23 — research instrumentation.** Candidate-specific cap-3
  `would_live_accept`; full-session NY stop ladder
  `ny_flush_buy_4h_s{4,6,8}` added to shadow (pairs with live book, not open-window).
- **Fri Jul 24 – Sat Jul 25 — HMM OOS validation #1** (spec below). *Not computed that weekend; executed Sat Aug 1 with frozen train ≤ Jun 30.*
- **Sun Jul 26 —** weekly review #1 (loop below).

#### HMM OOS validation #1 (pre-registered Jul 18) — **RESULT Aug 1: KILL**

- Frozen model: 6-state HMM trained ≤ Jun 30. **No retrain before this test.** ✓ held.
- Primary hypothesis: Asia entries during HMM states {H2, H5} outperform; other
  states are skippable. Filter was selected on Jul 1–17 data.
- Evaluation window: Jul 18 00:00 → Jul 24 24:00 UTC (fully out-of-sample).
- Metrics: (a) net/trade of in-state cap-3 Asia trades; (b) net PnL of trades the
  gate would have blocked; (c) mean filtered confidence on traded bars.
- **PASS** → G3 pilot (Asia HMM gate at 4% risk): in-state
  net/trade ≥ +1.0 AND blocked net ≤ 0 AND confidence ≥ 70%, on ≥ 15 accepted.
- **EXTEND** one week: < 15 accepted in-state, or mixed.
- **KILL**: in-state net/trade < +0.2 on ≥ 15 accepted, or blocked trades netted > +10 ATR.
- Secondary (report only): H5-only variant; confirm NY H4/H5 still shows no benefit.
- Scope note: live Asia book trades neutral+bull without HMM gate. This test was
  incremental value of H2/H5 only.

**Computed Aug 1 (live-like Asia filter: weekday + neutral/bull; candidate cap-3; 12 bps):**

| Window | Filter | in_cap3 | days | avg_net | sum_net | blocked_sum | conf | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|
| primary Jul18–24 | H2+H5 | 11 | 3 | **−2.764** | −30.40 | −4.10 | 85% | EXTEND (n<15) |
| extended Jul18–31 | H2+H5 | 15 | 5 | **−1.915** | −28.73 | −3.22 | 83% | **KILL** (avg < +0.2 on n≥15) |
| post-test Jul23–31 | H2+H5 | 6 | 3 | −0.410 | −2.46 | +1.95 | 66% | thin / no rescue |
| Jul1–17 selection (ref) | H2+H5 | 37 | — | +1.808 | +66.91 | — | — | in-sample only |

- Day driver on OOS: **2026-07-21 ≈ −30.6 ATR** dominates H5 bucket (top-day share ≫ 40%).
- H5-only mirrors H2+H5 (H2 almost empty OOS). NY H4+H5 secondary: still no benefit
  (cap3=3, avg≈−1.06 on both full-session and open books).
- **Decision: KILL Asia HMM {H2,H5} gate for live.** Selection-window edge did not
  transfer. Do not ship G3 pilot. Keep frozen Jun30 model only as research reference.
- **Aug 1 retrain: DEFER for gate use.** No live HMM path. Optional research-only
  train≤Jul31 snapshot may be versioned later; must not feed live gates without a
  new pre-registered OOS protocol.
- Artifacts: `research/output/reports/hmm_oos_*_2026-08-01.*`

### Week of Jul 27

- Semi-Markov age throttle validation #1: did >64h-neutral trades underperform as
  predicted, live and shadow?
- Asia partial-profit rule (take 50% when ≥ +2 ATR at 1h) OOS check on Jul 18+ data —
  measurement only; not an active promotion hypothesis.
- London/Late: begin OOS clock only if continuous quality-floor fills appear;
  instrumentation is already active.
- **Sat Aug 1 —** HMM monthly retrain deferred (see KILL above). OOS #1 executed.

### August

- **Sat Aug 1 / Sun Aug 2 —** stop-variant first read **DONE** (Asia s4/s6/s8 from
  Jul 20; full-session NY s4/s6/s8 from Jul 23). ≥5 distinct Asia days (7 live-like);
  NY live-like days = 4 → **NY day floor not met** (extend to Aug 9+). Note: tighter
  stop without cutting `risk_pct` increases size — path R can rise while USD DD worsens.
- **Sun Aug 9 —** (a) NY stop-ladder re-read if ≥5 live-like days; (b) earliest
  London/Late promotion only if ≥ 20 fresh OOS accepted with candidate-specific
  cap-3 + concentration checks.
- **Sun Aug 16 — weekend read #1** (measurement; live Asia/NY weekends remain off).
- **Sun Aug 16–23 —** NY scale-in decision (measurement/backlog; needs ≥ 30 samples).
- **Sun Aug 30 —** stop-variant decision: replace 10 ATR only if a variant clears G2.

#### Stop-variant first read (Aug 1) — **RESULT: no live change**

Method: candidate-specific cap-3, 12 bps, live-like session filters
(Asia: weekday + neutral/bull; NY: Tue–Fri + neutral/bear). Paired delta uses
baseline cap-3 keys.

**Asia (≥2026-07-20, live-like)**

| Strategy | raw | filt | cap3 | days | avg_net | sum_net | PF | stops | paired Δavg vs 10ATR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asia_pump_short_4h (10ATR) | 61 | 43 | 26 | 7 | −1.229 | −31.95 | 0.51 | 3 | — |
| s4 | 77 | 52 | 35 | 7 | −0.641 | −22.43 | 0.65 | 12 | **+0.378** |
| s6 | 66 | 48 | 30 | 7 | −0.668 | −20.03 | 0.66 | 7 | −0.150 |
| s8 | 64 | 46 | 29 | 7 | −1.011 | −29.32 | 0.56 | 5 | −0.006 |

**NY full-session (≥2026-07-23, live-like)**

| Strategy | raw | filt | cap3 | days | avg_net | sum_net | PF | stops | paired Δavg vs 10ATR |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ny_flush_buy_4h (10ATR) | 115 | 73 | 23 | 4 | −0.759 | −17.45 | 0.56 | 1 | — |
| s4 | 130 | 80 | 30 | 4 | −0.330 | −9.91 | 0.78 | 8 | **+0.208** |
| s6 | 121 | 76 | 28 | 4 | −0.710 | −19.89 | 0.61 | 6 | −0.228 |
| s8 | 115 | 73 | 24 | 4 | −0.879 | −21.09 | 0.52 | 3 | +0.129 |

Notes:
- Entire live-like window is **red for all books** (regime/period effect); ranking
  is relative only. Do not promote on absolute edge.
- **s4** is the only consistent relative improve (Asia +0.38, NY +0.21 paired avg)
  via cutting fat tails — at cost of many more stop hits (Asia 12 vs 3; NY 8 vs 1).
- **s6/s8** do not beat baseline on paired avg; s6 Asia slightly worse.
- Concentration: NY top day 2026-07-28 ≈ +12.6 ATR (~60–127% of book net) — fragile.
- Asia symbol skew: ETH dominates losses (10ATR cap3 ETH n=13 avg≈−1.84).
- G1 not cleared (need cap-3 net/trade ≥ +0.5). **Keep live stops at 10 ATR.**
- Next: continue shadow through **Aug 30 G2 decision**; interim NY day-count check
  Aug 9. If s4 stays best relative but books stay negative, still no live swap
  (robustness preference: capped stop only when edge is positive OOS).

### September

- **Sun Sep 6 — weekend verdict** for Asia at ±1.0 ATR resolution (measurement).
- Event-driven — bull-book formal G2: Asia plain short in the current bull window
  (provisional live already) needs multi-day stable OOS vs shadow.

### Monthly (first weekend of month)

- HMM walk-forward retrain + state-profile drift check.
- Session × regime matrix refresh; decision-log audit.
- Regime-detector audit report (flip lag / false transitions) from
  `storage/v6_telemetry.db` regime snapshots — measurement, not threshold tuning
  on the same window.

## Weekly research loop (Sundays, ~1h)

1. **Integrity:** insert errors, enriched-field null rates, orphaned rows; live vs
   shadow reconciliation (fill agreement, slippage vs 12 bps, strategy-scoped
   `would_live_accept` on post-Jul-23 rows).
2. **Variance scan:** PnL by regime × age-bin × session × HMM state × weekday.
   Flag cells with |avg| ≥ 0.5 ATR and n ≥ 15 not explained by known axes.
3. **Register update:** advance / extend / kill each active hypothesis strictly by
   its pre-registered criteria. One decision per hypothesis per week — no daily
   peeking for promotion decisions (daily ops monitoring is separate).
4. **New hypotheses:** pre-register BEFORE computing outcomes; must state the
   mechanism (who is on the other side, why does the effect persist). No cap on
   active count (removed Aug 22) — the binding constraint is kill-criterion quality.
5. **Ship:** approved live changes deploy Monday. Update the decision log.

## Active register (no cap)

Cap removed Aug 22 (user call): hypothesis count no longer limited; discipline
shifts entirely to pre-registration + kill criteria per row.

| Hypothesis | Gate | Next checkpoint | Kill criteria |
|---|---|---|---|
| ~~Asia HMM {H2,H5} gate~~ | **KILLED Aug 1** | — | OOS avg −1.92 on n=15 (extended); see decision log |
| 4/6/8 ATR stop variants (Asia + full-session NY) | G0 (first read done; **not G1**) | Aug 9 NY day re-count; Aug 30 G2 | no variant beats 10 ATR with **positive** OOS by Aug 30; NY still <5 live-like days → extend |
| ~~Bull Asia short~~ | **SUPERSEDED Aug 22** — not live; asia session gated to `allowed_btc_regimes: ["neutral"]`, bull-regime shadow shorts (n=66) all predate the gate change | — | — |
| NY quality floor (`follow_3h_all` ∩ session=ny vs `ny_flush_buy_4h`) | G0 | after ≥ 15 post-fix accepted OOS; Aug review | < +0.5 ATR improvement vs paired baseline, or candidate < +1.0, or day-concentrated |
| London `follow_3h_london` + Late `fade_6h_late` expansion | G0; quality floor active | Aug 9 earliest if ≥ 20 fresh OOS | concentration fails; or no fills for 2 weeks → park |
| 1h time-exit variants (`ny_flush_buy_1h`, `asia_pump_short_1h`, t12) | G0 | after ≥ 15 post-fix accepted OOS each | < baseline 4h paired Δavg, or day-concentrated |
| Regime age gate (`max_regime_age_bars`; neutral 24-48h NY toxic) | G0 (plumbing live, gate inert) | Aug 23 read; enable only after OOS confirm | toxic cell not reproduced OOS, or gate cuts valid winners (paired Δ ≤ 0) |
| Path-conditioned bull gate (V-flip veto): bull-regime entries tradeable only when previous regime ≠ bear ("V-flip bulls"), else require age ≥ 12 | G0 registered Aug 22 pre-outcome. Basis: full-window native-4h chain — be→bu runs n=30 median life 2b / P(reach a12) 7% vs ne→bu n=43 median 18b / 65%; mirror bu→be median 2 vs be→ne 26. Mechanism: V-flips are EMA200 whipsaws in high-vol bottoms; burst-follow enters exactly these. **Measurement only** — prev-state not plumbed anywhere (loader/engine have no prev-regime input); enabling = new code. Age-matched comparison REQUIRED: path must beat the age gate's information, else it is the same claim restated | first age-matched Δ read Sun Sep 6 (alongside OI confirm); formal call n ≥ 15 accepted entries per origin class or 4 weeks, whichever first | age-matched Δavg(neutral-born − bear-born bulls) ≤ +0.2R at n≥15/class, or effect fully subsumed by Sunday's age-gate verdict (then fold into that row), or fresh-window-only samples contradict full window once n≥10 |
| NY scale-in +0.5 ATR @ 1h (`ny_flush_buy_4h_scalein`) | G0 | wired Aug 21 (was uninstrumented); read Sun Sep 13 or n ≥ 30 scaled fills | scale-in net ≤ plain single-entry (paired vs `ny_flush_buy_4h`), or day-concentrated |
| Asia/NY weekend tradability | measurement → G0 | Aug 16 read; Sep 6 verdict | weekend paired Δavg ≤ 0 vs weekday, or concentration fails |
| OI-flush long rule: LONG entries with `oi_delta_30m_pct` < −1% (funding leg KILLED Aug 21 read — see log) | G0 (early off-cycle read done Aug 21: Δ +0.26/trade, n=464, 6/6 wk pos, all 4 sessions pos, top-day 17%) | **Sun Sep 6 fresh-window confirm** (Aug 22–Sep 5 data only): fresh LONG OI<−1% Δ ≥ +0.1/trade on n ≥ 100 | fresh-window Δ ≤ 0, top-day >40%, or vol_z-matched control erases edge (flush-longs run hot: volz 4.7 vs 2.2 all-longs) |
| Cluster breadth / market-wide liq flow filter | G0 | Sep 6 checkpoint (after candidate cap-3 trusted) | no incremental edge vs single-symbol cap-3, or concentration fails |
| Weekend NY h21 bear (`ny_flush_buy_4h`, Sat/Sun, hour 21) | G0 (measure only) | Sep 6 weekend verdict | n<15, or paired Δavg ≤ 0 vs weekday, or top-day >40% |
| London h12 neutral (`london_burst_fade`) | G0 (measure only) | Sep 6 | top-day >40% (current: Aug19 +3.77R, Jul23 +2.88R dominate), or n<15 |
| Asia D10 @ h5 retained (`max_decile` NOT applied) | G0 (no code — filter-plan correction) | next variance scan | h5 D10 n<15 or avg<+0.1 after fresh OOS |
| Monday risk bump (session risk_multiplier override) | G0 (measurement wired; **action path NOT wired** — loader parses `risk_multiplier` but no consumer in burst-follow engine/order path; enabling = new code) | Aug 25 | Monday premium not reproduced OOS, or WR source fails sample floor |
| Bear-regime enablement — **NY flush-buy only** (ny session `allowed_btc_regimes` [neutral,bull] → +bear; london & asia EXCLUDED by pre-registered screen) | G0 registered Aug 22 pre-outcome. Basis — bear cohort fresh 14d: ny_flush_24h +0.193R/n44, 8h +0.184/n43, 4h_s4 +0.122/n64; live-book variant `ny_flush_buy_4h` +0.038/n53 thin-pos; full window confirms (+0.07–0.28). Screen exclusions: london `burst_follow` bear fresh −0.00R/n314 → stays bull-only; `asia_pump_short_4h` bear fresh −0.104/n28 → stays neutral-only. Promotion = config-only flip, no engine code | Promotion gate G0→G2 at Sep 6: Aug 23–Sep 5 bear cohort of ny_flush_4h family avg > +0.05R/trade on n≥15, no single symbol >60% of cohort PnL, bear occupancy ≥8% of window bars (underpowered → roll forward, not fail) | Kill (any one): fresh-window bear avg ≤ 0 at n≥20; post-flip live bear cohort ≤ −0.3R/trade at n≥15 after ≥14d exposure; one symbol >70% of live bear PnL; bear-enabled fortnight trips equity brake or raises peak-DD >15% |
| ~~Confirmed/delayed entry for burst books~~ | **KILLED Aug 22 night replay** (own kill criteria). Validated harness: 3503/3606 exact match, Σdiff +3.7R on −360R, exit mix reproduced. Grid Δ/signal: A +0.054 / B0.25 +0.073 / B0.5 +0.067 / B0.75 +0.041 / C +0.068 — three variants cleared the +0.05 gate BUT: (1) take-rate 33–56% < 70% floor → kill #2 fires; (2) flagship damage — london h8-13 bull LONG dE/fill **−0.249** (base +0.286/n148 → var +0.037/n83), ny LONG −0.059, london SHORT −0.060; (3) weekly concentration: net +242R of which W34 alone +237R (crisis week, base −414R→−178R) while W30 cost −72R — regime insurance, not expectancy. Per-fill E stays negative in every variant (best −0.059). o2-fill robustness check passed (gap≈0 median, Δ/signal +0.064 unchanged) | — | see decision log; asymmetric session-scoped confirm (bleeding cells only) is a DIFFERENT hypothesis — needs fresh pre-registration if pursued, no retrofit. **Part3 per-session×side full grid (Aug 22 night):** take-rate NEVER ≥70% in any cell×variant (max 68%) → kill #2 fires even session-scoped. Beneficiary set = exactly the 4 losing cells (dE/signal: ny SHORT +0.26…+0.44, late LONG +0.30…+0.40, asia SHORT +0.09…+0.14, asia LONG +0.03…+0.08); every profitable cell negative per-signal (london L/S, ny LONG −0.02…−0.05; late SHORT per-fill +0.19…+0.31 BUT per-signal −0.08…−0.12 — skipped winners cost more than saved losers). Flagship london h8-13 bull LONG dE/fill negative in ALL 5 variants (−0.22…−0.34). Symbol concentration benign (top1 ≤33% gross-pos) |

**Open register slot:** uncapped as of Aug 19. Candidates only via pre-registration
before outcome compute; every entry must carry a kill criterion. No auto-fill of
stale cells.

## Measurement / backlog (not active promotion)

| Item | Status | Notes |
|---|---|---|
| Age throttle (4% if neutral > 64h) | live | weekly; remove if >64h cells not worse 3 weeks |
| Regime-detector audit | measurement | 14k+ snapshots in `v6_telemetry.db`; report lag/flips/transition-zone PnL; do not retune on same window |
| Asia partial 50% @ +2 ATR at 1h | measurement | week of Jul 27 OOS check |
| ~~Asia limit-entry (−1.5 ATR)~~ | **KILLED Aug 19** | anti-edge: limit fills after burst has moved; NY bear −0.013R vs market +0.033R, Asia neutral −0.021R vs +0.033R |
| Symbol allowlist / fee micro / MAE-peak exits | ignore for now | noise / non-executable as tested |
| TSL / 24h hold enablement | killed / ignore | plain time exits win on current books |
| Per-regime strategy maps (engine) | engineering backlog | build only after ≥ 2 session×regime cells pass G2 and require different strategies/stops |

## Weekend data status

**Resolved Jul 22:** live Asia and NY both exclude weekends. NY also excludes Mondays.
Shadow continues for measurement. Historical Late weekend +3.37 (n=37) is
pre-promotion shadow evidence only; quality-floor Late has had no fills since
~Jul 16 — OOS clock has not started.

## Backlog (unscheduled — pre-register before touching)

- Symbol heterogeneity / whitelists (needs per-symbol n ≥ 30).
- Proper MDP sizing with portfolio state — only after ≥ 2 months of live PnL.
- HMM state/confidence as live-logged columns — only if HMM gate reaches G3.
- Per-regime live engine maps — gated on dual G2 cells requiring different rules.
- London/Late half-risk live pilot — only after G2 on fresh quality-floor OOS.

## Decision log (do not relitigate without new data)

- **Jul 15 — TSL variants: killed.** Plain time exits beat TSL on both live books.
- **Jul 15 — NY Mondays: blackout.** −48 ATR on Monday samples.
- **Jul 18 — Stops stay 10 ATR live.** 2–5 ATR stops kill valid Asia winners
  (winner MAE p95 = 5.2 ATR); revisit only via shadow stop ladder + Aug checkpoints.
- **Jul 18 — No scale-ins live.** Asia scale-in negative; NY promising but thin.
- **Jul 18 — Session carryover: dead.** No significant Asia→London→NY PnL link.
- **Jul 18 — MDP on (regime, age) alone: parked.** Degenerate without portfolio state.
- **Jul 19 — Weekends: unproven.** Asia weekend negative vs weekday; prompted Jul 22 exclusion.
- **Jul 22 — Asia session/regime:** keep `asia_pump_short_4h` at 48 bars;
  allow neutral+bull, drop thin bear, exclude Sat/Sun. Reject blind 36-bar cut.
- **Jul 22 — NY session/regime:** full-session `ny_flush_buy_4h`; neutral+bear only;
  exclude Mon/Sat/Sun. NY bull remains out.
- **Jul 22 — Late/London:** not live. Matrices are hypothesis generators until
  fresh quality-floor OOS exists.
- **Jul 23 — Fill accounting:** resolve fills via userTrades; refuse unresolved
  entry/exit prices; net PnL/R includes commissions. Weekly cooldown removed.
- **Jul 23 — Cap-3 integrity:** `would_live_accept` is per-strategy from this date.
  Pre-Jul-23 shared-column labels are invalid for promotion.
- **Jul 23 — Ignore for now:** symbol allowlists, fee micro-tuning, MAE-peak exits,
  enabling TSL/24h. Next dollar of research effort: NY quality floor OOS + stop
  ladder day-count; growth bets (London/Late) wait for fills; per-regime engine
  waits for dual G2 cells.
- **Aug 1 — Asia HMM {H2,H5} gate: KILLED.** Frozen train≤Jun30. Primary Jul18–24
  EXTEND (in_cap3=11<15, avg=−2.76). Extended Jul18–31 KILL (in_cap3=15, avg=−1.92,
  blocked_sum=−3.22). Jul21 single-day −30.6 ATR drives H5 OOS. Selection Jul1–17
  avg=+1.81 did not transfer. No G3 pilot. No Aug1 gate retrain. NY H4+H5 still
  useless (n=3). Artifacts under `research/output/reports/hmm_oos_*_2026-08-01.*`.
- **Aug 1 — Stop ladder first read: no live change.** Live-like books all negative
  in window; relative winner s4 (Asia paired Δavg +0.38, NY +0.21) via more stops,
  not a green edge. s6/s8 no clear win. NY only 4 live-like days (need ≥5). Keep
  10 ATR live through Aug 30 G2 gate. CSVs: `stop_variant_*_2026-08-01.csv`.
- **Aug 16 — Asia regime filter: neutral-only.** Drop `bull` from live
  `allowed_btc_regimes` (`live_burst_ny_asia.yaml`). Bull-Asia insufficient
  evidence (n=142 blocked live, regime frequency low); neutral carries the edge.
  Revisit on next multi-day bull window.
- **Aug 16 — NY hours pilot: [16,17].** Shift live NY window from [14,15] to
  [16,17] (`live_burst_ny_asia.yaml`). Variance scan shows h16-17 edge; full-session
  reverted. Revisit after OOS accumulation.
- **Aug 16 — Regime age gate: plumbing added (inert).** Added `max_regime_age_bars`
  to `SessionBurstRule` + `LiqBurstFollowConfig`, engine age gate, and
  `compute_regime_age_bars` wiring in `main.py`. Default `None` = no-op, so live
  behaviour unchanged until explicitly enabled. Motivation: variance scan toxic cell
  neutral 24-48h NY −0.75 R (n=623). Enable only behind a pre-registered threshold
  + OOS confirmation.
- **Aug 16 — 1h time-exit variants registered (G0).** Added `ny_flush_buy_1h` and
  `asia_pump_short_1h` (time_bars=12) to shadow. Motivation: ~70% of winners peak by
  bar 6 (1.5h); 48-bar (4h) exit holds too long. Shadow-only until G0 read.
- **Aug 19 — Adversarial re-scan (tiered proposals): findings.**
  (a) **NY bear: KILLED.** live-accept NY SHORT = −0.066R (n=172); every
  real-n symbol red (SOL −8.06R n=33, ZEC −7.08R n=27, ETH −3.28R n=69).
  "LUNCUSDT 78% WR" is a non-live-accept sample artifact (symbol absent from
  the live-accept set). No symbol-weighting scheme rescues a structurally
  negative book. (b) **Late fade_6h_late: KILLED.** neutral-SHORT n=4,
  avg +6.97R, 50% WR — the +27.9R "alpha" is ~2 trades. (c) **Limit entry:
  KILLED** (anti-edge, see backlog). (d) **London h12 london_burst_fade &
  weekend NY h21 bear: G0 measure-only** — both top-day concentrated.
  (e) **D10@h5 retained** — no `max_decile` shipped; D10 toxicity is h6-7 only.
  (f) `risk_multiplier` field added to `SessionBurstRule` (inert, default 1.0).
  **Equity-scaling gate: NOT activated.** Live book is currently negative-edge
  (Asia neutral −0.679R, 46.3% WR, n=41); all proposal "high-WR" cells are
  tiny-n survivors (n=16/18/20/23). WR→sizing requires a WR source clearing
  sample floor (≥30 closed, ≥5 days, top-day ≤40%) before any value ≠1.0.
- **Aug 16 — CORRECTION + 2h variants added.** The "~70% peak by bar 6" premise was
  DISPROVEN on direct measurement: winner mean MFE peak = bar 32 (8h), only 3–6% of
  winners peak within 6 bars. Added `ny_flush_buy_2h` + `asia_pump_short_2h`
  (time_bars=24) as the primary candidate horizon. **Data-integrity note:** the
  `pnl_1h`/`pnl_2h` (and `mae_*`/`mfe_*`) checkpoint columns in `shadow_trades` are
  NOT usable for time-exit inference — they're written only when the shadow logger
  ticks at EXACTLY `bars_held in {12,24}` (PNL) or `{36,72,...}` (MAE/MFE), so they
  are logger-uptime-gapped and their coverage is anti-correlated with performance
  (bare windows Jul 6–14, Aug 1–7 = the strongest weeks). Time-exit hypothesis is
  therefore answered by shadow-logging 1h/2h/4h as REAL strategy variants, where
  `pnl_atr` is the unconditional primary exit value, not a best-effort checkpoint.
  Also added London follow 1h/2h variants (`follow_1h_london`, `follow_2h_london`)
  against the 3h baseline, and NY/Asia 2h (`ny_flush_buy_2h`, `asia_pump_short_2h`)
  as the primary candidate horizon.
- **Aug 21 — London bull wired (G0) + NY bull hours reshuffle.** Regime bull
  (dist_pct ~15%, age 9+). **London:** `burst_follow` LONG h8-13 bull wired live as
  G0 forward-test — shadow +32.0R/83 (+0.385/trade, WR 64%, 6/8 days pos, Aug OOS
  +0.29R/trade), SL 10 ATR / TP 3 ATR / 30-min time exit (time_bars=6 on 5m bars — earlier '3h' label was wrong), pos_imb ≥ 0.5, no weekends,
  min_decile 1 (d1 = best bucket +17.5R/57). `london_burst_fade` REJECTED: edge is
  100% in its 30 TP hits (+90R) while 113 time-exits bleed −32.7R; h08 alone +36.8R
  of +45.3R total; Jul15 = 73% of P&L. `follow_3h_london`/`follow_6h_london`: zero
  bull trades (quality floor never fired in bull). Declared fragility: Jul16 = 57%
  of burst_follow P&L; would_live_accept subset n=27 −6.0R. Kill: net/trade < +0.2R
  after 30 accepted, or top-day > 40% of live P&L. **NY bull hours [14,15,17] →
  [14,16,17]:** h15 dropped (−1.5R avg n5, 0/3 days positive), h16 added (+1.18R
  avg n6, 2/2 days positive, all-July — thin), h17 retained despite Aug19 = 110% of
  hour P&L (recent regime evidence positive Aug19-20; existing kill criteria cover).
  h18 excluded (flat −0.02R n12); h19 watchlisted (+13.4R n9, 4/5 days pos — not
  wired, re-check after ~2 more bull weeks). TP sensitivity on London (Aug 21, exact 5m-bar replay of all 83 bull trades, validated +32.2R vs +32.0R actual, mix 7tp/76time/0sl):
  - SL grid @TP3/30min: SL10 +32.2R (0 stops) | SL8 +32.4 (1) | SL6 +34.4 (1) | SL5 +35.4 (1).
    SL6 delta = ONE trade (id16468, MAE −8.85 → −6 saves 2.9R). No harm, tail-saving, but n=1 event.
  - TP-up @30min: TP4 +30.1 (1 fill) | TP5/off +27.8 (0 fills) — strictly worse. Max MFE in sample = 3.81 ATR;
    only 7/83 ever touch 3 inside the 30-min window; zero touch 4. Nothing above 3 fills inside the window.
  - Post-exit (next 24h after our 30-min exit, n=76): med +3.6 ATR up AND med −7.2 down; 27/76 ran ≥5 up,
    54/76 fell ≥3. Ripping continues after exit but symmetric — naive longer holds lose (1h +21.9, 2h +7.8,
    4h +3.3; SL hits during the dip phase).
  - 8h hold @TP3 = +53.8R but top-3 days = 138% of total (Jul 15/16/21); Aug 12 = −18.2R. Jul-rip artifact, not a rule.
  - CONCLUSION: keep TP3/30min. SL6 defensible (non-negative, tail-saving) — pre-register before wiring.
  - G0 candidate: breakeven/trail design to capture post-exit runners without paying the dip; needs shadow first.
- Aug 21: post-exit tracking WIRED into shadow recorder (signal_shadow.py): post_mfe_atr/post_mae_atr/post_bars
  columns (24h = 288x5m window after close) + trade_r_path table (per-bar r_high/r_low/r_close, phase open|post).
  Legacy closed trades stamped elapsed (31,480); only new closes tracked going forward. 83 London bull trades
  backfilled from klines — cross-check vs manual analysis: median post_mfe 3.63 (manual 3.6), p90 9.06 (9.1),
  27/76 ran ≥5 ATR, 54/76 fell ≥3 — exact match. Trail/breakeven G0 sims now a SQL join, no external kline pulls.
TP sensitivity on London: TP=2.0 wash
  (+32.1R vs +32.0R), TP=1.5 worse (−3.3R) → 3 ATR kept. Stop never binds in-sample
  (0/83 hits, worst MAE −8.85 once, winners max adverse −3.62) → 10 ATR kept for
  shadow comparability; s6 variant pre-registerable in shadow if pursued.
- **Aug 21 (later) — h19 promoted from watchlist to live NY bull window.** Hours
  now [14,16,17,19]. Basis: +13.4R/9 (+1.49R avg, 4/5 days positive). Deviation
  from plan: promoted same day instead of waiting ~2 bull weeks — user decision,
  accepted risk is thin n=9 and h19's +13.4R includes Jul concentration similar
  to h16/h17 fragility. Kill criteria extended: if h19 live-accepted trades reach
  n≥10 with net/trade < 0, drop h19 (revert to [14,16,17]). Config comment updated;
  dry-load verified; loader test 1/1.
- **Aug 21 (evening) — funding/OI + scale-in checkpoints re-registered; scale-in actually instrumented.**
  Audit finding: funding/OI enrichment was NOT missing — `funding_rate_symbol` live since Jul 1
  (coverage 97.5% Jul → 99.5% Aug), `oi_delta_30m_pct` since Jul 14 (46.1% Jul → 99.4% Aug). The Aug 16
  checkpoint failed as PROCESS (no read computed on the day), not instrumentation; OI's ≥3-wk floor matured
  ~Aug 4. Re-registered: **Sun Sep 6 read**, kill criteria unchanged. Scale-in WAS genuinely unwired
  (zero `*scale*` rows in shadow_trades; recorder had no add-on support). Wired `ny_flush_buy_4h_scalein`
  into the shadow recorder: resting add-on unit eligible from bar 12 (1h) onward, fill at entry ∓ 0.5 ATR
  (adverse side), one add max, blended avg entry persisted via new `scale_filled_price` column; SL/time
  anchors stay on the FIRST entry; exits evaluated pre-scale on the trigger bar (conservative stop-first);
  post-scale pnl/MFE/MAE/path rows measure vs blended entry. Paired baseline `ny_flush_buy_4h`; expected
  scale-fill rate ~14%/entry (v9 Jun 9 replay). Checkpoint **Sun Sep 13 or n ≥ 30 scaled fills**, kill
  unchanged. Harness restart cost assessed ≈ zero: shadow open rows are DB-backed and resume management
  on the next bar; v5 paper book recovers positions/equity from its own tables on boot. Synthetic validation:
  fill/blend, no-touch, stop-first-same-bar, pre-bar-12 ineligibility — all pass; portfolio tests 2/2.
- **Aug 21 (late) — funding/OI checkpoint READ EARLY (off-cycle). Funding leg KILLED; OI leg narrowed to long-side flush rule.**
  Universe: closed shadow trades with both fields, primary books only (12 books, n=15,354 paired; stop-variant
  books excluded — they re-count the same entries and would pseudo-replicate).
  - Funding sign conditioning: dead (pooled Δ −0.005 / +0.002 vs baseline). Extreme |funding| ≥ p95 (0.00015):
    flips sign by side (L −0.15 n=950, S +0.13 n=601) and unstable across weeks (SHORT +0.37 W29 → −0.66 W32 →
    −0.83 W33). Kill criterion "unstable sign across weeks" met → funding leg closed.
  - OI-delta buckets pooled: <−1% +0.248 | [−1,0) +0.034 | [0,+1] −0.042 | >+1% −0.192 — the >+1% bucket is
    80% of net on ONE day (Aug 19) → rejected as day-concentrated artifact.
  - Survivor: **LONG & OI<−1%** — Δ +0.264/trade, n=464, **6/6 weeks positive** (W28–W33: +0.16/+0.15/+0.35/
    +0.41/+0.37/+0.14), positive in all 4 sessions (NY +0.16, Asia +0.24, London +0.62, Late +0.22), top day
    17% of net (Aug 20). Mechanism: buying into open-interest capitulation washout; shorting into it is NOT
    reliable (SHORT OI<−1%: Δ −0.45 pooled but only 2/4 weeks → veto case rejected). Below the original +0.5
    promotion bar → stays G0, narrowed hypothesis, fresh-window confirm Sep 6.
  - Caveats: (a) flush-longs run hotter vol_z (avg 4.67 vs 2.23 all-longs) — vol_z-matched control is part of
    Sep 6 kill criteria; (b) independent measurement check vs the external PM2 `oi-collector` (hermes_lab,
    `oi_live.db`, BTC/ETH/SOL/XRP only, oi_history fresh to <1h): shadow `oi_delta_30m_pct` agrees
    (r=0.721, median |Δ| 0.164pp, mean bias −0.002pp, n=2,083 pairs) → shadow OI field validated; collector's
    funding_history remains sparse/broken for alts and its funding cross-check is inconclusive (r=0.05,
    likely predicted-vs-settled timing mismatch) — collector usable as OI sanity check on 4 symbols only.
- **Aug 22 — register wiring audit** (funding/OI fresh-window confirm deferred to Sep 6 as planned).
  Checked every active register row for claimed instrumentation vs actual data flow.
  - Row "Monday risk bump" was marked "plumbing inert" — FALSE. `risk_multiplier` is parsed by
    config/loader.py but has NO consumer in the burst-follow engine/order path (only swing_break_engine
    has its own sizing hook, different system). If the Aug 25 read confirms a Monday premium, enabling
    it requires NEW code. Register wording corrected to "action path NOT wired".
  - Row "Bull Asia short" claimed provisional-live at 4% but the asia session has been gated to
    neutral-only (`allowed_btc_regimes: ["neutral"]`) in live config — marked SUPERSEDED; bull-regime
    shadow shorts (n=66) all predate the gate change. Register active rows now 13 (was 14) vs cap 10.
  - Regime age gate verified genuinely plumbed (loader default `max_regime_age_bars=None` +
    consumer at liq_burst_follow_engine.py:290) — no gap, gate inert by design until OOS confirm.
  - Healthy/measurable, no action: cluster_breadth & market_liq_flow_usd 100% coverage on Aug closed
    rows (11,235/11,286); weekend NY h21 cell already n=29 (+0.398 avg) ≥ floor ahead of Sep 6;
    london h12 n=295; ny_flush_buy_1h accepted n=58 (+0.381) readable; follow_3h_all NY accepted n=9
    (<15, correctly waiting).
  - Two books flagged against their own kill criteria, formal calls at Sun Aug 23 read:
    (1) fade_6h_late starving + bleeding — 3 fills since Aug 1 (−1.13 / −1.19 / −10R full stop Aug 20);
    strict "no fills for 2 weeks → park" not yet triggered but trajectory failing.
    (2) asia_pump_short_1h kill criterion MET on accepted OOS since Jul 23: n=70, WR 39%, avg −0.831 vs
    paired 4h baseline n=130, WR 48%, avg −0.400 (Δ −0.43, variant worse than baseline).
- **Aug 22 (late) — Markov refresh on clean native-4h series; Jul 17 duration/survival stats SUPERSEDED.**
  Recomputed the ADX14>25 + EMA200 regime chain (engines/btc_regime.py methodology, completed bars,
  WARMUP=200 discarded) over Aug 2024–Aug 2026 plus fresh window Jul 18–Aug 22.
  Finding: `research/markov_regime_analysis.md` §2–§4 built 4h bars from 1h klines → run fragmentation;
  its dwell times are ~4× too short (neutral mean 7.8b/max 39b claimed vs 29.1b/max 121b actual; bull
  5.5b/max 35b vs 14.5b/max 71b). Occupancy was robust (49.7/25.2/25.1 vs 50.1/24.1/25.8 ✓) and the
  edge×regime×age tables (§6, straight from shadow DB) are unaffected. Doc §9 dynamic-gate config was
  calibrated against fragmented runs → unvalidated; the registered age-gate read (Sun Aug 23) judges on
  live OOS trades regardless, so no plan change. Validation: current bull run since Aug 19 12:00 = 16
  completed bars vs shadow age 15 (±1 convention ✓); label agreement vs 18,787 shadow entries since
  Jul 18 = 91.6%, residual explained by live detector pricing the incomplete bar (btc_regime.py:35).
  Fresh window: neutral-heavy (60.7%), bull occupancy 15.2% with mean run only 8.0b, churn 0.40/day
  (vs 0.30 baseline), zero bull age cells n≥10 → Sun's gate verdict must come from trade-level samples
  only. Clean full-window survival: bull P(survive+4|age) 66% young → 89% (a13-18) → 79% (a19+, n=408).
  Numbers: `/root/hermes_lab/markov_refresh_results_20260822.md`. Do NOT cite doc §3/§4 going forward.
- **Aug 22 (late) — Bear-regime enablement registered G0 (NY-only); scope narrowed by fresh-window
  screen; pre-Sunday ops notes.**
  Blanket "enable bear everywhere" FAILED the screen: london book (`burst_follow` LONG) bear fresh-14d
  −0.004R/trade on n=314 (full window +0.008 — never had a bear edge); `asia_pump_short_4h` bear fresh
  −0.104/n28. What survives: ny_flush family in bear — fresh 24h +0.193/n44, 8h +0.184/n43, 4h_s4
  +0.122/n64, live-book 4h base +0.038/n53; full window confirms (+0.07–0.28). Registered NY-only G0
  with promotion gate + kills (see register). Expected live contribution if promoted: **+0.3–0.6R/wk**
  (not the +0.5–1.5R first quoted — that used a full-window aggregate the per-book split rejects).
  Ops notes for Sun Aug 23 read:
  (1) v5 shadow harness still on OLD london config (time_bars=6) — shadow london fills no longer proxy
  live (24). Restart harness AFTER Sunday's read so its window stays clean; log the config split point.
  Restart cost = seconds (DB-backed open rows recover).
  (2) London 120m exit: expect ZERO OOS signal Sunday (live since Fri; exclude_weekdays [5,6] = no
  weekend london fills). First paired Δ read Sun Sep 6 — do not judge Sunday.
  (3) Current bull is a V-flip (be→bu Aug 19 12:00, age ~17). P(still bull Sun) ≈ 20–29%. Pre-commit:
  age-gate read runs regardless of regime state Sunday; if flipped, read proceeds on the fresh be→*
  sample — more informative, not less.
  (4) Query pitfall reminder: yaml exclude_weekdays Python 0=Mon vs SQL %w 0=Sun — inverted a cell
  verdict once before.
- **Aug 22 (late II) — MAE lifecycle analysis: tight-stop/re-entry family KILLED on data; early-adversity
  gradient discovered; confirmed-entry G0 registered.**
  Data: causal `run_mae_atr`/`run_mfe_atr` (33,181 closed trades since Jun 27) + full-life `trade_r_path`
  on n=456 burst_follow trades Jul15+. Findings:
  - Burst-book MAE histogram is UNIMODAL (49% <1 ATR, thin tail P(>6)≈0.3–2.5%) — there is no separable
    "deep-tail mode" for a stop to isolate. Static tight-stop counterfactuals: burst_follow loses at every
    k∈[1..5]; best case anywhere = london_burst_fade k=1–2 at +0.019…+0.093R upper bound with P(stop)=54% —
    a wash. **Tighter stops + re-entry: closed as a family** (also v9 precedent: scale-fill starvation 14%).
  - Re-entry fuel after time exits is thin: P(post-exit continuation ≥ +2 ATR) ≈ 5–6% on 5m books.
  - The real structure: book expectancy IS the MAE split — full-life shallow-half +0.97R vs deep-half
    −1.05R (n=5784); quartiles monotone +1.24/+0.70/−0.13/−1.97. Early adversity is causal info:
    bar-1 MAE<0.5 → E=+0.37/n270 vs ≥0.5 → E=−0.80/n186; bar-3 MAE≥2 → E=−2.23/n92.
  - BUT exit-side abort cannot harvest it: aborting at −1.25 (thr 1.0 + slippage) to avoid losses averaging
    −1.31 nets only +0.03R/trade (wash); k=6 variants negative. Threshold-crossers often partially recover.
  - t0 features predict the split only weakly (best: cluster_breadth≥4 E=−0.19/n1810 vs breadth1 +0.01;
    regime_age 13–48 −0.18; cascade_active −0.18). The first bar itself is the strongest predictor →
    the tradable form is CONFIRMED/DELAYED ENTRY (skip if first post-signal bar closes adverse), not abort,
    not t0 filters. Registered G0 with replay gate + kill criteria (see register). Naive bound Δ≈+0.33R/trade;
    realistic ≈ +0.15–0.25R after winner-delay cost.
  - 4h flush books are the mirror case: deep MAE is the FEATURE (ny_flush_24h 45% MAE>6 ATR, E=+1.63);
    their mae_3h lift (early quiet → E=+5.62 vs rough → −1.03) is management-side info only.



- **Aug 22 (night III) — Confirmed/delayed-entry G0 KILLED by own replay.** Validated exact-price
  paired replay on burst_follow Jul15→now (3,606 signals, 27 symbols, baseline reproduced: 3503/3606
  exact, Σdiff +3.7R on −360R, exit mix 3302/273/31 vs actual). Grid Δ/signal cleared +0.05 gate for
  B0.25/B0.5/C (+0.067…+0.073) but three kills fire regardless:
  (1) take-rate 33–56% < 70% floor — same starvation mechanism that died in v9;
  (2) flagship population damage: london h8-13 bull LONG dE/fill −0.249 (base +0.286/n148 →
      variant +0.037/n83) — the confirm skips exactly the momentum winners; ny LONG −0.059,
      london SHORT −0.060;
  (3) net +242R is 98% one week (W34 crisis: base −414R → var −178R) and W30 gave back −72R of a
      good week — the rule is crisis insurance bought with good-week tax, not per-trade edge;
      per-fill E negative in every variant (best −0.059).
  Fill realism checked: close→next-open gap ≈ 0 median (o2-fill Δ/signal +0.064 ≈ unchanged), so
  the kill is NOT a fill-model artifact.
  Naive path-lift upper bound (+0.33R/trade from Aug 22 late II) was an over-estimate because it
  priced winners at the ORIGINAL entry; delayed fills re-anchor TP/SL higher and eat the run.
  Residual observation, NOT retrofitted into this row: improvement concentrates in bleeding cells
  (late LONG +0.289/fill, asia LONG +0.133, ny SHORT +0.090). A session-scoped asymmetric confirm
  is a different hypothesis requiring fresh pre-registration if ever pursued. Harness scripts kept
  at /root/hermes_lab/confirmed_entry_replay.py (+part2), kline cache /tmp/kcache_bf/.

- **Aug 22 (night IV) — Per-session×side full grid (part3), verdict unchanged and strengthened.**
  40 cell×variant tests. Take-rate never reaches the 70% floor in ANY cell (max 68%, late/LONG B75
  and asia/LONG B75) → pre-registered kill #2 fires even under session-scoped application; no
  scoping rescue exists for THIS hypothesis as registered. Beneficiaries are exactly the book's
  four losing cells — dE/signal: ny SHORT +0.26…+0.44, late LONG +0.30…+0.40, asia SHORT
  +0.09…+0.14, asia LONG +0.03…+0.08 — while every currently-profitable cell is negative
  per-signal (london L −0.04…−0.05, london S −0.02…−0.07, ny L −0.03…−0.05; late SHORT improves
  per-fill +0.19…+0.31 but loses per-signal −0.08…−0.12: half its winners are skipped).
  Flagship london h8-13 bull LONG: dE/fill negative in all five variants (−0.22…−0.34).
  Symbol concentration of variant PnL benign (top1 ≤33 percent of gross-pos in late/LONG,
  asia/LONG, ny/SHORT). Note ny/SHORT B50 variant total is still −54.5R absolute — improvement
  there means losing less, not making money. Any future asymmetric-confirm candidate = new G0
  with pinned cell set and fill floor chosen BEFORE outcome compute; post-hoc best-of-40
  selection must not inherit this row's kill lines.

---

## Aug 22 (day) — Full-sample expectancy mining (33k closed trades)

Exit-side replay on pathed trades: calibration passed 96.1% exact, but ALL exit levers died robustness
(path base = Aug 20-21 only). Pivoted to full-sample entry features + external OI join.

Ranked levers (full detail: /root/hermes_lab/MINING_RESULTS.md):
- L1 fade-trend-gate: pooled fade-shorts E +0.00/+0.05/-1.24 across ADX 15-25/25-35/>=35; >=35 bucket = single
  event (Aug 20-22), 24/27 symbols negative, 529 would-live trades ~-600R. Tier B for cutoff, Tier A for gradient.
- L2 low-vol-brake: fade-shorts q1-rvol24 E=-1.13 (n=1,868) vs mid +0.08; mirror long-follows q1 +0.96. Tier A.
- L3 OI: shadow oi_delta validated vs oi_live.db (corr .46, n=6,327). LONG-follow after -1..0% flush +0.295
  (n=3,097, biggest-n edge found); SHORT-fade at >=+1% -1.20. Deep flush <-1% hurts follows (-0.27).
- L4 asia_pump funding gate: pos_hi funding -1.64 (n=113) vs pos_lo +0.55. Day-concentration check pending.
- L5 winners-run: giveback_share 0.32-0.53 all books; ny_flush_24h realized +1.63 vs time-exit +5.93. Paper-sim only.
- L6 removals: burst_follow SHORT unrescuable (-0.11 flat); setup_follow LONG dead weight.

DRAFT G0s (register before next weekly loop):
- G0-A block SHORT fades when btc_adx>=35 OR oi30m>=+1% | kill: blocked-set fwd E>0 | success: >=2R/wk avoided,
  <20% positive-fade cut | window 4wk or n>=100 blocked.
- G0-B block SHORT fades when rvol24<0.065 (separate arm for attribution).
- G0-C block asia_pump_short_4h when funding>=0.0001.
- G0-D paper-sim trailing/extended hold on ny_flush_buy_24h + follow_* | kill: giveback_share worsens.

## G0 WIRED 2026-08-22T14:10Z - gate cluster forward read
Writer untouched (v5_forward_test.py -> signal_shadow.py unchanged; no restart).
Frozen thresholds in VIEW gate_g0 (storage/signal_shadow.db): ADX>=35, rvol24<=q1=0.0648,
OI d30m>=+1% (short block) and <=-1% (long flush arm), asia_pump% funding>=1bp, late-session longs,
burst_follow SHORT book. Forward-only cutoff 2026-08-22T14:10.
Read: python3 research/gate_weekly_read.py        (forward window)
      python3 research/gate_weekly_read.py --all  (backfill reconcile)
Floors n>=15 AND >=5 days/cell; kill: E<=-0.30 confirmed -> live-config proposal; E>=+0.30 falsified.
