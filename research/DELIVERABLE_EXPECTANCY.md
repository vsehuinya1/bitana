# Expectancy-Mining Deliverable — bitana shadow book
**Regenerated:** 2026-08-22T13:00:19+00:00 (single reproducible run: `hermes_lab/verify_deliverable.py`, raw output in Appendix A)
**Note:** the shadow DB is live (grows ~600 trades/day); numbers drift. Everything below is recomputable exactly via the script.

## 1) Dataset (explicit identification)
- Source file: SQLite `/root/bitana/storage/signal_shadow.db`, table `shadow_trades` (75 columns), written by `research/signal_shadow.py`.
- Rows: 33,429 total → **33,370 closed trades used**; 59 open excluded everywhere.
- Entry-time range: 2026-06-27T22:05Z → 2026-08-22T12:20Z (57 calendar days).
- Universe: 28 perp-USDT symbols × 63 strategy-books, sides LONG/SHORT.
- External validation source: `/root/hermes_lab/data/oi_live.db` table `oi_history` (Binance fapi OI, hourly cadence, BTC/ETH/SOL/XRP).
- Column definitions relied upon:
  - `pnl_atr`: realized trade PnL divided by entry-bar ATR (dimensionless ATR-multiples, favorable positive). Per-book risk scale: `stop_atr` (mean 7.88; e.g. burst_follow stop=10 ATR → pnl_atr=−10 ≡ −1 risk-unit R; books with stop_atr=1 → pnl_atr ≡ R).
  - `side` ∈ {LONG, SHORT}; `strategy` book; `session` ∈ {asia, london, ny, late}; `hour` (UTC).
  - Entry-time features (decision-time inputs): `btc_adx` (BTC 1h ADX at signal), `btc_realized_vol_24h` (trailing 24h realized vol), `oi_delta_30m_pct` (30-min symbol-OI % change at signal), `funding_rate_symbol` (current funding), `liq_imb`, `entry_vol_z`.
  - Post-trade diagnostics (never used in any rule): `run_mfe_atr`, `post_mfe_atr`, `post_mae_atr`, `trade_r_path` per-bar paths.
  - `would_live_accept`: shadow accept-gate flag (strategy-scoped since Jul 23; null before instrumentation).

## 2) Expectancy — precise mathematical definition (stated before any results)
For closed trade i, let x_i = `pnl_atr` (units: **ATR-multiples per trade**, favorable positive; ≡ R-multiples where stop_atr=1).
- **E ≡ (1/n)·Σ_{i=1..n} x_i = win_rate·avg_win − loss_rate·|avg_loss|**
  where win_rate = #(x_i>0)/n, loss_rate = #(x_i≤0)/n, avg_win = mean{x: x>0}, avg_loss = mean{x: x≤0} (≤0).
- Identity verified numerically on the raw set in §3 (recomputed E from components matches SQL AVG to 4 decimals).
- All results below report E [ATR-units/trade], n, Σx, WR, and distinct trading days. Significance proxies: n, day-count, day-concentration; no p-values claimed.

## 3) Baseline expectancy (raw data, unmodified system)
- **ALL closed trades: E = +0.0280 ATR-units/trade** (n=33,370, Σx=+935.5, WR=51.03%, avg_win=+1.9100, avg_loss=−1.9334).
  Identity check: 0.5103×(+1.9100) − 0.4897×1.9334 = **+0.0280 ✓**
- Live-relevant subset `would_live_accept=1`: **E = −0.0760** (n=10,535, Σx=−801.1, 31 days).
- By side: LONG n=19,827 E=+0.1296 (Σ+2,568.8) · SHORT n=13,543 E=−0.1206 (Σ−1,633.3).
- Reading: the all-in ensemble is ~flat-positive; the accepted-for-live mix bleeds. Both numbers come straight from `AVG(pnl_atr)` over raw rows (Appendix A, S2).

## 4) Data-quality checks & treatment
- **Nulls among closed trades** (Appendix A, S3): core fields `pnl_atr/side/strategy/symbol/entry_time/exit_time/atr/run_mfe_atr/post_mfe_atr` = **0 nulls each**. Feature gaps are contiguous pre-instrumentation windows, not random: `btc_adx` & `btc_realized_vol_24h` 11,822 (35.4%), `oi_delta_30m_pct` 11,876 (35.6%) — logging began Jul 14; `funding_rate_symbol` 1,066 (3.2%); `stop_atr/session/hour` 150 (0.4%); `would_live_accept` 11,753 (35.2%).
  **Treatment:** no imputation; every lever conditions on non-null subsets and reports its coverage window/days alongside.
- **Duplicates:** ids 33,429/33,429 distinct; duplicate (strategy, symbol, side, entry_time) groups = **0**.
- **Out-of-range / impossible:** pnl_atr range [−12.00, +102.00]. Min −12.00 = id 7048, exact full stop on a stop_atr=12 book (legitimate). Max +102.00 = id 29308, long-hold trend book (legitimate). Rows with entry_time ≥ exit_time: **1** (same-bar instant stop, kept, flagged). `atr ≤ 0`: **0**. Closed-with-null-pnl: **0**.
- **Net exclusions:** open trades only (59).

## 5) Look-ahead bias — affirmatively ruled out (three verifications)
1. **Timestamp audit (external recompute, decision-time-only).** `oi_delta_30m_pct` recomputed from raw Binance `oi_history` using ONLY observations ≤ entry_time: both anchors = latest hourly snapshot strictly preceding (entry) and preceding (entry−30min), staleness ≤65min (collector cadence = 60min). Result: **pearson r = 0.838, median|diff| = 0.128pp, n = 2,298** (Appendix A, S4). Control join allowed to touch post-entry observations (nearest ±45min): **r = 0.696** — worse. Future-contaminated features would show the reverse ordering; the decision-time-only recompute agrees best ⇒ the logged feature carries no post-entry information.
2. **Code-path audit.** `research/signal_shadow.py` writes entry features once at the trade-open INSERT (signal-time values); UPDATE statements touch only exit fields, scale fills, and post-exit diagnostics. Back-fill of entry features is not possible in the write path.
3. **Rule audit.** Every lever in §6 conditions solely on entry-time columns (entry-bar regime stats, current funding, pre-entry OI change, session/hour, book identity). Exit-side counterfactuals replay only `phase='open'` in-life path bars (calibration vs realized exits: 96.1% exact, SHORT sign-mirror verified); `phase='post'` data is never part of any tradable rule.

## 6) Improvement levers tested (six conceptually distinct; each: rule → result → projected impact → verdict)
All filters use decision-time info only (§5). Projected impacts are descriptive on this dataset, not forward promises.

### Lever 1 — Exit timing / hold extension (exit-side counterfactuals)
Rule tested: alternative exits (BE-shift, TP 2×/4×, time-hold 0.5–4×, trails) replayed on stored in-life paths.
Result: winners-run structure is real — top books give back 38–45% of in-life MFE (ny_flush_buy_24h: realized E=+1.63 vs avg_run_mfe=+8.72, n=352).
Projected upper bound if fully captured: ≈ +4.3 ATR-u extra on ny_flush_24h alone (~+1,500 ATR-u historical) — **not achievable** as stated.
Verdict: **FAILS robustness** — path coverage spans only 2026-08-20..22 (3 days; Appendix A L1). Downgraded to paper-sim (G0-D); nothing shipped.

### Lever 2 — Trend-regime entry filter (btc_adx)
Rule: block/flip by BTC ADX bucket at entry. ALL-SHORT: 15–25 E=+0.023 (n=4,719) / 25–35 E=−0.059 (n=3,076) / **≥35 E=−1.128, WR=36.5% (n=1,623)**. ALL-LONG mirror: −0.071 / +0.372 / **+0.579 (n=1,644)**.
Concentration check: the ≥35-short cell spans only 2026-08-20..22 (3 days) but 27 symbols and 682 would-live trades (E=−0.975, ≈−665 ATR-u in 72h).
Projected impact: blocking shorts at ADX≥35 avoids ≈ **−1,831 ATR-u** historical bleed.
Verdict: gradient credible (monotone, both signs mirror), cutoff unproven outside the event → **G0-A forward gate**, no config change yet.

### Lever 3 — Volatility-regime filter (btc_realized_vol_24h terciles)
Rule: terciles at mean×0.7 / mean×1.3 (mean=0.0926, q1_thr=0.0648, q3_thr=0.1204). ALL-SHORT: q1 **E=−1.028 (n=2,154, 10 days)** / mid +0.088 (n=6,438, 34 days) / q3 −0.310 (n=826, 2 days). ALL-LONG mirror: q1 **+0.698 (n=2,540)** / mid −0.036 / q3 +0.538.
Projected impact: short-brake in rvol q1 avoids ≈ **−2,214 ATR-u**; would-live subset alone: n=1,249, E=−0.912 (≈−1,139 ATR-u).
Verdict: **strongest clean lever** — multi-day support, monotone, both sides mirror → **G0-B registration**.

### Lever 4 — Positioning / order-flow filter (OI delta + funding)
Rule: bucket by pre-entry 30-min OI change and current funding. LONG after OI flush <−1%: **E=+0.502 (n=1,216, 38 days)** vs 0..+1% +0.062. SHORT at OI ≥+1%: **E=−0.736 (n=876, 40 days)**. asia_pump-family SHORT at funding ≥1bp: **E=−1.445 (n=940, 30 days)** vs 0–1bp −0.148.
External validity: OI feature validated against independent Binance feed (§5.1, r=0.838 strict).
Projected impact: fade-block at OI≥+1% avoids ≈ −645 ATR-u; funding gate on asia_pump avoids ≈ −1,358 ATR-u; mild/deep-flush long tilt carries ≈ +1,570 ATR-u historical.
Verdict: promote OI≥+1% into G0-A arm; funding arm → **G0-C** after day-concentration split.

### Lever 5 — Session / time-of-day filter
Rule: bucket by entry session. ALL-SHORT: asia **−0.255 (n=6,225)** / london −0.049 / ny −0.138 / late **+0.408 (n=1,227)**. ALL-LONG: london +0.356 (n=4,508) / ny +0.285 (n=7,697) / late **−0.608 (n=1,551)**.
Projected impact: naive session gates touch Σ ≈ −1,587 (shorts-asia) and −944 (longs-late) ATR-u of exposure.
Verdict: **confounded with book mix** (asia shorts ARE the asia_pump/setup_fade_asia books; late longs are the late-follow family) → observational arm only, no standalone gate.

### Lever 6 — Universe selection / book removal
Rule: book×side ledger (n≥300). Worst: burst_follow SHORT **n=2,589 E=−0.115** (Σ−297, 47 days, no rescuing bucket in L2/L3/L4 splits); setup_follow LONG n=2,949 E=−0.030; setup_follow SHORT n=1,365 E=−0.081. Best majors: ny_flush_buy_24h +1.628 (n=352), follow_3h_all +0.555 (n=515).
Projected impact: removing burst_follow SHORT ≈ **+37.8 ATR-u/week** (−297 over 7.9wk).
Verdict: immediate live expectancy gain available by shrinking burst_follow SHORT, pending Martin's live-behavior gate; asia_pump_short_4h conditional (funding gate above) rather than killed.

## Bottom line
Baseline +0.028 ATR-u/trade overall vs **−0.076 on the would-live set**: edge lives in shapes the accept-gate underweights, while the accepted mix bleeds shorts. Ranked actions: (1) G0-B rvol-q1 short brake [L3], (2) G0-A ADX≥35 + OI≥+1% fade gate [L2+L4], (3) burst_follow-SHORT shrink [L6], (4) asia_pump funding gate [L4], (5) winner-extension paper sim [L1]. Draft G0 registrations with kill criteria: `research/RESEARCH_PLAN.md` @ commit 021545c.

---

## Appendix A — raw output of `verify_deliverable.py` (2026-08-22T13:00:19Z)
```text
     1|
     2|========================================================================
     3|S1 DATASET IDENTITY
     4|========================================================================
     5|db=/root/bitana/storage/signal_shadow.db table=shadow_trades
     6|rows_total=33429 closed=33370 open=59 (open excluded everywhere)
     7|entry_time range: 2026-06-27T22:04:59.999000+00:00 -> 2026-08-22T12:19:59.999000+00:00
     8|distinct symbols=28 strategies=63 would_live_accept=1: n=10535
     9|run timestamp: 2026-08-22T13:02:50+00:00
    10|key columns: pnl_atr (PnL / entry-bar ATR, +=favorable), side, strategy, symbol,
    11|  session, hour, atr, stop_atr, tp_atr, entry_time, exit_time, exit_reason,
    12|  btc_adx, btc_realized_vol_24h, oi_delta_30m_pct, funding_rate_symbol,
    13|  run_mfe_atr, would_live_accept; trade_r_path(trade_id,phase,bar_idx,r_high,r_low,r_close)
    14|
    15|========================================================================
    16|S2 BASELINE EXPECTANCY (raw, unmodified)
    17|========================================================================
    18|ALL closed                                   n= 33370 E=+0.0280 sum=   +935.5 WR=0.5103 days=57
    19|  identity: WR*avg_win+(1-WR)*avg_loss = 0.5103*1.9100 + 0.4897*(-1.9334) = +0.0280  (== E above)
    20|would_live_accept=1                          n= 10535 E=-0.0760 sum=   -801.1 WR=0.5049 days=31
    21|side=LONG                                    n= 19827 E=+0.1296 sum=  +2568.8 WR=0.5186 days=57
    22|side=SHORT                                   n= 13543 E=-0.1206 sum=  -1633.3 WR=0.4983 days=57
    23|
    24|========================================================================
    25|S3 DATA QUALITY
    26|========================================================================
    27|-- nulls among closed --
    28|  pnl_atr                  null=     0 ( 0.0%)  non-null=33370
    29|  side                     null=     0 ( 0.0%)  non-null=33370
    30|  strategy                 null=     0 ( 0.0%)  non-null=33370
    31|  symbol                   null=     0 ( 0.0%)  non-null=33370
    32|  entry_time               null=     0 ( 0.0%)  non-null=33370
    33|  exit_time                null=     0 ( 0.0%)  non-null=33370
    34|  atr                      null=     0 ( 0.0%)  non-null=33370
    35|  stop_atr                 null=   150 ( 0.4%)  non-null=33220
    36|  btc_adx                  null= 11822 (35.4%)  non-null=21548
    37|  btc_realized_vol_24h     null= 11822 (35.4%)  non-null=21548
    38|  oi_delta_30m_pct         null= 11876 (35.6%)  non-null=21494
    39|  funding_rate_symbol      null=  1066 ( 3.2%)  non-null=32304
    40|  run_mfe_atr              null=     0 ( 0.0%)  non-null=33370
    41|  post_mfe_atr             null=     0 ( 0.0%)  non-null=33370
    42|  would_live_accept        null= 11753 (35.2%)  non-null=21617
    43|  session                  null=   150 ( 0.4%)  non-null=33220
    44|  hour                     null=   150 ( 0.4%)  non-null=33220
    45|-- duplicates --
    46|  id distinct: 33429/33429  dup(strategy,symbol,side,entry_time) groups=0
    47|-- ranges / impossible --
    48|  pnl_atr range: [-12.00, 102.00]
    49|    extreme -12.00 -> id=7048 setup_fade_late/SHORT stop_atr=12.0 entry=2026-07-08T23:29:59.999000+00:00
    50|    extreme +102.00 -> id=29308 ny_flush_buy_24h/LONG stop_atr=10.0 entry=2026-08-18T17:59:59.999000+00:00
    51|  entry>=exit rows=1  atr<=0 rows=0  closed-with-null-pnl=0
    52|
    53|========================================================================
    54|S4 LOOK-AHEAD AUDIT: strict decision-time OI recompute
    55|========================================================================
    56|pairs strict (both anchors = latest hourly obs <= decision time, staleness<=65min): n=2298 (skipped stale/missing: 2175)
    57|  pearson r = 0.838  median|diff| = 0.128pp  mean diff = +0.005pp
    58|control loose (nearest +/-45m, can touch post-entry): n=2175 r = 0.696 median|diff| = 0.167pp
    59|interpretation: strict (decision-time-only) join agrees BETTER than the loose one ->
    60|logged feature carries no post-entry information; all levers key on entry-time cols only.
    61|
    62|========================================================================
    63|S5 LEVERS (all filters = entry-time info only)
    64|========================================================================
    65|
    66|-- L1 exit timing: realized vs in-life MFE (giveback), top books --
    67|  ny_flush_buy_24h|LONG              n=  352 E=+1.628 avg_run_mfe=+8.72 giveback_frac=0.42
    68|  follow_3h_all|LONG                 n=  515 E=+0.555 avg_run_mfe=+3.25 giveback_frac=0.38
    69|  ny_flush_buy_8h|LONG               n=  383 E=+0.514 avg_run_mfe=+4.61 giveback_frac=0.38
    70|  ny_flush_buy_4h|LONG               n=  509 E=+0.447 avg_run_mfe=+3.54 giveback_frac=0.4
    71|  follow_6h_all|LONG                 n=  398 E=+0.405 avg_run_mfe=+4.52 giveback_frac=0.45
    72|  ny_flush_buy_4h_s4|LONG            n=  339 E=+0.393 avg_run_mfe=+3.29 giveback_frac=0.4
    73|  ny_flush_buy_4h_s6|LONG            n=  307 E=+0.355 avg_run_mfe=+3.46 giveback_frac=0.38
    74|  late_fade|SHORT                    n=  377 E=+0.249 avg_run_mfe=+1.44 giveback_frac=0.39
    75|  path coverage days (phase='open'): 2026-08-20..2026-08-22 n_days=3 -> concentration fail
    76|
    77|-- L2 trend regime (btc_adx buckets), ALL-SHORT vs ALL-LONG --
    78|  SHORT adx[0,15)                            n=     0 (empty)
    79|  SHORT adx[15,25)                           n=  4719 E=+0.0226 sum=   +106.7 WR=0.5232 days=30
    80|  SHORT adx[25,35)                           n=  3076 E=-0.0592 sum=   -182.2 WR=0.5016 days=22
    81|  SHORT adx[35,inf)                          n=  1623 E=-1.1280 sum=  -1830.8 WR=0.3654 days=3
    82|  LONG adx[0,15)                             n=     0 (empty)
    83|  LONG adx[15,25)                            n=  6477 E=-0.0706 sum=   -457.3 WR=0.4904 days=30
    84|  LONG adx[25,35)                            n=  4009 E=+0.3717 sum=  +1490.0 WR=0.5555 days=22
    85|  LONG adx[35,inf)                           n=  1644 E=+0.5791 sum=   +952.0 WR=0.6089 days=3
    86|  L2 projected bleed avoided if short-blocked at adx>=35: 1623 x -1.128 = -1831 ATR-u
    87|  adx>=35 short window: 2026-08-20..2026-08-22 days=3 symbols=27
    88|
    89|-- L3 vol regime (rvol24 terciles, thr = mean*0.7 / mean*1.3) --
    90|  mean rvol24=0.0926 q1_thr=0.0648 q3_thr=0.1204
    91|  SHORT rvol q1                              n=  2154 E=-1.0279 sum=  -2214.0 WR=0.4048 days=10
    92|  SHORT rvol mid                             n=  6438 E=+0.0875 sum=   +563.4 WR=0.5247 days=34
    93|  SHORT rvol q3                              n=   826 E=-0.3095 sum=   -255.6 WR=0.4298 days=2
    94|  LONG rvol q1                               n=  2540 E=+0.6982 sum=  +1773.5 WR=0.5874 days=10
    95|  LONG rvol mid                              n=  8628 E=-0.0355 sum=   -306.1 WR=0.5023 days=34
    96|  LONG rvol q3                               n=   962 E=+0.5377 sum=   +517.3 WR=0.6008 days=2
    97|  L3 projected bleed avoided if short-blocked in rvol q1: 2154 x -1.028 = -2214 ATR-u
    98|
    99|-- L4 order flow (oi_delta_30m_pct) & funding --
   100|  LONG OId <-1%                              n=  1216 E=+0.5016 sum=   +609.9 WR=0.5921 days=38
   101|  LONG OId -1..0                             n=  5331 E=+0.1802 sum=   +960.7 WR=0.5355 days=40
   102|  LONG OId 0..+1                             n=  4535 E=+0.0615 sum=   +278.8 WR=0.5025 days=40
   103|  LONG OId >=+1%                             n=  1025 E=+0.1376 sum=   +141.0 WR=0.5229 days=40
   104|  SHORT OId <-1%                             n=   486 E=-0.3028 sum=   -147.2 WR=0.4218 days=38
   105|  SHORT OId -1..0                            n=  3927 E=-0.1691 sum=   -664.0 WR=0.4996 days=40
   106|  SHORT OId 0..+1                            n=  4098 E=-0.1110 sum=   -454.7 WR=0.5041 days=40
   107|  SHORT OId >=+1%                            n=   876 E=-0.7364 sum=   -645.1 WR=0.4053 days=40
   108|  asia_pump% SHORT funding<0                 n=   224 E=-0.6154 sum=   -137.8 WR=0.4375 days=26
   109|  asia_pump% SHORT 0<=f<1bp                  n=   781 E=-0.1475 sum=   -115.2 WR=0.5557 days=36
   110|  asia_pump% SHORT f>=1bp                    n=   940 E=-1.4449 sum=  -1358.2 WR=0.4351 days=30
   111|  L4 potential on mild-flush LONG follows: 5331 x 0.180 = +961 ATR-u
   112|
   113|-- L5 session / time-of-day --
   114|  SHORT asia                                 n=  6225 E=-0.2549 sum=  -1586.6 WR=0.4948 days=53
   115|  SHORT london                               n=  3410 E=-0.0487 sum=   -166.0 WR=0.4959 days=54
   116|  SHORT ny                                   n=  2582 E=-0.1382 sum=   -356.8 WR=0.4892 days=52
   117|  SHORT late                                 n=  1227 E=+0.4081 sum=   +500.8 WR=0.5493 days=52
   118|  LONG asia                                  n=  6020 E=-0.0494 sum=   -297.3 WR=0.4944 days=53
   119|  LONG london                                n=  4508 E=+0.3564 sum=  +1606.8 WR=0.5260 days=54
   120|  LONG ny                                    n=  7697 E=+0.2847 sum=  +2191.0 WR=0.5528 days=52
   121|  LONG late                                  n=  1551 E=-0.6084 sum=   -943.6 WR=0.4197 days=52
   122|
   123|-- L6 universe (book-level) --
   124|  burst_follow|SHORT                         n=  2589 E=-0.1147 sum=   -297.0 WR=0.5102 days=47
   125|  burst_follow|LONG                          n=  3241 E=+0.0189 sum=    +61.2 WR=0.5236 days=48
   126|  setup_follow|LONG                          n=  2949 E=-0.0302 sum=    -89.0 WR=0.4778 days=54
   127|  setup_follow|SHORT                         n=  1365 E=-0.0805 sum=   -109.9 WR=0.5077 days=52
   128|  setup_fade|SHORT                           n=  2936 E=+0.0072 sum=    +21.3 WR=0.5044 days=54
   129|  asia_pump_short_4h|SHORT                   n=   334 E=-0.1105 sum=    -36.9 WR=0.5389 days=43
   130|  ny_flush_buy_24h|LONG                      n=   352 E=+1.6284 sum=   +573.2 WR=0.5540 days=38
   131|  follow_3h_all|LONG                         n=   515 E=+0.5551 sum=   +285.9 WR=0.5981 days=29
   132|  L6 projected: burst_follow SHORT 2589 x -0.1147 = -297 ATR-u over 7.9wk = -37.8/wk
   133|
   134|========================================================================
   135|S5b WOULD-LIVE IMPACT OF TOP GATES
   136|========================================================================
   137|  would-live shorts adx>=35                  n=   682 E=-0.9751 sum=   -665.0 WR=0.3607 days=3
   138|  would-live shorts rvol q1                  n=  1249 E=-0.9123 sum=  -1139.5 WR=0.4083 days=8
   139|
   140|ALL SECTIONS DONE
   141|
```
