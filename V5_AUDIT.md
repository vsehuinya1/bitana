# Bitana Liq-Cluster Engine: Full Version Audit
## V3 → V4 → V5 Evolution | May 2026

---

## 1. V3 — Baseline System

### Architecture
- **Engine file:** `engines/liq_cluster_engine.py`
- **Forward tester:** `tools/v3_forward_test.py`
- **Config:** `config/v3_forward_test.yaml`

### Entry Logic
- **Cascade filter:** Coinalyze liquidation history, 90-day lookback, p90 threshold
  - Requires 2 of last 3 days above p90
  - Short squeeze only (long_liq > short_liq)
  - ret_5d > -5% filter
- **5m confirmations:** 4/6 required
  - breakout (close > 60-bar range high)
  - imb_z > 2.0 (taker imbalance)
  - vol_z > 3.0 (volume spike)
  - body_strength > 0.60
  - impulse > 0.30%
  - momentum (close > EMA-20)
- **BTC alignment:** Required BTCUSDT close > EMA-20 for all entries

### Exit Logic (single config for all trades)
- Stop loss: -1R (fixed)
- Volatility trail: 2.0x ATR
- Structure trail: 12-bar swing low
- Max hold: 288 bars (24 hours)
- No decay

### Sizing
- Flat 2% risk per trade (base), 4% if BTC-aligned

### Backtest Results (Jan-Apr 2026, 28 symbols)
- 385 trades, 61.6% WR, +96.1R, PF 1.65
- With OI filter layers: 372 trades, +125.3R, PF 1.81

### Known Issues
- BTC alignment added correlation risk, not edge
- Single exit config didn't account for trade archetype
- No aggression scoring — all trades treated equally
- JSON serialization bug in forward tester (bool values in confirmations dict)

---

## 2. V4 — Aggression Score + Per-Decile Exits

### Architecture
- **Engine file:** `engines/liq_cluster_engine_v4.py` (new file, V3 untouched)
- **Forward tester:** `tools/v4_forward_test.py`
- **Config:** `config/v4_forward_test.yaml`

### What Changed from V3

#### 2a. Aggression Score (NEW)
10-component composite score computed at entry from 5m candles:

| # | Component | Weight | Measures |
|---|-----------|--------|----------|
| 1 | taker_imb_z | 0.10 | Taker buy/sell imbalance z-score |
| 2 | delta_persistence | 0.10 | Consecutive same-sign delta bars |
| 3 | oi_acceleration | 0.08 | Volume acceleration (OI proxy) |
| 4 | range_expansion_pctile | 0.15 | Current range vs historical percentile |
| 5 | volume_concentration | 0.10 | Last 3 bars / last 10 bars volume |
| 6 | clv | 0.07 | Close position within bar range |
| 7 | wick_rejection | 0.08 | Lower/upper wick ratio |
| 8 | spread_expansion | 0.10 | Current range vs average range |
| 9 | velocity | 0.07 | Price change normalized by ATR×√lookback |
| 10 | cascade_intensity | 0.15 | Volume z-score + range z-score average |

**Score range:** 0-100 via `(composite + 2) / 4 * 100`

**Decile boundaries (from 386-trade baseline):**
- D1: <66.9, D2: <70.5, D3: <73.2, D4: <75.4, D5: <77.2
- D6: <78.8, D7: <80.8, D8: <82.5, D9: <85.0, D10: ≥85.0

#### 2b. Per-Decile Exit Parameters (NEW)
Each decile gets tailored exits based on lifecycle analysis:

| Decile | Trail (ATR) | Struct Lookback | Max Hold | Decay | Archetype |
|--------|-------------|-----------------|----------|-------|-----------|
| D1 | 3.0x | 48 bars | 500 | OFF | Slow grinder |
| D2 | 3.0x | 48 bars | 500 | OFF | Slow grinder |
| D3 | 2.0x | 24 bars | 288 | OFF | Clean impulse |
| D4 | 2.0x | 12 bars | 288 | 15b/1.5R/0.6x/3red | Mixed |
| D5 | 2.0x | 12 bars | 288 | 15b/1.5R/0.6x/3red | Standard+ |
| D6 | 2.0x | 12 bars | 288 | 12b/1.5R/0.6x/3red | Standard |
| D7 | 2.5x | 36 bars | 358 | 20b/2.0R/0.8x/4red | Structural squeeze |
| D8 | 2.5x | 36 bars | 358 | 20b/2.0R/0.8x/4red | Structural squeeze+ |
| D9 | 1.5x | 8 bars | 100 | 8b/1.5R/0.5x/3red | Climax |
| D10 | 1.5x | 8 bars | 100 | 8b/1.5R/0.5x/3red | Exhaustion |

#### 2c. Per-Decile Half-Kelly Sizing (NEW)
Risk per trade varies by decile based on backtest Kelly:

| Decile | Kelly | Half-Kelly | WR | PF | Total R |
|--------|-------|------------|-----|-----|---------|
| D1 | 1.8% | 0.9% | 79% | 1.32 | +2.1R |
| D2 | 22.1% | 11.0% | 65% | 1.89 | +10.9R |
| D3 | 20.0% | 10.0% | 62% | 1.78 | +8.7R |
| D4 | 12.3% | 6.2% | 56% | 1.52 | +5.8R |
| D5 | 36.7% | 18.4% | 60% | 2.41 | +18.3R |
| D6 | 10.6% | 5.3% | 55% | 1.44 | +4.2R |
| D7 | 10.1% | 5.1% | 57% | 1.51 | +6.8R |
| D8 | 31.9% | 16.0% | 64% | 2.09 | +15.3R |
| D9 | 47.0% | 23.5% | 64% | 3.75 | +38.5R |
| D10 | 33.7% | 16.9% | 58% | 2.37 | +20.6R |

#### 2d. New Filters (NEW)
- **Regime filter:** Suppresses cascades when 10-day mean liq < 30% of 90-day mean
- **Min cascade strength:** 0.10x (blocks entries on negligible cascades)
- **Per-symbol loss limit:** 3 consecutive stop_losses → pause until new cascade
- **BTC alignment:** REMOVED (useless correlation risk)

#### 2e. Bug Fixes
- JSON serialization: bool values in confirmations dict converted to strings
- Telegram: switched from Markdown to HTML parse_mode
- Decile boundaries: recomputed from full 386-trade baseline (was misaligned)
- D1 struct_lookback: 12→48 bars (was shaking out slow grinders immediately)
- D7-D8 struct_lookback: 12→36 bars (was too tight for structural squeezes)
- Recovery: aggression/decile recomputed from candle buffer on restart

### Backtest Results (Jan-Apr 2026, 28 symbols)
- 372 trades, 61.6% WR, +125.3R, PF 1.81 (full_stack layer)
- D9 alone: 39 trades, 64.1% WR, +38.52R, PF 3.75

### Known Issues
- Per-decile exits too tight for D9-D10 (fast decay kills winners)
- Per-decile sizing adds complexity
- D10 has high stop rate (42%) — exhausting to trade live

---

## 3. V5 — Simplified + Optimized

### Architecture
- **Engine file:** `engines/liq_cluster_engine_v5.py`
- **Forward tester:** `tools/v5_forward_test.py`
- **Config:** `config/v5_forward_test.yaml`

### What Changed from V4

#### 3a. Decile Filtering (CHANGED)
**D4 and D10 dropped** — negative expectancy in backtest:

| Decile | V4 Total R | V5 Action | Why |
|--------|-----------|-----------|-----|
| D4 | -4.51R (15 trades, 47% WR, PF 0.44) | **DROPPED** | Negative expectancy |
| D10 | -7.51R (84 trades, 58% WR, PF 0.73) | **DROPPED** | Negative expectancy, 42% stop rate |

**Tradeable deciles:** D1, D2, D3, D5, D6, D7, D8, D9

#### 3b. Sizing Simplified (CHANGED)
**From per-decile half-Kelly → flat 4% per trade**

Backtest comparison (156 trades, D4+D10 dropped):

| Model | Final Equity | Max DD | ROI |
|-------|-------------|--------|-----|
| Half-Kelly (per-decile) | $893K | 68.8% | +8836% |
| Full Kelly (per-decile) | $3.55M | 94.1% | +35442% |
| **Flat 4%** | **$265K** | **30.5%** | **+2556%** |
| Flat 2% (V4 style) | $57K | 16.4% | +472% |
| Flat 5% | $535K | 36.8% | +5259% |

**Why flat 4%:**
- Per-decile Kelly adds complexity without meaningful improvement over flat 4%
- Flat 4% gives 30.5% max DD (livable) vs 68.8% for half-Kelly
- Simpler = more robust in production
- Still achieves +2556% ROI on backtest

#### 3c. Everything Else (UNCHANGED from V4)
- Aggression score: same 10-component system
- Decile boundaries: same (from 386-trade baseline)
- Per-decile exits: same trail/hold/decay parameters
- Entry confirmations: same 4/6 checks
- Cascade filter: same (liq_lookback=90, p90, short_squeeze, ret5d>-5%)
- Regime filter: same (min_ratio=0.30, lookback=10 days)
- Min cascade strength: same (0.10x)
- Per-symbol loss limit: same (3 consecutive stops)
- BTC alignment: still removed

### Backtest Results (Jan-May 2026, 28 symbols)

**Overall (D4+D10 dropped):**
- 156 trades, 65.4% WR, +93.14R, PF 2.92

**By Month:**
| Month | Trades | WR | R |
|-------|--------|-----|-----|
| Feb | 17 | 71% | +32.93R |
| Mar | 28 | 64% | +19.81R |
| Apr | 56 | 63% | +25.70R |
| May | 55 | 58% | +14.71R |

**By Decile:**
| Decile | Trades | WR | Total R | PF | Avg R |
|--------|--------|-----|---------|-----|-------|
| D1 | 49 | 63% | +45.28 | 4.01 | +0.924 |
| D2 | 31 | 61% | +19.10 | 2.69 | +0.616 |
| D3 | 21 | 67% | +6.95 | 2.07 | +0.331 |
| D5 | 12 | 75% | +4.56 | 2.43 | +0.380 |
| D6 | 2 | 100% | +1.38 | inf | +0.689 |
| D7 | 14 | 64% | +2.23 | 1.82 | +0.159 |
| D8 | 14 | 64% | +13.24 | 3.45 | +0.945 |
| D9 | 13 | 69% | +0.41 | 1.10 | +0.031 |

**By Exit Reason:**
- vol_trail: 108 trades, +163.04R
- stop_loss: 48 trades, -69.90R

**By Symbol (top 5):**
- ARB: 14 trades, +16.54R
- APT: 6 trades, +13.35R
- DOT: 12 trades, +11.11R
- TAO: 14 trades, +9.43R
- NEAR: 7 trades, +7.31R

### Live Status
- Running since 02:58 UTC May 20, 2026
- 28 symbols monitored
- 0 open positions (waiting for cascade + confirmation)
- Heartbeat active, all systems nominal

---

## 4. Key Design Decisions & Rationale

### Why Drop D4 and D10?
- **D4:** 15 trades, 47% WR, PF 0.44. Negative expectancy. The aggression score range (73.2-75.4) captures choppy transitions between clean impulses and structural squeezes — no consistent edge.
- **D10:** 84 trades, 58% WR, PF 0.73. Despite high aggression scores (climax events), the 42% stop rate and tight exits result in net negative. These are exhaustion/blow-off moves that reverse too quickly for the trailing stop to capture.

### Why Flat 4% Instead of Per-Decile Kelly?
- Per-decile Kelly gives higher total return but with 2x the drawdown (68.8% vs 30.5%)
- Flat 4% is simpler, more robust, and easier to reason about in production
- The edge is in the entry/exit logic, not in sizing discrimination
- 30.5% max DD is livable; 68.8% is not

### Why Keep Per-Decile Exits?
- Lifecycle analysis showed each decile has a distinct behavioral archetype
- D1-D2 (slow grinders) need wide trails and no decay
- D7-D8 (structural squeezes) need wide trails and long hold
- D9 (climax) needs tight trails and fast decay
- Single exit config (V3) was cutting winners too early across all types

### Why Remove BTC Alignment?
- Added correlation risk without improving WR or PF
- Our edge is in liquidation cascades, not BTC trend direction
- Counter-trend entries during bounces are where the edge lives
- Trend filter backtest: blocks +16.4R of winners

### Why the Regime Filter?
- May 2026 had low-liquidity regime (cascade strength 0.03-0.05 vs backtest 1.3-2.3x)
- Without it, NEARUSDT and others kept triggering on noise
- Regime filter detects sustained liq degradation (10-day mean < 30% of 90-day mean)
- Zero impact on historical trades (problematic regime outside backtest window)

---

## 5. Data Infrastructure

### Coinalyze Liquidation Data
- **Source:** `https://api.coinalyze.net/v1/liquidation-history`
- **Format:** Daily aggregated, timestamps in seconds
- **Symbol format:** `SOLUSDT_PERP.A` (API) → normalized to `SOLUSDT` (internal)
- **DB:** `backtest_data/coinalyze_liq.db`, table `liquidation_history`
- **Columns:** `timestamp` (unix seconds), `symbol`, `long_liq`, `short_liq`
- **Rows:** 3,851 (Jan 1 – May 20, 2026, 28 symbols)
- **Note:** Both `_PERP.A` and plain symbol names exist in DB (ingested from different fetch scripts). Backtest uses plain names.

### Binance Klines
- **Source:** Binance Futures REST API (`fapi/v1/klines`)
- **Format:** 5m candles, timestamps in milliseconds
- **DB:** `backtest_data/klines_5m.db`, table `klines`
- **Columns:** `symbol`, `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`, `taker_buy_volume`
- **Rows:** 1,156,204 (Jan 1 – May 20, 2026, 28 symbols × ~40,000 each)

### Daily Closes
- **Source:** Binance daily klines (for ret_5d calculation)
- **DB:** `backtest_data/coinalyze_liq.db`, table `daily_closes`
- **Columns:** `symbol`, `date`, `close`

---

## 6. File Map

| File | Purpose |
|------|---------|
| `engines/liq_cluster_engine.py` | V3 engine (untouched, production) |
| `engines/liq_cluster_engine_v4.py` | V4 engine (reference) |
| `engines/liq_cluster_engine_v5.py` | V5 engine (current, live) |
| `tools/v3_forward_test.py` | V3 forward tester |
| `tools/v4_forward_test.py` | V4 forward tester |
| `tools/v5_forward_test.py` | V5 forward tester (current, live) |
| `config/v3_forward_test.yaml` | V3 config |
| `config/v4_forward_test.yaml` | V4 config |
| `config/v5_forward_test.yaml` | V5 config (current) |
| `V4_AUDIT.md` | V4 audit document |
| `V4_MIGRATION.md` | V3↔V4 migration guide |
| `backtest_data/klines_5m.db` | 1.1M+ 5m candles |
| `backtest_data/coinalyze_liq.db` | Liq history + daily closes |
| `backtest_output/v5_full_backtest_trades.csv` | V5 backtest trade log |
| `backtest_output/v5_full_backtest.py` | V5 backtest script |
| `backtest_output/v5_filter_test.py` | Filter comparison script |
| `backtest_output/v5_compound_test2.py` | Compounding simulation |
| `storage/v5_forward_test.db` | V5 live paper trading DB |
| `tg_bot/alerts.py` | Telegram alerts (HTML parse_mode) |

---

## 7. Backtest Methodology

### Simulation Approach
- Sequential event processing: 1.1M candle events across 28 symbols, sorted by timestamp
- Per-symbol candle buffer: last 200 candles at each event
- Cascade tracker: updated once per day per symbol from liq history
- Position management: engine.manage_position() called every 5m candle
- Entry: engine.evaluate() called every 5m candle (if no position, past warmup)
- Fees: 4.5 bps taker, 2.0 bps slippage
- Warmup: 30 days (liq history buildup)

### Compounding Model
- Fixed fractional sizing: risk_pct applied to current equity each trade
- PnL scaling: pnl_usd = trade_R × equity × risk_pct
- Sequential: each trade's exit equity = next trade's entry equity
- No leverage modeling (paper trading uses leverage but backtest assumes spot-like)

### Limitations
- No partial fills or slippage beyond fixed bps
- No funding rates
- No position overlap modeling (max 1 position per symbol)
- Cascade tracker uses daily data (not intraday), so cascade activation has 1-day latency
- Backtest assumes immediate fill at candle close price ± slippage

---

## 8. Known Limitations & Risks

1. **Cascade data latency:** Daily liq data means cascade activation detected ~1 day late
2. **Regime filter:** May suppress valid entries during gradual liq decline
3. **D9 break-even:** 13 trades, +0.41R. Not worth the 23.5% risk. Consider dropping.
4. **May degradation:** WR dropped from 63% (Feb-Apr) to 58% (May). Market regime shift?
5. **Low trade frequency:** ~1.3 trades/day across 28 symbols. Long dry spells possible.
6. **Single exchange:** Binance only. No cross-exchange arbitrage or hedging.
7. **No short entries:** System only goes long (short squeeze thesis). Misses long squeeze opportunities.
