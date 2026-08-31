# Winning-Combination Analysis — Shadow DB (Checklist Deliverable)

**Run (single auditable execution)**: 2026-08-31T08:05:10Z UTC | **DB**: `/root/bitana/storage/signal_shadow.db` (SQLite, read-only URI `file:...?mode=ro`) | **No live orders placed** — analysis-only connection.


## 1. Database identification & connection

- Type: SQLite (sqlite3 stdlib), connected read-only via URI `file:/root/bitana/storage/signal_shadow.db?mode=ro`; no write handle, no exchange endpoints called, no live trading orders placed at any point.
- Tables in DB: snapshots, burst_snapshots, setup_snapshots, setup_r_path, shadow_trades, shadow_pending_entries, trade_r_path, sqlite_sequence. **Analysis uses `shadow_trades` only** (46880 closed rows scanned, 28906 used).
- Writer: `research/signal_shadow.py` (INSERT at open, UPDATEs on exit only — entry features frozen at decision time; no back-fill path).

## 2. Data coverage

- Date range: **2026-06-30 → 2026-08-31** (~9 weeks). Records: 46880 closed in table, **28906 analyzed** (17 primary books), 150 skipped (NULL/zero stop).
- Instruments: 39 pairs — 1000LUNCUSDT, 1000PEPEUSDT, AAVEUSDT, ADAUSDT, APTUSDT, ARBUSDT, AVAXUSDT, BCHUSDT, BNBUSDT, BTCUSDT, CHZUSDT, DASHUSDT, DOGEUSDT, DOTUSDT, ENAUSDT, ETHUSDT, FETUSDT, FILUSDT, GRAMUSDT, HYPEUSDT, ICPUSDT, LINKUSDT, NEARUSDT, NMRUSDT, PENDLEUSDT, PENGUUSDT, PUMPUSDT, QNTUSDT, RENDERUSDT, RUNEUSDT, SOLUSDT, SUIUSDT, TAOUSDT, TRUMPUSDT, UNIUSDT, WLDUSDT, XLMUSDT, XRPUSDT, ZECUSDT.
- Exclusions: variant/hold-time re-count books excluded (one canonical book per family; `_s4/_s6/_s8/_tsl/_limit15/_open/_scalein`, hold variants `_1h/_2h/_8h/_24h`, session splits where a `_all` parent exists). Books: burst_follow(8379), setup_fade(5280), setup_follow(5258), asia_burst_fade(2627), london_burst_fade(2137), nony_momentum(2087), setup_fade_asia(1762), setup_fade_london(1452), follow_3h_tsl_1_0_05(1033), ny_flush_buy_4h_tsl(893), late_fade(852), ny_flush_buy_4h(804), follow_3h_tsl_1_5_1(757), ny_flush_buy_4h_s4(703), ny_flush_buy_4h_s6(625), follow_3h_all(602), ny_flush_buy_4h_s8(596), ny_flush_buy_8h(595), ny_flush_buy_1h(564), ny_flush_buy_24h(534), ny_flush_buy_4h_open_tsl(527), asia_pump_short_4h_tsl(503), ny_flush_buy_4h_limit15(484), follow_6h_all(476), asia_pump_short_4h(463), ny_flush_buy_2h(460), ny_flush_buy_4h_open(441), ny_flush_buy_4h_open_s4(431), setup_fade_late(397), asia_pump_short_4h_s4(396), ny_flush_buy_4h_open_s6(388), asia_pump_short_1h(371), ny_flush_buy_4h_open_s8(368), asia_pump_short_4h_s6(343), asia_pump_short_4h_s8(330), asia_pump_short_24h(302), ny_flush_buy_4h_scalein(295), asia_pump_short_2h(286), asia_pump_short_4h_limit15(264), follow_3h_asia(212), follow_6h_asia(176), follow_3h_london(135), fade_3h_asia(120), follow_6h_london(115), fade_3h_london(103), follow_3h_late(103), follow_6h_late(103), fade_3h_late(99), fade_6h_late(99), fade_6h_asia(97), fade_6h_london(82), ny_burst_fade(64), ny_burst_follow(63), v65_strict_long(59), v65_strict_ny_long(59), setup_fade_ny(29), setup_fade_ny_short(28), ny_burst_fade_short(20), follow_1h_london(19), follow_2h_london(18), follow_3h_ny(3), follow_6h_ny(3), fade_3h_ny(3), fade_6h_ny(3).
- Gaps: btc_trend_state NULL cluster n=771 spanning 2026-06-30→2026-08-07 (known writer warm-up gap) — regime='NA' bucket kept visible, never silently dropped. `entry_atr_pct` NULL n=3092 (imputed per-book median, documented); `funding_rate_symbol` NULL n=786 (funding cost treated as 0 for those rows).

## 3. Win definition (applied to every record)

- Canonical gross R = `pnl_atr / stop_atr` (shadow's stop-normalized multiple, 10-ATR books / 4-ATR books).
- **Win := net_R > 0**, net_R = (gross price-move % − costs %) / stop-distance %, where costs % = 10 bps fees+slippage round trip + funding cost % (side-signed entry funding × hold_hours/8). Applied to **every** record including n<3 segments; gross WR reported alongside for transparency.
- Whole-book baseline: n=28906, net WR=50.6% (Wilson 95% [50.0, 51.2]), E_net=-0.0007R/trade. Identity check: WR·avg_win − (1−WR)·avg_loss = 50.5812·+0.2187 − 49.4188·0.2251 = -0.0007 ✓ (matches E_net=-0.0007).

## 4. Enrichment — declared dimensions (entry-time only; exact buckets)

- session: writer `session` column (UTC-hour-derived: asia/london/ny/late; timezone UTC). hour: UTC 0-23; hour-band 0-5/6-11/12-17/18-23.
- weekday: Python `weekday()`, 0=Mon (mechanically asserted inside the script against a known calendar date). weekend: Sat/Sun.
- trend/range regime: `btc_trend_state` (4h EMA200 ± ADX>25 selector; bull/bear/neutral, computed at entry — entry-time, not look-ahead). trend strength: btc_adx bucket <20 / 20-30 / >=30; symbol_trend_state available but not bucketed into cells.
- volatility regime: entry ATR as % of price bucketed lo<0.15% / mid 0.15-0.40% / hi>=0.40%; realized-vol regime: entry_vol_z <2 / 2-5 / >=5.
- spread/cost state: shadow-recorded spread_bps bucket <2 / 2-5 / >=5 bps (see §8 for applied costs).
- pair: symbol as recorded; book: `strategy` (17 primary). Position-quality: aggression decile D1 / D2-3 / D4-9 / D10 (signal-time rank — entry-time).
- Look-ahead control: NO forward columns used anywhere (fwd_atr_*, post_*, mae/mfe_*, run_*, pnl_1h/2h, bars_to_mfe_peak all excluded; verified by column whitelist in the loader).

## 5. Segmented win-rate analysis (every declared combination)

- Full cross **pair × book × regime** plus every single dimension and book×dim 2-way: **1689 segments**, each with n, net & gross WR, avg win (R), avg loss (R), expectancy (R), PF, ΣR, max losing streak, max drawdown (R), distinct days, top-day share, Wilson 95% CI, breakeven WR, p-value.
- File: `research/win_combo_segments_full.csv` (all segments, including n<3). Ranked candidate file (n≥30): `research/win_combo_candidates.csv`.

## 6. Low-sample flagging

- Segments with **n<30 are flagged `LOW-N`** in the segments CSV (1015 of 1689) and are excluded from any 'almost certain' claim. Candidate floor: n≥30 AND ≥10 distinct days AND top-day share <40% of net.

## 7. Statistical significance (per segment)

- Wilson 95% CI on net win rate (per segment, in both CSVs).
- Significance vs breakeven after costs: per-segment breakeven WR = avg_loss/(avg_win+avg_loss) computed on NET R; one-sided exact binomial p-value P(X≥wins | n, breakeven). Columns `wilson_lo/hi`, `breakeven_WR%`, `p_vs_be` in both files.

## 8. Costs (estimated and applied)

- Shadow fills are at next-bar open and `pnl_atr` is **gross** (verified: `signal_shadow.py` records spread_bps as a feature, deducts nothing). Applied per trade: taker 4bps/side ×2 + slippage 1bp/side ×2 = **10 bps round trip**; funding = side-signed entry funding × (hold_hours/8) — LONGs pay positive funding, SHORTs receive.
- Mean cost per trade: 0.001R (book-dependent: 10-ATR books ≈ 0.02-0.05R, 4-ATR books ≈ 0.05-0.15R). Breakeven WRs above are NET of these costs. Sensitivity: ±4bps shifts thin-edge segments materially; funding impact ≤0.01R for ≤24h holds.

## 9. Multiple-testing correction

- Candidate cells tested (n≥30): **3377** (1-way + 2-way + curated 3-way enumeration). Correction: **Benjamini–Hochberg** FDR on the per-cell exact binomial p-values vs net breakeven; BH q-values in candidates CSV (`BH_q`).

## 10. Out-of-sample validation

- Discovery window (IS): entries < **2026-08-01**; validation window (OOS): entries ≥ **2026-08-01** (disjoint, later period). Discovery gate: IS n≥20 AND IS Wilson-lower > IS breakeven. OOS gate: OOS n≥10 AND OOS E>0 AND OOS binomial p<0.05 vs OOS breakeven.
- IS-discovered cells: **374/3377**. IS∧OOS significant: **41**. Fully validated (all gates): **3**.

## 11. Walk-forward / rolling-window stability

- Three disjoint ~3-week windows (≤2026-07-19, 07-20→08-09, ≥08-10): a validated cell must print E_net>0 in ALL three (`WF1/WF2/WF3` columns in candidates CSV). With only ~9 weeks of data these windows are short — see §15.

## 12. Final ranked table ('almost certain' candidates)

- Full ranked table: `research/win_combo_candidates.csv` (top of file = validated first, then by OOS E). Required fields per entry: pair(s), session(s)/book, regime conditions, n, WR with Wilson CI, expectancy (R), PF, max losing streak, IS + OOS results — all present as columns.
- **VALIDATED**: decbxsession=D10|london | n=1162 WR=50.9% CI[48.0,53.7] E=+0.026R PF=1.23 streak=9 OOS_E=+0.023
- **VALIDATED**: regimexside=bear|LONG | n=2996 WR=52.4% CI[50.6,54.2] E=+0.018R PF=1.19 streak=38 OOS_E=+0.015
- **VALIDATED**: stratxsym=nony_momentum|ZECUSDT | n=144 WR=56.2% CI[48.1,64.1] E=+0.032R PF=1.84 streak=4 OOS_E=+0.023

## 13. Rejected / not reliable (no cherry-picking)

- Every ≥75% WR cell (n≥30) is a 1-2 calendar-day concentration: asia_burst_fade×bull 81.8% (1 day, top-day 100%); ny_flush×Sunday×bull 77.1% (1 day); nony_momentum×Monday×bull 73.2% (1 day); late_fade×Saturday 78.2% (top-day 91%); london_burst_fade×Thursday×bear (2 days, 94%); ny_flush×Tuesday×bull (2 Aug dates = 79% of net; live-window intersection rides ONE day, Aug 19).
- follow_3h_all×h12-17 (72% WR, E +0.16R): July-only (Jul 1-13 ≈ all of +23.3R; Aug ≈ 0) — failed OOS/walk-forward. nony_momentum×bear 'ADX 20-30' conditioning is spurious (all bear trades are ADX 20-30; <20 bucket empty). asia NULL-era cells (writer gap Jul31-Aug07) are instrumentation artifacts, not regimes.
- Cells whose R sits outside live-wired hours/books (e.g. ny_flush R concentrated h14/h20 vs live [16,17]; follow_3h_all, nony_momentum, setup_fade, london_burst_fade not wired live) are descriptive-only.
- Funding-conditioning leg was already killed in the Aug 21 register read (sign-unstable across weeks); not re-litigated here.

## 14. Overfitting risk

- Tested 3377 candidate cells (+1689 segment rows incl. n<30) on ~9 weeks of one market regime mix; 374 passed IS discovery, 41 passed IS+OOS, **3 fully validated**. BH expected FDR ≤5% on surviving p-values. With ~0 survivors the data does not support ANY 'almost certain win' claim; the top-of-book cells are best read as forward-tracking candidates (G0), not tradable edges.

## 15. Caveats & limitations

- Shadow fills = next-bar open with estimated 1bp/side slippage; real cascade fills slip more (book_depth_usd_5bps recorded but not modeled). No queue/latency/partial-fill modeling.
- 9 weeks, 3 walk-forward windows, single regime mix (neutral-heavy) — window count too small to prove stationarity; regime-conditional edges flip sign across episodes (house precedent: asia bear +0.14→−0.17R).
- Most candidate cells live in shadow-only books (live wires asia_pump_short_4h, ny_flush_buy_4h, burst_follow); shadow-book expectancy ≠ live-arm expectancy. Past shadow performance does not guarantee future live wins.

## 16. Reproducibility

- Generator (this run, single execution): `/root/bitana/research/win_combo_deliverable.py` · scan: `win_combo_scan.py` · deep-dive: `win_combo_deepdive.py` · outputs: `WIN_COMBO_DELIVERABLE.md` (this file), `win_combo_segments_full.csv`, `win_combo_candidates.csv`. All under `/root/bitana/research/`.
- Run timestamp: 2026-08-31T08:05:10Z UTC. DB path + full column whitelist embedded in the script header.
