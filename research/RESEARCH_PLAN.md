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
7. Max 10 active hypotheses at once. Live config changes ship at week boundaries
   (Mondays), except safety cuts.
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
   mechanism (who is on the other side, why does the effect persist). Cap: 10 active.
5. **Ship:** approved live changes deploy Monday. Update the decision log.

## Active register (max 10)

| Hypothesis | Gate | Next checkpoint | Kill criteria |
|---|---|---|---|
| ~~Asia HMM {H2,H5} gate~~ | **KILLED Aug 1** | — | OOS avg −1.92 on n=15 (extended); see decision log |
| 4/6/8 ATR stop variants (Asia + full-session NY) | G0 (first read done; **not G1**) | Aug 9 NY day re-count; Aug 30 G2 | no variant beats 10 ATR with **positive** OOS by Aug 30; NY still <5 live-like days → extend |
| Bull Asia short | provisional live at 4%; formal G1 → G2 | current bull/neutral window review (Asia live-like Jul20–30 still red) | negative in next / current multi-day bull window |
| NY quality floor (`follow_3h_all` ∩ session=ny vs `ny_flush_buy_4h`) | G0 | after ≥ 15 post-fix accepted OOS; Aug review | < +0.5 ATR improvement vs paired baseline, or candidate < +1.0, or day-concentrated |
| London `follow_3h_london` + Late `fade_6h_late` expansion | G0; quality floor active | Aug 9 earliest if ≥ 20 fresh OOS | concentration fails; or no fills for 2 weeks → park |
| 1h time-exit variants (`ny_flush_buy_1h`, `asia_pump_short_1h`, t12) | G0 | after ≥ 15 post-fix accepted OOS each | < baseline 4h paired Δavg, or day-concentrated |
| Regime age gate (`max_regime_age_bars`; neutral 24-48h NY toxic) | G0 (plumbing live, gate inert) | Aug 23 read; enable only after OOS confirm | toxic cell not reproduced OOS, or gate cuts valid winners (paired Δ ≤ 0) |
| NY scale-in +0.5 ATR @ 1h | G0 | Aug 16–23 (needs n ≥ 30) | scale-in net ≤ plain single-entry, or day-concentrated |
| Asia/NY weekend tradability | measurement → G0 | Aug 16 read; Sep 6 verdict | weekend paired Δavg ≤ 0 vs weekday, or concentration fails |
| Funding-rate / OI-delta conditioning | G0 | Aug 16 (≥ 3 wk enriched fields) | conditioning adds < +0.5 ATR vs baseline, or unstable sign across weeks |
| Cluster breadth / market-wide liq flow filter | G0 | Sep 6 checkpoint (after candidate cap-3 trusted) | no incremental edge vs single-symbol cap-3, or concentration fails |

**Open register slot:** 0 free (10 active). Candidates only via pre-registration before
outcome compute. Do not auto-fill — a new candidate requires killing or promoting one.

## Measurement / backlog (not active promotion)

| Item | Status | Notes |
|---|---|---|
| Age throttle (4% if neutral > 64h) | live | weekly; remove if >64h cells not worse 3 weeks |
| Regime-detector audit | measurement | 14k+ snapshots in `v6_telemetry.db`; report lag/flips/transition-zone PnL; do not retune on same window |
| Asia partial 50% @ +2 ATR at 1h | measurement | week of Jul 27 OOS check |
| Asia limit-entry (−1.5 ATR) | already shadow-logging | research-only until live limit path exists |
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
