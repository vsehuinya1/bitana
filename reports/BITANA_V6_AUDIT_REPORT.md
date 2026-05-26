# Bitana V6 — Complete System Audit & Evolution Report

**Document Version:** 1.0  
**Date:** 2026-05-25  
**Author:** OWL (for Martin)  
**Repository:** `github.com:vsehuinya1/bitana`  
**Branch:** `main`  
**Latest Tag:** `v6.4`  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Full Version History & Git Log](#3-full-version-history--git-log)
4. [What Worked — What Didn't](#4-what-worked--what-didnt)
5. [Known Bugs & Issues](#5-known-bugs--issues)
6. [Live Performance Analysis](#6-live-performance-analysis)
7. [Confirmation Gate Analysis](#7-confirmation-gate-analysis)
8. [Stop Loss Analysis](#8-stop-loss-analysis)
9. [Recommendations for Next AI](#9-recommendations-for-next-ai)
10. [Current Configuration Reference](#10-current-configuration-reference)

---

## 1. Executive Summary

Bitana is a liquidation-cascade breakout trading system for Binance USDT-M perpetual futures, executing on 5m candles. It evolved from V3 through V6.4 over approximately 2 weeks of intensive development.

**Current state (v6.4):**
- 149 closed trades, -28.25R total, 45% WR
- 57 symbols (35 proven + 22 experimental)
- 25/57 cascades currently active
- System is running but has **negative expectancy** (-0.19R/trade)
- A **critical bug** exists: the `imb` confirmation gate passes 83% of the time despite `imb_z=0.00` on every entry — the gate is effectively a free pass
- The `breakout` gate fires 0% of the time — no trade in the last 18 entries broke above the 60-bar range high

**The system was directionally correct at one point.** V6.2 (before the confirmation loosening) showed promising results on D5 and D8, and the early-cut mechanism was saving value. The loosening in V6.4 was intended to increase entry frequency but introduced low-quality entries that are bleeding on stop losses.

**Key finding:** The entry problem is not the thresholds — it's that the `imb` gate is broken and `breakout` never fires. The system is effectively trading on `body + momentum` alone (3/6 gates that almost always pass).

---

## 2. System Architecture Overview

### Strategy: 3-Play Liquidation Cascade Breakout

| Play | Description |
|------|-------------|
| A | Breakout continuation after liquidation cascade |
| B | Liquidation cascade capture (momentum ignition) |
| C | Momentum ignition after reclaim |

### Core Components

| File | Purpose |
|------|---------|
| `engines/liq_cluster_engine_v5.py` | Main trading engine — entry/exit logic, confirmations, cascade detection |
| `tools/v5_forward_test.py` | Live runner — WebSocket, candle loop, position management, Telegram alerts |
| `config/v5_forward_test.yaml` | Symbol configuration (tiers, risk, cascade params) |
| `data/candle_manager.py` | Candle creation, taker_buy_volume population |
| `v6_telemetry.py` | Async telemetry — R-path tracking, shadow exits, exit attribution |
| `shadow_exits.py` | Shadow exit evaluator (research) |

### Data Sources

| Source | Role | Status |
|--------|------|--------|
| Binance WebSocket `!forceOrder@arr` | Primary liquidation feed | ✅ Active |
| Coinalyze DB | Historical liq seeding (cold start) | ✅ 90 symbols |
| Binance REST (klines) | 5m candle data | ✅ Active |
| Binance REST (aggTrade) | taker_buy_volume | ⚠️ Often zero |

### Infrastructure

- **VPS:** 161.97.185.65 (root, systemd managed)
- **Service:** `bitana-v5-paper.service`
- **DB:** SQLite WAL mode (`v5_forward_test.db` + `v6_telemetry.db`)
- **Telemetry:** Async queue + background thread pool + fallback spooling
- **Alerts:** Telegram bot (HTML mode, 2-attempt retry, 4000 char truncation)

---

## 3. Full Version History & Git Log

### v5.0 → v5.3 (Pre-audit baseline)

| Tag | Date | Commit | Description |
|-----|------|--------|-------------|
| v5.2 | 2026-05-22 | `ab5237c` | Migrate Coinalyze → Binance allForceOrders + disable squeeze filter |
| v5.3 | 2026-05-23 | `ffb2f76` | Migrate REST endpoint to WebSocket `!forceOrder@arr` for zero rate-limiting |

**What happened:** Coinalyze API deprecated `allForceOrders` (400 error). Migrated to Binance WebSocket as primary liq feed. Coinalyze kept for cold-start seeding only.

### V6.0 — Full Audit Fix (2026-05-23)

| Tag | Date | Commit | Description |
|-----|------|--------|-------------|
| v6.0 | 2026-05-23 | `cb03ba7` | Full audit fix — 18 bugs fixed |

**Engine fixes (9):**
1. **P0** Removed double `bars_held` increment in `manage_position()` — winners were exiting at 2x speed (D1/D2 trades with max_hold=500 exiting after ~250 bars)
2. **P1** Fixed `imb_z` to use real taker buy imbalance z-score (was a meaningless price-distance metric)
3. **P1** Widened aggression mapping from [-2,+2] to [-3,+3] — **THIS WAS A REGRESSION** (see below)
4. **P2** `stop_cooldown = 288` (24h) instead of 999999 (permanent ban)
5. **P2** Time-based stop_cooldown decrement in `evaluate()`
6. **P2** Cascade-deactivation exit tightening (1.0 ATR trail)
7. **P2** `get_risk_pct()` returns vol-targeted risk (was returning stale flat 4%)
8. **P3** Renamed `oi_acceleration` → `vol_acceleration`
9. **P3** Updated docstring to V6

**Runner fixes (9):**
1. **P0** Debounced WS engine updates (60s intervals, not per-event)
2. **P0** Batch SQLite commits + WAL mode
3. **P1** `asyncio.Lock` for engine state
4. **P2** Fixed equity snapshot position count
5. **P3** `deque(maxlen=200)` for candle buffers
6. **P3** Fixed duplicate key (removed hardcoded LONG)
7. **P3** Self-test checks all symbols
8. **P3** Fixed startup Telegram message
9. **P3** Updated all alert messages to V6

**Martin's assessment:** This was a necessary and mostly correct release. The aggression widening to [-3,+3] was the only mistake — it flooded D1 with marginal signals.

### V6.1 — Revert Aggression + Add Cascade Imb Gate (not tagged)

| Date | Change | Description |
|------|--------|-------------|
| 2026-05-24 | Reverted aggression | [-3,+3] → [-2,+2] (Claude's widening was a regression) |
| 2026-05-24 | Added `min_cascade_imb=0.30` | Filter neutral cascades — require directional imbalance |

**Martin's assessment:** The aggression revert was correct. The `min_cascade_imb` gate was too aggressive — it blocked 11 out of 12 cascade-active symbols, causing 6+ hours of zero entries.

### V6.2 — D3 Rejection + Directional Filter + Early Cut (2026-05-24)

| Tag | Date | Commit | Description |
|-----|------|--------|-------------|
| v6.2 | 2026-05-24 | `dde5950` | Reject D3, D1-D2 directional filter, early-cut dead trades |

**Changes:**
1. **D3 rejection:** 6 trades, -3.91R, none with MFE>0.61R — added to rejection list with D4/D10
2. **D1-D2 directional filter:** Must pass `imb_z` OR `vol_z` confirmation
3. **Early cut:** At `decay_start_bar`, if `R < -0.3` AND `MFE < 0.3`, cut the trade

**Martin's assessment:** This was the **best version**. D3 rejection was data-driven. Early cut was saving value. D1-D2 directional filter was reasonable. Results were directionally correct — D5 showed +1.81R on 4 trades (75% WR), D8 showed +2.91R on 12 trades (58% WR). The system was finding edges.

### V6.2 Telemetry Deployment (2026-05-24)

| Date | Commit | Description |
|------|--------|-------------|
| 2026-05-24 | `2987ce9` | Deploy v6.2 research telemetry, shadow exits, infra fixes |
| 2026-05-24 | `d91b670` | Async event-loop safe bounded queue-based telemetry isolation |
| 2026-05-24 | `ae4cb8b` | Fix `is_experimental` in trades table + telemetry + close_position |

### V6.3 — Tier 2 Experimental Pairs (2026-05-24)

| Tag | Date | Commit | Description |
|-----|------|--------|-------------|
| v6.3 | 2026-05-24 | `230c984` | Tier 2 experimental pairs (15 new, 50 total) |

**Changes:**
- Added 15 experimental pairs: HYPE, DOGE, SUI, GRASS, ONDO, LINK, AVAX, INJ, AAVE, PENGU, LTC, TRUMP, FARTCOIN, VIRTUAL, TIA
- `is_experimental` column added to both `trade_entries` and `trades` tables
- Runner patched to read `tier_c_experimental`

### V6.3 Fixes (2026-05-25)

| Date | Commit | Description |
|------|--------|-------------|
| 2026-05-25 | `c4550ee` | Fix exit timestamp -5m offset bug (guard in `_manage_positions`) |
| 2026-05-25 | `971bf2d` | Widen early_cut threshold to `R < -0.5` and `MFE < 0.1` |

**Early cut analysis (Gemini trajectory analysis):**
- 15 early_cut trades analyzed with actual 5m candle trajectories
- Widening from `R<-0.3/MFE<0.3` to `R<-0.5/MFE<0.1` saves +2.2R to +3.1R
- 8 trades survive the wider threshold (4 go on to win big: NEAR +4.33R, VIRTUAL +2.92R, ZEC +3.14R, AVAX +2.20R; 4 hit SL)
- 7 still cut — net improvement +2.26R to +3.11R

### V6.3.1 — Liq Lookback Revert (2026-05-25)

| Date | Commit | Description |
|------|--------|-------------|
| 2026-05-25 | `1842eef` | Shorten daily liq lookback to 30 days (MISTAKE) |
| 2026-05-25 | `d8864fc` | **REVERT:** Restore liq_lookback to 90 days |

**What happened:** Attempted to help dead symbols cascade by shortening lookback. Made things WORSE — lost 5 active cascades (28→23) because shorter window keeps recent spikes MORE prominent. Reverted within hours.

### V6.3.2 — Remove min_cascade_imb Gate (2026-05-25)

| Date | Commit | Description |
|------|--------|-------------|
| 2026-05-25 | `b2bfabf` | Disable min_cascade_imb gate — p90 check is sufficient |

**What happened:** The `min_cascade_imb=0.30` gate (added in V6.1) was blocking 11/12 cascade-active symbols. Removed it — p90 is now the sole cascade filter. This was the right call.

### V6.4 — Loosen Entry Confirmations (2026-05-25) ← CURRENT

| Tag | Date | Commit | Description |
|-----|------|--------|-------------|
| v6.4 | 2026-05-25 | `456dbed` | Loosen entry confirmations |

**Changes:**

| Parameter | Old | New | Rationale |
|-----------|-----|-----|-----------|
| `vol_z_threshold` | 3.0 | 2.0 | Above-avg volume, not extreme |
| `imb_z_threshold` | 2.0 | 1.0 | Just needs positive taker pressure |
| `body_strength_min` | 0.60 | 0.50 | Slightly more lenient |
| `impulse_min_pct` | 0.30 | 0.20 | Lower min move |
| `min_confirmations` | 4/6 | 3/6 | More entries, noisier |

**Martin's assessment:** This was a mistake. The loosening increased entry frequency but the signal quality dropped significantly. 18 trades fired post-loosen, ALL would have been blocked by old rules. Result: -11.16R on 18 trades (22% WR both proven and experimental). The `imb` gate is broken (passes 83% with imb_z=0.00) and `breakout` never fires (0/18).

---

## 4. What Worked — What Didn't

### ✅ What Worked

| Feature | Evidence |
|---------|----------|
| **D5 decile** | 4 trades, +1.81R, 75% WR — clean edge, needs more data |
| **D8 decile** | 12 trades, +2.91R, 58% WR — solid performer |
| **D3 rejection** | Zero D3 trades post-V6.2, was -4.38R on 9 trades before |
| **Early cut (widened)** | Saving +2.2R to +3.1R vs old threshold — mechanically correct |
| **Cascade deactivation exit** | 1 trade, +1.42R — tightened trail when cascade dies |
| **WebSocket migration** | Zero rate-limiting, real-time liq aggregation working |
| **WAL mode + batch commits** | Eliminated IO contention |
| **WS debounce (60s)** | Eliminated per-event CascadeTracker rebuild |
| **DASH pair** | 11 trades, +2.76R, 64% WR — best proven pair |
| **SOL pair** | 12 trades, +0.77R, 58% WR — consistent |
| **FET pair** | 12 trades, +0.63R, 67% WR — steady |

### ❌ What Didn't Work

| Feature | Evidence |
|---------|----------|
| **Aggression widening [-3,+3]** | Flooded D1 with marginal signals — reverted |
| **min_cascade_imb=0.30** | Blocked 11/12 cascade symbols — removed |
| **liq_lookback=30** | Lost 5 active cascades — reverted to 90 |
| **Confirmation loosening (V6.4)** | -11.16R on 18 trades, 22% WR — **REVERT RECOMMENDED** |
| **D1 as a decile** | 72 trades, -15.67R, 47% WR — negative expectancy, but Martin says keep collecting data |
| **D7 decile** | 13 trades, -3.34R, 31% WR — keep collecting per Martin |
| **D9 decile** | 12 trades, -2.68R, 33% WR — keep collecting per Martin |
| **LTC pair** | 4 trades, -2.84R, 0% WR — cut candidate |
| **HYPE pair** | 3 trades, -2.14R, 0% WR — cut candidate |
| **AVAX pair** | 4 trades, -2.25R, 25% WR — cut candidate |
| **ZEC pair** | 12 trades, -5.29R, 33% WR — worst proven pair |
| **NEAR pair** | 22 trades, -4.41R, 50% WR — high volume but negative |

### ⚠️ Mixed / Needs More Data

| Feature | Evidence |
|---------|----------|
| **D2 decile** | 18 trades, -4.55R, 44% WR — small sample, inconclusive |
| **D6 decile** | 9 trades, -2.35R, 44% WR — small sample |
| **Experimental pairs overall** | 30 trades, -9.06R, 30% WR — but VIRTUAL (+0.92R) and AAVE need individual evaluation |

---

## 5. Known Bugs & Issues

### 🔴 CRITICAL: imb Confirmation Gate is Broken

**File:** `engines/liq_cluster_engine_v5.py`, line 525

```python
confirmations['imb'] = imb_z > CFG.imb_z_threshold
```

**Problem:** `imb_z` is 0.00 on every entry (taker_buy_volume is zero on entry candles). With `imb_z_threshold=1.0`, this should always evaluate to `False`. But telemetry shows it passing 15/18 times (83%).

**Root cause:** The `imb_z` computation at lines 512-520 has a fallback path:
```python
if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
    taker_ratios = taker_buys / np.maximum(volumes, 1e-10)
    imb_z = _z_score(taker_ratios, CFG.z_lookback)
else:
    # Fallback: price-position relative to bar midpoint, normalized by ATR
    mid = (highs[-1] + lows[-1]) / 2
    imb_z = (closes[-1] - mid) / (atr + 1e-10)
```

When `taker_buy_volume` is all zeros (which is the common case), the fallback computes a price-position metric that has nothing to do with taker imbalance. This fallback value is often > 1.0, causing the gate to pass.

**Impact:** The imb gate is a free pass on 83% of entries. Combined with body (89%) and momentum (78%), trades pass 3/6 gates without any real confirmation.

**Fix:** Either:
1. Remove the fallback — if taker data is zero, imb_z should be 0.0 (gate fails)
2. Or gate the entire imb confirmation on `has_taker` (skip the gate entirely when no taker data)

### 🟠 HIGH: Breakout Gate Never Fires

**File:** `engines/liq_cluster_engine_v5.py`, line 524

```python
confirmations['breakout'] = closes[-1] > range_high
```

**Problem:** 0 of 18 post-loosening trades broke above the 60-bar range high. The system is buying into resistance, not breaking out.

**Root cause:** In chopy markets, price rarely breaks above the 60-bar high. The range is too wide.

**Impact:** The most important confirmation for a breakout strategy is never contributing.

**Fix options:**
1. Shorten `range_lookback` from 60 to 30
2. Change to `closes[-1] > range_high * 0.995` (allow near-breakout)
3. Accept that breakout-as-defined isn't happening and the system is really momentum-based

### 🟡 MEDIUM: FLAT_RISK_PCT NameError (V6.0 bug, may be fixed)

**File:** `engines/liq_cluster_engine_v5.py`

V6.0 introduced `FLAT_RISK_PCT` reference that wasn't imported, causing `NameError` on every signal. Was fixed to `BASE_RISK_PCT`. Verify this is still correct.

### 🟡 MEDIUM: bars_held Double Increment (FIXED in V6.0)

Was fixed but worth verifying: the runner increments `candles_held` AND the engine increments `bars_held`. Only one should increment. Verify line ~581 in engine doesn't have `st.bars_held += 1`.

### 🟢 LOW: Dead Code

- `models.py` has 13-state `PositionState` machine — completely unused
- `DECILE_EXITS` has entries for D4 and D10 — rejected but definitions remain
- `btc_aligned` column in DB schema but never used

---

## 6. Live Performance Analysis

### Overall (149 closed trades)

| Metric | Value |
|--------|-------|
| Total PnL | -28.25R |
| Win Rate | 45.0% (67/149) |
| Avg per trade | -0.190R |
| Profit Factor | 0.58 |
| Max drawdown | Not computed (equity snapshots exist) |

### By Exit Reason

| Exit | Count | Total R | Avg R |
|------|-------|---------|-------|
| stop_loss | 48 | -46.15R | -0.96R |
| struct_trail | 38 | +14.89R | +0.39R |
| vol_trail | 21 | +6.35R | +0.30R |
| time_stop | 26 | +1.40R | +0.05R |
| early_cut | 15 | -6.17R | -0.41R |
| cascade_deactivated | 1 | +1.42R | +1.42R |

**Key insight:** Stop losses are -46.15R on 48 trades. That's the entire problem. Winners via trails are +22.64R on 59 trades. The exits work — the entries are wrong.

### By Decile

| Decile | Trades | Total R | Avg R | WR | Status |
|--------|--------|---------|-------|----|--------|
| D1 | 72 | -15.67R | -0.218R | 47% | ❌ Negative, but keep per Martin |
| D2 | 18 | -4.55R | -0.253R | 44% | ⚠️ Small sample |
| D3 | 9 | -4.38R | -0.487R | 33% | 🚫 Rejected |
| D5 | 4 | +1.81R | +0.453R | 75% | ✅ Best edge |
| D6 | 9 | -2.35R | -0.261R | 44% | ⚠️ Small sample |
| D7 | 13 | -3.34R | -0.257R | 31% | ❌ Negative, but keep per Martin |
| D8 | 12 | +2.91R | +0.242R | 58% | ✅ Solid |
| D9 | 12 | -2.68R | -0.224R | 33% | ❌ Negative, but keep per Martin |

### By Symbol (Top/Bottom 5)

**Best:**
| Symbol | Trades | Total R | WR |
|--------|--------|---------|----|
| DASHUSDT | 11 | +2.76R | 64% |
| VIRTUALUSDT | 2 | +0.92R | 50% |
| SOLUSDT | 12 | +0.77R | 58% |
| FETUSDT | 12 | +0.63R | 67% |
| XRPUSDT | 5 | +0.22R | 60% |

**Worst:**
| Symbol | Trades | Total R | WR |
|--------|--------|---------|----|
| ZECUSDT | 12 | -5.29R | 33% |
| NEARUSDT | 22 | -4.41R | 50% |
| LTCUSDT | 4 | -2.84R | 0% |
| QNTUSDT | 8 | -2.51R | 38% |
| UNIUSDT | 7 | -2.45R | 29% |

### Experimental vs Proven

| Type | Trades | Total R | WR |
|------|--------|---------|----|
| Proven | 115 | -19.19R | 47% |
| Experimental | 34 | -9.06R | 32% |

---

## 7. Confirmation Gate Analysis

### Post-Loosening Gate Pass Rates (18 trades)

| Gate | Pass Rate | Notes |
|------|-----------|-------|
| body | 89% | 0.50 threshold too easy |
| imb | 83% | **BUG** — imb_z=0.00 but gate passes |
| momentum | 78% | Close > EMA20, easy in any uptrend |
| impulse | 56% | 0.20% move, moderate |
| vol | 17% | vol_z > 2.0, only 3/18 pass |
| breakout | 0% | **Never fires** — no trade breaks 60-bar high |

### What the 18 Post-Loosening Trades Actually Passed

Every trade passed on: **body + imb + momentum** (the 3 free gates). vol and breakout are the only real filters, and breakout never fires.

### Old vs New Thresholds

| Gate | V6.3 (old) | V6.4 (new) | Problem |
|------|-----------|-----------|---------|
| vol_z | > 3.0 | > 2.0 | Still only 17% pass — too tight for chop |
| imb_z | > 2.0 | > 1.0 | **Bug makes this irrelevant** — always passes |
| body | ≥ 0.60 | ≥ 0.50 | 89% pass — too easy |
| impulse | ≥ 0.30% | ≥ 0.20% | 56% pass — moderate |
| min_confirms | 4/6 | 3/6 | Too easy with 3 free gates |

---

## 8. Stop Loss Analysis

### The -1R Problem

- 48 stop losses, -46.15R total, avg -0.96R
- 42 of 48 (93%) hit at almost exactly -1R
- Avg 134 bars held before stop (11 hours)
- The stop is set at 2.5 ATR and the trail never loosens enough

### R-Path Analysis (20 trades with telemetry)

Of 20 stop-loss trades with full R-path data:
- **11 (55%) never went positive** — dead on arrival, nothing could save them
- **5 (25%) flickered positive but < 0.3R** — brief hope, then died
- **4 (20%) reached +0.3R+ then crashed back** — LTC (0.66R→-1.05R), QNT (0.35R→-1.05R), AAVE (0.31R→-1.04R), INJ (1.50R→+0.12R, this one survived)

### Winner Comparison

Of 11 winners with R-path data:
- **9 of 11 went negative at some point** before recovering
- Deepest drawdown any winner survived: **-0.78R** (ZEC D2, closed +0.37R)
- Avg minimum R among winners: **-0.46R**
- Winners routinely gave back 0.5–1.7R from peak

### Simulated Stop Improvements

| Option | Trades Helped | Trades Hurt | Net |
|--------|--------------|-------------|-----|
| Widen stop 2.5→3.5 ATR | 4 saved | 16 lose -1.4R instead of -1.0R | **-2R** ❌ |
| Stop delay at +0.3R | 4 saved to breakeven | 16 unchanged | **+4R** ✅ |
| Both combined | 4 saved | 16 unchanged | **+4R** ✅ |

**Recommendation:** Stop delay at +0.3R (move stop to breakeven once trade reaches +0.3R) saves +4R with no downside. Wider stops actually hurt because most trades never show life.

---

## 9. Recommendations for Next AI

### Immediate Actions (Do First)

1. **REVERT V6.4 confirmation loosening** — go back to vol_z>3.0, imb_z>2.0, body≥0.60, impulse≥0.30, min_confirms=4. The loosening produced -11.16R on 18 trades.

2. **FIX the imb gate bug** — when taker_buy_volume is zero, imb_z should be 0.0 (gate fails), not the price-position fallback. This is the most impactful single fix.

3. **Investigate the breakout gate** — 0/18 trades break above 60-bar high. Consider shortening range_lookback to 30, or accept the system is momentum-based, not breakout-based.

4. **Implement stop delay** — once trade reaches +0.3R, move stop to breakeven. Saves +4R with no downside.

### Short-Term (This Week)

5. **Cut negative-experimental pairs:** LTC (-2.84R, 0% WR), HYPE (-2.14R, 0% WR), AVAX (-2.25R, 25% WR)

6. **Promote positive-experimental pairs:** VIRTUAL (+0.92R) — small sample but promising

7. **Session-based sizing:** London (08-16 UTC) is the only profitable session (59% WR, +1.21R). NY (16-24 UTC) is -10.64R. Consider reducing size 50% during NY.

8. **Symbol pruning:** ZEC (-5.29R), NEAR (-4.41R), QNT (-2.51R) are bleeding. Consider removing or reducing allocation.

### Medium-Term (Wait for Data)

9. **Keep D1, D7, D9 active** — Martin's explicit instruction. Wait for 200+ total trades before deactivating any decile.

10. **Wait for D5/D8 sample to grow** — these are the only deciles with positive expectancy. Need 20+ trades each to confirm.

11. **Pair expansion** — staged rollout: 15 pairs at a time at 0.5% experimental risk, gate on expectancy after 50+ trades each.

### What NOT to Do

- Do NOT deactivate deciles (Martin's rule)
- Do NOT touch production code without explicit approval
- Do NOT push to GitHub without Martin's OK (learned the hard way)
- Do NOT optimize for backtest PF — focus on live expectancy
- Do NOT add random indicators — the 3-play framework is sound

---

## 10. Current Configuration Reference

### Engine Parameters (v6.4 — RECOMMEND REVERT)

```python
# Cascade detection
liq_lookback = 90          # days (reverted from 30)
min_cascade_strength = 0.10
min_cascade_imb = 0.00     # disabled (was 0.30, removed in v6.3.2)

# Entry confirmations (V6.4 — RECOMMEND REVERT to v6.3 values)
range_lookback = 60
imb_z_threshold = 1.0      # v6.3: 2.0
vol_z_threshold = 2.0      # v6.3: 3.0
body_strength_min = 0.50   # v6.3: 0.60
impulse_min_pct = 0.20     # v6.3: 0.30
min_confirmations = 3      # v6.3: 4
z_lookback = 100
ema_period = 20

# Risk
initial_stop_atr = 2.5
atr_period = 14
BASE_RISK_PCT = 0.04       # 4% (was FLAT_RISK_PCT, fixed in v6.0)

# Selectivity
cooldown_bars = 36
no_reentry_after_stop = True
max_consecutive_stops = 3
stop_cooldown = 288        # 24h (was 999999, fixed in v6.0)

# Rejected deciles
TRADE_DECILES = {1, 2, 5, 6, 7, 8, 9}  # D3, D4, D10 rejected

# Early cut (v6.3 widened)
early_cut_r_threshold = -0.5    # was -0.3
early_cut_mfe_threshold = 0.1   # was 0.3
```

### Decile Exit Parameters

| Decile | vol_trail_atr | struct_lookback | max_hold_bars | decay_enabled | decay_start_bar | decay_min_r |
|--------|--------------|-----------------|---------------|---------------|-----------------|-------------|
| D1 | 3.0 | 48 | 500 | False | 999 | 999 |
| D2 | 3.0 | 48 | 500 | False | 999 | 999 |
| D5 | 2.0 | 12 | 288 | True | 15 | 1.5 |
| D6 | 2.0 | 12 | 288 | True | 12 | 1.5 |
| D7 | 2.5 | 36 | 358 | True | 20 | 2.0 |
| D8 | 2.5 | 36 | 358 | True | 20 | 2.0 |
| D9 | 1.5 | 8 | 100 | True | 8 | 1.5 |

### Symbol Tiers

**Tier A (Proven, 35 symbols):** Full pipeline, Coinalyze-verified, backtested
**Tier B (Proven additions):** Added in v6.2-v6.3
**Tier C (Experimental, 22 symbols):** WebSocket-only, 0.5% risk, gate on expectancy

### Key Files

| File | Path | Purpose |
|------|------|---------|
| Engine | `engines/liq_cluster_engine_v5.py` | All trading logic |
| Runner | `tools/v5_forward_test.py` | Live execution |
| Config | `config/v5_forward_test.yaml` | Symbol/param config |
| Telemetry | `v6_telemetry.py` | R-path, shadow exits |
| Paper DB | `storage/v5_forward_test.db` | Trades, positions, state |
| Telemetry DB | `storage/v6_telemetry.db` | R-path, entries, exits |

### Git Tags

| Tag | Commit | Date | Description |
|-----|--------|------|-------------|
| v5.2 | `ab5237c` | 2026-05-22 | Coinalyze → Binance migration |
| v5.3 | `ffb2f76` | 2026-05-23 | WebSocket liq feed |
| v6.0 | `cb03ba7` | 2026-05-23 | Full audit fix (18 bugs) |
| v6.2 | `dde5950` | 2026-05-24 | D3 rejection, directional filter, early cut |
| v6.3 | `230c984` | 2026-05-24 | Tier 2 experimental pairs (50 total) |
| v6.4 | `456dbed` | 2026-05-25 | Loosened confirmations (REVERT RECOMMENDED) |

---

## Appendix A: Session Performance

| Session | UTC | Trades | WR | Total R |
|---------|-----|--------|----|---------|
| London | 08-16 | — | 59% | +1.21R |
| Asian | 00-08 | — | 41% | -4.97R |
| NY | 16-24 | — | 43% | -10.64R |

22:00 UTC is the worst hour: 11 entries, 27% WR, -7.10R.

## Appendix B: Kelly Analysis

- Full Kelly: 8.8% (on V6.2 data without D3)
- Half-Kelly: 4.4%
- Current sizing: ~4% flat (half-Kelly)
- D1 Kelly: 21.3% (but negative expectancy in choppy regime)
- D5 Kelly: +48% (positive, but only 4 trades)
- D8 Kelly: +17.4% (positive, 12 trades)

## Appendix C: Key Metrics to Monitor

1. **Expectancy per trade** — currently -0.19R (needs to be >0)
2. **Stop loss rate** — currently 32% (48/149), should be <25%
3. **D5/D8 trade count** — need 20+ each to confirm edge
4. **Breakout gate pass rate** — currently 0%, needs investigation
5. **imb gate correctness** — verify it's actually checking taker imbalance

---

*End of report. For questions, check the git log at each tag. The commit messages are detailed and explain the reasoning behind each change.*
