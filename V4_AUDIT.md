# V4 Liq-Cluster Engine — Complete Audit Document
## May 17-19, 2026 Development Session

---

## 1. AGGRESSION SCORE SYSTEM

### 10 Components (computed at entry from 5m candles)

| # | Component | Weight | What it measures |
|---|-----------|--------|------------------|
| 1 | taker_imb_z | 0.10 | Taker buy/sell imbalance z-score vs recent history |
| 2 | delta_persistence | 0.10 | Consecutive bars with same-sign delta |
| 3 | oi_acceleration | 0.08 | Volume acceleration (OI proxy) — short-term vs long-term |
| 4 | range_expansion_pctile | 0.15 | Current range vs historical percentile |
| 5 | volume_concentration | 0.10 | Last 3 bars / last 10 bars volume ratio |
| 6 | clv | 0.07 | Close position within bar range |
| 7 | wick_rejection | 0.08 | Lower wick (bulls) or upper wick (bears) / total range |
| 8 | spread_expansion | 0.10 | Current range vs average range |
| 9 | velocity | 0.07 | Price change normalized by ATR × √lookback |
| 10 | cascade_intensity | 0.15 | Average of volume z-score and range z-score |

**Score range:** 0-100 (composite normalized via `(score + 2) / 4 * 100`)

**Boundaries (from 386-trade baseline):**
- D1: ≤68.2, D2: ≤71.4, D3: ≤73.6, D4: ≤75.2, D5: ≤77.2
- D6: ≤78.8, D7: ≤80.8, D8: ≤82.5, D9: ≤84.1, D10: >84.1

---

## 2. DECILE ARCHETYPES & EXIT PARAMETERS

### Format: `(trail_atr_mult, decay_bars_min, decay_mfe_min, decay_pullback_atr, decay_consec_red, max_hold_bars, struct_lookback)`

| Decile | Archetype | Trail | Decay | Max Hold | Struct LB | Trades | WR | Total R | PF |
|--------|-----------|-------|-------|----------|-----------|--------|-----|---------|-----|
| D1 | Slow grinders | 3.0x | OFF | 500 | 48 | 38 | 58% | +0.52 | 1.03 |
| D2 | Grinders | 3.0x | OFF | 500 | 48 | 39 | 56% | +10.93 | 1.64 |
| D3 | Clean impulses | 2.0x | OFF | 288 | 24 | 37 | 57% | +8.71 | 1.54 |
| D4 | Standard | 2.0x | 15b | 288 | 12 | 39 | 59% | +4.20 | 1.26 |
| D5 | Standard+ | 2.0x | 15b | 288 | 12 | 35 | 63% | +18.27 | 2.41 |
| D6 | Moderate | 2.0x | 12b | 288 | 12 | 37 | 51% | +4.70 | 1.26 |
| D7 | Structural squeezes | 2.5x | 20b | 358 | 36 | 36 | 56% | +3.56 | 1.22 |
| D8 | Structural squeezes+ | 2.5x | 20b | 358 | 36 | 36 | 61% | +15.29 | 2.09 |
| D9 | Climax events | 1.5x | 8b | 100 | 8 | 39 | 64% | +38.52 | 3.75 |
| D10 | Climax traps | 1.5x | 8b | 100 | 8 | 36 | 58% | +20.57 | 2.37 |

**Overall: 372 trades, 58.3% WR, +125.26R, PF 1.81**

### Decile Behavior Details

**D1-D2 (Slow Grinders):** Low aggression score = non-explosive candle pattern. These are slow moves that need wide trails (3.0x ATR) and NO decay. They grind higher over time. 48-bar struct_lookback prevents premature exits. D1 backtest capture was only 41% — leaving money on table.

**D3 (Clean Impulses):** Sweet spot. Clean directional moves with good follow-through. No decay needed — let them run. 24-bar struct_lookback. Best risk/reward ratio.

**D4-D5 (Standard):** Moderate aggression. Relaxed decay (15 bars minimum) prevents shaking out normal pullbacks. Standard 2.0x trail.

**D6 (Moderate):** Middle of the curve. Standard parameters. Lowest WR (51%) but still profitable.

**D7-D8 (Structural Squeezes):** High aggression = real short covering. Wide trails (2.5x ATR), suppressed decay (20 bars), long hold (358 bars). 36-bar struct_lookback (up from 18 — fixed after NEAR D8 failure). These are the big winners when they work.

**D9 (Climax Events):** Highest PF (3.75). Tight trails (1.5x), fast decay (8 bars), short hold (100 bars). These are explosive moves that reverse quickly. Best per-trade expectancy (+0.988R avg).

**D10 (Climax Traps):** Similar to D9 but slightly lower PF (2.37). These are blow-off moves — enter late, exit fast. 50% stop rate. The tight parameters are essential.

---

## 3. FILTERS & GATES

### 3.1 Cascade Regime Filter
- **Parameter:** `min_regime_ratio=0.30`, `regime_lookback_days=10`
- **Logic:** If recent 10-day mean liq < 30% of full lookback mean, suppress cascades
- **Purpose:** Blocks entries during sustained low-liq periods (e.g., May regime)
- **Backtest impact:** 0 trades blocked (problematic regime outside backtest window)

### 3.2 Minimum Cascade Strength Gate
- **Parameter:** `min_cascade_strength=0.10`
- **Logic:** Block entry when cascade strength (latest_liq / p90) < 0.10
- **Purpose:** Blocks noise entries on weak cascades (e.g., NMRUSDT at 0.01)
- **Backtest impact:** Blocks 4 trades (all losers, avg -0.56R). Improves total R by +2.25R

### 3.3 Trend Filter — DISABLED
- **Parameter:** `require_trend_filter=False`
- **Logic:** Would block long entries when daily close < EMA-20
- **Why disabled:** Backtest shows it blocks +16.4R of winners. Our edge IS counter-trend entries during bounces. RSI filters also tested — all hurt performance.

### 3.4 Per-Symbol Loss Limit
- **Parameter:** `max_consecutive_stops=3`
- **Logic:** After 3 consecutive stop_losses on a symbol, pause entries until a new cascade activates
- **Purpose:** Prevents bleeding on pairs that changed regime (e.g., NEAR May 2026)
- **Reset condition:** New cascade activation (cascade_active transitions False→True)

### 3.5 Existing Filters (from V3, unchanged)
- `ret5d_min=-5.0` — Block if 5-day return < -5%
- `require_short_squeeze=True` — Block if imb >= 0 (no short squeeze)
- `cooldown_bars=36` — 36-bar cooldown after any exit
- `no_reentry_after_stop=True` — No re-entry after stop_loss in same cascade window
- `min_confirmations=4` — Need 4/6 entry confirmations

---

## 4. ENTRY CONFIRMATIONS (6 layers)

1. **Breakout** — Close above range high
2. **Imbalance** — Taker buy volume > threshold (z-score > 2.0)
3. **Volume** — Volume z-score > 3.0
4. **Body** — Candle body > 60% of range
5. **Impulse** — Move > 0.3% from open
6. **Momentum** — Close > EMA-20

Minimum 4/6 required. All 6 cascades must be active (daily liq context).

---

## 5. EXIT MECHANISMS (in priority order)

1. **Stop Loss** — Fixed at entry_price - 2.5x ATR
2. **Partial TP** — 50% at 2.5R (wick trigger)
3. **Volatility Trail** — Highest price since entry - decile-specific ATR multiple
4. **Structure Trail** — Decile-specific swing low lookback (8-48 bars)
5. **Expansion Decay** — After N bars, if MFE > X and pullback > Y and Z consecutive red bars
6. **Time Stop** — Decile-specific max hold (100-500 bars)

---

## 6. BUGS FOUND (via NEARUSDT live trading)

### Bug #1: hold_candles not reset on new entry after recovery
- **Symptom:** DB shows 236 candles held, actual was 13
- **Root cause:** Counter carried over from previous position on restart
- **Severity:** MEDIUM — affects metrics, not PnL
- **Status:** Known, not yet fixed in forward tester

### Bug #2: Aggression score recomputes differently on recovery
- **Symptom:** Entry alert said agg=68, exit alert said agg=53
- **Root cause:** Candle buffer shifts between entry and recovery restart
- **Severity:** MEDIUM — could use wrong exit params on recovered positions
- **Status:** Known, not yet fixed

### Bug #3: exit_time stored incorrectly in DB
- **Symptom:** DB says exit at 12:59 (50 min after entry), log says 13:03:42
- **Severity:** LOW — cosmetic

### Bug #4: vol_trail never activates when ATR > MFE
- **Symptom:** D8 NEAR trade: ATR=0.0072, 2.5xATR=1.07R, MFE=0.66R. Vol trail was below entry entire trade
- **Root cause:** struct_trail (18-bar) was the only active exit, too tight for choppy market
- **Severity:** HIGH — directly loses money
- **Status:** FIXED — D7-D8 struct_lookback increased from 18 to 36 bars

### Bug #5: D1 params (wide trail, no decay) in downtrends
- **Symptom:** D1 slow-grinder params on counter-trend long = slow bleed to stop
- **Root cause:** D1 params assume ranging market, not trending downtrend
- **Severity:** MEDIUM — wrong param set for regime
- **Status:** Partially addressed by per-symbol loss limit

---

## 7. REGIME ANALYSIS: NEARUSDT March vs May

### March (Backtest Period)
- Price: 1.149 → 1.188 (+3.4%, gradual)
- Daily vol: 4.59%
- Liq: mean 164K, max 649K (high, frequent cascades)
- RSI: 40-55 (neutral)
- **Result: 14 trades, +3.40R, 71% WR**
- **Regime: RANGING WITH BOUNCES — edge works**

### May (Live Period)
- Price: 1.288 → 1.488 (+15.5%, volatile)
- Daily vol: 4.42%
- Liq: unknown (backtest DB ends April 30)
- RSI: 79-84 (overbought)
- Key event: May 6 spike 1.262→1.488 (+17.8% in one day)
- **Result: 7 trades, -3.48R, 14% WR**
- **Regime: OVERBOUGHT REVERSAL — edge does NOT work**

### Why Filters Can't Catch This
- RSI>70 filter: Blocks +21.9R of winners in backtest (our edge IS overbought bounces)
- Trend filter (close<EMA20): Blocks +16.4R of winners
- Combined filters: All hurt backtest performance
- **The regime shift is a live-only phenomenon — no backtest filter can catch it**
- The per-symbol loss limit (3 consecutive stops) is the only effective defense

---

## 8. CONFIGURATION SUMMARY

```python
@dataclass(frozen=True)
class V4Config:
    # Context (daily)
    liq_lookback: int = 90
    liq_percentile: float = 0.90
    liq_min_lookback: int = 30
    liq_window: int = 2
    require_short_squeeze: bool = True
    ret5d_min: float = -5.0
    
    # V4 regime filter
    min_regime_ratio: float = 0.30
    regime_lookback_days: int = 10
    min_cascade_strength: float = 0.10
    
    # V4 trend filter — DISABLED
    require_trend_filter: bool = False
    daily_ema_period: int = 20
    
    # V4 per-symbol loss limit
    max_consecutive_stops: int = 3
    
    # Entry confirmation (5m)
    range_lookback: int = 60
    imb_z_threshold: float = 2.0
    vol_z_threshold: float = 3.0
    body_strength_min: float = 0.60
    impulse_min_pct: float = 0.30
    ema_period: int = 20
    z_lookback: int = 100
    min_confirmations: int = 4
    
    # Selectivity
    cooldown_bars: int = 36
    no_reentry_after_stop: bool = True
    
    # Risk
    atr_period: int = 14
    initial_stop_atr: float = 2.5
    
    # Exits (defaults)
    vol_trail_atr: float = 2.0
    struct_lookback: int = 12
    decay_threshold: float = 0.30
    partial_r: float = 2.5
    partial_fraction: float = 0.50
    max_hold_bars: int = 288
```

---

## 9. FILES

| File | Purpose |
|------|---------|
| `engines/liq_cluster_engine_v4.py` | V4 engine (767 lines) |
| `tools/v4_forward_test.py` | Live forward tester |
| `config/v4_forward_test.yaml` | V4 config |
| `V4_MIGRATION.md` | V3↔V4 switch documentation |
| `backtest_output/final_full_stack_trades.csv` | 372 trades, full backtest |
| `backtest_output/final_baseline_trades.csv` | 386 trades, baseline |
| `backtest_output/agg_exit_v2_backtest.py` | Decile-specific exit backtest |
| `backtest_output/lifecycle_analysis_v2.py` | Trade archetype analysis |
| `backtest_data/klines_5m.db` | 1M+ 5m candles (Jan-Apr 2026) |
| `backtest_data/coinalyze_liq.db` | Real Coinalyze liquidation data |
| `backtest_data/daily_closes.db` | Daily close prices |
| `storage/v4_forward_test.db` | Live trade DB |

---

## 10. CURRENT STATE (May 19, 2026 15:45 UTC)

- **V4 forward tester:** Running (PID 1214923)
- **Telegram:** Working (HTTP 200)
- **Equity:** $7,711.15
- **Open positions:** 0
- **Total trades:** 14 (live)
- **NEAR consecutive stops:** 1 (needs 3 to trigger block)
- **Cascades active:** 1/28

---

## 11. BACKTEST RESULTS SUMMARY

| Metric | Baseline | V4 Full Stack | Delta |
|--------|----------|---------------|-------|
| Trades | 386 | 372 | -14 |
| Win Rate | 61.6% | 58.3% | -3.3% |
| Total R | +96.76 | +125.26 | +28.50 |
| PF | 1.65 | 1.81 | +0.16 |
| Avg R | +0.251 | +0.337 | +0.086 |
| Max DD | 29.7% | 25.3% | -4.4% |

**Best symbols:** APT (+12.4R), ZEC (+12.2R), ENA (+11.2R), ARB (+9.8R)
**Worst symbols:** ETH (-4.7R), FIL (-4.4R), TAO (-3.9R), BNB (-2.0R)

**Per-decile Kelly:** D1=1.8%, D2=22.1%, D3=20.0%, D4=12.3%, D5=36.7%, D6=10.6%, D7=10.1%, D8=31.9%, D9=47.0%, D10=33.7%

---

## 12. KEY DECISIONS & RATIONALE

1. **Real Coinalyze data is essential:** Proxy cascade filter produced -36R (garbage); real data produced +96R (real edge)

2. **ret5d_min=-5% is correct:** Relaxing to -10% adds 32 trades but reduces total R from +96 to +88

3. **Aggression predicts trade archetype, not entry quality:** D1-D2 = slow grinders, D3-D4 = clean impulses, D7-D8 = structural squeezes, D9-D10 = climax traps

4. **Aggression should modify exits, not just sizing:** Trail width, decay sensitivity, max hold, and struct_lookback all vary by decile

5. **D9-D10 are climax/exhaustion events:** 50% stop rate, driven by cascade_intensity (+16.6) and spread_expansion (+5.3), low clv (0.55). These are blow-off moves, not continuation setups

6. **D1-D2 need wide trails (3x ATR) and no decay:** Slow grinders with huge MFE (4.5R) but terrible capture (41%). The 37% expansion_decay rate was shaking them out too early

7. **D3 needs decay OFF:** Clean impulses were degraded by standard decay (12-bar minimum)

8. **max_hold is irrelevant at 288 bars:** No trade ever exceeds 112 bars

9. **V4 created as new file, not modifying V3:** Both versions runnable side by side. One-line import change to switch

10. **Trend filter DISABLED:** Our edge IS counter-trend. RSI and EMA filters all hurt backtest performance

11. **Per-symbol loss limit (3 stops):** Only effective defense against regime shifts. Resets on new cascade activation

12. **Evidence-based changes only:** No parameter changes without backtest validation
