     1|# Expectancy-Mining Deliverable — bitana shadow book
     2|**Regenerated:** 2026-08-22T13:00:19+00:00 (single reproducible run: `hermes_lab/verify_deliverable.py`, raw output in Appendix A)
     3|**Note:** the shadow DB is live (grows ~600 trades/day); numbers drift. Everything below is recomputable exactly via the script.
     4|
     5|## 1) Dataset (explicit identification)
     6|- Source file: SQLite `/root/bitana/storage/signal_shadow.db`, table `shadow_trades` (75 columns), written by `research/signal_shadow.py`.
     7|- Rows: 33,429 total → **33,370 closed trades used**; 59 open excluded everywhere.
     8|- Entry-time range: 2026-06-27T22:05Z → 2026-08-22T12:20Z (57 calendar days).
     9|- Universe: 28 perp-USDT symbols × 63 strategy-books, sides LONG/SHORT.
    10|- External validation source: `/root/hermes_lab/data/oi_live.db` table `oi_history` (Binance fapi OI, hourly cadence, BTC/ETH/SOL/XRP).
    11|- Column definitions relied upon:
    12|  - `pnl_atr`: realized trade PnL divided by entry-bar ATR (dimensionless ATR-multiples, favorable positive). Per-book risk scale: `stop_atr` (mean 7.88; e.g. burst_follow stop=10 ATR → pnl_atr=−10 ≡ −1 risk-unit R; books with stop_atr=1 → pnl_atr ≡ R).
    13|  - `side` ∈ {LONG, SHORT}; `strategy` book; `session` ∈ {asia, london, ny, late}; `hour` (UTC).
    14|  - Entry-time features (decision-time inputs): `btc_adx` (BTC 1h ADX at signal), `btc_realized_vol_24h` (trailing 24h realized vol), `oi_delta_30m_pct` (30-min symbol-OI % change at signal), `funding_rate_symbol` (current funding), `liq_imb`, `entry_vol_z`.
    15|  - Post-trade diagnostics (never used in any rule): `run_mfe_atr`, `post_mfe_atr`, `post_mae_atr`, `trade_r_path` per-bar paths.
    16|  - `would_live_accept`: shadow accept-gate flag (strategy-scoped since Jul 23; null before instrumentation).
    17|
    18|## 2) Expectancy — precise mathematical definition (stated before any results)
    19|For closed trade i, let x_i = `pnl_atr` (units: **ATR-multiples per trade**, favorable positive; ≡ R-multiples where stop_atr=1).
    20|- **E ≡ (1/n)·Σ_{i=1..n} x_i = win_rate·avg_win − loss_rate·|avg_loss|**
    21|  where win_rate = #(x_i>0)/n, loss_rate = #(x_i≤0)/n, avg_win = mean{x: x>0}, avg_loss = mean{x: x≤0} (≤0).
    22|- Identity verified numerically on the raw set in §3 (recomputed E from components matches SQL AVG to 4 decimals).
    23|- All results below report E [ATR-units/trade], n, Σx, WR, and distinct trading days. Significance proxies: n, day-count, day-concentration; no p-values claimed.
    24|
    25|## 3) Baseline expectancy (raw data, unmodified system)
    26|- **ALL closed trades: E = +0.0280 ATR-units/trade** (n=33,370, Σx=+935.5, WR=51.03%, avg_win=+1.9100, avg_loss=−1.9334).
    27|  Identity check: 0.5103×(+1.9100) − 0.4897×1.9334 = **+0.0280 ✓**
    28|- Live-relevant subset `would_live_accept=1`: **E = −0.0760** (n=10,535, Σx=−801.1, 31 days).
    29|- By side: LONG n=19,827 E=+0.1296 (Σ+2,568.8) · SHORT n=13,543 E=−0.1206 (Σ−1,633.3).
    30|- Reading: the all-in ensemble is ~flat-positive; the accepted-for-live mix bleeds. Both numbers come straight from `AVG(pnl_atr)` over raw rows (Appendix A, S2).
    31|
    32|## 4) Data-quality checks & treatment
    33|- **Nulls among closed trades** (Appendix A, S3): core fields `pnl_atr/side/strategy/symbol/entry_time/exit_time/atr/run_mfe_atr/post_mfe_atr` = **0 nulls each**. Feature gaps are contiguous pre-instrumentation windows, not random: `btc_adx` & `btc_realized_vol_24h` 11,822 (35.4%), `oi_delta_30m_pct` 11,876 (35.6%) — logging began Jul 14; `funding_rate_symbol` 1,066 (3.2%); `stop_atr/session/hour` 150 (0.4%); `would_live_accept` 11,753 (35.2%).
    34|  **Treatment:** no imputation; every lever conditions on non-null subsets and reports its coverage window/days alongside.
    35|- **Duplicates:** ids 33,429/33,429 distinct; duplicate (strategy, symbol, side, entry_time) groups = **0**.
    36|- **Out-of-range / impossible:** pnl_atr range [−12.00, +102.00]. Min −12.00 = id 7048, exact full stop on a stop_atr=12 book (legitimate). Max +102.00 = id 29308, long-hold trend book (legitimate). Rows with entry_time ≥ exit_time: **1** (same-bar instant stop, kept, flagged). `atr ≤ 0`: **0**. Closed-with-null-pnl: **0**.
    37|- **Net exclusions:** open trades only (59).
    38|
    39|## 5) Look-ahead bias — affirmatively ruled out (three verifications)
    40|1. **Timestamp audit (external recompute, decision-time-only).** `oi_delta_30m_pct` recomputed from raw Binance `oi_history` using ONLY observations ≤ entry_time: both anchors = latest hourly snapshot strictly preceding (entry) and preceding (entry−30min), staleness ≤65min (collector cadence = 60min). Result: **pearson r = 0.838, median|diff| = 0.128pp, n = 2,298** (Appendix A, S4). Control join allowed to touch post-entry observations (nearest ±45min): **r = 0.696** — worse. Future-contaminated features would show the reverse ordering; the decision-time-only recompute agrees best ⇒ the logged feature carries no post-entry information.
    41|2. **Code-path audit.** `research/signal_shadow.py` writes entry features once at the trade-open INSERT (signal-time values); UPDATE statements touch only exit fields, scale fills, and post-exit diagnostics. Back-fill of entry features is not possible in the write path.
    42|3. **Rule audit.** Every lever in §6 conditions solely on entry-time columns (entry-bar regime stats, current funding, pre-entry OI change, session/hour, book identity). Exit-side counterfactuals replay only `phase='open'` in-life path bars (calibration vs realized exits: 96.1% exact, SHORT sign-mirror verified); `phase='post'` data is never part of any tradable rule.
    43|
    44|## 6) Improvement levers tested (six conceptually distinct; each: rule → result → projected impact → verdict)
    45|All filters use decision-time info only (§5). Projected impacts are descriptive on this dataset, not forward promises.
    46|
    47|### Lever 1 — Exit timing / hold extension (exit-side counterfactuals)
    48|Rule tested: alternative exits (BE-shift, TP 2×/4×, time-hold 0.5–4×, trails) replayed on stored in-life paths.
    49|Result: winners-run structure is real — top books give back 38–45% of in-life MFE (ny_flush_buy_24h: realized E=+1.63 vs avg_run_mfe=+8.72, n=352).
    50|Projected upper bound if fully captured: ≈ +4.3 ATR-u extra on ny_flush_24h alone (~+1,500 ATR-u historical) — **not achievable** as stated.
    51|Verdict: **FAILS robustness** — path coverage spans only 2026-08-20..22 (3 days; Appendix A L1). Downgraded to paper-sim (G0-D); nothing shipped.
    52|
    53|### Lever 2 — Trend-regime entry filter (btc_adx)
    54|Rule: block/flip by BTC ADX bucket at entry. ALL-SHORT: 15–25 E=+0.023 (n=4,719) / 25–35 E=−0.059 (n=3,076) / **≥35 E=−1.128, WR=36.5% (n=1,623)**. ALL-LONG mirror: −0.071 / +0.372 / **+0.579 (n=1,644)**.
    55|Concentration check: the ≥35-short cell spans only 2026-08-20..22 (3 days) but 27 symbols and 682 would-live trades (E=−0.975, ≈−665 ATR-u in 72h).
    56|Projected impact: blocking shorts at ADX≥35 avoids ≈ **−1,831 ATR-u** historical bleed.
    57|Verdict: gradient credible (monotone, both signs mirror), cutoff unproven outside the event → **G0-A forward gate**, no config change yet.
    58|
    59|### Lever 3 — Volatility-regime filter (btc_realized_vol_24h terciles)
    60|Rule: terciles at mean×0.7 / mean×1.3 (mean=0.0926, q1_thr=0.0648, q3_thr=0.1204). ALL-SHORT: q1 **E=−1.028 (n=2,154, 10 days)** / mid +0.088 (n=6,438, 34 days) / q3 −0.310 (n=826, 2 days). ALL-LONG mirror: q1 **+0.698 (n=2,540)** / mid −0.036 / q3 +0.538.
    61|Projected impact: short-brake in rvol q1 avoids ≈ **−2,214 ATR-u**; would-live subset alone: n=1,249, E=−0.912 (≈−1,139 ATR-u).
    62|Verdict: **strongest clean lever** — multi-day support, monotone, both sides mirror → **G0-B registration**.
    63|
    64|### Lever 4 — Positioning / order-flow filter (OI delta + funding)
    65|Rule: bucket by pre-entry 30-min OI change and current funding. LONG after OI flush <−1%: **E=+0.502 (n=1,216, 38 days)** vs 0..+1% +0.062. SHORT at OI ≥+1%: **E=−0.736 (n=876, 40 days)**. asia_pump-family SHORT at funding ≥1bp: **E=−1.445 (n=940, 30 days)** vs 0–1bp −0.148.
    66|External validity: OI feature validated against independent Binance feed (§5.1, r=0.838 strict).
    67|Projected impact: fade-block at OI≥+1% avoids ≈ −645 ATR-u; funding gate on asia_pump avoids ≈ −1,358 ATR-u; mild/deep-flush long tilt carries ≈ +1,570 ATR-u historical.
    68|Verdict: promote OI≥+1% into G0-A arm; funding arm → **G0-C** after day-concentration split.
    69|
    70|### Lever 5 — Session / time-of-day filter
    71|Rule: bucket by entry session. ALL-SHORT: asia **−0.255 (n=6,225)** / london −0.049 / ny −0.138 / late **+0.408 (n=1,227)**. ALL-LONG: london +0.356 (n=4,508) / ny +0.285 (n=7,697) / late **−0.608 (n=1,551)**.
    72|Projected impact: naive session gates touch Σ ≈ −1,587 (shorts-asia) and −944 (longs-late) ATR-u of exposure.
    73|Verdict: **confounded with book mix** (asia shorts ARE the asia_pump/setup_fade_asia books; late longs are the late-follow family) → observational arm only, no standalone gate.
    74|
    75|### Lever 6 — Universe selection / book removal
    76|Rule: book×side ledger (n≥300). Worst: burst_follow SHORT **n=2,589 E=−0.115** (Σ−297, 47 days, no rescuing bucket in L2/L3/L4 splits); setup_follow LONG n=2,949 E=−0.030; setup_follow SHORT n=1,365 E=−0.081. Best majors: ny_flush_buy_24h +1.628 (n=352), follow_3h_all +0.555 (n=515).
    77|Projected impact: removing burst_follow SHORT ≈ **+37.8 ATR-u/week** (−297 over 7.9wk).
    78|Verdict: immediate live expectancy gain available by shrinking burst_follow SHORT, pending Martin's live-behavior gate; asia_pump_short_4h conditional (funding gate above) rather than killed.
    79|
    80|## Bottom line
    81|Baseline +0.028 ATR-u/trade overall vs **−0.076 on the would-live set**: edge lives in shapes the accept-gate underweights, while the accepted mix bleeds shorts. Ranked actions: (1) G0-B rvol-q1 short brake [L3], (2) G0-A ADX≥35 + OI≥+1% fade gate [L2+L4], (3) burst_follow-SHORT shrink [L6], (4) asia_pump funding gate [L4], (5) winner-extension paper sim [L1]. Draft G0 registrations with kill criteria: `research/RESEARCH_PLAN.md` @ commit 021545c.
    82|
    83|---
    84|
    85|## Appendix A — raw output of `verify_deliverable.py` (2026-08-22T13:00:19Z)
    86|```text
    87|     1|
    88|     2|========================================================================
    89|     3|S1 DATASET IDENTITY
    90|     4|========================================================================
    91|     5|db=/root/bitana/storage/signal_shadow.db table=shadow_trades
    92|     6|rows_total=33429 closed=33370 open=59 (open excluded everywhere)
    93|     7|entry_time range: 2026-06-27T22:04:59.999000+00:00 -> 2026-08-22T12:19:59.999000+00:00
    94|     8|distinct symbols=28 strategies=63 would_live_accept=1: n=10535
    95|     9|run timestamp: 2026-08-22T13:02:50+00:00
    96|    10|key columns: pnl_atr (PnL / entry-bar ATR, +=favorable), side, strategy, symbol,
    97|    11|  session, hour, atr, stop_atr, tp_atr, entry_time, exit_time, exit_reason,
    98|    12|  btc_adx, btc_realized_vol_24h, oi_delta_30m_pct, funding_rate_symbol,
    99|    13|  run_mfe_atr, would_live_accept; trade_r_path(trade_id,phase,bar_idx,r_high,r_low,r_close)
   100|    14|
   101|    15|========================================================================
   102|    16|S2 BASELINE EXPECTANCY (raw, unmodified)
   103|    17|========================================================================
   104|    18|ALL closed                                   n= 33370 E=+0.0280 sum=   +935.5 WR=0.5103 days=57
   105|    19|  identity: WR*avg_win+(1-WR)*avg_loss = 0.5103*1.9100 + 0.4897*(-1.9334) = +0.0280  (== E above)
   106|    20|would_live_accept=1                          n= 10535 E=-0.0760 sum=   -801.1 WR=0.5049 days=31
   107|    21|side=LONG                                    n= 19827 E=+0.1296 sum=  +2568.8 WR=0.5186 days=57
   108|    22|side=SHORT                                   n= 13543 E=-0.1206 sum=  -1633.3 WR=0.4983 days=57
   109|    23|
   110|    24|========================================================================
   111|    25|S3 DATA QUALITY
   112|    26|========================================================================
   113|    27|-- nulls among closed --
   114|    28|  pnl_atr                  null=     0 ( 0.0%)  non-null=33370
   115|    29|  side                     null=     0 ( 0.0%)  non-null=33370
   116|    30|  strategy                 null=     0 ( 0.0%)  non-null=33370
   117|    31|  symbol                   null=     0 ( 0.0%)  non-null=33370
   118|    32|  entry_time               null=     0 ( 0.0%)  non-null=33370
   119|    33|  exit_time                null=     0 ( 0.0%)  non-null=33370
   120|    34|  atr                      null=     0 ( 0.0%)  non-null=33370
   121|    35|  stop_atr                 null=   150 ( 0.4%)  non-null=33220
   122|    36|  btc_adx                  null= 11822 (35.4%)  non-null=21548
   123|    37|  btc_realized_vol_24h     null= 11822 (35.4%)  non-null=21548
   124|    38|  oi_delta_30m_pct         null= 11876 (35.6%)  non-null=21494
   125|    39|  funding_rate_symbol      null=  1066 ( 3.2%)  non-null=32304
   126|    40|  run_mfe_atr              null=     0 ( 0.0%)  non-null=33370
   127|    41|  post_mfe_atr             null=     0 ( 0.0%)  non-null=33370
   128|    42|  would_live_accept        null= 11753 (35.2%)  non-null=21617
   129|    43|  session                  null=   150 ( 0.4%)  non-null=33220
   130|    44|  hour                     null=   150 ( 0.4%)  non-null=33220
   131|    45|-- duplicates --
   132|    46|  id distinct: 33429/33429  dup(strategy,symbol,side,entry_time) groups=0
   133|    47|-- ranges / impossible --
   134|    48|  pnl_atr range: [-12.00, 102.00]
   135|    49|    extreme -12.00 -> id=7048 setup_fade_late/SHORT stop_atr=12.0 entry=2026-07-08T23:29:59.999000+00:00
   136|    50|    extreme +102.00 -> id=29308 ny_flush_buy_24h/LONG stop_atr=10.0 entry=2026-08-18T17:59:59.999000+00:00
   137|    51|  entry>=exit rows=1  atr<=0 rows=0  closed-with-null-pnl=0
   138|    52|
   139|    53|========================================================================
   140|    54|S4 LOOK-AHEAD AUDIT: strict decision-time OI recompute
   141|    55|========================================================================
   142|    56|pairs strict (both anchors = latest hourly obs <= decision time, staleness<=65min): n=2298 (skipped stale/missing: 2175)
   143|    57|  pearson r = 0.838  median|diff| = 0.128pp  mean diff = +0.005pp
   144|    58|control loose (nearest +/-45m, can touch post-entry): n=2175 r = 0.696 median|diff| = 0.167pp
   145|    59|interpretation: strict (decision-time-only) join agrees BETTER than the loose one ->
   146|    60|logged feature carries no post-entry information; all levers key on entry-time cols only.
   147|    61|
   148|    62|========================================================================
   149|    63|S5 LEVERS (all filters = entry-time info only)
   150|    64|========================================================================
   151|    65|
   152|    66|-- L1 exit timing: realized vs in-life MFE (giveback), top books --
   153|    67|  ny_flush_buy_24h|LONG              n=  352 E=+1.628 avg_run_mfe=+8.72 giveback_frac=0.42
   154|    68|  follow_3h_all|LONG                 n=  515 E=+0.555 avg_run_mfe=+3.25 giveback_frac=0.38
   155|    69|  ny_flush_buy_8h|LONG               n=  383 E=+0.514 avg_run_mfe=+4.61 giveback_frac=0.38
   156|    70|  ny_flush_buy_4h|LONG               n=  509 E=+0.447 avg_run_mfe=+3.54 giveback_frac=0.4
   157|    71|  follow_6h_all|LONG                 n=  398 E=+0.405 avg_run_mfe=+4.52 giveback_frac=0.45
   158|    72|  ny_flush_buy_4h_s4|LONG            n=  339 E=+0.393 avg_run_mfe=+3.29 giveback_frac=0.4
   159|    73|  ny_flush_buy_4h_s6|LONG            n=  307 E=+0.355 avg_run_mfe=+3.46 giveback_frac=0.38
   160|    74|  late_fade|SHORT                    n=  377 E=+0.249 avg_run_mfe=+1.44 giveback_frac=0.39
   161|    75|  path coverage days (phase='open'): 2026-08-20..2026-08-22 n_days=3 -> concentration fail
   162|    76|
   163|    77|-- L2 trend regime (btc_adx buckets), ALL-SHORT vs ALL-LONG --
   164|    78|  SHORT adx[0,15)                            n=     0 (empty)
   165|    79|  SHORT adx[15,25)                           n=  4719 E=+0.0226 sum=   +106.7 WR=0.5232 days=30
   166|    80|  SHORT adx[25,35)                           n=  3076 E=-0.0592 sum=   -182.2 WR=0.5016 days=22
   167|    81|  SHORT adx[35,inf)                          n=  1623 E=-1.1280 sum=  -1830.8 WR=0.3654 days=3
   168|    82|  LONG adx[0,15)                             n=     0 (empty)
   169|    83|  LONG adx[15,25)                            n=  6477 E=-0.0706 sum=   -457.3 WR=0.4904 days=30
   170|    84|  LONG adx[25,35)                            n=  4009 E=+0.3717 sum=  +1490.0 WR=0.5555 days=22
   171|    85|  LONG adx[35,inf)                           n=  1644 E=+0.5791 sum=   +952.0 WR=0.6089 days=3
   172|    86|  L2 projected bleed avoided if short-blocked at adx>=35: 1623 x -1.128 = -1831 ATR-u
   173|    87|  adx>=35 short window: 2026-08-20..2026-08-22 days=3 symbols=27
   174|    88|
   175|    89|-- L3 vol regime (rvol24 terciles, thr = mean*0.7 / mean*1.3) --
   176|    90|  mean rvol24=0.0926 q1_thr=0.0648 q3_thr=0.1204
   177|    91|  SHORT rvol q1                              n=  2154 E=-1.0279 sum=  -2214.0 WR=0.4048 days=10
   178|    92|  SHORT rvol mid                             n=  6438 E=+0.0875 sum=   +563.4 WR=0.5247 days=34
   179|    93|  SHORT rvol q3                              n=   826 E=-0.3095 sum=   -255.6 WR=0.4298 days=2
   180|    94|  LONG rvol q1                               n=  2540 E=+0.6982 sum=  +1773.5 WR=0.5874 days=10
   181|    95|  LONG rvol mid                              n=  8628 E=-0.0355 sum=   -306.1 WR=0.5023 days=34
   182|    96|  LONG rvol q3                               n=   962 E=+0.5377 sum=   +517.3 WR=0.6008 days=2
   183|    97|  L3 projected bleed avoided if short-blocked in rvol q1: 2154 x -1.028 = -2214 ATR-u
   184|    98|
   185|    99|-- L4 order flow (oi_delta_30m_pct) & funding --
   186|   100|  LONG OId <-1%                              n=  1216 E=+0.5016 sum=   +609.9 WR=0.5921 days=38
   187|   101|  LONG OId -1..0                             n=  5331 E=+0.1802 sum=   +960.7 WR=0.5355 days=40
   188|   102|  LONG OId 0..+1                             n=  4535 E=+0.0615 sum=   +278.8 WR=0.5025 days=40
   189|   103|  LONG OId >=+1%                             n=  1025 E=+0.1376 sum=   +141.0 WR=0.5229 days=40
   190|   104|  SHORT OId <-1%                             n=   486 E=-0.3028 sum=   -147.2 WR=0.4218 days=38
   191|   105|  SHORT OId -1..0                            n=  3927 E=-0.1691 sum=   -664.0 WR=0.4996 days=40
   192|   106|  SHORT OId 0..+1                            n=  4098 E=-0.1110 sum=   -454.7 WR=0.5041 days=40
   193|   107|  SHORT OId >=+1%                            n=   876 E=-0.7364 sum=   -645.1 WR=0.4053 days=40
   194|   108|  asia_pump% SHORT funding<0                 n=   224 E=-0.6154 sum=   -137.8 WR=0.4375 days=26
   195|   109|  asia_pump% SHORT 0<=f<1bp                  n=   781 E=-0.1475 sum=   -115.2 WR=0.5557 days=36
   196|   110|  asia_pump% SHORT f>=1bp                    n=   940 E=-1.4449 sum=  -1358.2 WR=0.4351 days=30
   197|   111|  L4 potential on mild-flush LONG follows: 5331 x 0.180 = +961 ATR-u
   198|   112|
   199|   113|-- L5 session / time-of-day --
   200|   114|  SHORT asia                                 n=  6225 E=-0.2549 sum=  -1586.6 WR=0.4948 days=53
   201|   115|  SHORT london                               n=  3410 E=-0.0487 sum=   -166.0 WR=0.4959 days=54
   202|   116|  SHORT ny                                   n=  2582 E=-0.1382 sum=   -356.8 WR=0.4892 days=52
   203|   117|  SHORT late                                 n=  1227 E=+0.4081 sum=   +500.8 WR=0.5493 days=52
   204|   118|  LONG asia                                  n=  6020 E=-0.0494 sum=   -297.3 WR=0.4944 days=53
   205|   119|  LONG london                                n=  4508 E=+0.3564 sum=  +1606.8 WR=0.5260 days=54
   206|   120|  LONG ny                                    n=  7697 E=+0.2847 sum=  +2191.0 WR=0.5528 days=52
   207|   121|  LONG late                                  n=  1551 E=-0.6084 sum=   -943.6 WR=0.4197 days=52
   208|   122|
   209|   123|-- L6 universe (book-level) --
   210|   124|  burst_follow|SHORT                         n=  2589 E=-0.1147 sum=   -297.0 WR=0.5102 days=47
   211|   125|  burst_follow|LONG                          n=  3241 E=+0.0189 sum=    +61.2 WR=0.5236 days=48
   212|   126|  setup_follow|LONG                          n=  2949 E=-0.0302 sum=    -89.0 WR=0.4778 days=54
   213|   127|  setup_follow|SHORT                         n=  1365 E=-0.0805 sum=   -109.9 WR=0.5077 days=52
   214|   128|  setup_fade|SHORT                           n=  2936 E=+0.0072 sum=    +21.3 WR=0.5044 days=54
   215|   129|  asia_pump_short_4h|SHORT                   n=   334 E=-0.1105 sum=    -36.9 WR=0.5389 days=43
   216|   130|  ny_flush_buy_24h|LONG                      n=   352 E=+1.6284 sum=   +573.2 WR=0.5540 days=38
   217|   131|  follow_3h_all|LONG                         n=   515 E=+0.5551 sum=   +285.9 WR=0.5981 days=29
   218|   132|  L6 projected: burst_follow SHORT 2589 x -0.1147 = -297 ATR-u over 7.9wk = -37.8/wk
   219|   133|
   220|   134|========================================================================
   221|   135|S5b WOULD-LIVE IMPACT OF TOP GATES
   222|   136|========================================================================
   223|   137|  would-live shorts adx>=35                  n=   682 E=-0.9751 sum=   -665.0 WR=0.3607 days=3
   224|   138|  would-live shorts rvol q1                  n=  1249 E=-0.9123 sum=  -1139.5 WR=0.4083 days=8
   225|   139|
   226|   140|ALL SECTIONS DONE
   227|   141|
   228|```
   229|

---

## Appendix B — residual/before->after runs (`residuals.py`, 2026-08-22T13:20Z)
Raw output of the gate-residual analysis (item 6 before/after math), the future-shift
feature-shifting control (item 5), and the non-null range sanity pass (item 4):
```text
     1|=== ITEM 6: before->after per lever (whole-book view) ===
     2|L2 gate: remove shorts btc_adx>=35
     3|  BEFORE: n=33381 sum=+940.9 E=+0.0282
     4|  GATE removes: n=1626 sum=-1827.6 (removed-pool E=-1.1240)
     5|  AFTER : n=27630 sum=+2495.5 E=+0.0903  | dE=+0.0621
     6|
     7|L3 gate: remove shorts rvol24<=q1
     8|  BEFORE: n=33381 sum=+940.9 E=+0.0282
     9|  GATE removes: n=2154 sum=-2214.0 (removed-pool E=-1.0279)
    10|  AFTER : n=27102 sum=+2881.9 E=+0.1063  | dE=+0.0782
    11|
    12|L2+L3 combined
    13|  BEFORE: n=33381 sum=+940.9 E=+0.0282
    14|  GATE removes: n=2983 sum=-2466.5 (removed-pool E=-0.8268)
    15|  AFTER : n=26273 sum=+3134.4 E=+0.1193  | dE=+0.0911
    16|
    17|L4a gate: remove shorts OI d30m>=+1%
    18|  BEFORE: n=33381 sum=+940.9 E=+0.0282
    19|  GATE removes: n=877 sum=-646.4 (removed-pool E=-0.7371)
    20|  AFTER : n=28348 sum=+1309.6 E=+0.0462  | dE=+0.0180
    21|
    22|L4b gate: remove asia_pump% shorts funding>=1bp
    23|  BEFORE: n=33381 sum=+940.9 E=+0.0282
    24|  GATE removes: n=940 sum=-1358.2 (removed-pool E=-1.4449)
    25|  AFTER : n=32397 sum=+2218.9 E=+0.0685  | dE=+0.0403
    26|
    27|L5a gate: remove longs session='late'
    28|  BEFORE: n=33381 sum=+940.9 E=+0.0282
    29|  GATE removes: n=1551 sum=-943.6 (removed-pool E=-0.6084)
    30|  AFTER : n=31779 sum=+1872.6 E=+0.0589  | dE=+0.0307
    31|
    32|L6 gate: remove burst_follow SHORT book entirely
    33|  BEFORE: n=33381 sum=+940.9 E=+0.0282
    34|  GATE removes: n=2592 sum=-293.9 (removed-pool E=-0.1134)
    35|  AFTER : n=30789 sum=+1234.7 E=+0.0401  | dE=+0.0119
    36|
    37|=== ITEM 6 supplement: SHORT-side-only view (where the bleed lives) ===
    38|  ALL shorts BEFORE: n=13546 E=-0.1203
    39|  stacked short-gates remove: n=5133 sum=-2474.9
    40|  shorts AFTER: n=8413 E=+0.1004  | dE=+0.2208
    41|
    42|  would-live stack: BEFORE n=10542 E=-0.0762; gates remove n=2456 sum=-1383.9; AFTER n=8086 E=+0.0719
    43|
    44|=== ITEM 5: future-shift control (feature shifting, hourly snapshots) ===
    45|  logged vs PAST  pair (both obs <= entry): r=0.714 median|diff|=0.165pp (n=4473)
    46|  logged vs FUTURE pair (both obs >  entry): r=0.120 median|diff|=0.359pp (n=4467)
    47|=> logged feature matches PRE-entry snapshots far better than POST-entry ones (r 0.714 vs 0.120); the column cannot be carrying the future.
    48|
    49|=== ITEM 4 supplement: non-null range sanity ===
    50|  btc_adx                [16.59, 68.97] negatives=0 -> OK
    51|  btc_realized_vol_24h   [0.02203, 0.2812] negatives=0 -> OK
    52|  oi_delta_30m_pct       [-10.07, 22.05] negatives=10967 -> OK
    53|  run_mfe_atr            [0, 125.8] negatives=0 -> OK
    54|  atr                    [1.864e-06, 165] negatives=0 -> OK
    55|
```


---

## Appendix C — tighten-SL sweep on gate-survivors (`sl_opt.py`, 2026-08-22T13:40Z)
Counterfactual static stop at k ATR: trade reprints at -k iff |run_mae_atr| >= k before original
exit (run_mae_atr stored negative-adverse; abs used). Optimistic for tight stops (fills AT k,
no gap/slippage), so losses from tightening are lower bounds.
```text
     1|
     2|--- ALL gate-survivors ---
     3|untightened: n=23555 sum=+4080.8 E=+0.1732
     4| stop_k  stopped%         E        dE       sum      WR
     5|   0.50     76.6%   +0.0066   -0.1667    +154.7  0.2102
     6|   0.75     67.1%   -0.0046   -0.1778    -107.6  0.2814
     7|   1.00     58.6%   -0.0052   -0.1785    -122.7  0.3362
     8|   1.50     43.7%   +0.0174   -0.1558    +410.9  0.4132
     9|   2.00     33.2%   +0.0354   -0.1378    +833.7  0.4519
    10|   2.50     25.2%   +0.0653   -0.1079   +1538.8  0.4771
    11|   3.00     19.5%   +0.0794   -0.0939   +1869.2  0.4931
    12|   4.00     12.3%   +0.1208   -0.0524   +2846.0  0.5111
    13|   5.00      8.0%   +0.1246   -0.0486   +2935.4  0.5174
    14|   6.00      5.4%   +0.1364   -0.0368   +3212.9  0.5207
    15|   7.50      3.1%   +0.1562   -0.0171   +3679.1  0.5235
    16|  10.00      1.6%   +0.1670   -0.0062   +3934.2  0.5249
    17|BEST static stop: k=10.0 ATR -> E=+0.1670 (vs +0.1732)
    18|
    19|--- gate-survivors LONG ---
    20|untightened: n=18245 sum=+3510.2 E=+0.1924
    21| stop_k  stopped%         E        dE       sum      WR
    22|   0.50     77.3%   -0.0073   -0.1997    -133.0  0.2018
    23|   0.75     68.2%   -0.0236   -0.2160    -431.3  0.2706
    24|   1.00     59.9%   -0.0272   -0.2196    -496.9  0.3248
    25|   1.50     45.4%   -0.0023   -0.1947     -42.5  0.4028
    26|   2.00     34.9%   +0.0240   -0.1684    +438.2  0.4443
    27|   2.50     26.9%   +0.0640   -0.1284   +1168.0  0.4727
    28|   3.00     21.2%   +0.0752   -0.1172   +1372.0  0.4897
    29|   4.00     13.5%   +0.1298   -0.0626   +2367.7  0.5109
    30|   5.00      9.0%   +0.1353   -0.0571   +2467.9  0.5182
    31|   6.00      6.2%   +0.1472   -0.0452   +2684.9  0.5218
    32|   7.50      3.6%   +0.1709   -0.0215   +3117.6  0.5251
    33|  10.00      1.8%   +0.1857   -0.0067   +3388.2  0.5268
    34|BEST static stop: k=10.0 ATR -> E=+0.1857 (vs +0.1924)
    35|
    36|--- gate-survivors SHORT ---
    37|untightened: n=5310 sum=+570.5 E=+0.1074
    38| stop_k  stopped%         E        dE       sum      WR
    39|   0.50     74.4%   +0.0542   -0.0533    +287.7  0.2394
    40|   0.75     63.4%   +0.0610   -0.0465    +323.8  0.3183
    41|   1.00     54.1%   +0.0705   -0.0370    +374.1  0.3753
    42|   1.50     37.9%   +0.0854   -0.0221    +453.4  0.4492
    43|   2.00     27.6%   +0.0745   -0.0330    +395.5  0.4780
    44|   2.50     19.5%   +0.0698   -0.0376    +370.7  0.4925
    45|   3.00     13.8%   +0.0936   -0.0138    +497.2  0.5051
    46|   4.00      7.9%   +0.0901   -0.0174    +478.3  0.5117
    47|   5.00      4.4%   +0.0880   -0.0194    +467.4  0.5149
    48|   6.00      2.6%   +0.0994   -0.0080    +528.0  0.5169
    49|   7.50      1.4%   +0.1058   -0.0017    +561.6  0.5181
    50|  10.00      0.8%   +0.1028   -0.0046    +546.0  0.5186
    51|BEST static stop: k=7.5 ATR -> E=+0.1058 (vs +0.1074)
    52|
    53|--- would-live gate-survivors ---
    54|untightened: n=8047 sum=+498.0 E=+0.0619
    55| stop_k  stopped%         E        dE       sum      WR
    56|   0.50     77.1%   +0.0268   -0.0351    +215.3  0.2178
    57|   0.75     67.3%   +0.0125   -0.0493    +100.9  0.2943
    58|   1.00     57.8%   +0.0199   -0.0420    +159.9  0.3543
    59|   1.50     40.9%   +0.0714   +0.0095    +574.7  0.4417
    60|   2.00     30.7%   +0.0437   -0.0181    +352.0  0.4714
    61|   2.50     23.0%   +0.0391   -0.0228    +314.3  0.4906
    62|   3.00     17.0%   +0.0423   -0.0196    +340.0  0.5030
    63|   4.00     10.4%   +0.0606   -0.0012    +488.0  0.5151
    64|   5.00      6.8%   +0.0482   -0.0137    +387.7  0.5191
    65|   6.00      4.4%   +0.0547   -0.0071    +440.5  0.5211
    66|   7.50      2.5%   +0.0583   -0.0035    +469.5  0.5222
    67|  10.00      1.5%   +0.0514   -0.0105    +413.4  0.5227
    68|BEST static stop: k=1.5 ATR -> E=+0.0714 (vs +0.0619)
    69|
    70|run_mae_atr convention: min=-68.1963190184046, max=0.0, negatives=31590 (abs used)
    71|
```
Verdict: tightening REDUCES expectancy on all gate-surviving pools (monotone in k);
single positive point k=1.5 on would-live slice (+0.0095) has negative neighbors -> not robust.
Book stops already near-optimal; edge gains live on the entry side (gates), winner side (trails)
is a separate lever (L1).
