# Bitana Markov & Regime Analysis — All Results
**Generated:** 2026-07-17  
**Data:** BTC 4h bars Jan 2024 – Jul 2026 (Jan–Jun 2026 out-of-sample)  
**Shadow trades:** Jul 1–17 2026 (500+ trades/day, 17 enriched fields)

---

## 1. Regime Transition Matrix (ADX/EMA)
**States:** bull, bear, neutral (ADX>25 + price vs EMA200)  
**Period:** Jan 2024 – Jul 2026 (2.5 years, ~5,500 4h bars)

| From → To | bull    | bear    | neutral |
|-----------|---------|---------|---------|
| bull      | 84.1%   | 2.0%    | 13.9%   |
| bear      | 1.6%    | 84.8%   | 13.6%   |
| neutral   | 7.7%    | 6.6%    | 85.7%   |

**Stationary distribution:** neutral 50.0%, bull 25.7%, bear 24.3%  
**Median duration:** neutral 20h, bull 16h, bear 16h  
**Reversion prob:** bull→neutral 75.0%, bear→neutral 72.4%  
**Implication:** Neutral is the hub state; bull/bear are short excursions.

---

## 2. Semi-Markov (Regime-Age) Edge Analysis
**Key finding:** Regime age flips edge sign for Asia short.

| Strategy | Young (<16h) | Mature (16-32h) | Old (32-64h) | Very Old (>64h) |
|----------|-------------|-----------------|--------------|-----------------|
| Asia plain 4h (short) | –0.40 ATR | +5.36 ATR | +0.87 ATR | +1.26 ATR |
| NY 4h (long) | –0.28 ATR | +0.92 ATR | +0.11 ATR | –0.05 ATR |
| London fade | –0.62 ATR | +0.41 ATR | –0.12 ATR | –0.18 ATR |

**Current neutral age:** 0h (flipped ~04:06 UTC Jul 17)  
**P(neutral lasts 16h+):** 66.7%  
**P(neutral lasts 32h+):** 40.7%

---

## 3. Session Carryover (Asia → London → NY)
**Period:** Jul 1–17 2026, weekday sessions only

| Prev Session | Next Session | Regime  | N  | Avg PnL (ATR) | WR  | Notes |
|--------------|--------------|---------|----|---------------|-----|-------|
| Asia +10+    | London       | bull    | 3  | –2.1          | 33% |       |
| Asia +10+    | London       | neutral | 4  | +0.8          | 50% |       |
| Asia –10+    | London       | neutral | 3  | –1.2          | 33% |       |
| London +10+  | NY           | bull    | 2  | +1.5          | 50% |       |
| London +10+  | NY           | neutral | 3  | +0.3          | 33% |       |
| London –10+  | NY           | neutral | 4  | –0.7          | 25% |       |

**Implication:** Weak carryover; each session should be traded independently with regime gate.

---

## 4. MDP Gate Optimization
**States:** (regime, age_bin) × 3 actions = {block, allow, reduce}  
**Reward:** Expected PnL from shadow trades Jul 1–17  
**Discount:** γ = 0.95

**Optimal Policy (value iteration):**

| State | Age Bin | Action | Value | Rationale |
|-------|---------|--------|-------|-----------|
| bull  | <16h    | BLOCK  | –0.12 | Asia short loses, NY mixed |
| bull  | 16-32h  | ALLOW  | +0.89 | Asia strong, London fade works |
| bull  | >32h    | REDUCE | +0.31 | Edge decaying |
| neutral | <16h  | ALLOW  | +0.73 | Baseline edge |
| neutral | 16-32h| ALLOW  | +0.78 | Stable |
| neutral | >32h  | ALLOW  | +0.65 | Still positive |
| bear  | <16h    | ALLOW  | +1.24 | All strategies strong |
| bear  | 16-32h  | ALLOW  | +1.18 | Peak edge |
| bear  | >32h    | ALLOW  | +0.92 | Still strong |

**Current state:** neutral, age=0 → **ALLOW** (value +0.73)

---

## 5. HMM Latent State Detection (6 States)
**Features:** 4h log_ret, vol, vol_ratio, range_pct, trend  
**Best BIC:** 6 states (50,442)  
**Training:** Jan 2024 – Jul 2026

### State Characteristics

| State | Freq | Avg Ret | Vol | Trend | Interpretation |
|-------|------|---------|-----|-------|----------------|
| 0     | 6.5% | –0.0099 | 0.016 | –0.005 | Crash / high-vol down |
| 1     | 11.5%| –0.0017 | 0.042 | –0.008 | High-vol chop / bearish |
| 2     | 22.2%| +0.0025 | 0.018 | +0.020 | **Bull trend** |
| 3     | 23.5%| +0.0007 | 0.010 | +0.002 | **Low-vol neutral / drift up** |
| 4     | 14.0%| +0.0017 | 0.034 | +0.000 | High-vol expansion / breakout |
| 5     | 22.3%| –0.0003 | 0.020 | –0.014 | **Bear trend / grind down** |

### Transition Matrix

| From | →0 | →1 | →2 | →3 | →4 | →5 |
|------|----|----|----|----|----|----|
| 0    | 24%| 5% | 7% | 28%| 1% | 34%|
| 1    | 0% | 61%| 2% | 0% | 35%| 1% |
| 2    | 9% | 4% | 83%| 5% | 0% | 0% |
| 3    | 15%| 1% | 6% | 79%| 0% | 0% |
| 4    | 0% | 14%| 9% | 0% | 70%| 7% |
| 5    | 0% | 6% | 2% | 8% | 0% | 84% |

**Current state:** 5 (bear trend) — **but ADX says neutral**  
**Agreement with ADX regime:**

| HMM State | bear | bull | neutral |
|-----------|------|------|---------|
| 0         | 17%  | 24%  | 59%     |
| 1         | 42%  | 27%  | 31%     |
| 2         | 12%  | 44%  | 44%     |
| 3         | 15%  | 23%  | 63%     |
| 4         | 42%  | 23%  | 35%     |
| 5         | 35%  | 16%  | 49%     |

States 2 & 3 map to bull/neutral; States 1, 4, 5 map to bear/neutral. **Low agreement** — HMM captures vol/regime mix, ADX captures trend strength.

### Strategy Edge by HMM State (Jul 1–17 shadow)

**asia_pump_short_4h (Asia short 4h):**
- State 0 (crash): +3.04 ATR (75% WR, n=16) ← **BEST**
- State 5 (bear trend): +1.16 ATR (72% WR, n=39) ← **STRONG**
- State 2 (bull trend): +0.73 ATR (50% WR, n=26)
- State 4 (breakout): +0.59 ATR (67% WR, n=6)
- State 3 (neutral): –0.74 ATR (20% WR, n=5)

**burst_follow (London long):**
- State 5 (bear): +0.20 ATR (54% WR, n=642) ← **BEST**
- State 3 (neutral): +0.09 ATR (53% WR, n=208)
- State 4 (breakout): +0.05 ATR (51% WR, n=343)
- State 2 (bull): +0.03 ATR (53% WR, n=889)
- State 0 (crash): –0.16 ATR (52% WR, n=237)

**fade_6h_late (London fade 6h):**
- State 2 (bull): +4.87 ATR (85% WR, n=13) ← **BEST**
- State 0 (crash): +3.10 ATR (83% WR, n=41) ← **STRONG**
- State 4 (breakout): –3.83 ATR (0% WR, n=5) ← **TOXIC**
- State 5 (bear): –3.95 ATR (14% WR, n=7) ← **TOXIC**

**london_burst_fade (London short):**
- State 4 (breakout): +0.58 ATR (65% WR, n=75) ← **BEST**
- State 5 (bear): ~0 ATR (50% WR, n=131)
- State 0 (crash): –0.52 ATR (37% WR, n=27)

**ny_flush_buy_4h_open_tsl (NY long TSL):**
- State 3 (neutral): +1.49 ATR (100% WR, n=8) ← small sample
- State 4 (breakout): +0.77 ATR (57% WR, n=7)
- State 2 (bull): +0.33 ATR (65% WR, n=26)
- State 5 (bear): –0.59 ATR (61% WR, n=36)

---

## 6. Integrated Decision Rules

### Current State (Jul 17 04:06 UTC): NEUTRAL, age=0
- **MDP:** ALLOW (value +0.73)
- **Semi-Markov:** P(neutral lasts 16h+) = 66.7%
- **HMM:** State 5 (bear trend) but ADX says neutral — **trust ADX for gating, HMM for sizing**

### Live Config v1.1.0 (deployed):
- **Asia:** `asia_pump_short_4h` plain 4h exit, neutral+bear gate
- **NY:** `ny_flush_buy_4h_open` hours 14–17 UTC Tue–Fri, neutral+bear gate
- **London:** shadow only (burst_follow + fade_6h_late)

### Jul 18 Gate Decision Rule:
- Need **≥30 live-config bull trades** with **≥+0.5 ATR/trade** → widen gate to include bull
- Current bull sample: ~12 closes over 3 sessions (insufficient)

### Sizing Recommendation:
| Regime | Base Size | HMM Override | Notes |
|--------|-----------|--------------|-------|
| Neutral | 25–50% | State 3 → 50%, State 0 → 75% | Daily review |
| Bear | 50–75% | State 5 → 75%, State 1 → 50% | Peak edge |
| Bull | 0% (blocked) | State 2 → 25% (Asia only) | Requires Jul 18 validation |

---

## 7. Key Takeaways

1. **Neutral is the dominant regime (50% time)** — fund at 25–50% with daily review
2. **Regime age matters more than regime label** — young bull kills Asia short, mature bull prints
3. **fade_6h_late is the only strategy with cross-regime edge** — works in crash (State 0) AND bull (State 2), dies in bear (State 5) and breakout (State 4)
4. **HMM State 0 (crash) is alpha-rich** — Asia short +3.04, fade_6h_late +3.10, but rare (6.5%)
5. **Current neutral age = 0** — enter at 25–50% size, reassess daily
6. **Jul 18 decision:** ≥30 bull trades +0.5 ATR → widen gate; else stay neutral+bear only