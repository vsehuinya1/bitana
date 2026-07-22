# Bitana Research Plan

_Last updated: Wed 2026-07-22. Live config: v1.1.1 (`2615df5`)._
_Companion prompt for deep research sessions: `research/QUANT_RESEARCH_PROMPT.md`._

Single source of truth for what we test, when, and how results get promoted into
(or killed out of) the live book.

## Ground rules

1. Objective: understanding > backtest performance. Every apparent edge is presumed
   false until it survives out-of-sample.
2. Standard evaluation: cap-3 portfolio sim, 1 per symbol, 12 bps round-trip costs,
   PnL in ATR (1 ATR ≈ 0.8% equity at 8% risk / 10 ATR stop).
3. No lookahead: regime and HMM labels from completed 4h bars only; models frozen
   before their evaluation window (HMM walk-forward: train ≤ month-end, filter forward).
4. Pre-registration: hypothesis, mechanism, test window, and pass/fail criteria are
   written in this file BEFORE outcomes are computed.
5. Selection windows never overlap evaluation windows.
6. Sample floors: no conclusion on < 15 cap-3 accepted trades or < 5 distinct days
   (regime-split cells: ≥ 3 days). Top day may contribute ≤ 40% of net.
7. Max 5 active hypotheses at once. Live config changes ship at week boundaries
   (Mondays), except safety cuts.

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
  (logging only, no schema change): `asia_pump_short_4h_s{4,6,8}`,
  `ny_flush_buy_4h_open_s{4,6,8}`.
- **Daily (~10 min) —** service health, insert errors, live fills vs shadow,
  slippage vs the 12 bps assumption, BTC regime + age. Until fixed, explicitly
  audit live `entry_price` / `avg_fill_price`: Jul 22 closes were persisted with
  zero entry prices, corrupting trade PnL/R and the consecutive-loss counter.
- **Wed Jul 22 — live session retune shipped.** Asia remains
  `asia_pump_short_4h` at 48 bars, now neutral+bull only and weekends off.
  NY switches to full-session `ny_flush_buy_4h`, neutral+bear only, with
  Mon/Sat/Sun off. The proposed blind Asia 36-bar cut is rejected: peak MFE is
  not a realizable 3h exit, and later-peaking trades contain the winners.
- **Fri Jul 24 – Sat Jul 25 — HMM OOS validation #1** (spec below).
- **Sun Jul 26 —** weekly review #1 (loop below). Weekend checkpoint: Jul 25–26
  adds weekend days 5–6 for the live books.

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
- Asia partial-profit rule (take 50% when ≥ +2 ATR at 1h) OOS check on Jul 18+ data.
- Restore and verify continuous `fade_6h_late` shadow logging, then begin its
  H4/H5 OOS read. Historical rows stop around Jul 16; stale data cannot promote it.
- **Sat Aug 1 —** HMM monthly retrain (train ≤ Jul 31), only AFTER the validation
  decision. Version the model.

### August

- **Sun Aug 2 —** stop-variant first read (≥ 1 week of 4/6/8 ATR data).
- **Sun Aug 9 —** earliest `fade_6h_late` × H4/H5 promotion decision, only if
  continuous shadow logging has resumed and ≥ 20 fresh OOS trades are accepted
  → G3 pilot as a new late book if pass.
- **Sun Aug 16 — weekend read #1.** NY weekend reaches ±1.0 ATR detection power
  (~34 trades); Asia has ~10 weekend days. Decide: keep or lift any Asia weekend
  restriction.
- **Sun Aug 16–23 —** NY scale-in decision (needs ≥ 30 checkpoint samples;
  ~0.7/day → reached ~Aug 14).
- **Sun Aug 30 —** stop-variant decision: replace 10 ATR only if a variant clears
  G2 out-of-sample.

### September

- **Sun Sep 6 — weekend verdict** for Asia at ±1.0 ATR resolution (7 weekends of
  live-book data). If |effect| < 0.75 ATR and CI includes 0: default to trading
  weekends at 4% risk while logging continues, rather than waiting for ±0.5 power
  (that needs ~33 weekends — not worth the wait).
- Event-driven — bull-book promotion: Asia plain short in bull (+20.5 cap-3 net,
  Jul 15–17 in-sample) requires the next bull regime lasting ≥ 3 days to validate.

### Monthly (first weekend of month)

- HMM walk-forward retrain + state-profile drift check.
- Session × regime matrix refresh; decision-log audit (has new data contradicted
  any resolved item?).

## Weekly research loop (Sundays, ~1h)

1. **Integrity:** insert errors, enriched-field null rates, orphaned rows; live vs
   shadow reconciliation (fill agreement, slippage vs 12 bps, `would_live_accept`).
2. **Variance scan:** PnL by regime × age-bin × session × HMM state × weekday.
   Flag cells with |avg| ≥ 0.5 ATR and n ≥ 15 not explained by known axes.
3. **Register update:** advance / extend / kill each active hypothesis strictly by
   its pre-registered criteria. One decision per hypothesis per week — no daily
   peeking for promotion decisions (daily ops monitoring is separate).
4. **New hypotheses:** pre-register BEFORE computing outcomes; must state the
   mechanism (who is on the other side, why does the effect persist). Cap: 5 active.
5. **Ship:** approved live changes deploy Monday. Update the decision log.

## Active register

| Hypothesis | Gate | Next checkpoint | Kill criteria |
|---|---|---|---|
| Asia HMM {H2,H5} gate | G1 → G2 | Jul 24–25 | net/trade < +0.2 on ≥ 15 OOS |
| `fade_6h_late` × H4/H5 | G1; logging stale | Aug 9 earliest, after ≥ 20 fresh OOS | same |
| 4/6/8 ATR stop variants | G0 (deployed Jul 19) | Aug 2 first read | no variant beats 10 ATR OOS by Aug 30 |
| Asia partial 50% @ +2 ATR at 1h | G1 | week of Jul 27 | OOS improvement < +0.3/trade |
| NY scale-in +0.5 ATR @ 1h | G0 | ~Aug 16 (n ≥ 30) | CI includes 0 at n = 30 |
| Asia/NY weekend tradability | measurement; live off | Aug 16 read; Sep 6 verdict | n/a |
| Late weekend fade | measurement; shadow only | after logging resumes | fresh OOS fails concentration/cost checks |
| Age throttle (4% if neutral > 64h) | G3 (live) | weekly | >64h cells not worse for 3 straight weeks → remove throttle |
| Bull Asia short | provisional live at 4%; formal G1 → G2 pending | current bull window review | negative in next bull window |

## Weekend data status (as of Jul 19)

Live books have 4 weekend days (Jul 11–12, 18–19). Early, underpowered reads:

| Book | Weekend n | Weekend avg | Weekday avg | ±1.0 ATR power reached |
|---|---:|---:|---:|---|
| Asia short | 22 | −0.36 | +1.44 | ~Sep 5–6 (7 more weekends) |
| NY flush | 10 | +1.74 | +0.17 | ~Aug 15–16 (4 more weekends) |
| Late fade | 37 | +3.37 | +0.65 | ~Aug 23 (sleeper candidate; no fills Jul 18–19 — watch) |
| London fade | 130 | −0.14 | +0.11 | already powered — flat, stays dead |

**Resolved Jul 22:** live Asia and NY both exclude weekends. NY also continues
to exclude Mondays. Shadow logging should continue so the Aug 16 / Sep 6
measurement checkpoints remain valid. `fade_6h_late` is not live; its +3.37
weekend average (n=37) is historical shadow evidence, not a deployment result,
and requires fresh forward-shadow data before promotion.

## Backlog (unscheduled — pre-register before touching)

- Funding-rate / OI-delta conditioning (enriched fields since Jul 14; ≥ 3 weeks of
  data by mid-Aug).
- Cluster breadth / market-wide liq flow as entry filters (same data window).
- Symbol heterogeneity / whitelists (earlier claim was built on misread horizons;
  needs per-symbol n ≥ 30).
- Weekend-specific strategies, especially late fade (+3.37/trade, n=37
  historical shadow) — restore continuous logging, then require fresh OOS and
  concentration checks before any live pilot.
- Proper MDP sizing with state = (regime, age, open risk, drawdown) — only after
  ≥ 2 months of live PnL.
- HMM state/confidence as live-logged columns — only if the HMM gate reaches G3;
  until then it is reconstructable offline from BTC 4h bars + `entry_time`.
- London `follow_3h_london` revival — historical shadow is positive but
  day-clustered and logging is stale. Paper-first only; require a fresh OOS
  window and concentration checks before promotion.

## Decision log (do not relitigate without new data)

- **Jul 15 — TSL variants: killed.** Plain time exits beat TSL on both live books.
- **Jul 15 — NY Mondays: blackout.** −48 ATR on Monday samples.
- **Jul 18 — Stops stay 10 ATR.** 2–5 ATR stops kill valid Asia winners
  (winner MAE p95 = 5.2 ATR); revisit only via the 4/6/8 shadow variants.
- **Jul 18 — No scale-ins live.** Asia scale-in negative; NY promising but n = 12.
- **Jul 18 — Session carryover: dead.** No significant Asia→London→NY PnL link.
- **Jul 18 — MDP on (regime, age) alone: parked.** Degenerate — collapses to a
  contextual bandit without portfolio state.
- **Jul 19 — Weekends: unproven.** Asia weekend −0.36/trade (n = 22) vs weekday
  +1.44; this prompted the Jul 22 live exclusion below.
- **Jul 22 — Asia session/regime:** keep `asia_pump_short_4h` at 48 bars;
  allow neutral+bull, drop thin bear, and exclude Sat/Sun. Reject a blind 36-bar
  cut because MFE timing does not specify a realizable exit and later peaks
  contain the profitable cohort.
- **Jul 22 — NY session/regime:** replace the open-window label/filter with
  full-session `ny_flush_buy_4h`; allow neutral+bear only and exclude
  Mon/Sat/Sun. NY bull remains out pending stronger OOS evidence.
- **Jul 22 — Late/London:** neither is live. Historical session-regime matrices
  are hypothesis generators; stale or day-clustered books must resume shadow
  logging and pass fresh OOS before promotion.
