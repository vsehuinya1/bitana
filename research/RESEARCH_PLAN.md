# Bitana Research Plan

_Last updated: Thu 2026-07-23. Live config: v1.1.1 (`bf434ed`)._
_Companion prompt for deep research sessions: `research/QUANT_RESEARCH_PROMPT.md`._

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
7. Max 5 active hypotheses at once. Live config changes ship at week boundaries
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
- **Fri Jul 24 – Sat Jul 25 — HMM OOS validation #1** (spec below).
- **Sun Jul 26 —** weekly review #1 (loop below).

#### HMM OOS validation #1 (pre-registered Jul 18)

- Frozen model: 6-state HMM trained ≤ Jun 30. **No retrain before this test.**
- Primary hypothesis: Asia entries during HMM states {H2, H5} outperform; other
  states are skippable. Filter was selected on Jul 1–17 data.
- Evaluation window: Jul 18 00:00 → Jul 24 24:00 UTC (fully out-of-sample).
- Metrics: (a) net/trade of in-state cap-3 Asia trades; (b) net PnL of trades the
  gate would have blocked; (c) mean filtered confidence on traded bars.
- **PASS** → G3 pilot (Asia HMM gate at 4% risk, week of Jul 27): in-state
  net/trade ≥ +1.0 AND blocked net ≤ 0 AND confidence ≥ 70%, on ≥ 15 accepted.
- **EXTEND** one week: < 15 accepted in-state (likely if BTC stays in H3), or mixed.
- **KILL**: in-state net/trade < +0.2 on ≥ 15 accepted, or blocked trades netted > +10 ATR.
- Secondary (report only): H5-only variant; confirm NY H4/H5 still shows no benefit.
- Scope note: the live Asia book now trades neutral+bull without an HMM gate.
  This test evaluates the incremental value of filtering Asia to H2/H5; it does
  not reopen the separately resolved session-regime allowlist.

### Week of Jul 27

- Semi-Markov age throttle validation #1: did >64h-neutral trades underperform as
  predicted, live and shadow?
- Asia partial-profit rule (take 50% when ≥ +2 ATR at 1h) OOS check on Jul 18+ data —
  measurement only; not an active promotion hypothesis.
- London/Late: begin OOS clock only if continuous quality-floor fills appear;
  instrumentation is already active.
- **Sat Aug 1 —** HMM monthly retrain (train ≤ Jul 31), only AFTER the validation
  decision. Version the model.

### August

- **Sun Aug 2 —** stop-variant first read (≥ 1 week of Asia s4/s6/s8 + new full-session
  NY s4/s6/s8). Require ≥ 5 distinct days before any live stop change. Note: tighter
  stop without cutting `risk_pct` increases size — path R can rise while USD DD worsens.
- **Sun Aug 9 —** earliest London/Late promotion decision, only if ≥ 20 fresh OOS
  accepted trades with candidate-specific cap-3 and concentration checks pass.
- **Sun Aug 16 — weekend read #1** (measurement; live Asia/NY weekends remain off).
- **Sun Aug 16–23 —** NY scale-in decision (measurement/backlog; needs ≥ 30 samples).
- **Sun Aug 30 —** stop-variant decision: replace 10 ATR only if a variant clears G2.

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
   mechanism (who is on the other side, why does the effect persist). Cap: 5 active.
5. **Ship:** approved live changes deploy Monday. Update the decision log.

## Active register (max 5)

| Hypothesis | Gate | Next checkpoint | Kill criteria |
|---|---|---|---|
| Asia HMM {H2,H5} gate | G1 → G2 | Jul 24–25 | net/trade < +0.2 on ≥ 15 OOS |
| 4/6/8 ATR stop variants (Asia + full-session NY) | G0 → G1 | Aug 2 first read | no variant beats 10 ATR OOS by Aug 30; < 5 days → extend |
| Bull Asia short | provisional live at 4%; formal G1 → G2 | current bull window review | negative in next / current multi-day bull window |
| NY quality floor (`follow_3h_all` ∩ session=ny vs `ny_flush_buy_4h`) | G0 | after ≥ 15 post-fix accepted OOS; Aug review | < +0.5 ATR improvement vs paired baseline, or candidate < +1.0, or day-concentrated |
| London `follow_3h_london` + Late `fade_6h_late` expansion | G0; quality floor active | Aug 9 earliest if ≥ 20 fresh OOS | concentration fails; or no fills for 2 weeks → park |

## Measurement / backlog (not active promotion)

| Item | Status | Notes |
|---|---|---|
| Asia/NY weekend tradability | live off; shadow continues | Aug 16 / Sep 6 reads |
| Age throttle (4% if neutral > 64h) | live | weekly; remove if >64h cells not worse 3 weeks |
| Regime-detector audit | measurement | 14k+ snapshots in `v6_telemetry.db`; report lag/flips/transition-zone PnL; do not retune on same window |
| Asia partial 50% @ +2 ATR at 1h | measurement | week of Jul 27 OOS check |
| NY scale-in +0.5 ATR @ 1h | backlog | ~Aug 16 if n ≥ 30 |
| Portfolio / same-side / cluster gates | backlog | after candidate-specific cap-3 is trusted; expect portfolio DD help more than avg R |
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

- Funding-rate / OI-delta conditioning (enriched fields since Jul 14; ≥ 3 weeks by mid-Aug).
- Cluster breadth / market-wide liq flow as entry filters.
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
