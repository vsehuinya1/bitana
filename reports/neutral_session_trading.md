# NEUTRAL-REGIME TRADING PLAN — Bitana live burst engine (2026-09-23)

Written 2026-09-23 17:2xZ by OWL (GLM 5.3 flash; owner order: "have Claude look for how to trade the sessions during neutral, with reasons, taking into account the pending 21:15Z restart"). The Claude subagent pass timed out mid-analysis; OWL completed it directly with the same read-only method. The only writes are this file and RESEARCH_PLAN.md rows. No config or engine change is proposed for tonight.

**Era / method rules.** Shadow book: `storage/signal_shadow.db` (WAL — copied with -wal), `shadow_trades`, closed rows, 2026-08-24 → 2026-09-23T12:36:54Z (pre-parity). **Zero parity-era neutral rows exist** (regime bull since ~Sep-13, and all of Sep-23 pre-flip), so every number below is pre-parity. R conversion is era-aware live stop: ny_flush_buy_1h = pnl_atr/6 before Sep-6, /5 after (regime_stop_atr neutral was 6.0 pre-Sep-6, 5.0 after). burst_follow = /6 (SL6). Shadow variant stops are wider (10) — book R overstates live R on wide-MAE legs; fees −0.035R/leg live not in book. WLA=1 = passed the live gate set **as configured at row time**.btc_trend_state is the shadow's regime label (same selector family, `engines/btc_regime.py`).

Regime context: bull for 29+ 4h bars, BTC dist +10.5%. Selector = 4h EMA200 + ADX>25 with hysteresis (enter ≥25.5, exit <24.5), 5-min refresh. A neutral flip is a live possibility this week.

---

## 1. Case study — today (2026-09-23, bull, live)

- **29 legs, −$86.21 net, ΣR −3.18, equity $421 → $335 (−20.5%).** Fees −$29.15 (≈ −0.9R inside the net number). Brake: gross losses ≈ −$153/421 ≈ 36% < 50% — no trip.
- Decomposition (live, by close hour): h14 **−2.49R** · h16 −1.10R · h12 (london tail) −0.79R · h17 −0.22R · h09 −0.15R · wins: h10/h11/h13/h15 = +1.58R.
- **The h14 trio is the day:** SOL/ETH/XRP entered 14:10Z on the flush continuation, each stopped at the 5-ATR regime stop within **1 candle** (14:16Z) = **−2.19R combined**; NEAR 16:06 also stopped (−0.53R, 6 candles). Shadow's same 14:10 signal, on the SL10 variant, rode the drawdown to a positive time exit (UNI +0.12R@SL5-eq).
- **The day was real in the book too, not an execution artifact:** shadow ny_flush_buy_1h WLA=1 closed same-day = **−2.93R (n=14)** vs live ny −2.62R net. London: shadow +0.50R (n=39) vs live −0.59R net (gap = pre-parity tail legs at 12:01–13:35 + fees).
- Bookkeeping notes: UNI 14:10 −0.446R booked via `external_close` (ack-race reconcile; matches exchange truth within $0.20); the 14:10:42 UNI re-burst blocked by the stale per-symbol row was WLA=0 as burst_follow, and its 2h/4h_s4 variants lost −1.27R/−1.0R — the block likely saved money.

## 2. Neutral-state book, per arm (pre-parity)

### NY — ny_flush_buy_1h (allowed in neutral today: h16–17 only)
- **WLA=1 neutral: n=156, E+0.133, ΣR+20.7, WR .64, PF 2.47, 11 days, top-day 35% (≤40% bar ✓). Ex-top-day (Sep-16): E+0.091, PF 1.97.** Decile 1 slice: E+0.124 (n=121) — D1-unlock holds in neutral. h16 E+0.195 (n=27/9d), h17 E+0.359 (n=28/7d, top-day 75% — cell-concentrated, flagged).
- Widen check beyond h16–17 (RAW neutral, Mon–Fri, vol_z≥0, era-aware): h14 proxy E+0.019 (n=33), h15 proxy E+0.044 (n=44); **h14–15 combined proxy E+0.034 (n=77), top-day 112% → fails the prereg bar (fresh E>+0.03 at n≥30/≥3d with top≤40%)**. h18–20 neutral RAW: E −0.087/−0.103/−0.095, PF 0.38–0.53 — **never wire**.
- Verdict: **KEEP the current neutral config (h16–17, 5-ATR stop). The book already endorses it, and it is stronger than the bull pass-set (E+0.072, PF 1.61).** Do not widen to h14–15 on this evidence.

### London — burst_follow (neutral EXCLUDED today)
- Neutral RAW all-hours: n=4,348, E+0.004, PF 1.04 — flat. The only wired-epoch neutral book (pre-Aug-26 basis) is E−0.017/PF 0.86 (the −43.3R history). Per hour (RAW, SL6): h9 −0.026, h10 −0.002, h11 +0.029 (top 69%), **h13 +0.087 (n=266, PF 2.05, top 38%)**.
- h13 live-real proxy (Mon–Fri, vol_z≥0): **E+0.065, n=192, PF 1.69, top-day 66% → ex-top-day ≈ +0.022 = marginal, concentration-blocked.** A prior plan row (Sep-13 checkpoint) already holds "london h12 neutral n=181 E+0.078 PF1.52 top 33.2% — measure-only G0".
- Verdict: **stays excluded.** Re-open only via the existing G0/watch track once top-day ≤40% reproduces in the parity era. The cost of staying dark while neutral is bounded (~+0.02R/leg on ~5–10 legs/neutral-day, fees-adjusted ≈ 0 — this is not where money lives).

### Asia — pump_short_4h (arm disabled since Sep-3)
- **The asia pump-short is regime-flipped vs the LONG arms: neutral E+0.414 ATR/leg (n=261, 18d, WR .54) vs bull E−0.206 (n=277) — the fade works in chop, dies in trending bull.** asia_burst_fade is negative in both regimes (−0.19/−0.20) — stays dead. pump-short top-day 54% (Sep-1) > 40% bar; R conversion depends on variant stop (4/6/8 ATR ⇒ ≈ +0.05..+0.10R/leg) — pin the variant before any read.
- Verdict: **no re-enable now.** Two standing riders already block it: PREREG-ASIA-DISTCAP (BTC dist +10.5% > +5% — a re-armed asia trades zero legs today) and the pre-approved mandatory OI block on any arm enablement. Register a conditional G0 (below) so the decision is mechanical when dist and regime line up.

## 3. The 5-ATR stop — first counter-evidence day (bull, applies to neutral too)

- Sep-6 tighten (8/6→5/5) basis claimed "outcomes identical at 5/6/8/10, 0 stopouts at 5" on n=30 wired-cohort paths. **Today produced 4 live stop-outs at 5 ATR (−2.72R) incl. three in a single candle each.**
- Live 1h arm since Sep-6, all legs (incl. stopped): **n=37, ΣR+1.93, E+0.052** — vs survivors-only cohort (hold=12) n=33 E+0.141, and vs shadow SL10 variant same period **E+0.102 (n=53, 1 stopout)**. Live-real is running ~−0.05R/leg below the wide-stop book — almost all of the gap is today.
- This is a **watch + Sunday revert check**, not an emergency: E is still positive, and the widen/down-size alternatives (widening stops back = bigger losers on non-whipsaw days) need the paired read. Registered below as NEUT-STOP (the neutral 5-ATR stop is the higher-risk instance: neutral chop whipsaws more).

## 4. What the 21:15Z restart adds (and why nothing waits for it)

- Tonight arms: OI gate → dark (`oi_inflow_dark` tags + `would_have_matched` probes), per-reason gate counters by (session, reason) on :8082, hourly INFO summaries, burst re-check removal. Differential replay: accept sets identical.
- **Post-flip observability:** once BTC regime flips neutral, the counters give live counts of (a) london `regime`-blocked candidates per hour (how much flow the exclusion is rejecting — decidable at live counts instead of shadow proxies), (b) NY neutral candidate flow vs portfolio/cluster rejects, (c) OI-dark behavior in neutral. Until then, counters accrue bull data.
- **No config change is needed before or at tonight's restart for any recommendation above.** The current neutral behavior (NY h16–17 on, london/asia dark) already matches the book. Neutral-regime changes, if any survive the Sunday loop, are owner-applied config edits that would ride a LATER restart.

## 5. Prereg rows registered today (see RESEARCH_PLAN.md, same timestamp)

1. **NY-NEUT-KEEP** (status row, no wiring): keep NY neutral = h16–17; book E+0.133 ex-top +0.091, top 35%. Kill/alter bar: parity-era neutral cohort E<0 at n≥30/≥5d or top-day >40%.
2. **LON-NEUT-H13 watch** (measure-only, merges with the prior h12-neutral G0): population london neutral h12–13, Mon–Fri, vol_z≥0, parity era only; promote bar: E>+0.05 at n≥100/≥5d with top-day ≤40%; kill: E<0 at n≥100.
3. **ASIA-PUMP-NEUTRAL conditional G0**: trigger = (BTC dist <+5%) AND (regime = neutral AND ≥3 consecutive 4h neutral bars) AND two clean weekend-tape inputs per the Sep-7 protocol; arm = asia_pump_short_4h with pinned variant stop; mandatory riders: arm_oi_p1 OI block + PREREG-ASIA-DISTCAP. Basis: neutral +0.414 ATR/leg (n=261/18d) vs bull −0.206; promotion requires variant-pinned E>+0.03R at n≥50/≥5d, top-day ≤40%.
4. **NEUT-STOP revert watch**: population live 1h-arm legs (all, incl. stopped) from the 5/5 tighten; baseline = shadow SL10 variant paired. Read at Sunday loop (Sep-27) — n≥30 already met today (n=37, E+0.052 vs book +0.102). Revert bar: live-real E < book baseline by >0.05R/leg at n≥50, OR a second multi-stopout day (>3 stops) in any regime; on revert, neutral stop 5→6 first (bull stays 5 pending its own read).

## 6. Ranked owner actions

1. Nothing tonight: restart proceeds as scheduled (OI dark + observability + exit-retry patch if reviewed). Neutral config already correct.
2. Sunday loop (Sep-27): run NEUT-STOP revert check (armed, n≥30 met today) and the LON-TAIL/PREREG-OIGATE reads already registered.
3. If BTC flips neutral before Sunday: NY h16–17 trades automatically (book-positive, top-day 35%) — no action needed; expect london/asia to sit out; log the first parity-era neutral day as the seed for the watch rows.
4. Optional read-only: keep `regime` per-reason counters under weekly review after the flip — they price the london-neutral exclusion at live counts.
