# FABLE MONDAY PLAN — OPERATIONAL SUMMARY
**From: 5.6 SOL analysis** | **Date: 2026-07-18** | **For: Monday 2026-07-20 go-live**

---

## 🎯 MONDAY LIVE CONFIG

| Parameter | Value | Change |
|-----------|-------|--------|
| **Asia session** | `asia_pump_short_4h` — SHORT, 4h time-exit, neg_imb≥0.5 | ✅ ON (Mon–Fri) |
| **NY session** | **BLACKOUT** — no NY trades Monday | ✅ Enforced |
| **London/Late** | Disabled live | ✅ Shadow only |
| **Stop** | **10 ATR** (unchanged) | ❌ No narrowing |
| **Risk per trade** | **4% equity** (not 8%) | ⬇️ **HALVED** |
| **Max concurrent** | 3 | Unchanged |
| **Max per symbol** | 1 | Unchanged |
| **Max per 15m cluster** | 3 | Unchanged |
| **Scaling into winners** | **NO** | ❌ Disabled |
| **Partial profit** | Research only: +2 ATR at 1h → take 50% | 👁️ Log only |

---

## 📊 WHY 4% RISK? — Semi-Markov Regime Age

| Neutral Age | Asia net/trade | NY net/trade | Action |
|-------------|----------------|--------------|--------|
| **<16h** | +3.76 ATR | +3.79 ATR | Full risk (8%) |
| **16–32h** | +4.63 ATR | +0.97 ATR | Full risk |
| **32–64h** | −0.44 ATR | +3.36 ATR | Reduce |
| **>64h** | **+0.23 ATR** | **−0.95 ATR** | **4% risk** ⚠️ |

**Monday Asia opens at neutral age >64h** (flipped Fri 04:06 UTC → ~60h by Mon 00:00, >64h by Mon Asia open).
→ **Asia edge degrades to +0.23 ATR/trade** → **halve risk to 4%**.

---

## 🔬 KEY TEST RESULTS (Jul 8–17)

### Stop Width
- 6 ATR vs 10 ATR Asia neutral: **tied after risk normalization**
- 8 ATR NY neutral: slightly better but **weak**
- Winner MAE p95: Asia 5.17 ATR, NY 3.45 ATR → **stops <6 ATR kill valid winners**
- **Keep 10 ATR** until 4/6/8 ATR shadow variants capture exact stop timing

### Scaling In
| Session | Test | Result |
|---------|------|--------|
| Asia | Scale 0.5× after +1h green | **−4.87 ATR** total PnL |
| Asia | Winners after +1h green | Lost avg **−0.39 ATR** subsequently |
| NY | Scale ≥+0.5 ATR at 1h | +0.86 ATR avg (n=12, CI crosses zero) |
| NY | Scale ≥+2 ATR at 1h | 4/4 positive (n=4, too small) |
| Asia | **Partial profit 50% at +2 ATR / 1h** | **+3.02 ATR improvement** ✅ |

→ **Do NOT scale in Monday.** Partial profit at +2 ATR/1h is a **research candidate** — log only.

### Session Carryover
- Asia → London: corr +0.08 (p=0.84)
- London → NY: corr −0.17 (p=0.71)
- Asia → NY: corr −0.35 (p=0.39)
→ **No signal. Do not use prior-session PnL as gate.**

### HMM (6-state, trained through Jun 30, forward-tested Jul)
- NMI vs ADX: 0.144 (captures different info)
- Asia H2+H5 filter: **+66.9 ATR** cap-adjusted net
- `fade_6h_late` in H4/H5: **+79.8 ATR** (strongest new candidate)
- **H5 confidence only 54%** → **Do NOT use live Monday. Log HMM state/confidence for another week.**

### MDP
- Specified model degenerate (trading actions don't affect BTC transitions)
- With variance penalty: block/reduce mature neutral, block bull except thin 16–32h pocket
- Needs equity drawdown, open risk, cluster load, recent losses in state → **not ready**

---

## 📅 MONDAY CHECKLIST

| Item | Status |
|------|--------|
| Asia `asia_pump_short_4h` armed | ✅ |
| Risk = **4%** (not 8%) | ⬇️ **SET** |
| Stop = 10 ATR | ✅ |
| No scaling in | ✅ |
| NY Monday blackout | ✅ |
| London/Late disabled live | ✅ |
| Partial profit at +2 ATR/1h | 👁️ **LOG ONLY** |
| HMM state logging | 👁️ **ENABLE** |
| 4/6/8 ATR stop variants | 👁️ **SHADOW** |
| NY scale-in checkpoints | 👁️ **SHADOW** |
| `fade_6h_late` × H4/H5 | 👁️ **SHADOW** |

---

## 🎯 RESEARCH PIPELINE (post-Monday)
1. **Asia H2/H5 filter** — HMM state filter for Asia
2. **Asia partial profit** — 50% at +2 ATR after 1h
3. **NY scale-in checkpoints** — ≥+0.5/+1/+2 ATR at 1h
4. **`fade_6h_late` × H4/H5** — strongest candidate (+79.8 ATR)
5. **Actual 4/6/8 ATR stop variants** — shadow only until capacity/release logic proven

---

## ⚠️ NO LIVE CONFIG CHANGED DURING TESTS
All above is **research-only** until validated. Monday runs v1.1.0 with **risk=4%** as the only change.