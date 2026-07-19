# Bitana Research Plan

_Last updated: Sun 2026-07-19. Live config: v1.1.0 (go-live Mon Jul 20)._
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
  slippage vs the 12 bps assumption, BTC regime + age.
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

### Week of Jul 27

- Semi-Markov age throttle validation #1: did >64h-neutral trades underperform as
  predicted, live and shadow?
- Asia partial-profit rule (take 50% when ≥ +2 ATR at 1h) OOS check on Jul 18+ data.
- `fade_6h_late` × H4/H5 OOS read #1.
- **Sat Aug 1 —** HMM monthly retrain (train ≤ Jul 31), only AFTER the validation
  decision. Version the model.

### August

- **Sun Aug 2 —** stop-variant first read (≥ 1 week of 4/6/8 ATR data).
- **Sun Aug 9 —** `fade_6h_late` × H4/H5 promotion decision (needs ≥ 20 OOS
  accepted) → G3 pilot as a new late book if pass.
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
| `fade_6h_late` × H4/H5 | G1 → G2 | Aug 9 | same |
| 4/6/8 ATR stop variants | G0 (deployed Jul 19) | Aug 2 first read | no variant beats 10 ATR OOS by Aug 30 |
| Asia partial 50% @ +2 ATR at 1h | G1 | week of Jul 27 | OOS improvement < +0.3/trade |
| NY scale-in +0.5 ATR @ 1h | G0 | ~Aug 16 (n ≥ 30) | CI includes 0 at n = 30 |
| Weekend tradability | measurement | Aug 16 read; Sep 6 verdict | n/a |
| Age throttle (4% if neutral > 64h) | G3 (live) | weekly | >64h cells not worse for 3 straight weeks → remove throttle |
| Bull Asia short | G1 | next bull regime ≥ 3 days | negative in next bull window |

## Weekend data status (as of Jul 19)

Live books have 4 weekend days (Jul 11–12, 18–19). Early, underpowered reads:

| Book | Weekend n | Weekend avg | Weekday avg | ±1.0 ATR power reached |
|---|---:|---:|---:|---|
| Asia short | 22 | −0.36 | +1.44 | ~Sep 5–6 (7 more weekends) |
| NY flush | 10 | +1.74 | +0.17 | ~Aug 15–16 (4 more weekends) |
| Late fade | 37 | +3.37 | +0.65 | ~Aug 23 (sleeper candidate; no fills Jul 18–19 — watch) |
| London fade | 130 | −0.14 | +0.11 | already powered — flat, stays dead |

**Open live decision before Sat Jul 25:** config v1.1.0 trades Asia weekends at
full risk (only NY Mondays are excluded). Weekend Asia is unproven and trending
negative. Options: (a) exclude weekends from live Asia (`exclude_weekdays: [5, 6]`)
until the Aug 16 read — recommended; (b) trade them at full risk for live data.
Shadow logs weekends either way.

## Backlog (unscheduled — pre-register before touching)

- Funding-rate / OI-delta conditioning (enriched fields since Jul 14; ≥ 3 weeks of
  data by mid-Aug).
- Cluster breadth / market-wide liq flow as entry filters (same data window).
- Symbol heterogeneity / whitelists (earlier claim was built on misread horizons;
  needs per-symbol n ≥ 30).
- Weekend-specific strategies, esp. late fade (+68 cap-3 ATR on 4 weekend days —
  measurement first, Sep 6).
- Proper MDP sizing with state = (regime, age, open risk, drawdown) — only after
  ≥ 2 months of live PnL.
- HMM state/confidence as live-logged columns — only if the HMM gate reaches G3;
  until then it is reconstructable offline from BTC 4h bars + `entry_time`.
- London book revival — dead on weekdays AND weekends; needs a new mechanism to
  justify any further test.

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
  +1.44; explicit live decision required before Jul 25 (see above).
