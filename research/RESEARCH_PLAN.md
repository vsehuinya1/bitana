# Bitana Research Plan

_Last updated: Mon 2026-08-10. Live config: `config/live_burst_ny_asia.yaml` (cap-6, NY Early)._
_Companion prompt for deep research sessions: `research/QUANT_RESEARCH_PROMPT.md`._

Single source of truth for what we test, when, and how results get promoted into
(or killed out of) the live book.

## Ground rules

1. Objective: understanding > backtest performance. Every apparent edge is presumed
   false until it survives out-of-sample.
2. Standard evaluation: **candidate-specific** portfolio sim, 1 per symbol,
   12 bps round-trip costs, PnL in ATR (and R = ATR / stop_atr).
   Report **both** cap-3 (research baseline) and **cap-6** (live-aligned as of
   Aug 9). Promotion criteria below still use cap-3 unless a hypothesis explicitly
   says cap-6.
3. No lookahead: regime and HMM labels from completed 4h bars only; models frozen
   before their evaluation window (HMM walk-forward: train ≤ month-end, filter forward).
4. Pre-registration: hypothesis, mechanism, test window, and pass/fail criteria are
   written in this file BEFORE outcomes are computed.
5. Selection windows never overlap evaluation windows.
6. Sample floors: no conclusion on < 15 accepted trades or < 5 distinct days
   (regime-split cells: ≥ 3 days). Top day may contribute ≤ 40% of net.
7. Max 5 active hypotheses at once. Live config changes ship at week boundaries
   (Mondays), except safety cuts.
8. **Research integrity:** do not use the shared historical `would_live_accept`
   column for promotion decisions on trades opened before the Jul 23 fix. From
   Jul 23 onward, `would_live_accept` is strategy-scoped. Offline analyses must
   still recompute candidate-specific acceptance when mixing pre/post windows.
9. **Shadow integrity:** paper FO DB is `storage/force_orders_paper.db` (isolated
   from live). Burst tape gap Jul 31–Aug 7 08:30 UTC was backfilled as
   `trigger='burst_backfill'` — usable for PnL, not for microstructure fields.

## Promotion ladder

| Gate | Meaning | Objective criteria |
|---|---|---|
| G0 | Idea | Pre-registered: claim, mechanism, window, criteria |
| G1 | In-sample candidate | cap-3 net/trade ≥ +0.5 ATR, n ≥ 20, ≥ 5 days |
| G2 | OOS-validated | ≥ 1 week AND ≥ 15 accepted OOS; net/trade ≥ +0.5 ATR (volume-cutting filters: ≥ +1.0); concentration + cost checks pass |
| G3 | Live pilot | 1–2 weeks at half risk (or current book risk × 0.5); slippage ≤ 1.5× modeled; live/shadow fill agreement ≥ 90% |
| G4 | Full size | book risk (live default; currently 15% on VPS) |

Kill at any gate: OOS net/trade < +0.2 ATR after 30 accepted, mechanism disproven,
or day-concentration fails. Killed ideas go to the decision log below.

## Live book snapshot (Aug 10)

| Item | State |
|---|---|
| Portfolio | cap **6** concurrent + cap **6** cluster; 1 per symbol |
| Asia | `asia_pump_short_4h`, D2+, weekends off, regimes per live yaml |
| NY | `ny_flush_buy_4h`, **hours 14–15 only**, D2+, Mon/Sat/Sun off, neutral+bear |
| Live PnL (through Aug 9) | Asia **+2.4R / 14**; NY **~0R / 12**; combined **+2.3R** |
| Shadow Jul+ cap-6 (live-ish) | Asia **~+0.15R/trade**; NY full **~+0.08R**; NY Early **~+0.18R** |
| Ops | FO DBs split; equity-shutdown auto-clears on wallet recovery; transfer poll staged |

**Stance:** provisionally profitable book; Asia carries; NY Early is the live anchor.
Do not add pairs until NY earns its slot or is explicitly shrunk.

---

## NY repair plan (pre-registered Aug 9)

Mechanism: full-session NY mixes a strong early-flush cell with toxic late hours
and weak deciles. Fix by **cutting junk**, not by adding symbols.

### Evidence snapshot (shadow Jul+, D2+, live-ish calendar/regime, cap-6)

| Slice | n | E[R] | WR | Note |
|---|---:|---:|---:|---|
| NY all live-ish | 64 | +0.08 | 64% | baseline — too thin for full slot |
| Hour 14 UTC | 24 | **+0.20** | 83% | primary edge |
| Hours 18 / 21 | — | negative | — | toxic; cut candidates |
| D2–3 | 26 | −0.02 | 54% | dead weight |
| D7–9 / D10 | 11 / 30 | +0.23 / +0.10 | 82% / 73% | quality |
| Friday | 25 | +0.09 | 76% | best weekday |
| Wednesday | 11 | +0.06 | 45% | weakest WR |
| `ny_flush_buy_4h_open` | 19 | +0.17 | 74% | open-window variant |
| `ny_flush_buy_4h_s4` | 24 | +0.30 | 71% | stop ladder — size/DD caution |

### Ordered suggestions → promotion path

#### NY-A — Early session only (primary)

- **Change:** restrict live NY to open/early window (≈ 14–15 UTC) **or** switch
  live strategy to `ny_flush_buy_4h_open`.
- **Gate now:** G0 (selected Aug 9 from in-sample shadow).
- **OOS clock:** Aug 11 (Mon) → Aug 22 (Fri). Evaluate **Sun Aug 23**.
- **PASS → G3 Mon Aug 24:** OOS E[R] ≥ +0.15 at cap-6, n ≥ 15, ≥ 5 days,
  top day ≤ 40% of net; beats full-session paired baseline by ≥ +0.05R/trade.
- **G3 pilot:** 1 week at half NY risk (Asia unchanged). **G4 Mon Aug 31** if
  live/shadow agreement ≥ 90% and pilot E[R] ≥ +0.10.
- **KILL:** OOS E[R] < +0.05 on n ≥ 20, or worse than full-session baseline.

#### NY-B — Decile floor D7+ (or D6+) — reverted Aug 10

- Shipped with NY-A Aug 9; **reverted to `min_decile: 2` Aug 10** — Early alone
  beat Early+D7 in-sample (+0.18 vs +0.16R). Not an active hypothesis.

#### NY-C — Friday overweight / Wednesday cut (secondary)

- **Change:** full NY size Friday; skip or 0.5× risk Wednesday.
- **Gate now:** G0 measurement — **do not ship alone** before NY-A.
- **Read:** Sun Aug 23 with NY-A. Promote Mon Aug 24 only as add-on if
  Fri−Wed gap remains ≥ +0.10R/trade OOS and n_Fri ≥ 8.
- **KILL:** gap disappears OOS or concentration fails.

#### NY-D — Stop ladder s4/s6 (research → careful pilot)

- **Change:** replace 10 ATR stop with shadow `ny_flush_buy_4h_s4` (or s6).
- **Gate now:** G0/G1 pending. Tighter stop **increases USD size** at same
  `risk_pct` — path R can rise while DD worsens.
- **Context Aug 10:** full-session s6 ΔE[R] ≈ **+0.07–0.08** vs 10 (WR ~71%,
  DD ok); s4 clears +0.10 but DD fails. Soft bar for **s6: ΔE[R] ≥ +0.08**.
  Under **Early alone**, s6 does **not** show that lift (n thin) — do not ship
  Early+s6 from full-session evidence.
- **Decision:** **Sun Aug 30** (or earlier if full-session s6 clears soft bar +
  DD and we explicitly choose to widen NY hours). Prefer evaluating stop
  changes via **NY-F** when live stays Early@10.
- **PASS → G3:** s6 ≥ +0.08R/trade vs 10 (s4 still ≥ +0.10), n ≥ 20, ≥ 5 days,
  max DD (USD and R) not worse than plain by > 20%.
- **KILL:** no beat by Aug 30 on adequate sample; or DD fails.

#### NY-E — Shrink until earned (ops, not alpha)

- **If NY-A misses Aug 23:** Mon Aug 24 set NY to **half risk** and/or **cap-2**
  (Asia stays cap-6). Re-expand only after a tightened cell clears G2.
- Safety cut — may ship mid-week if live NY week is ≤ −2R with n ≥ 8.

#### NY-F — Split book: Early@10 + h16–17@s6 (measure)

- **Claim:** Early **h14–15 @ 10 ATR** is the robust live anchor; adding
  **h16–17 @ 6 ATR** (open-window stop, no overlap / no double-fire on the
  same burst) lifts total R without wrecking Early WR/DD.
- **Not this:** open@s6 over all 14–17 as the sole book (hot E[R], n-thin);
  not Early+s6 (no in-sample lift under Early).
- **In-sample peek Aug 10 (live-ish):** Early@10 ≈ +0.18R / WR 82%; combo ≈
  +0.23R / WR 84% — lift is a handful of h16–17@s6 trades. **Measure, don’t ship.**
- **Gate now:** G0. Live stays Early@10 / D2+.
- **Clock:** shadow Aug 11 → Aug 22. Peek **Sun Aug 17**; decide **Sun Aug 24**
  (with NY-A).
- **PASS → consider G3 add-on Mon Aug 24:** combo beats Early@10 alone by
  ≥ +0.05R/trade at cap-6; n_combo ≥ 20; **n_(h16–17@s6) ≥ 8**; ≥ 5 days;
  maxDD not >20% worse than Early alone; top day ≤ 40% of combo net.
- **KILL:** h16–17@s6 bucket net ≤ 0 on n ≥ 8; or combo ≤ Early alone on n ≥ 20.

### What NOT to do for NY

- Do not add pairs to “fix” NY.
- Do not enable NY bull (live gate stays neut+bear until a separate G2).
- Do not promote TSL/24h (killed Jul 15).
- Do not loosen Monday blackout without new data.

---

## Asia notes (protect the earner)

- Live Asia remains the carrying book. Cap-6 Mon expectancy (neutral D2+) ≈ **+0.23R**.
- Strong cells: **Friday Asia** (live n=7, WR ~86%, E ~+0.38R — thin but best
  live cell); Thursday Asia shadow also strong WR.
- Do not dilute Asia with weekend/Tue experiments. New Asia filters need their
  own G0 and must not reduce Fri/Thu edge.

---

## Dated timeline

### Week of Jul 20 – Jul 23 (historical)

- Go-live, session retune (Asia neut+bull weekends off; NY full-session neut+bear,
  Mon/Sat/Sun off), fill accounting via userTrades, weekly cooldown removed,
  candidate-specific `would_live_accept`, NY full-session stop ladder in shadow.

### August

- **Sun Aug 2 —** stop-variant first read (extend if < 5 days).
- **Sun Aug 9 —** weekly review. Cap-6 live. London/Late **parked** (starved;
  < 20 fresh OOS). NY repair plan pre-registered (this section).
- **Mon Aug 10 —** Asia under cap-6; NY-B (D7) **reverted**; NY stays Early@10.
  NY-F (Early@10 + h16–17@s6) pre-registered for measure.
- **Mon Aug 11 → Fri Aug 22 —** NY-A OOS / G3 pilot window; NY-F shadow measure
  (h16–17@s6 bucket growth).
- **Sun Aug 16 —** weekend read #1 (measurement only); mid-window NY peek
  (no promote).
- **Sun Aug 17 —** NY-F first peek (h16–17@s6 n / combo vs Early alone).
- **Sun Aug 23 —** **NY-A promotion decision.** If miss → NY-E shrink.
- **Sun Aug 24 —** **NY-F decide** (with Mon Aug 24 ship window if PASS).
- **Sun Aug 30 —** stop-variant decision (NY-D / Asia s4–s8); prefer NY-F path
  if live remains Early@10.
- **Mon Aug 31 —** earliest NY-A G4 (full NY risk) if G3 pilot clean.

### September

- **Sun Sep 6 —** weekend verdict for Asia (±1.0 ATR resolution).
- Event-driven — bull-book formal G2 if needed; per-regime engine maps only after
  ≥ 2 session×regime cells independently pass G2.

### Monthly (first weekend of month)

- HMM walk-forward retrain + state-profile drift check.
- Session × regime matrix refresh; decision-log audit.
- Regime-detector audit from `storage/v6_telemetry.db` — measurement only.

## Weekly research loop (Sundays, ~1h)

1. **Integrity:** FO DB isolation, insert errors, burst tape continuity, live vs
   shadow reconciliation, strategy-scoped `would_live_accept` (post-Jul-23).
2. **Variance scan:** PnL by regime × session × weekday × hour × decile.
   Flag cells with |avg| ≥ 0.5 ATR and n ≥ 15 unexplained by known axes.
3. **Register update:** advance / extend / kill each active hypothesis by
   pre-registered criteria. One promotion decision per hypothesis per week.
4. **New hypotheses:** pre-register BEFORE computing outcomes. Cap: 5 active.
5. **Ship:** approved live changes deploy Monday. Update the decision log.

## Active register (max 5)

| Hypothesis | Gate | Next checkpoint | Promote / kill |
|---|---|---|---|
| **NY-A early session h14–15** | **G3 live** (shipped Aug 9; D7 dropped Aug 10) | Pilot → **Sun Aug 23** | KEEP if live/shadow E[R]≥+0.10, n≥10, not worse than pre-change week. REVERT full-session if E[R]<0 on n≥12 |
| **NY-F Early@10 + h16–17@s6** | G0 measure | peek **Sun Aug 17**; decide **Sun Aug 24** | PASS: combo ≥ Early+0.05R/trade, n≥20, n_h16-17≥8, DD ok. KILL: h16–17 bucket ≤0 on n≥8 |
| **NY-D / Asia stop ladder s4/s6/s8** | G0 → G1/G2 | decide **Sun Aug 30** | s6 soft bar ΔE[R]≥+0.08; s4 ≥+0.10; DD not >20% worse. Prefer via NY-F if Early stays |
| **NY-C Fri overweight / Wed cut** | G0 add-on | read **Sun Aug 23** with A | Ship Mon Aug 24 only if A keeps and Fri−Wed gap ≥+0.10 OOS |
| **NY-E shrink (half risk / cap-2)** | ops | **Mon Aug 24** if A misses; sooner if live NY week ≤−2R | Re-expand only after tightened cell G2 |

## Parked / measurement (not active promotion)

| Item | Status | Notes |
|---|---|---|
| London `follow_3h_london` + Late `fade_6h_late` | **parked Aug 9** | Starved under quality floor; no fills ≥ 2 weeks. Reopen only when ≥ 5 accepted/week for 2 weeks, then need ≥ 20 OOS for G2 |
| Asia/NY weekend tradability | live off; shadow continues | Aug 16 / Sep 6 reads |
| Age throttle (4% if neutral > 64h) | live / verify | weekly |
| Asia HMM {H2,H5} gate | stalled / revisit | only if fresh in-state n ≥ 15 |
| Bull Asia short formal G2 | provisional live | multi-day bull window review |
| Asia partial 50% @ +2 ATR at 1h | measurement | |
| NY scale-in +0.5 ATR @ 1h | backlog | after NY-A/B resolved; n ≥ 30 |
| Portfolio / same-side gates beyond cap-6 | backlog | DD-focused |
| Asia limit-entry (−1.5 ATR) | shadow-logging | until live limit path exists |
| Symbol allowlist / fee micro / MAE-peak exits | ignore | |
| TSL / 24h hold | killed | plain time exits win |
| Per-regime strategy maps (engine) | engineering backlog | after ≥ 2 dual G2 cells |
| More pairs | **not now** | only from shadow cells that clear G2 independently |

## Weekend data status

Live Asia and NY exclude weekends. NY also excludes Mondays. Shadow continues.
London/Late OOS clock is **not running** while starved (parked Aug 9).

## Backlog (unscheduled — pre-register before touching)

- Funding-rate / OI-delta conditioning.
- Cluster breadth / market-wide liq flow as entry filters.
- Symbol heterogeneity / whitelists (per-symbol n ≥ 30).
- Proper MDP sizing with portfolio state — after ≥ 2 months live PnL.
- HMM state/confidence as live-logged columns — only if HMM gate reaches G3.

## Decision log (do not relitigate without new data)

- **Jul 15 — TSL variants: killed.** Plain time exits beat TSL on both live books.
- **Jul 15 — NY Mondays: blackout.** −48 ATR on Monday samples.
- **Jul 18 — Stops stay 10 ATR live.** Revisit only via shadow stop ladder + Aug 30.
- **Jul 18 — No scale-ins live.** Asia scale-in negative; NY thin.
- **Jul 18 — Session carryover: dead.**
- **Jul 18 — MDP on (regime, age) alone: parked.**
- **Jul 22 — Asia session/regime:** `asia_pump_short_4h` 48 bars; weekends off.
- **Jul 22 — NY session/regime:** full-session `ny_flush_buy_4h`; neut+bear;
  Mon/Sat/Sun off. NY bull out.
- **Jul 22 — Late/London:** not live until fresh quality-floor OOS.
- **Jul 23 — Fill accounting + cap-3 integrity.** Weekly cooldown removed.
- **Aug 7–8 — Ops:** paper FO isolated to `force_orders_paper.db`; burst gap
  backfilled (`burst_backfill`); equity shutdown clears when wallet/DD healthy.
- **Aug 9 — Live portfolio cap-6** (concurrent + cluster). Research still reports
  cap-3 and cap-6.
- **Aug 9 — London/Late: parked** (starved). NY repair plan pre-registered (NY-A…E).
- **Aug 9 — Ignore for now:** more pairs, NY bull, TSL/24h, fee micro-tuning.
- **Aug 9 — NY-A/B shipped live (early):** NY `hours: [14, 15]`, `min_decile: 7`;
  Mon/Sat/Sun still off. Asia unchanged. Treat Aug 11–22 as **G3 pilot** (was
  pre-registered OOS); decide keep/revert **Sun Aug 23**. Stop ladder still Aug 30.
- **Aug 10 — NY-B D7 reverted** (`min_decile: 2`). Early alone beat Early+D7
  in-sample. Live = Early@10 only.
- **Aug 10 — NY-F pre-registered:** measure Early@10 + h16–17@s6 (no double-fire).
  Peek Sun Aug 17; decide Sun Aug 24. Do not ship from Aug 10 peek.
