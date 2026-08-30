# Bitana Research Plan

_Last updated: Sun 2026-08-23 (weekly audit #5). Live config: Asia neutral-only 4h + NY bull h[14,16,17,19] + London bull `burst_follow` h8-13 (TP3/SL10/120m exit, since Aug 21). Register uncapped._
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

- **Fri Aug 28 — London bull stop-ladder interim read (validated kline replay,
  pre-Aug-30).** Population: London bull burst_follow, h9/10/11/13 weekday,
  Jul 15–Aug 27 — n=242 (13 days; weekend n=55 excluded from live book).
  Harness: 5m fapi klines, entry = signal-bar close, SL-before-TP conservative,
  stored atr column; validation vs recorded pnl_atr 294/297 (median Δ 0.0000,
  max 0.105), recorded run_mae_atr min (−5.11) reproduced independently by replay.
  **Result: zero stop-touches at SL6/8/10** — exit mix identical in all arms
  (225 time / 17 tp / 0 sl), PF identical 2.39, worst in-book in-life MAE −5.11 ATR
  (only 1 trade < −5, none < −5.5). Stop width is **non-binding** in the current
  book; SL6/SL8 vs SL10 differ only by R-denominator rescaling (avg +0.047R →
  +0.058R → +0.078R), i.e. a position-size multiplier ≡ risk_pct 24→30/40%.
  Caveats: over-cap reductions grow 6/242 (SL10) → 25/242 (SL6); fee drag +67%
  (median 0.020R → 0.033R); per-trade $ tail cap is −1R under every width; the
  single historical −6+ ATR breach (id16468, ETHUSDT, MAE −8.85) is **hour 12 —
  outside the current book** (it motivated the Aug-21 "SL6 saves 2.9R" note).
  Verdict: no variant clears G2 as a stop *improvement* (no outcome changes);
  keep SL10. Any tighter stop is a sizing decision (risk_pct), not a stop decision.
  **Liq-aware re-read (same day, decisive):** engine margin model (risk_manager
  L121-128) ⇒ lev = 6·notional/eq = 1.44·(ep/atr)/SL, eq-independent at 24%/6
  slots ⇒ liq ≈ 0.69·SL − 0.004·(ep/atr) ATR: SL10 → median 6.0 ATR (p10 5.2),
  SL6 → median 3.4 ATR = **inside the stop for 242/242 trades**. On observed
  paths: SL10 0 liq, 13-day net +$6.65; SL8 3 liq, +$6.48; SL6 13 liq (2 were
  SL10 winners: id31803 SOL +2.16, id38759 AVAX +0.39) + 25 lev-cap reductions
  + fees ×1.67 ⇒ net +$0.17. Equivalence check: SL10@40% ≡ SL6@24% (+$0.17,
  identical liq/reduced counts) — stop width and risk_pct are one dial
  (notional); liq geometry follows notional, not the stop line. Paper ladder
  SL6>SL8>SL10 (+18.8/+14.1/+11.3R) **inverts** in realized $:
  SL10>SL8>SL6 (+6.65/+6.48/+0.17). Stop-ladder closed at SL10 for London bull;
  binding constraint recorded: liq_atr ≈ 0.69·SL − 0.004·(ep/atr) at 24%/6
  slots. If more per-trade size is wanted, lever = max_concurrent slots
  (6→3 doubles liq headroom) or lower risk_pct, G-gated with liq-aware replay.
- **Sat Aug 1 / Sun Aug 2 —** stop-variant first read **DONE** (Asia s4/s6/s8 from
  Jul 20; full-session NY s4/s6/s8 from Jul 23). ≥5 distinct Asia days (7 live-like);
  NY live-like days = 4 → **NY day floor not met** (extend to Aug 9+). Note: tighter
  stop without cutting `risk_pct` increases size — path R can rise while USD DD worsens.
- **Sun Aug 9 —** (a) NY stop-ladder re-read if ≥5 live-like days; (b) earliest
  London/Late promotion only if ≥ 20 fresh OOS accepted with candidate-specific
  cap-3 + concentration checks.
- **Sun Aug 16 — weekend read #1** (measurement; live Asia/NY weekends remain off).
- **Sun Aug 16–23 —** NY scale-in decision (measurement/backlog; needs ≥ 30 samples).
- **Sun Aug 23 — weekly audit #5** executed (decision log at EOF).
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
| London `follow_3h_london` expansion (Late `fade_6h_late` half **CLOSED Aug 23**) | G0; quality floor active | parked until continuous quality-floor fills appear | concentration fails; or no fills for 2 weeks → park |
| Time-exit variants — `ny_flush_buy_1h` ALIVE (accepted n77 +0.215 vs 4h −0.393, Δ +0.61 → cap-3 G1 eval queued); ~~`asia_pump_short_1h`~~ / ~~`asia_pump_short_2h`~~ **KILLED Aug 23** (−0.385/n84, −1.216/n57 vs baseline −0.126/n137) | G0 | ny-1h G1 eval next loop | survivor kill: < baseline 4h paired Δavg, or day-concentrated |
| Regime age gate (`max_regime_age_bars`; neutral 24-48h NY toxic) | G0 (plumbing live, inert); **Aug 23 read: EXTEND → Sep 6** — fresh cell n=99/2d avg −0.45 (day floor unmet); side-asymmetric: SHORT −1.86/n48 vs LONG +0.87/n51, whole-cell cut nets +0.45/trade but kills LONG winners | Sep 6 re-read; enable only after OOS confirm on ≥3 days | toxic cell not reproduced OOS, or gate cuts valid winners (paired Δ ≤ 0); SHORT-only refinement **PRE-REGISTERED Aug 23 → see §PREREG-AGESHORT at EOF** |
| Path-conditioned bull gate (V-flip veto): bull-regime entries tradeable only when previous regime ≠ bear ("V-flip bulls"), else require age ≥ 12 | G0 registered Aug 22 pre-outcome. Basis: full-window native-4h chain — be→bu runs n=30 median life 2b / P(reach a12) 7% vs ne→bu n=43 median 18b / 65%; mirror bu→be median 2 vs be→ne 26. Mechanism: V-flips are EMA200 whipsaws in high-vol bottoms; burst-follow enters exactly these. **Measurement only** — prev-state not plumbed anywhere (loader/engine have no prev-regime input); enabling = new code. Age-matched comparison REQUIRED: path must beat the age gate's information, else it is the same claim restated | first age-matched Δ read Sun Sep 6 (alongside OI confirm); formal call n ≥ 15 accepted entries per origin class or 4 weeks, whichever first | age-matched Δavg(neutral-born − bear-born bulls) ≤ +0.2R at n≥15/class, or effect fully subsumed by Sunday's age-gate verdict (then fold into that row), or fresh-window-only samples contradict full window once n≥10 |
| NY scale-in +0.5 ATR @ 1h (`ny_flush_buy_4h_scalein`) | G0 | wired Aug 21; 23 scaled fills @ Aug 23 (< 30); read Sun Sep 13 or n ≥ 30 scaled fills | scale-in net ≤ plain single-entry (paired vs `ny_flush_buy_4h`), or day-concentrated |
| Asia/NY weekend tradability | measurement → G0 | Aug 16 read; Sep 6 verdict | weekend paired Δavg ≤ 0 vs weekday, or concentration fails |
| OI-flush long rule: LONG entries with `oi_delta_30m_pct` < −1% (funding leg KILLED Aug 21 read — see log) | G0 (early off-cycle read done Aug 21: Δ +0.26/trade, n=464, 6/6 wk pos, all 4 sessions pos, top-day 17%) | **Sun Sep 6 fresh-window confirm** (Aug 22–Sep 5 data only): fresh LONG OI<−1% Δ ≥ +0.1/trade on n ≥ 100 | fresh-window Δ ≤ 0, top-day >40%, or vol_z-matched control erases edge (flush-longs run hot: volz 4.7 vs 2.2 all-longs) |
| Cluster breadth / market-wide liq flow filter | G0 | Sep 6 checkpoint (after candidate cap-3 trusted) | no incremental edge vs single-symbol cap-3, or concentration fails |
| Weekend NY h21 bear (`ny_flush_buy_4h`, Sat/Sun, hour 21) | G0 (measure only) | Sep 6 weekend verdict | n<15, or paired Δavg ≤ 0 vs weekday, or top-day >40% |
| London h12 neutral (`london_burst_fade`) | G0 (measure only) | Sep 6 | top-day >40% (current: Aug19 +3.77R, Jul23 +2.88R dominate), or n<15 |
| Asia D10 @ h5 retained (`max_decile` NOT applied) | G0 (no code — filter-plan correction) | next variance scan | h5 D10 n<15 or avg<+0.1 after fresh OOS |
| ~~Monday risk bump~~ **KILLED Aug 25 (due-date read)** | Premium NOT reproduced — Mon is the WORST weekday since Jul1: n=121 closed, avg −0.65 ATR (−0.065 R/tr), net −78.5, vs Fri best +1.31; every other weekday positive; regime split negative BOTH cells (bull −0.58/n31, neutral −0.82/n84); fresh Aug24 −17.9R/−0.58. Kill #1 fires outright; WR sample floor moot. Monday exclusion stays; action path never built (no code) | — | — |
| Bear-regime enablement — **NY flush-buy only** (ny session `allowed_btc_regimes` [neutral,bull] → +bear; london & asia EXCLUDED by pre-registered screen) | G0 registered Aug 22 pre-outcome. Basis — bear cohort fresh 14d: ny_flush_24h +0.193R/n44, 8h +0.184/n43, 4h_s4 +0.122/n64; live-book variant `ny_flush_buy_4h` +0.038/n53 thin-pos; full window confirms (+0.07–0.28). Screen exclusions: london `burst_follow` bear fresh −0.00R/n314 → stays bull-only; `asia_pump_short_4h` bear fresh −0.104/n28 → stays neutral-only. Promotion = config-only flip, no engine code | Promotion gate G0→G2 at Sep 6: Aug 23–Sep 5 bear cohort of ny_flush_4h family avg > +0.05R/trade on n≥15, no single symbol >60% of cohort PnL, bear occupancy ≥8% of window bars (underpowered → roll forward, not fail) | Kill (any one): fresh-window bear avg ≤ 0 at n≥20; post-flip live bear cohort ≤ −0.3R/trade at n≥15 after ≥14d exposure; one symbol >70% of live bear PnL; bear-enabled fortnight trips equity brake or raises peak-DD >15% |
| Bear-regime enablement — **NY flush-buy 8h/24h** (`ny_flush_buy_8h`, `ny_flush_buy_24h` LONG) | G0 registered Aug 30. Basis — shadow full window: 8h +1.57R/n383 (73% WR, 7d), 24h +2.84R/n352 (55% WR, 38d); both LONG-only, extended hold (96/288 bars). Fresh 14d: 8h +0.184R/n43, 24h +0.193R/n44. Promotion = new strategy variant + config (engine supports time_bars). **Distinct from 4h family** — longer hold, different exit dynamics. | Promotion G0→G2 at Sep 20: fresh 14d (Aug 23–Sep 5) each variant avg > +0.10R on n≥15, ≥5 days, top-day <40%, rvol tercile control. | Kill (any): fresh avg ≤ 0 at n≥20; giveback_share ≥ 0.55 vs 4h baseline; live cohort ≤ −0.3R at n≥15 after ≥14d. |
| Bear-regime enablement — **London follow/fade 6h** (`follow_6h_london` LONG, `fade_6h_london` SHORT) | G0 registered Aug 30. Basis — shadow full window: follow +4.53R/n44 (88.6% WR, 4d), fade +4.47R/n21 (66.7% WR, 6d); 6h hold (72 bars), 10 ATR stop. Fresh 14d: follow +5.66R/n39 (97% WR), fade +4.47R/n21 (67% WR). **Requires NEW London session block** (currently bull-only structural LONG). Engine wiring needed: pos_imb_only + allowed_side split per strategy. | Promotion G0→G2 at Sep 20: fresh 14d each strategy avg > +1.0R on n≥15, ≥5 days, top-day <40%, both sides positive. | Kill (any): fresh avg ≤ 0 at n≥15; one side ≤ 0 while other >0 (asymmetric rescue); live cohort ≤ −0.5R at n≥15 after ≥14d. |
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

---

## Sun 2026-08-23 — Weekly audit #5 (04:45–05:15Z)

**Integrity:** writer live (last entry 04:39Z); 34,514 rows / 79 open / 0 stale >3d. Enrichment coverage Aug16+ closed (n=6,568): oi_delta 99.0%; funding/breadth/liq-flow/spread/post_mfe 100%. v5 harness clean since Sat 17:03Z restart (0 ERROR, 0 −4108) — NOTE: harness restarted BEFORE Sunday's read contrary to the ops note, so its London-config split point is Sat 17:03Z (no weekend London fills either side, window effectively clean). Live unit up since Fri 19:20:33 (NRestarts=0) BUT Friday it flapped 10× through the London window (07:52→09:52; one stop-timeout hang → SIGKILL 09:33) and logged ZERO live fills vs 33 shadow-accepted London trades (+4.71R) — reconciliation UNRESOLVED, verify vs Binance userTrades Monday. Zero live fills since Fri 19:20 is expected (weekend off).

**Age-unit correction:** `shadow_trades.btc_regime_age_bars` counts 4H bars (cross-check: bull age 15-16 bars on Aug 22 vs Markov refresh ✓). Earlier hour-based readings ("ages 1–2h") were wrong ×12 and are superseded.

**Checkpoint calls:**
1. **Regime age gate — EXTEND to Sep 6.** Toxic cell (neutral, age 6-12 bars, NY h14-21): fresh Aug16+ n=99/2 days avg −0.452 (day floor 2<3 unmet); since Jul18 n=291/8d avg −0.141 — original −0.75 does not reproduce at strength. Side split (last 14d): cell SHORT −1.857/n48/net −89 vs LONG +0.87/n51/net +44 — the real signal is "neutral mid-age NY SHORT"; a whole-cell cut nets ≈ +0.45/trade but removes 51 LONG entries. SHORT-only refinement = new pre-registration if pursued.
2. **Time-exit variants:** `asia_pump_short_1h` **KILLED** (accepted OOS Jul23+: n=84, −0.385, WR44%, net −32.4 vs paired 4h n=137 −0.126 WR51%; Δ −0.26; ladder < +0.2 also fired). `asia_pump_short_2h` **KILLED** (n=57, −1.216, net −69.3, Δ −1.09). `ny_flush_buy_1h` **SURVIVES** (n=77, +0.215, WR58.4% vs baseline −0.393, Δ +0.61) → cap-3 G1 evaluation queued next loop; `ny_flush_buy_2h` n=53 +0.164 same direction, thinner. Structural read: NY flush buys want shorter holds; Asia pump shorts want the full 4h.
3. **`fade_6h_late` CLOSED/parked.** Edge already killed Aug 19; fresh evidence seals it — 5 fills since Aug 1 net −20.17 including TWO −10R max-loss stops (Aug 20 + Aug 22). All-time n=78 +157.93 = essentially one July week. Stopped tracking; London half of the row remains parked on quality-floor fills.
4. **Stop ladder interim (no decision; Aug 30 G2 stands).** Live-like fresh Aug16+: Asia base −1.447/n30 vs s4 −0.888 / s6 −0.862 / s8 −0.954 (variants +0.49…+0.59 better, absolute still red); NY base +0.143/n16/4d vs s4 +0.29 / s6 +0.427 / s8 +0.177. Relative-improve pattern persists; NY day count still 4<5.
5. **Weekend measurement (feeds Sep 6):** base-arm Sat 13:00→now n=331, net −12.5, avg −0.038. Weekend NY-h21 bear cell n=44 (+15 wk/wk), 7 days, avg +0.751, net +33.0 — floor met, concentration check at verdict. Tier_c day-2: TRUMP −0.497/n72 (worst), DOGE +2.408/n59, HYPE +1.21/n46, PUMP +0.64/n43; GRAM 0 rows. None near graduation floors (≥5 days needed).
6. **Gate cluster forward read** (wired Aug22T14:10, forward-only): closed n=951, Σ +370R, E +0.39; ALL arms day-floor ✗ → accumulating, no calls. arm_fund1bp blocked-set E +2.79/n41 tracks FALSIFIED (consistent w/ funding kill Aug 21); arm_burst_s E −0.19 (bleed direction confirmed); arm_rvolq1 n=0 BENIGN — 0/961 shorts had rvol24 ≤ 0.0648 in the hot-vol window; arm healthy.
7. **OI-flush G0:** fresh-window tracker Aug22T00+ (LONG oi<−1%, all books pooled, RAW avg — not the registered Δ metric): n=311, −0.095, 2 days. Will clear n≥100 well before Sep 6; proper Δ vs non-flush longs computed at the Sep 6 verdict.
8. **NY quality floor:** `follow_3h_all` ∩ NY accepted since Jul23 n=12 (+1.766 avg, net +21.2) — still <15, keep waiting. follow-family since Aug 1 broadly green (3h_all +44.3/n45, 6h_all +22.8/n42).
9. **Scale-in** 23 fills < 30 → Sep 13 stands. **Monday risk bump** read due Tue Aug 25; action path STILL unwired (loader parses, no consumer — enabling = new code).

**Variance flags (last 14d, |avg|≥0.5, n≥15, outside known axes):** neutral a>48h late LONG −1.20/n41 · bull a<24h NY SHORT −1.13/n30 · bear a<24h late SHORT −0.62/n90 · bull a>48h late SHORT −0.85/n40 · neutral a<24h asia LONG −0.69/n80. No action without pre-registration.

---

## 2026-08-29T09:54Z — OWNER PEEK (interim, non-binding): OI-flush fresh-window tracker status
Formal verdict unchanged: **Sun Sep 6** (window Aug 22–Sep 5). Interim read Aug 22T00→now, LONG oi_delta_30m_pct<−1%, all books pooled, live-3% R convention, Δ vs non-flush longs computed EARLY (tracker said defer Δ to verdict — disclosed as peek):
- Flush longs n=1513 avg **−0.021R/tr** (net −31.3R, WR 53.6%) · non-flush n=6465 avg +0.008R/tr · **Δ = −0.029R/tr** — below confirm floor (+0.1) AND below kill line (≤0)
- Top-day concentration **52.4%** (kill >40%): Aug 28 −64.36R. Ex-0828 the flush book is ≈ +33R — concentration, not uniform bleed
- n≥100 met (1513). Both kill criteria currently in breach; 7 window-days remain. If sign does not flip by Sep 6, verdict shapes as KILL. No wiring exists (measurement-only row) — no action before formal read.

---

## Sun 2026-08-30 — Weekly audit #6 (06:47–07:15Z)

**Integrity:** writer live (last entry 06:44Z); 45,453 rows; 0 stale >3d open. Live unit up since Sun 06:26Z (restarted for NY-bear config revert, see below); 0 ERROR/traceback in 24h journal. Shadow live-reconciliation: live burst LONG n=70 avg −0.060 vs shadow ny_flush_buy_4h n=232 avg −0.056 Aug23+ — books tracking ✓.

**Regime state:** bull since Aug 20 (age 31–60 4h bars); brief neutral Aug 25–26 (age 0–2 bars, 213 rows); **last bear entry Aug 17** — no bear-regime trades for ANY bear G0 since registration window opened. All bear cohorts remain n=0 fresh.

**Live book Aug 23+:** 71 closed (70 LIQ_BURST_FOLLOW LONG + 1 COMPRESSION), Σ −6.48R, avg −0.091. Exit mix: time 61/−1.02 · SL 3/−2.79 · TP 3/+0.67 · external 4/−3.34. External closes: 3× burst LONG (WLD/ZEC/ADA) batch-closed Aug 25T14:00 at 20–22 bars (restart/config-change batch — benign); 1× COMPRESSION XRPUSDT −2.26R at 0 bars Aug 28T16:58 (immediate external close — flag: 0-bar hold at −2.26R suggests entry-instant manual/brake close; no action, monitor for recurrence).

**Checkpoint calls:**
1. **Gate cluster — forward vs backfill DIVERGENCE is the story.** Backfill (all rows since Aug22T14:10): arm_rvolq1 CONFIRMED (E −1.03/n2154), arm_oi_p1 CONFIRMED (−0.50/n1532), arm_fund1bp CONFIRMED (−0.65/n1475), arm_late_long CONFIRMED (−0.41/n1888). Forward-only window (last ~9d): fund1bp FALSIFIED (+0.53/n517), late_long FALSIFIED (+0.53/n337), adx35/burst_s inconclusive, rvolq1 n=0 (0/961 hot-window shorts met threshold — gate never binds). Read: the blocked pools bled in the immediate post-cutoff window but RECOVERED after — backfill confirms gates are deadweight now. No promotion per registered kill (forward window governs); cluster stays measurement-only. Do NOT wire any arm on backfill.
2. **OI-flush (verdict Sep 6):** fresh n=1553 avg −0.070R (net −108.5R, 8 days), Δ vs non-flush ≈ −0.03→−0.08 direction stable, top-day 52.4% > 40%. **Both kill criteria in breach for 8 straight days; verdict will be KILL unless a +50R flip in 6 days** — treat as dead walking.
3. **PREREG-AGESHORT:** cell T n=0 forward (needs neutral × age6-12 × NY h14-21 × SHORT; regime hasn't cooperated). C1 n=6. Dead-sample risk real; park decision Sep 6 per policy.
4. **PREREG-LONDHOLD:** 299 eligible / 5 days / 28 syms, stop_atr guard clean (med=min=max=10). Counts-only until Sep 6.
5. **AMENDMENT (registered row "NY 8h/24h bear", before any verdict — measurement binding):** the registered gate text said "each variant avg > +0.10R" without regime qualifier, but the row's basis figures were bear-subset. Binding clarified: **G0 read is bear-cohort ONLY** (matches row purpose "Bear-regime enablement"). Justification for acting now: this week's bull-only fresh data shows why — ny_flush_buy_24h n=134 avg **−0.751** in bull vs +2.84 full-window bear; follow_6h_london n=8 −1.18 and fade_6h_london n=9 −0.84 in bull vs +4.5 bear. The edges are regime-conditional BOTH ways; an all-regime gate would falsely kill a correctly-scoped bear row. Amendment disclosed here, before first formal read.
6. **NY flush 1h (G1 candidate):** Aug23+ n=383 avg +0.427 net +163.7 WR62.1% 7d — best NY variant this week, extends its Jul23+ survival read (+0.215/n77). G1 evaluation queued next loop per audit #5.
7. **Asia pump short 4h:** Aug23+ n=120 avg **+0.756** net +90.8 WR64.2% — strongest live-relevant book this week; s6 (+0.764) and s8 (+0.844) variants even better. No gate change; noting regime tailwind.
8. **London bull burst:** Aug23+ n=558 avg +0.145 net +81.1 — steady, config argmax unchanged.
9. **New G0s (registered Aug 30):** NY 8h/24h bear + London follow/fade 6h bear — all n=0 bear-cohort fresh (no bear regime). Accumulating; first bear-day data will feed Sep 20 reads.
10. **v65_strict_long / v65_strict_ny_long:** appeared in shadow Aug23+ (n=18, avg −0.618) — defined in signal_shadow.py:440 as bar/long 4.0/3.0 variants, no registered row. Unregistered tracking only; do not read for decisions.
11. **Config revert (owner order):** NY session `allowed_btc_regimes` bear enablement (wired early on Aug 30 against the registered Sep 6 gate) REVERTED same day — back to [neutral, bull]; `regime_stop_atr.bear` removed. Sep 6 gate unchanged and still governs.
12. **Tier_c 8-day review (owner order, same day as audit #6):** SUIUSDT and GRAMUSDT REMOVED from `tier_c_experimental` in v5_forward_test.yaml. SUI: persistent bleed −0.200 avg/n399, 5-of-8 days negative, cumulative −80.7R, ex-bleed (ex Aug25/28) still −0.121/n247 — fails the +E floor's spirit in both scopes. GRAM: n=2 signals in 8 days (dead market post-TON-delist), un-evaluable. **Process note:** no removal rule existed at the Aug 22 wire-in (only graduation floors); this removal is discretionary. Mirror rule NOW registered for future G0 wire-ins: *n≥100, ≥5d, avg < −0.10R ex-book-bleed-days → remove*. Kept: TRUMP (−0.136 headline is bleed-day concentration; ex-bleed +0.044/n516), PUMP (two-sided variance, not bleed), HYPE/AVAX/others (flat-to-positive). Next tier_c review: 30d window, ~Sep 21.

**Variance flags (Aug23+, |avg|≥0.5, n≥15, outside known axes):** ny_flush_buy_4h_open_s6 −0.496/n159 · open_s4 −0.478/n180 · open family broadly red while base flat — open-price fills underperform stop-price fills in bull; consistent with prior open-fill studies, no new action without pre-registration.

---

## PREREG-AGESHORT — Pre-registration 2026-08-23: Regime age gate, SHORT-only refinement (`age_gate_short_only`)

Registered: Sun 2026-08-23 (commit with this edit) · Stage: G0 measurement · Forward-only window starts **2026-08-24T00:00Z** · Parent row: "Regime age gate" (register). No gate code exists for this variant at registration time; nothing wired.

### Motivation (in-sample, disclosed up front)
This is a POST-HOC refinement carved out of the same 14d window that weakened the parent row's strong form. Disclosed basis: neutral × age 6-12 bars (24-48h, `btc_regime_age_bars`, 4H units) × NY h14-21 splits by side — SHORT n=48 avg −1.857 / net −89 vs LONG n=51 avg +0.87 / net +44. Whole-cell cut nets ≈ +0.45/trade but removes 51 winning LONG entries. Claim under test: the toxic signal is **SHORT-only**, and cutting only SHORTs dominates both no-gate and whole-cell alternatives.

### Hypothesis
In neutral regime, NY h14-21, entries at regime-age 6-12 4h-bars carry negative expectancy for SHORTS specifically; LONGs in the identical cell do not.

### Population & cell definition (identical to parent row for comparability)
- Source: `shadow_trades` status='closed' (full closed set per house convention, NOT would_live_accept subset), all books.
- Cell T: side=SHORT ∧ btc_regime=neutral ∧ btc_regime_age_bars ∈ [6,12] ∧ session=ny ∧ hour_utc ∈ [14,21].
- Control C1: side=SHORT, same session/regime/hours, age OUTSIDE [6,12] (<6 or >12).
- Mirror M: side=LONG inside cell T (diagnostic only, not a promote requirement by itself).

### Paired arms (identical trade set, three-way)
A = no gate · B = whole-cell cut (parent row's rule, both sides blocked) · C = SHORT-only cut. Decision prefers simplicity: if C does not clearly beat B, ship nothing new.

### Floors (ALL required before any call)
n ≥ 30 cell-T trades · ≥ 5 distinct entry days · top-day < 40% of arm-C avoided-PnL · effect survives `btc_realized_vol_24h` tercile control (present in middle tercile, not exclusively the hottest) · unit sanity: ages reported in 4H bars only.

### Promote criteria (all floors + all three)
1. Δavg(T − C1) ≤ −0.30R fresh-window (deliberately stricter than parent's reproduction bar — post-hoc origin demands it);
2. cell-LONG avg ≥ +0.20R fresh (cutting M would measurably hurt — otherwise fold back into parent's whole-cell row);
3. C beats B by ≥ +0.10R/trade on the paired set (else B-or-nothing on simplicity grounds).
Promote ⇒ write side-conditional gate proposal (G1→G2 ladder; NEW loader/engine code — side-aware `max_regime_age_bars`). Code written ONLY after promote, never at registration.

### Kill criteria (any ONE kills)
Fresh Δavg(T − C1) ≥ 0 · cell-LONG ALSO ≤ −0.30R (signal is whole-cell after all → fold into parent row) · top-day > 40% · rvol control erases effect · n < 15 by Sep 20 → dead-sample park (no silent extensions beyond one).

### Peeking policy
Weekly Sunday loop may report **counts only** (n, distinct days) before Sep 6 — no R-values. First expectancy read **Sun Sep 6** in parallel with parent row's OOS confirm. Formal call **Sun Sep 20**; if floors unmet on n, ONE extension to Oct 4, then park.

### Honesty notes
Discovered in-sample on the exact window cited above; the −1.86 figure will regress toward the mean. Thresholds (−0.30, three-way dominance, vol control, day-concentration) are sized for a post-hoc find, not a fresh hypothesis. If Sep 6 shows T between −0.30 and 0, the correct output is "not confirmed", not a lower bar.

### Measurement binding (amended 2026-08-23, BEFORE window open — canonical metric)
Reader of record: `research/gate_weekly_read.py` PREREG-AGESHORT block (single read path). Metric: `shadow_trades.pnl_atr`, full closed set, `hour` column (UTC), `btc_trend_state`/`btc_regime_age_bars` as of entry row. DISCLOSED: the ad-hoc motivation figures quoted above (SHORT −1.86/n48 · LONG +0.87/n51 · fresh −0.45) came from session queries whose exact filters do NOT reproduce under the canonical binding (no statistic on the canonical cell returns −0.45; values bounded [−10,+3] rule out winsorizing). Canonical in-sample read of the same Aug16+ window: **T n=99 E −2.07 (median −0.72, 2 days) · C1 n=47 E −0.18 · M n=178 E +2.64 · worst-day 107% of T sum** — direction of the side-asymmetry is identical and STRONGER under the canonical metric. Thresholds unchanged: Δ ≤ −0.30 was already sized conservatively; the canonical Δ(T−C1) in-sample is −1.89. All Sep 6/Sep 20 reads report canonical numbers only; the quoted session figures are superseded for decision purposes.

## London-bull config mining (2026-08-24, shadow-db grid)
Population: `burst_follow AND session='london' AND side='LONG' AND btc_trend_state='bull'`, closed, deduped (symbol,entry_time,side): **n=193, 11 distinct days Jul15–Aug23** (weekday core n=115 / 9d). Harness: 5m-kline replay, **validation 193/193 exact** vs `pnl_atr` (R=pnl_atr/stop_atr). Scripts `/tmp/london_bull_{grid,exit_grid,robust}.py`; bar cache `/tmp/london_bull_bars.json`.

Entry axes:
- imb axis untestable <0.5 (writer applies min_imb; all 193 rows ≥0.5). Raising to ≥0.7 cuts n 90%, no E gain (+0.031 vs +0.062) → keep 0.5.
- hours 8–13 each ~positive avg; h12 weakest (~0E). No narrowing justified.
- decile≥2 avg +0.049 vs dec1 +0.030 but −74% fills → reject as gate.
- regime age >18 bars (>72h): best avg (+0.069) but n=16 / 1 day. LONGs unhurt by stale age (mirror of SHORT toxicity in PREREG-AGESHORT) → no LONG age gate.
- Sat avg −0.000 wr42% (n=62) vs weekday +0.048 → weekend exclusion validated.

Exit grid (weekday core, SL10/TP3): 30m **+0.048** | 60m +0.043 | 90m +0.058 | 120m **+0.071** | 180m +0.080 R/trade. TP4@120m +0.065 < TP3@120m; TP5 = tie. Keep SL10/TP3/120m (= live config).
Robustness 120m-vs-30m delta: helps 6/9 days, 3/5 weeks (W31 −1.44, W33 −0.78); survives excl-top-day (+0.048 vs +0.039). Loss days = chop (Jul23/29, Aug12).
Hour×horizon: h13 decays with hold (+0.074→+0.040→+0.027); h11 improves (+0.021→+0.160→+0.222).

Caveats: 9 distinct days, in-sample optimization, topday Aug21 = 51% of 120m net, bull episodes only.

**Verdict**: current live London-bull arm config is the argmax of the tested grid — no immediate change. Candidate refinement (UNDERPOWERED, n~20/cell): hour-conditional hold (≤h11 extended to ~180m, h13 capped short). If pursued → preregister PREREG-LONDHOLD counts-only forward; do NOT wire from this read.

### Addendum (2026-08-24b): TP-vs-hold head-to-head (weekday core n=115, SL10)
TP3@120m +0.071 | TP2@120m +0.074 | TP1.5 +0.064 | **noTP@120m +0.080 | noTP@180m +0.099** R/trade. Removing the TP entirely dominates every TP level tested; longer hold adds more.
Consistency noTP@180m vs live: better 5/9 days, 3/5 weeks (W30 −0.84, W31 −0.54); survives excl-Aug21 (+0.089 vs +0.048/tr). Cost: wr 64→58%, larger chop-day losses (Jul23 −0.87Δ, Jul29 −0.54Δ).
Provenance note: the historical "+32R" reference cell = Σ pnl_atr in ATR-units through Aug20 (n=83) = **+3.2R canonical**. Sun Aug23 alone = +11 ATR-u = +1.1R (n16); Sat Aug22 = −0.01R (n62) — weekend avg≈0 is one flat Saturday, two data points total.
Status: noTP@180m is an in-sample leaderboard on 9 distinct days with trend-day concentration — NOT wired; candidate for PREREG-LONDHOLD forward test alongside hour-conditional-hold arm.

## PREREG-LONDHOLD — London-bull exit refinement
**Registered:** 2026-08-24T05:45Z. **Status:** ACTIVE, forward-only.
**Registration integrity:** window FROM=2026-08-24T00:00 but population is London h8-13 UTC Mon-Fri ⇒ earliest eligible entry is 2026-08-24T08:0xZ, i.e. ~2.2h AFTER this registration. Zero look-back by construction.

**Motivation.** In-sample exit grid (weekday core n=115, 9 days): live TP3@120m = +0.071 R/tr; noTP@180m = +0.099 (leaderboard); hour-conditional hold underpowered (~20 trades/cell). Exit refinement is the lowest-ranked lever in the E²/V ladder — this prereg uses a deliberately conservative promote bar ABOVE the in-sample point estimate.

**Population binding (exact):**
`shadow_trades WHERE status='closed' AND strategy='burst_follow' AND session='london' AND side='LONG' AND btc_trend_state='bull' AND entry_time>='2026-08-24T00:00'`, dedup(symbol, entry_time, side), weekday dow∈{1..5}. `liq_imb>=0.5` is an ASSERTED GUARD (held on all 193 in-sample rows; live gate enforces it) — violating rows are excluded and counted, never silently kept.

**Metric bindings (frozen to validated harness, commit 435433e, 193/193 exact):**
- LIVE leg: `R_live = pnl_atr / stop_atr` (canonical reader metric)
- Replay legs: 5m klines (fapi), entry index = bisect_right(bar_opens, entry_ms); collision order **STOP-FIRST**; time exit = Nth bar close; `R_arm = replay_atr_units / stop_atr`
- Paired endpoint per trade: Δ = R_arm − R_live
- Validation gate at every R-read: TP3@6-bar baseline replay must match stored R on ≥90% of pairs (|dR|<0.05) and incomplete pairs ≤10%, else the read is VOID → counts only

**Arms:**
- **T1 (primary):** SL10, no TP, hold 36 bars (180m). meanΔ(T1−LIVE) decides.
- **T2 (secondary):** SL10/TP3 kept, hold 48 bars if entry hour ∈ [8,11], else 24 bars. Evaluated only if T1 not promoted.
- Descriptive-only (no promotion path, anti forked-path): noTP@120m, TP2@120m.

**Floors (both arms):** n_pairs ≥ 30; distinct days ≥ 5; top-day ≤40% of ΣΔ; LIVE-leg E ≥ 0 in window (else ARM-LEVEL REGRESSION — exit tuning moot, route to arm review).

**Decision rule:**
- PROMOTE arm → propose wire change: meanΔ ≥ **+0.05 R/tr** with all floors + validation gate. T2 additionally requires early-cell (h8-11) n_pairs ≥ 20.
- KILL arm: meanΔ ≤ −0.05 with floors met → keep live, park question permanently.
- Else INCONCLUSIVE → keep live.

**Peek policy & timeline:** counts ONLY (n/days/symbols/hour-hist, no R values) until 2026-09-06T00:00Z. First R-read Sun Sep 6. Formal call Sun Sep 20. ONE extension max → Oct 4, then park.

**Power honesty:** in-sample point estimate meanΔ(T1) = +0.028 R/tr < promote bar +0.05. Absent a truly larger effect the expected outcome is NO-PROMOTION (keep live config). This prereg exists to catch a real ≥+0.05 edge or kill the leaderboard — it is NOT sized to confirm the in-sample estimate.

**Non-goals:** no wire change before promotion; no post-hoc arm substitution; descriptive arms may be quoted but carry no decision weight.

## AMENDMENT 2026-08-24T07:53Z — LONDHOLD: live wired to T1 config on OWNER OVERRIDE
Owner instructed wiring London bull to the 180m hold BEFORE first eligible entry
(window opened 08:00Z; change live 07:53Z, commit follows). Config-only:
`config/live_burst_ny_asia.yaml` → burst_follow.session_rules.london:
`tp_atr 3.0→999.0` (=noTP per config convention), `time_bars 24→36` (=180m @5m bars).
Backup: config/live_burst_ny_asia.yaml.pre_wire_20260824.
Consequences for PREREG-LONDHOLD (test design INTACT):
- Shadow UNCHANGED at TP3@30m (verified: holds ≤30min every day; LONG replay 38/38 Aug24) → recorded `pnl_atr` and reader baseline/validation gate still bind to the old exit rule. Reader unaffected.
- Baseline arm is now a FROZEN COUNTERFACTUAL (the recorded TP3@30m shadow book per the metric binding above — NOT replayed, NOT 120m; earlier draft said "replayed TP3@120m", contradicted both the frozen binding and the verified shadow exit rule; corrected 2026-08-25T08:20Z audit). Δ(T1−baseline) grades the candidate against the retired exit rule — which is the question that matters.
- Deployment preceded the forward verdict: this is an in-sample leaderboard action (+0.099R/tr weekday core n115; 58% wr; trend-day concentrated; excl-Aug21 +0.089 vs +0.048). Prereg stays alive to grade it; kill criteria (meanΔ ≤ −0.05 with floors) can now also trigger a REVERT recommendation.
- Accepted trade-off: −6pp win-rate, fatter right tail, no TP cap ⇒ larger single-trade dispersion on live equity.

## Amendment 2026-08-24T14:12Z — London bull pinned LONG-only (owner)
Owner confirmed intent: London-bull arm is LONG-only; pump-cascade SHORTs are not part of the arm.
- Live `config/live_burst_ny_asia.yaml` london rule: new structural field `allowed_side: LONG` (engine drops resolved-side mismatches, reason=side_pin). `pos_imb_only: true` retained — it already made live de-facto LONG-only; pin makes it explicit.
- Shadow harness UNCHANGED (no pos_imb_only / no pin) → keeps recording both sides as counterfactual; lets us measure what the pin forgoes.
- PREREG-LONDHOLD unaffected: reader binds side='LONG'.
- Restart 14:11:30Z, stop 1s, journal clean. Note: shadow burst_follow london/bull fired 40 SHORTs today (+1.62R, WR53%) — live never would have taken them (pos_imb_only).

## Amendment 2026-08-25T06:31Z — TEMPORARY micro-equity sizing bump (owner, UNCOMMITTED)
Balance fell to ≈$8; at 10% risk and ~15.4% stop width notional ≈ $5.2 sits at Binance's $5 min-notional edge → fill-starvation risk through London. Config-only bump, intentionally left uncommitted:
- `symbols.defaults.risk_pct` / `risk.default_risk_pct` / `burst_follow.risk_pct`: 10.0 → **14.0**; `risk.reduced_risk_pct`: 7.0 → **12.0**.
- Scope note: burst-follow sizing reads `burst_follow.risk_pct` for ALL sessions while active; motivation was London-only, but NY skips Mon and Asia is neutral-gated ⇒ effective exposure today = London only.
- Same-day code fix (uncommitted, NOT yet active — ships next restart): burst-follow previously bypassed the drawdown/consecutive-loss reducer (`main.py` consumed bf_cfg.risk_pct unconditionally); reducer now CAPS bf risk at reduced_risk_pct whenever the brake trips, bf risk stays the ceiling otherwise. Today's London trades size at flat 14% regardless of brake state.
- Backup: config/live_burst_ny_asia.yaml.pre_sizing_20260825. Restart 06:31Z clean, journal 0 ERROR since.
- Revert trigger: London window close or equity ≥ ~$12, whichever first (owner call) → restore backup + commit decision.

## PREREG-WKNDNY — Pre-registration 2026-08-25: bull-regime weekend NY-buy window (`weekend_ny_bull`)
**Registered:** 2026-08-25T06:45Z. **Status:** ACTIVE, forward-only.
**Registration integrity:** window FROM=2026-08-29T00:00Z (next Saturday 00:00 UTC). Aug22/23 and all earlier weekends are IN-SAMPLE and excluded by construction. Zero look-back.

**Motivation.** `ny_flush_buy_4h` is live-excluded Sat/Sun (`exclude_weekdays [0,5,6]`). Since Jul1 the exclusion left ≈ +7.4R on the table — but decomposed: neutral weekend days ≈ −2.0R combined; bear +3.4/+2.6; **bull +3.19 (Aug22, n30) + 4.21 (Aug23, n35)** = +7.40R/n65 = +0.114 R/tr on just TWO days. Hypothesis: NY-flush-buy edge persists on weekends under persistent bull regime; weekday-costume effect, same structure as Monday finding. This prereg grades it forward before any config change.

**Population binding (exact):**
`shadow_trades WHERE status='closed' AND strategy='ny_flush_buy_4h' AND btc_trend_state='bull' AND CAST(strftime('%w',entry_time) AS INT) IN (0,6) AND entry_time>='2026-08-29T00:00'`, dedup(symbol, entry_time, side). Strategy is LONG-only by construction (verified: 133/133 weekend rows since Jul1 are LONG — assert at every read, exclude+count violations). All recorded hours h14-21 kept — NO hour pre-filtering (post-hoc hour picks are how this plan's forks happen). Regime-tag integrity assert (added 2026-08-25T08:20Z, mirrors liq_imb pattern): reader must COUNT rows where `btc_trend_state IS NULL` (writer warm-up artifact Jul31–Aug7: 25 ny rows, ZERO weekend rows affected) and report them excluded-with-count — never silently dropped. Any NULL-regime row ON an in-window weekend day ⇒ read VOID, counts-only until re-audited.

**Metric binding:** `R = pnl_atr / stop_atr` per trade (canonical reader metric, identical to LONDHOLD live leg); E = mean(R). Reader implementation DEFERRED but must be committed and smoke-tested BEFORE first R-read (LONDHOLD pattern: peek-mode + backdated validation gate).

**Descriptive arms (no decision weight):**
- C1 control: weekday bull entries, same strategy/hours → weekend-vs-weekday gap.
- C2 sanity: neutral-regime weekend entries → expected ≤0; a positive C2 does NOT promote anything.

**Floors:** n ≥ 30; distinct weekend days ≥ 5; top-day ≤ 40% of ΣR (in-sample basis was 57% on one day — this floor is the honest bar the in-sample data would FAIL).

**Decision rule:**
- PROMOTE (→ G1 eval + execution-feasibility review): E ≥ **+0.05 R/tr** with all floors met.
- KILL: E < 0 with n≥30 and days≥5 met → park permanently; no re-proposal without new regime structure.
- Else INCONCLUSIVE → keep `exclude_weekdays` unchanged.

**Pre-wire gate (beyond R verdict):** promotion does NOT auto-wire. Weekend books are thin; shadow assumes mid-price instant fills (London would_live_accept subset showed −6.0R raw-shadow-missed fragility). Any wire proposal requires a fill-quality review (weekend spread/slippage proxy, partial-fill risk at min-notional sizes) presented to owner first.

**Peek policy & timeline:** counts ONLY until 2026-09-06T00:00Z. First R-read Sun Sep 6 — NOTE: only 2 weekends (Aug29/30, Sep5/6) accrue by then ⇒ max 4 days < floor(5), so first read is EXPECTED to be counts + extension declaration unless n/days already impossible (that is not a failure). Formal call Sun Sep 20 (5 weekends accrued: up to 10 days). ONE extension max → Oct 4, then park.

**Power honesty:** in-sample point estimate +0.114 R/tr on 2 days, top-day 57%. Promote bar +0.05 ≈ 44% haircut for regression to mean; still, with ~8-13 trades/day expected, n≈65 by Sep 20 gives CI half-width ≈ ±0.09-0.11 R/tr at observed dispersion — this prereg is powered to catch a REAL edge at roughly half in-sample strength or kill a zero, NOT to fine-resolve anything between. Absent real effect, expected outcome is INCONCLUSIVE→keep excluded.

**Non-goals:** no changes to asia_pump_short_4h weekend handling (Sat −1.8 / Sun −5.7 since Jul1 — bleeds, stays excluded); no london_burst_fade weekend arm (sign-unstable: Sat −7.5 / Sun +8.1); no live config edit before PROMOTE + exec review; no post-hoc hour/symbol subsetting.

## Amendment 2026-08-25T20:11Z — Post-mortem: Aug 25 live burst_follow day (−1.90R realized; ops findings; ZERO prereg impact)

Scope: owner-requested forensic review of the day's live session. Everything below is DESCRIPTIVE session observation — none of it feeds a prereg read (LONDHOLD metric binds shadow rows + frozen replay, not live fills; WKNDNY window opens Aug 29).

**Day P&L:** 12 closed trades **−1.90R / −$1.91 gross**. Batches: 08:15+08:46 → −0.48R · 09:56 → −0.41R · 12:05-15 → −1.08R (owner external closes 14:00Z, scratch-EV validated earlier) · 13:10-35 → +0.07R. Evening book (NEAR/APT/XRP/ZEC, entered 17:10-19:35) still MANAGING at log time (~−0.34R marked); time-exits due 20:10-22:35Z.

**Attribution (descriptive):**
1. *Exit-config exposure, not execution.* Cumulative MFE across the 12 closed = **+0.54R vs final −1.89R**; best single-trade peak only +0.36R; three entries never traded above entry (APT/SOL/ZEC-noon — chased-top signature). Matched-pair against the FROZEN TP3@30m counterfactual on live's own signals: counterfactual +2.08R vs live −1.23R (9 matched / 3 unmatched); hour-weighted counterfactual E ≈ +0.81R. This is the accepted trade-off logged in the 2026-08-24T07:53Z amendment ("larger single-trade dispersion on live equity") realized on a fade-tape day (BTC 81.3k @02:00Z → 78.8k). No action — T1 grading belongs to the prereg reader.
2. *Hour mix.* Live fired into the two weakest LONG hours of the day (shadow LONG-closed basis: 08z avg −0.19R/n15, 12z −0.57R/n21 = worst-of-day). Cluster detector fires into intraday downtrend pockets; the bull gate is 4h-grain. Any intraday-regime idea goes through the G0 ladder — NOT a mid-window edit.
3. *Ops findings.*
   - CORRECTION to interim verbal report: the 13:25:45Z NEAR reject was pure exchange margin (−2019), NOT a cap-bypass race — book held 5/6 at send time and portfolio logic behaved correctly. Real issue = micro-equity feasibility (cf. Aug 24 ADA skip at $0.67 notional < $5 min): account cannot reliably fund a 6-slot book at current balance. Proposed for owner decision: pre-send free-margin/min-notional pre-check (skip-and-log instead of exchange rejects); consider whether max_positions should reflect fundable slots at current equity. OWL touched no code.
   - Restart gap 14:05Z (repeat of Aug 24 pattern): a burst_follow bar-close signal inside the stop/start window was lost. Standing proposal unchanged: avoid restarts ±5min around 5m bar boundaries when slots are occupied + startup catchup replay (paper-first).
   - Telegram alert escaping failure was a one-off (last failure 2026-08-24T12:21Z; sends verified working all day Aug 25). Harden payload escaping at next code-touch.

**Non-actions (peek policy):** LONDHOLD counts-only until 2026-09-06T00:00Z — today contributes shadow rows only; the live wiring outcome carries zero formal weight in any read. No config/gate/sizing edits beyond the standing uncommitted 14% bump (revert trigger unchanged).
### 2026-08-25T20:17Z — Addendum: live bot WS hang (found post-report)
**Symptom:** no TP/trail/time exits since 19:35Z. NEAR/APT time-exits overdue.
**Root cause:** exchange FIN'd kline+1 WS ≈19:35Z (post-ZEC-fill). Sockets sit CLOSE-WAIT w/ unread CLOSE frames; main loop idle in epoll. No ping/pong timeout, no reconnect watchdog, no staleness alarm → silent zombie. Liq socket still ESTAB so entries remain armed into an exit-dead book.
**Evidence:** ss CLOSE-WAIT fd19/23 · candles_held frozen 36/31/7 · r_path froze 19:34:59.999 · py-spy epoll-idle · journal silent since 14:05.
**Immediate action proposed:** systemctl restart bitana-live-burst-follow (recover_positions reloads 4 open). AWAITING OWNER GO — not executed.
**Fix spec added:** WS ping_interval=20/timeout=10; staleness guard >90s no-msg → reconnect; alert if any feed silent >2 bars.

### 2026-08-25T20:32Z — RETRACTION of 20:17Z addendum
20:17Z "WS hang" was a false alarm. Evening entries are NY-tier time_bars=48 (240m): exits due 21:10/21:35/23:35Z, none overdue. candles_held was counting correctly; CLOSE-WAIT sockets were Telegram DC (149.154.x), not exchange. Restart executed on owner instruction ~20:22Z was harmless: recover_positions restored all 4 positions, counting verified resuming post-restart. WS-watchdog fix spec retracted (no feed-drop evidence). Standing findings unchanged: exit-config giveback, hour mix, NEAR margin-reject pre-check proposal, 14:05 restart gap.
**Lesson:** resolve socket peer IPs before attributing feed failure; read per-position stored time_bars from signals.signal_data before declaring exits overdue.


### 2026-08-25T20:37Z — Amendment: 14:00Z external_close trio resolved (owner action)
Owner manually closed WLD/ZEC/ADA ~14:00Z to free slots for NY hour-1 — not a bot fault; exit_reason=external_close correct.
**Counterfactual** (exchange klines, held to scheduled tb=36 exits): actual −1.08R vs hypothetical −1.21R → **+0.13R edge to the early close** (WLD −0.167 vs −0.214 · ZEC −0.514 vs −0.723 · ADA −0.400 vs −0.269; ADA only improver).
**Freed-slot yield:** NY entries 16:10–16:36 realized +0.075R (XRP +0.089 / SOL +0.003 / ETH −0.017) + 4 positions open at report time.
Day stands: 12 closed −1.89R ($−1.91) = time-exit book −0.81R/9 + manual slot-free −1.08R/3 (counterfactual-fair). No change to standing findings.


## 2026-08-25T21:30Z — Amendment: day close −5.21R; DAILY_LOSS brake found alert-only; tb=48 vs tb=36 evening counterfactual
- Day final: 16 closed, **−5.21R / $−4.62** (~−51% equity). Evening book (17:10–19:35Z LONGs): APT −0.905R SL@bar47, XRP −1.034R SL@bar43, NEAR −0.533R time_exit@bar48, ZEC −0.847R SL@bar19 = −3.32R.
- **FINDING (code, not strategy)**: `risk/brakes.py record_loss()` emits BRAKE_TRIGGERED events for DAILY_LOSS but never sets `is_paused` — settings.yaml comment says "-> pause", code pauses nothing. Only EQUITY_PAUSE/EQUITY_SHUTDOWN block entries. Book remained armed all evening after 4 CRITICAL alerts. Owner to decide intended behavior.
- State now: equity $3.45, is_paused=0/is_shutdown=0, reduced mode self-latched (consec_losses=5, reduced_remaining=4 @12%), daily_realized_loss=0.489 vs 0.20 limit (resets 00:00Z).
- Counterfactual (tb=36 exits at entry+180m vs actual): APT −0.216 vs −0.905 · NEAR −0.196 vs −0.533 · XRP −0.446 vs −1.034 · ZEC ~−0.71 prov vs −0.847 → tb36 ≈ −1.57R vs actual −3.32R (**+1.75R cost of 240min hold tonight**, n=4 same-direction tape).
- Market: BTC topped ~80.7k 05:00Z, ground down 16h to day low 77,808 AT 21:00Z (during holding window); evening drift −0.99% ≈ morning −0.84%, ATR5m evening 156 < morning 173 — grind, not vol event.
- Verdict per prereg discipline: single correlated evening, n=4 → tagged as in-window observation ONLY. Both hold lengths have now looked bad within 36h ⇒ exit-config sensitivity high; PREREG-LONDHOLD forward window decides. No live re-wire off tonight's sample.

## 2026-08-25T22:20Z — Regime-selector verdict + PREREG-GRINDVETO registration

Owner re-asked whether the regime selector is ideal after BTC ground −3.5% over 16h while `btc_trend_state` stayed 'bull'.

**Selector anatomy** (`engines/btc_regime.py`): 4h EMA200 + ADX(14)>25 → bull/bear/neutral. Macro filter by construction. Measured 2026-08-25T22:05Z: bull, px 78,591 vs EMA200 67,813 (+15.9%), ADX 44.2. Bear flip requires another −15.9%; neutral flip requires ADX→25. A 16h intraday grind is invisible to it **by design**, not by malfunction.

**Missing layer identified — intraday tape context.** Shadow LONGs since Aug-4 split by BTC trailing-12h return AT ENTRY (entry-time knowable, no leak):

| strategy | up-cell (>0%) | grind-cell (−2..0%) |
|---|---|---|
| ny_flush_buy_4h (LIVE-mirrored) | n=205 E=+0.786R WR61% | n=107 E=**−0.727R** WR51% |
| ny_flush_buy_4h_s4 | n=222 E=+0.683R | n=143 E=−0.908R |
| burst_follow | n=699 E=+0.13R | n=361 E=+0.18R (no effect) |

Tonight's four live losses (APT/NEAR/XRP/ZEC, all `shadow_strategy=ny_flush_buy_4h` side_mode=follow) entered at BTC-12h ≈ −1.5% — inside the bleed cell. Correction of record: these were ny_flush_buy_4h follow fills, NOT burst-follow (earlier mislabel).

Today-evening shadow flush book: 177 closed, WR 19%, Σ≈−733R in their own 8.2-ATR-stop units — same-direction confirmation.

### PREREG-GRINDVETO (registered 2026-08-25T22:20Z)
- **Claim tested**: entry-time BTC-12h-return ∈ (−2%, 0%) marks a toxic cell for LONG flush-buy arms; a veto there improves expectancy without starving healthy-bull entries.
- **Veto (frozen)**: block LONG ny_flush_buy* arms when BTCUSDT trailing-12h return ∈ (−2%, 0%), computed from hourly closes ≤ candidate-bar close. No other cells affected. Does NOT apply to burst_follow or any fade SHORT arm (no cell effect shown).
- **Forward-only** from 2026-08-26T00:00Z. Shadow stays UNGATED (2026-08-23 owner decision); prereg reads conditional stats off the same feed.
- Counts-only until 2026-09-06; first R-read 2026-09-06; formal call 2026-09-20; one extension max 2026-10-04.
- Metric: `shadow_trades.pnl_atr/stop_atr` canonical R, per-arm, cell-split per frozen rule. Primary arm: ny_flush_buy_4h.
- Floors: n≥30 AND days≥5 in BOTH cells (primary arm).
- Promote: grind-cell E ≤ −0.30R AND up-cell E ≥ +0.30R AND (up−grind) delta ≥ +0.50R/tr → propose live veto wiring (owner gate required).
- Kill: grind-cell E > 0, or up-cell starves (n<30) → drop, log, no re-registration of same claim.

### Exit-hold sensitivity (tier-neutral observation, NOT a config option set)
Evening four, marked R at time-since-entry: +60m Σ+0.42R · +90m −0.32R · +120m −0.25R · +180m −0.86R · actual(tb48/SL) −3.32R. All four positive at one hour, aggregate negative by 90min. Recorded as decay-shape info only; 180-min is the LONDON tier parameter and was never an NY option (owner correction noted).

### 2026-08-25T22:55Z — GRINDVETO full-history backtest (owner-requested)
Ran veto against ALL shadow history (Jun27→Aug25): 6,246 closed LONG flush rows; 2,593 in grind cell.
- Full-history grind-cell E = **−0.052R (scratch)**; up-cell +0.506R. Veto blocks Σ−134R while removing ~26% of entries → **blanket veto NOT historically justified**.
- Month split of same cell: Jul +0.457R (n=1,262) vs Aug −0.534R (n=1,331) → episodic/regime-dependent, not structural. Earlier "toxic cell" framing (Aug-4+ window) retracted as subwindow artifact.
- Negative grind days: 9 days Σ−2,081R (worst: Aug-25 −776R on n=424, Jul-27 −337R, Aug-12 −272R, Aug-10 −267R). Positive grind days: 11 days Σ+1,876R (best Jul-20 +376R).
- Staleness conditioning (hours since BTC fresh 24h-high) tested as separator: every bucket flips sign Jul↔Aug → no stable separator found; no refinement registered.
- Prereg unchanged (counts-only G0 from Aug-26). Early live wiring NOT recommended on this evidence.

### 2026-08-26T00:10Z — Owner Q: neutral-rules counterfactual + regime-parameter sweep
Q1: Could BTC have been tagged NEUTRAL under other selector parameters? NO within trend family:
ADX(14,4h) never <56 on Aug25 (thr 25); price +5.9%/+11%/+16% above EMA50/100/200 at day low;
2h-bar selector stays bull (ADX 28-40); grid EMA{100,150,200}xADX{20,22.5,27.5} = zero flips Aug23-26.
Calling it neutral requires a different selector family, not different parameters.
Q2: Live-book counterfactual, trade per NEUTRAL rules during bull week:
Aug20 -0.14 -> 0 (ZEC h15 blocked) | Aug21 +0.69 -> 0 (london x13 blocked, SYMMETRIC COST)
Aug25 -5.21 -> -2.30 (london x12 blocked +1.89 saved; ZEC h19 blocked +0.85 saved; h17 trio kept
@10ATR: APT -1.00 stop@bar47, XRP -1.00 stop@bar42, NEAR -0.302 time-exit = -2.30).
Bull-week live: -4.66R actual -> -2.30R neutral-rules. Saves 2.36R but NOT the evening:
h17 NY buys are legal in neutral too and still lose -2.30R. Regime layer is not where the loss lived.
London arm status vs own kill criteria: 25/30 accepted trades, net -1.20R (-0.048/tr < +0.2R line) ->
tracking BELOW kill threshold with 5 trades to go before mandatory freeze review.

### 2026-08-25T22:40Z — Owner rulings (3), implemented + live from restart 22:36Z
1. DAILY_LOSS -> PAUSE (was alert-only): record_loss() sets is_paused on 20% cross;
   persists past midnight; /resume required; post-resume same-day entries stay gated by
   cumulative-loss soft block until 00:00Z reset. Commit b350626.
2. London arm OFF for Aug 26 (owner: won't trade at this rate; -0.048R/tr vs +0.2R kill line).
   Session rule commented out, re-enable = uncomment. Commit 037281d.
3. Reduced-mode override: none issued — latch stays 4x12%, moot while bull gates Asia and
   London off; first exposure = Wed NY evening.

### 2026-08-25T22:54Z — Tue x NY-hour observation (OPEN, next prereg gate — not actioned mid-prereg)
Owner Q: write off tonight's NY? Counterfactual without h17/h19:
- Live Aug25: ALL 4 NY losses sat inside the two evening clusters (h17 trio -3.32R incl stops @21:05-21:10Z
  w/ 33-77bps stop slippage; ZEC h19 -0.85R). Day ex-h17/h19 = London-only **-1.90R** (actual -5.21R).
  Clean counterfactual: zero live signals fired in ny h14/h16 today (signals table) — nothing given up.
- Shadow last-4-Tuesdays, LIVE-WIRED hours only {14,16,17,19}:
  h14 +16.03R (n=633) | h16 +5.71R (n=58) | h17 **-20.60R (n=162)** | h19 **-35.20R (n=117)**
  -> Tuesdays ex-17/19: **+21.7R** vs -34.1R with them (~56R swing/4wk; signs not levels — shadow R not live-scalable).
- Cross-check vs neutral-rules CF above: regime layer keeps h17 trio (-2.30R legal-in-neutral loss).
  Two independent decompositions point at Tue-evening LONG h17/h19 as the bleed locus.
- Candidate arm at next G-gate: weekday x hour gate excluding Tue ny {17,19}. Counts-only criteria.
Hygiene note: adjacent log entries stamped 22:55Z / 26T00:10Z appear LOCAL-time (UTC+2) mislabeled as Z;
future entries must use `date -u`.

## 2026-08-26T07:34Z — Amendment: London RE-ENABLED (owner), TP3/SL10/30m revert + h8/h12 drop + min-notional sizing floor
Owner instruction ("Wire London with these specs TP3/SL10/30min, drop h8 and h12; risk % has to pass the
Binance minimum, balance under $4; actually commit this time").
1. **Exit reverted to shadow baseline** TP3@SL10@30m (`tp_atr 3.0 / time_bars 6`). Rationale: the Aug24
   noTP/180m wiring had zero shadow history for this arm (all n=949 closed london LONGs ran TP3@30m) and
   tripped its kill line in 25 accepted trades (−0.048R/tr). Aug25 same-day replay of the 12 live fills
   under TP3/30m: −0.44R vs −1.89R actual (0 tp / 12 time / 0 sl). This is a REVERT to the measured
   config, not a new optimization.
2. **Hours h8/h12 dropped (OWNER OVERRIDE, post-hoc slice)** — hours now [9,10,11,13]. Shadow bleeders on
   the closed book (n=949): h8 −25R PF0.75, h12 −61R PF0.67. LOGGED AS DEVIATION: hour exclusion is a
   post-hoc slice of the same book that set the kill line; no OOS support yet. Kill criteria unchanged
   and now apply to the REDUCED-hour arm.
3. **Sizing floor**: all four risk_pct copies 14→24 (`symbols.defaults`, `burst_follow.risk_pct`,
   `risk.default_risk_pct`), reduced tier 12→21. Basis: equity $3.45, london LONG stop widths p50 3.5 /
   p95 10.0 / max 15.4 (%); notional = eq·pct/width → p95-stop trades $8.28 ≥ $5+buffer; max-width still
   clears the $5.10 skip-guard ⇒ zero silent skips on $5-min symbols. **NOT covered**: ETH ($20 min,
   fires only on <~4% stop widths ≈ half the book), BTC ($50 min, effectively dark). Dollar risk honesty:
   one stop-out = ~$0.83 ≈ 24% of equity; DAILY_LOSS brake budget (20% = $0.69) is exceeded by a SINGLE
   full stop ⇒ brake trips after one loss at current equity (owner's Aug25 hard-pause ruling applies).
4. **Reduced-mode surgery (stop-svc-first procedure)**: risk_state carried consecutive_losses=5 /
   reduced_trades_remaining=4 from Aug25 bleed → would have pinned effective risk ≤21 via reducer chain
   and partially re-silenced the book. Cleared per owner instruction (risk must pass minimum): counters
   zeroed, risk_pct_active=24, peak_equity=current_equity, DD=0. brake_state untouched (was clean).

### Watch items (tracked here + decision-log commits until wired into code)
- **h8/h12 exclusion OOS check**: shadow h8/h12 performance tracked weekly vs wired-hours arm; if the
  excluded hours flip positive over ≥4 weeks, re-open as prereg candidate (not silent re-add).
- **Kill line on the reduced-hour arm**: net/trade < +0.2R after 30 accepted live trades, or top-day
  >40% of P&L → freeze. Counter resets from today.
- **ETH partial-fill semantics**: fires only when entry stop width <~4%; log any ETH skip with width
  ≥4% so we can quantify what the $20 minimum costs the arm.
- **Fri weakness** (standing observation from earlier weeks): london exclude_weekdays=[5,6] already
  drops Fri/Sat/Sun — nothing to do, keep watching shadow Fri cells for sign flips.
- **Tue ny h17/h19** (22:54Z OPEN item): next G-gate candidate arm, counts-only criteria.
- **PREREG-GRINDVETO**: registered 25T22:20Z, full-history BT done; awaiting window data.

## 2026-08-26T12:21Z — Amendment: NY hours corrected — Tue-only h17/h19 exclusion (owner)

Owner instruction ("set the correct hours for NY"), actioning the 22:54Z OPEN item early.
New knob `SessionBurstRule.excluded_weekday_hours: dict[int, list[int]]` (0=Mon..6=Sun),
applied AFTER regime_hours resolution, wins per (weekday,hour) cell; engine gate
reason="weekday_hour_excluded". NY wired {1: [17, 19]} = Tuesday h17/h19 only.

Measured basis (closed books, live weekdays Tue-Fri):
- ny_flush_buy_4h book (session's tagged baseline), n=661: Tue h17 -31.2R avg -3.9
  (bull-regime -25.7 of it), Tue h19 -16.8 vs Wed h17 +42.2 / Fri h17 +18.4 /
  Fri h19 +23.2 -> weekday-scoped drop, NOT whole-hour (whole-hour would cut ~+60R).
- BASELINE CAVEAT (logged honestly): burst_follow book (live's actual detector
  family) full-history DISAGREES — Tue h17 +8.9R n=34, Tue h19 +12.9R n=26. The
  Aug25 bleeder finding (-20.6R/-35.2R) was a last-4-Tuesdays phenomenon there,
  not structural full-history. Decision rests on ny_flush book + recent-window
  agreement; flagged as deviation risk if burst_follow book turns out to be the
  live-faithful baseline.

Verification: py_compile both files; typed dry-load round-trip {1:[17,19]}; gate
truth-table (Tue h17/h19 blocked; Tue h14/h16 + Wed/Fri cells still fire); commit
a56a79a; restart 12:11Z with 1 open London position (ETHUSDT — second trade ZEC
time-exited 12:10:43 pre-restart); recovery verified incl. candles_held 3->5 across
post-restart bar boundaries, reconciliation advancing, zero err/warn.

Watch items:
- **Sep 1 (Tue)**: expect ZERO ny entries in h17/h19 (weekday_hour_excluded);
  any fill there = gate bug, freeze NY arm same day.
- Weekly: Wed/Fri h17/h19 shadow cells must stay positive (+60R combined at wire
  time); sign flip on either = revisit whole-hour drop question with fresh data.
- Kill criteria unchanged (net/trade <+0.2R over 30 accepted trades, top-day >40%).


## 2026-08-30T13:05Z — OWNER-ORDERED LIVE CONFIG CHANGES (audit #6 addendum, item 13)
- risk_pct 24.0 -> 10.0 (all 3 keys + reduced 21.0 -> 8.75). Basis: wallet funded $3.45 -> $24.59 USDT (verified mainnet read-only);
  24% was the min-notional floor at $3.45 equity, not an expectancy pick. At 10%/$24.59: $5-min alts clear $6.50 floor to 2.6% widths;
  ETH ($20 min) fires <~8% widths; BTC ($50 min) effectively dark (<~5% widths) — owner-accepted.
- Asia session exclude_weekdays [5,6] -> [2,5,6]: Tuesday blocked. Shadow asia_pump_short_4h Tue-neutral n=33 -0.086R PF 0.50
  (losses across all hours, h00 worst -0.25R n9); Monday-neutral is the strong cell (n=30 +0.216R PF 2.97).
- OPEN watch item (hours): plain-variant dead zone h01-05 (n=94 -0.056R avg, h03 worst -0.176R PF 0.29) vs strong h00/06/07
  (n=75 +8.9R combined). NOT replicated in tsl mirror (inverted) — no hour blocks until cross-variant evidence. Revisit at 30d.
- Restart 13:03Z: checksum 72eb7007a3c712b7, brakes clear, no EQUITY_SHUTDOWN. Service bitana-live-burst-follow.
- 13b (13:56Z): brakes.daily_loss_limit_pct 0.85 -> 0.50 (owner). At 10% risk = 5 stop-outs; worst observed day (~3.8R) scales to ~38% at 10%, 50% survives it. Checksum 004570bc3c778a20, brakes clear on restart.
- 13c (16:40Z): London hold shadows (1h/2h/3h/6h) KEPT per owner — forward window failed (Aug21+: 6h -0.006R n18, 3h -0.003R n18 vs 30min baseline +0.022R n616 PF1.45; full-history edge = July bear-regime artifact, unreachable under bull-only gate). Revisit ~Sep 30 or n>=30/variant. Kill criteria unchanged.
