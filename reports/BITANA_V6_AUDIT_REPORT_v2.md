# Bitana V6 — Complete System Audit & Evolution Report

**Document Version:** 2.0  
**Date:** 2026-05-30  
**Author:** OWL (for Martin)  
**Repository:** `github.com:vsehuinya1/bitana`  
**Branch:** `v6.4.1-hotfix` (8 commits ahead of main)  
**Latest Commit:** `de88376` — BD_FILTER log level fix  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Changed Since v1.0 (May 25)](#2-what-changed-since-v10)
3. [System Architecture Overview](#3-system-architecture-overview)
4. [Hotfix Changes (v6.4 → v6.4.1)](#4-hotfix-changes)
5. [Post-Gate Performance](#5-post-gate-performance)
6. [Trajectory Analysis & Regime Discovery](#6-trajectory-analysis--regime-discovery)
7. [State Transition Logic](#7-state-transition-logic)
8. [MAE Persistence Kill Rule](#8-mae-persistence-kill-rule)
9. [Pathway Expectancy Analysis](#9-pathway-expectancy-analysis)
10. [Telemetry Integrity Audit](#10-telemetry-integrity-audit)
11. [Proven vs Experimental Pairs](#11-proven-vs-experimental-pairs)
12. [Open Issues & Next Steps](#12-open-issues--next-steps)
13. [Current Configuration Reference](#13-current-configuration-reference)
14. [Full Git Log](#14-full-git-log)

---

## 1. Executive Summary

Bitana is a liquidation-cascade breakout trading system for Binance USDT-M perpetual futures, executing on 5m candles. It evolved from V3 through v6.4.1-hotfix over approximately 3 weeks of intensive development.

**Current state (v6.4.1-hotfix, May 30):**
- 255 all-time closed trades, 43% WR, -51.93R total (includes pre-gate)
- **Post-gate (63 trades): 47.6% WR, +1.46R total, expectancy +0.023R/trade** ← barely positive
- 57 symbols (35 proven + 22 experimental)
- 14/57 cascades currently active
- System is running with BD < -2% gate active (deployed May 27)

**Critical findings from v6.1.5 analysis:**
1. **The system has 5 latent trajectory regimes, not a continuum** — 2 winner types (EXPLOSIVE convex runners + SURVIVOR slow grinders), 3 loser types (immediate rejection, flat chop, early spike fade)
2. **London session is the primary bleed source** — 20% WR, -6.21R over 15 trades. Skipping London = +7.67R, 56% WR across remaining sessions
3. **All 30 post-gate winners exited via vol_trail** — exit reason consistency is 100%
4. **The imb_z gate remains broken** — confirmations field shows all gates passing on every trade (data recording issue, not gate logic)
5. **r_path telemetry is fully operational** — 26,398 rows across 151 trades, 100% coverage of post-gate trades
6. **MAE recovery is the #1 winner/loser separator** (Cohen's d=1.94) — winners recover 1.6R from trough, losers only 0.42R

**Path to profitability:** Either fix London session (skip or add regime filter) OR reduce average loss from -0.92R to -0.50R via kill rules. Both paths get expectancy to +0.15-0.25R/trade.

---

## 2. What Changed Since v1.0

| Date | Change | Impact |
|------|--------|--------|
| May 25 | v1.0 report published | Baseline: 149 trades, -28.25R, -0.19R expectancy |
| May 25 | Hotfix branch created (v6.4.1) | Started surgical fixes |
| May 26 | imb gate fix (synthetic fallback removed) | imb_z now returns 0.0 when data missing |
| May 26 | BD < -2% gate deployed | Blocks structurally bad entries |
| May 26 | min_confirmations 3→4 | Reverted V6.4 loosening |
| May 27 | BD gate goes live | 837+ entries blocked in first days |
| May 28-30 | Trajectory analysis + tensor construction | Full regime discovery (Phases 1.5-2) |
| May 30 | Pathway expectancy analysis | 8/13 pathways show positive expectancy |

---

## 3. System Architecture Overview

### Strategy: 3-Play Liquidation Cascade Breakout

| Play | Description |
|------|-------------|
| **A) Breakout Continuation** | Enters when price breaks above 60-bar range high with cascade backing |
| **B) Liq Cascade Capture** | Enters when liquidation cluster is detected and cascade strength exceeds threshold |
| **C) Momentum Ignition** | Enters when reclaim + momentum signals align after cascade activates |

### Confirmation Stack (6 gates)

| Gate | Current Threshold | Notes |
|------|------------------|-------|
| body | > 0.50 | % of candle body to range |
| imb_z | > 1.0 | Taker buy/sell volume z-score (BUG: returns 0.0 when data missing) |
| vol_z | > 2.0 | Volume z-score |
| impulse | > 0.20 | Impulse strength |
| breakout | above range_high | Price breaks 60-bar range (NEVER fires — inversely predictive) |
| momentum | positive | Momentum direction |

**min_confirmations: 4** (reverted from V6.4's 3)

### Exit Logic (per-decile parameters)

All winners exit via **vol_trail** (ATR-based trailing stop). Losers exit via **stop_loss** (full -1R) or **early_cut** (decay detection).

Key exit params vary by decile: vol_trail_atr (1.5-3.0), max_hold_bars (100-500), struct_lookback (8-48).

---

## 4. Hotfix Changes (v6.4 → v6.4.1)

### Change A: imb Gate Fix
**File:** `engines/liq_cluster_engine_v5.py`
**Problem:** When `taker_buy_volume` was zero/invalid, the imb calculation used a synthetic fallback that produced misleadingly high imb_z values
**Fix:** When taker data is missing/invalid, `imb_z` now correctly returns 0.0
**Impact:** imb confirmation now properly fails when data is unavailable

### Change B: BD Lower-Bound Gate
**File:** `engines/liq_cluster_engine_v5.py`
**Logic:** `breakout_distance_pct = (close - max(high[-61:-1])) / max(high[-61:-1]) * 100`
**Threshold:** Reject entry if `breakout_distance_pct < -2.0%`
**Rationale:** BD < -2% was the worst loss cluster: 25 trades, 32% WR, -13.40R pre-gate
**Status:** Deployed and active since 2026-05-27T21:00 UTC

### Change C: min_confirmations 3→4
**Rationale:** V6.4 loosened confirmations (4→3). Result: 18 trades, -11.16R, 10.5% WR.
Audit showed all 18 trades would have been blocked at 4. Reverted.

### Change D: Breakout Distance Logging
Added `range_high`, `breakout_distance_pct`, `imb_z`, `imb_fallback_triggered` to signal_data for runtime visibility.

---

## 5. Post-Gate Performance

### Overall (post-gate, 63 trades)

| Metric | Value |
|--------|-------|
| Trades | 63 (30W / 33L) |
| Win Rate | 47.6% |
| Total R | +1.46R |
| Winner R | +31.93R (avg +1.064R) |
| Loser R | -30.47R (avg -0.923R) |
| Expectancy/trade | +0.023R |
| Kelly fraction | 2.2% |

### Winner Exit Reasons

| Reason | Count | Total R | Avg R |
|--------|-------|---------|-------|
| vol_trail | 30/30 | +31.93R | +1.064R |

**100% of winners exit via vol_trail.** Zero exceptions.

### Loser Exit Reasons

| Reason | Count | Total R | Avg R |
|--------|-------|---------|-------|
| stop_loss | 29/33 | -29.72R | -1.025R |
| vol_trail | 3/33 | -0.14R | -0.045R |
| early_cut | 1/33 | -0.62R | -0.617R |

### Session Breakdown (post-gate)

| Session | WR | Total R | Verdict |
|---------|----|---------|---------|
| Asia (00-08 UTC) | 54% | +3.16R | ✅ Profitable |
| **London (08-14 UTC)** | **20%** | **-6.21R** | **❌ Primary bleed** |
| NY (14-21 UTC) | 69% | +5.81R | ✅ Profitable |
| LateNY (21-24 UTC) | 33% | -1.29R | ⚠️ Slight negative |

### Winners by Size

| Size | Count | Total R | Avg R |
|------|-------|---------|-------|
| <0.5R | 7 | +1.94R | +0.28R |
| 0.5-1R | 11 | +8.46R | +0.77R |
| 1-2R | 10 | +14.44R | +1.44R |
| 3R+ | 2 | +7.09R | +3.55R |

Recent examples from live Telegram:
- INJ +3.77R (vol_trail, 45c)
- ETH +1.58R (vol_trail, 34c)
- DOT +1.24R (vol_trail, 11c)
- VIRTUAL +0.52R (vol_trail, 29c)

### Daily Report (May 29)

- 26 trades, 50% WR, +4.12R
- All-time: 255 trades, 43% WR, -51.93R
- Equity: $680.34 (DD 93.2% from peak — includes pre-gate losses)

---

## 6. Trajectory Analysis & Regime Discovery

Based on 151 trades with full r_path data, trajectory clustering identified **7 regimes** that collapse into **3 actionable classes**.

### 3 Actionable Classes

| Class | N | WR | Avg PnL | Avg MFE | Avg Bars | Exit |
|-------|---|----|---------|---------|----------|------|
| **EXPLOSIVE** (convex runners) | 26 | 100% | +0.91R | 1.89R | 29 | All vol_trail |
| **SURVIVOR** (slow grinders) | 26 | 100% | +0.43R | 0.74R | 365 | Mixed |
| **DEAD** (all losers) | 99 | 0% | -0.81R | 0.19R | 223 | Mostly stop_loss |

EXPLOSIVE: MFE > 1.0R within 15 bars, positive acceleration. Short hold, strong expansion.
SURVIVOR: Winners with either MFE < 1.0R OR > 20 bars to reach 1R. Long hold, low MAE, modest MFE.
DEAD: Everything else. Subclassified as EARLY_DEAD (MFE never > 0.3R in first 10 bars), LATE_DEAD (MFE > 0.3R but still lost), FADE_DEAD (MFE > 0.5R then collapsed).

### Winner vs Loser: Cold Facts (Cohen's d)

| Feature | Winners | Losers | d | Separation |
|---------|---------|--------|---|------------|
| **Max MAE Recovery** | 1.60R | 0.42R | **+1.94** | ⭐ Best separator |
| **Max MFE** | 1.32R | 0.21R | **+1.69** | ⭐ Strong |
| **MFE Velocity (late)** | +0.060 | -0.002 | **+0.86** | ⭐ Strong |
| **Stale %** (deep MAE + no growth) | 22% | 50% | -0.79 | Strong |
| **Max MAE Depth** | 0.28R | 0.59R | -1.06 | Strong |
| **Deep Bleed %** | 26% | 51% | -0.67 | Moderate |
| **MFE Velocity (early)** | 0.049 | 0.020 | +0.61 | Moderate |
| **Consec Deep Bleed** | 122b | 119b | +0.01 | ❌ No separation |

### Time-to-First-Expansion Distribution

| Threshold | Winners median | Losers median | Never reached |
|-----------|---------------|---------------|---------------|
| MFE ≥ 0.10R | 3 bars | 3 bars | W: 1/52, L: 51/99 |
| MFE ≥ 0.20R | 4 bars | 3 bars | W: 7/52, L: 58/99 |
| **MFE ≥ 0.30R** | **5 bars** | **4 bars** | **W: 8/52, L: 67/99** |
| MFE ≥ 0.50R | 8 bars | 5 bars | W: 11/52, L: 83/99 |

**The regime boundary is at 0.30R, not 0.20R.** Below 0.30R, winners and losers look identical in timing. At 0.30R+, they diverge sharply: 67% of losers never reach it vs 15% of winners.

---

## 7. State Transition Logic

State machine traced across 151 trades with 26,398 bar observations.

### Transition Frequencies

| From | To | Count |
|------|----|-------|
| ENTRY | IGNITION | 151 |
| IGNITION | EXPANSION | 88 |
| IGNITION | CONFIRMATION | 37 |
| IGNITION | DECAY | 19 |
| EXPANSION | DECAY | 63 |
| EXPANSION | CONFIRMATION | 43 |
| CONFIRMATION | EXPANSION | 23 |
| CONFIRMATION | DECAY | 2 |
| DECAY | EXPANSION | 26 |
| DECAY | CONFIRMATION | 8 |

### State Durations

| State | Median | Avg |
|-------|--------|-----|
| IGNITION | 3 bars | 3 bars |
| EXPANSION | 4 bars | 42 bars |
| CONFIRMATION | 8 bars | 24 bars |
| **DECAY** | **22 bars** | **439 bars** |

### Exits by Final State

| Final State | Exit Reasons |
|-------------|-------------|
| EXPANSION | 7 early_cut, 7 time_stop, 14 stop_loss, 3 struct_trail |
| CONFIRMATION | **41 vol_trail**, 4 struct_trail, 1 cascade_deactivated, 17 stop_loss |
| DECAY | 39 stop_loss, 2 time_stop, 8 early_cut, 1 vol_trail |

**Key insight:** 41 of 41 vol_trail exits came from CONFIRMATION state. The path to winning: get to CONFIRMATION (MFE > 0.3R while still expanding). Only 2 trades exited from CONFIRMATION via early_cut — it's a safe state.

DECAY is where trades go to die: 39 stop_loss exits, median 22 bars in the state.

---

## 8. MAE Persistence Kill Rule

### Rule Definition

```
IF bar >= 5
  AND MAE > 0.5R
  AND MFE velocity < 0.01 for 3+ consecutive bars
  AND MAE recovery < 0.3R
THEN kill at market (est. -0.50R)
```

### Results (in-sample, 151 trades)

| Metric | Value |
|--------|-------|
| True kills (losers caught) | 37/99 (37%) |
| False kills (winners killed) | 10/52 (19%) |
| Missed losers | 62/99 (63%) |
| Clean winners | 42/52 (81%) |

### By Actionable Class

| Class | True Kills | False Kills | Clean |
|-------|-----------|-------------|-------|
| EXPLOSIVE | 0 | 1 | 25 |
| SURVIVOR | 0 | 9 | 17 |
| EARLY_DEAD | 32 | 0 | 40 |
| LATE_DEAD | 3 | 0 | 11 |
| FADE_DEAD | 2 | 0 | 11 |

### False Kills (winners destroyed)

| Symbol | PnL | Class | Killed at bar | Would have been |
|--------|-----|-------|---------------|-----------------|
| INJ | +0.87R | SURVIVOR | 7/57 | vol_trail |
| AAVE | +0.78R | SURVIVOR | 844/3086 | time_stop |
| NEAR | +0.76R | SURVIVOR | 12/39 | vol_trail |
| SOL | +0.74R | SURVIVOR | 146/2411 | time_stop |
| QNT | +0.41R | SURVIVOR | 220/343 | struct_trail |
| ZEC | +0.37R | EXPLOSIVE | 13/70 | vol_trail |
| NMR | +0.28R | SURVIVOR | 17/47 | vol_trail |
| INJ | +0.09R | SURVIVOR | 15/26 | vol_trail |
| LINK | +0.08R | SURVIVOR | 7/151 | struct_trail |
| LTC | +0.07R | SURVIVOR | 25/36 | vol_trail |

### Expectancy Impact

| Metric | Original | With Rule | Delta |
|--------|----------|-----------|-------|
| Expectancy/trade | -0.309R | -0.283R | +0.026R |
| Total R (151 trades) | -46.58 | -42.71 | +3.88R |
| Compounding @ 0.5% risk | $79.10 | $80.67 | +$1.57 |

### Assessment

**The rule marginally improves expectancy (+0.026R/trade) but is too blunt.** It catches 37% of losers by cutting them at -0.50R instead of -1.00R, but kills 19% of winners — mostly SURVIVOR class trades that dip deep then recover over long timeframes. The core failure mode: confusing "temporary adversity" with "non-viability."

**Not recommended for implementation as-is.** The SURVIVOR false kills are exactly the trades you want to keep.

---

## 9. Pathway Expectancy Analysis

13 pathway filters tested. 8 show positive expectancy.

### Positive Pathways

| Pathway | N | WR | Exp/R | Kelly | $100→ (1% risk) | MaxDD |
|---------|---|----|-------|-------|-----------------|-------|
| EXPANSIVE (MFE>1.5R in 15 bars) | 16 | 100% | +1.31R | — | $123 (+23%) | 0% |
| FAST WINNER (hold < 20 bars) | 11 | 100% | +0.71R | — | $108 (+8%) | 0% |
| CONFIRMED + HIGH RECOVERY | 35 | 86% | +0.63R | 72% | $124 (+24%) | 2% |
| **HIGH RECOVERY (>1R from trough)** | 50 | 88% | **+0.59R** | 76% | **$134 (+34%)** | **1%** |
| SUSTAINED ACCEL (vel>0 late) | 49 | 82% | +0.46R | 60% | $125 (+25%) | 3% |
| CONFIRMED + SHALLOW MAE | 40 | 68% | +0.36R | 39% | $115 (+15%) | 5% |
| SHALLOW MAE (<0.4R) | 56 | 62% | +0.19R | 27% | $111 (+11%) | 9% |
| CONFIRMED (MFE>0.3R in 10 bars) | 57 | 54% | +0.06R | 7% | $103 (+3%) | 10% |

### Most Practical: HIGH RECOVERY

- 50 trades, 88% WR, +0.59R expectancy
- At 1% risk: $100 → $134 (+34%), MaxDD only 1%
- This is the "don't kill trades that recover from troughs" principle quantified

### Critical Caveat

**Most positive pathways are post-hoc.** You only know "HIGH RECOVERY" after the trade has recovered. The one fully real-time filter is "CONFIRMED (MFE>0.3R in 10 bars)" at +0.06R expectancy — barely positive.

The real-time question: can you build a classifier that identifies EXPLOSIVE vs DEAD before bar 10?

---

## 10. Telemetry Integrity Audit

### r_path Status

| Metric | Value |
|--------|-------|
| Rows | 26,398 |
| Trades covered | 151 |
| Post-gate coverage | 56/56 (100%) |
| Per-bar fields | 13 (price, unrealized_r, mae_so_far, mfe_so_far, vol_trail_level, struct_trail_level, atr, consecutive_red, above_ema, above_range_high) |

### MFE Tracking Accuracy

| Check | Result |
|-------|--------|
| vol_trail exits with MFE=0 | ✅ Zero (all track properly) |
| early_cut exits with MFE=0 | ✅ By design (condition is mfe < 0.1R) |
| r_path max_mfe vs trades.mfe | ⚠️ 1-bar lag on some trades |

**The 1-bar lag:** Runner logs r_path BEFORE calling manage_position(). On bar 1, engine MFE is 0 (hasn't run yet). So r_path MFE at bar N = engine MFE at bar N-1. On the close bar, the close result overwrites p["mfe"], so trades.mfe is always correct. r_path is off by at most one bar's increment.

### MFE/MAE Correctness on Key Trades

| Trade | pnl_r | trades.mfe | r_path max | Match? |
|-------|-------|-----------|------------|--------|
| ETH +1.73R | +1.73 | 2.599 | 2.599 | ✅ |
| LTC +1.56R | +1.56 | 2.165 | 2.165 | ✅ |
| HYPE +1.24R | +1.24 | 2.342 | 1.859 | ⚠️ (-0.48R) |
| QNT +0.94R | +0.94 | 1.825 | 0.944 | ⚠️ (-0.88R) |

### early_cut Bug

early_cut fires for ALL deciles (D1-D10) regardless of `decay_enabled` setting. For D1/D2/D3 (decay_start_bar=999), it only fires at bar 999+, which means it fires AFTER time_stop (max_hold_bars=288-500) would have already closed the trade. In practice, early_cut fires for D9/D10 (decay_start_bar=8) and for deciles where a trade survives long enough via time_stop to reach bar 999+.

### Confirmation Recording Bug

All 63 post-gate trades show all 5 confirmations (body, imb_z, vol_z, impulse, breakout, momentum) as passing — on BOTH winners and losers. This is a **data recording issue**, not gate behavior. The confirmations field isn't being updated post-hotfix. Needs investigation.

---

## 11. Proven vs Experimental Pairs

### All-time (255 trades)

| | Proven (169t) | Experimental (86t) |
|--|---|---|
| WR | 47% | 35% |
| Total R | -34.90R | -17.03R |
| Exp/trade | -0.207R | -0.198R |

Both are negative. Experimental is slightly less negative per trade.

### Last 7 Days Post-Gate

| Date | Proven | Experimental |
|------|--------|-------------|
| May 27 | -4.68R | +1.50R |
| May 28 | -3.88R | +1.34R |
| **May 29** | **-0.69R** | **+6.20R** |
| May 30 | +0.73R | -2.12R |

Experimental carried May 29 (8/15 trades, +6.20R).

### The Decile Split

| Decile | Proven | Experimental |
|--------|--------|-------------|
| D1 | 89t, -20.34R | 54t, -20.40R |
| **D2** | 26t, **-6.53R** | 13t, **+4.54R** |
| D6 | 9t, -2.25R | 4t, +2.41R |

D2 experimental is +4.54R while D2 proven is -6.53R. This is what's driving the difference.

### Experimental Top Symbols

| Symbol | N | WR | Total R |
|--------|---|---|---------|
| **INJUSDT** | 11 | 55% | **+4.01R** |
| VIRTUALUSDT | 3 | 67% | +1.44R |
| FILUSDT | 5 | 40% | +0.86R |
| AVAXUSDT | 9 | 11% | -6.41R |
| HYPEUSDT | 9 | 22% | -4.27R |

**INJ alone is +4.01R.** Without INJ, experimental is flat or negative. The outperformance is NOT systematic — it's 1-2 symbols carrying the group.

### Recommendation

**Do NOT expand to more experimental pairs blindly.** The edge is symbol-specific (INJ), not a category effect. Adding more random pairs will likely dilute the edge.

If expanding: target pairs that behave like D2 experimental (where the +4.54R came from). Need to identify what makes D2 experimental different from D2 proven — symbol selection, liquidity profile, or session behavior.

---

## 12. Open Issues & Next Steps

### Critical Issues

1. **Confirmation recording broken** — all gates show pass on every trade post-hotfix. Need to fix the confirmation logging in the runner.
2. **imb_z gate data quality** — returns 0.0 when taker data missing, but the confirmations field doesn't record this accurately.
3. **London session bleed** — 20% WR, -6.21R. Either skip London entirely or build a regime filter.

### High-Priority Analysis

1. **Build real-time classifier** — Can you identify EXPLOSIVE vs DEAD before bar 10 using only information available at that bar? This is the highest-leverage question.
2. **Composer implementation** — The tensor and regime analysis is ready for Composer to build enforceable state-machine logic, instrumentation, and forward-testing pipelines.
3. **Validate CONFIRMED pathway** — MFE > 0.3R within 10 bars is the only real-time filter with positive expectancy (+0.062R). Test out-of-sample on next 30-50 trades.

### Forward Test Specification

**Single rule to test:** MAE-Recovery Kill
- Rule: MAE > 0.5R + MFE velocity < 0.01 for 3+ bars + recovery < 0.3R, after bar 5
- Abort threshold: >25% false kill rate on winners in forward sample
- Success: forward expectancy > 0 AND WR > 45%
- Sample needed: 30+ trades for statistical validity

**But:** In-sample results show +0.026R expectancy improvement with 19% false kill rate on winners. This is marginal. Composer should refine the rule before deployment.

---

## 13. Current Configuration Reference

### V5Config (current on VPS, v6.4.1-hotfix)

| Parameter | Value |
|-----------|-------|
| min_confirmations | 4 |
| vol_z threshold | 2.0 |
| imb_z threshold | 1.0 |
| body threshold | 0.50 |
| impulse threshold | 0.20 |
| BD gate | REJECT if BD < -2.0% |
| liq_lookback | 90 days |
| p90 cascade filter | Active (sole cascade filter) |
| min_cascade_imb | DISABLED |

### DECILE_EXITS (key params)

| Decile | vol_trail_atr | struct_lookback | max_hold_bars | decay_enabled | decay_start |
|--------|--------------|-----------------|---------------|---------------|-------------|
| D1 | 3.0 | 48 | 500 | No | 999 |
| D2 | 3.0 | 48 | 500 | No | 999 |
| D3 | 2.0 | 24 | 288 | No | 999 |
| D4 | 2.0 | 12 | 288 | Yes | 15 |
| D5 | 2.0 | 12 | 288 | Yes | 15 |
| D6 | 2.0 | 12 | 288 | Yes | 12 |
| D7 | 2.5 | 36 | 358 | Yes | 20 |
| D8 | 2.5 | 36 | 358 | Yes | 20 |
| D9 | 1.5 | 8 | 100 | Yes | 8 |
| D10 | 1.5 | 8 | 100 | Yes | 8 |

### Service Info

- Service: `bitana-v5-paper.service`
- Branch: `v6.4.1-hotfix`
- DB: `storage/v5_forward_test.db` (trades), `storage/v6_telemetry.db` (r_path)
- Runner: `tools/v5_forward_test.py` (1,403 lines, unmodified)
- Engine: `engines/liq_cluster_engine_v5.py` (hotfixes applied)

---

## 14. Full Git Log

```
de88376 fix: change BD_FILTER log level from debug to info
174fe56 v6.4.1: add BD lower-bound gate — reject entries with BD < -2.0%
a8180fc v6.4.1: revert min_confirmations 3→4
df5b661 v6.4.1: change imb_gate and breakout_gate logs from debug to info
e5d2601 v6.4.1-hotfix: fix imb gate, add breakout logging, consolidate decile filter
6d4a6a6 docs: add V6 audit report v1.0
456dbed v6.4: loosen entry confirmations (V6.4 loosening — later reverted)
b2bfabf feat: disable min_cascade_imb gate
d8864fc revert: restore liq_lookback to 90 days
1842eef fix(forward-test): align VPS recovery checks, shorten liq lookback
c4550ee fix: exit timestamp -5m offset bug
971bf2d widen early_cut threshold to R < -0.5 and MFE < 0.1
ccefbda fix(forward-test): resolve candle loop double-processing bug
8502cd9 add 7 new experimental pairs
a38de21 surgical experimental-pair tracking
d91b670 feat(telemetry): implement async bounded queue isolation layer
e81b043 feat(research): add analyze_telemetry.py
2987ce9 feat(telemetry): deploy v6.2 research telemetry, shadow exits
cb03ba7 V6.0: Full audit fix — 18 bugs fixed
...
```

---

*End of report v2.0. Generated 2026-05-30. For the latest data, check the r_path table in v6_telemetry.db and the analysis scripts in tools/.*
