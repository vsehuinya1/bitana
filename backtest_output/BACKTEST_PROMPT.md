# BACKTEST PROMPT — Aggression-Modified Exit Parameters
# Copy this entire prompt to Claude or Gemini to verify results.

## TASK
Backtest a modified V3 liq-cluster trading engine with aggression-decile-modified exit parameters on Binance perpetual futures data from February to April 2026 (28 altcoin symbols). Compare the modified exit engine against the baseline (original exit parameters) and report lifecycle behavior by aggression decile.

## DATA SOURCES

### Database 1: `backtest_data/klines_5m.db`
Table: `klines`
Columns: symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume
- 1,015,000 rows total
- 29 symbols (28 trading + BTCUSDT for reference)
- 35,000 bars per symbol
- Timestamps in milliseconds
- Date range: ~Jan 1 – May 1, 2026

### Database 2: `backtest_data/coinalyze_liq.db`
Table: `liquidation_history`
Columns: symbol, timestamp, long_liq, short_liq
- 3,356 rows
- Symbol format: `SOLUSDT_PERP.A` (strip `_PERP.A` to match klines symbols)
- Timestamp in SECONDS (not milliseconds)
- Daily aggregated data (one row per symbol per day)

Table: `daily_closes`
Columns: symbol, date, close
- Symbol format: `SOLUSDT` (matches klines)
- Date format: `YYYY-MM-DD`

## SYMBOLS (28 trading + 1 reference)

Tier A: NEARUSDT, ZECUSDT, ADAUSDT, WLDUSDT, UNIUSDT, NMRUSDT, PENDLEUSDT, ARBUSDT, RENDERUSDT, RUNEUSDT, FETUSDT, DOTUSDT

Tier B: TONUSDT, SOLUSDT, 1000LUNCUSDT, ENAUSDT, 1000PEPEUSDT, XRPUSDT, FILUSDT, BNBUSDT, TAOUSDT, CHZUSDT, DASHUSDT, QNTUSDT, ICPUSDT, XLMUSDT, APTUSDT, ETHUSDT

Reference: BTCUSDT (not traded, only for alignment checks)

## ENGINE CONFIGURATION (V3 — frozen, do not change)

```
liq_lookback = 90
liq_percentile = 0.90
liq_min_lookback = 30
liq_window = 2
require_short_squeeze = True
ret5d_min = -5.0
range_lookback = 60
imb_z_threshold = 2.0
vol_z_threshold = 3.0
body_strength_min = 0.60
impulse_min_pct = 0.30
ema_period = 20
z_lookback = 100
min_confirmations = 4
cooldown_bars = 36
no_reentry_after_stop = True
atr_period = 14
initial_stop_atr = 2.5
vol_trail_atr = 2.0
struct_lookback = 12
decay_threshold = 0.30
partial_r = 2.5
partial_fraction = 0.50
max_hold_bars = 288
```

## CASCADE DETECTION (daily level)

For each symbol, maintain a rolling window of daily liquidation history (max 95 rows). On each new day:

1. Append the day's liq row (long_liq + short_liq = total_liq)
2. If fewer than 30 days of history: cascade_inactive
3. Compute p90 of total_liq over the lookback window (90 days, or all available if less)
4. Cascade is ACTIVE if any of the last `liq_window + 1` = 3 days exceed p90
5. AND `require_short_squeeze`: liquidation imbalance (long_liq - short_liq) / total_liq must be negative (more short liquidations than long)
6. AND `ret5d_min`: 5-day return computed from daily_closes (current close / close 6 days ago - 1) * 100 must be > -5%

The cascade state updates once per day. All 5m bars within that day see the same cascade state.

## 5m TRIGGER (per symbol, per bar)

Only evaluate if:
- cascade_active = True
- cooldown = 0
- not stopped_in_window (if no_reentry_after_stop)
- not already in a trade

Compute on the last N 5m candles (N = max of range_lookback, z_lookback, ema_period*3):

1. ATR(14) using true range
2. EMA(20)
3. Range high = max high over last `range_lookback` bars (excluding current)
4. Volume z-score over last `z_lookback` bars
5. Taker imbalance: (taker_buy_volume - taker_sell_volume) / (taker_buy_volume + taker_sell_volume) for each bar, then z-score
6. Body strength = |close - open| / (high - low) for current bar
7. Impulse = (close - open) / open * 100 for current bar

Confirmations (need >= 4 of 6):
- breakout: close > range_high
- imb: taker_imb_z > 2.0
- vol: vol_z > 3.0
- body: body_strength > 0.60
- impulse: bar_return_pct > 0.30%
- momentum: close > EMA(20)

Entry: at close of qualifying bar
Stop: entry_price - 2.5 * ATR

## AGGRESSION SCORE (10 components, computed at entry)

Computed on the last 21 bars (20 lookback + current). Each component is normalized, then weighted:

| # | Component | Weight | Formula |
|---|-----------|--------|---------|
| 1 | taker_imb_z | 0.10 | z-score of (close - mid)/(high-low) vs prior 20 bars |
| 2 | delta_persistence | 0.10 | consecutive same-sign closes / 20 |
| 3 | oi_acceleration | 0.08 | (mean vol last 5 / mean vol prior 15) - 1 |
| 4 | range_expansion_pctile | 0.15 | percentile of current bar's range vs prior 20 ranges |
| 5 | volume_concentration | 0.10 | sum(vol last 3) / sum(vol last 10) |
| 6 | clv | 0.07 | (close - low)/(high - low) * 2 - 1 |
| 7 | wick_rejection | 0.08 | lower_wick/range if green, upper_wick/range if red |
| 8 | spread_expansion | 0.10 | (current_range / mean_prior_20_ranges) - 1 |
| 9 | velocity | 0.07 | (close - close_20_bars_ago) / (mean_range * sqrt(20)) |
| 10 | cascade_intensity | 0.15 | (vol_z + range_z) / 2 |

Composite = weighted sum, then normalized to 0-100: `max(0, min(100, (composite + 2) / 4 * 100))`

Assign decile by ranking all trades by aggression score (D1 = lowest 10%, D10 = highest 10%).

## EXIT RULES — TWO VERSIONS TO COMPARE

### BASELINE Exit (original V1)
- Stop loss: price <= entry - 2.5 * ATR
- Expansion decay: IF bars_held >= 8 AND max_mfe >= 1.5R AND pullback_from_max >= 0.3 * ATR → exit at close
- Vol trail: IF max_mfe >= 2.0R AND close < best_price - 2.0 * ATR → exit at close
- Max hold: bars_held >= 288 → exit at close

### MODIFIED Exit (V2 — aggression-decile-dependent)

Stop loss is always entry - 2.5 * ATR (unchanged).

Trail width, decay sensitivity, and max hold vary by aggression decile:

| Decile | Trail ATR | Decay Condition | Max Hold |
|--------|-----------|----------------|----------|
| D1 | 3.0x | OFF (never trigger decay) | 500 bars |
| D2 | 3.0x | OFF | 500 bars |
| D3 | 2.0x | Standard (see below) | 288 bars |
| D4 | 2.0x | Standard | 288 bars |
| D5 | 2.0x | Standard | 288 bars |
| D6 | 2.0x | Standard | 288 bars |
| D7 | 2.5x | Suppressed (see below) | 350 bars |
| D8 | 2.5x | Suppressed | 350 bars |
| D9 | 1.5x | Aggressive (see below) | 100 bars |
| D10 | 1.5x | Aggressive | 100 bars |

**Standard decay** (D3-D6): bars_held >= 12 AND max_mfe >= 1.5R AND pullback >= 0.6 * ATR AND consecutive_red >= 3

**Suppressed decay** (D7-D8): bars_held >= 20 AND max_mfe >= 2.0R AND pullback >= 0.8 * ATR AND consecutive_red >= 4

**Aggressive decay** (D9-D10): bars_held >= 6 AND max_mfe >= 1.0R AND pullback >= 0.4 * ATR AND consecutive_red >= 2

**Vol trail**: IF max_mfe >= 2.0R AND close < best_price - (trail_ATR * ATR) → exit at close

## SIZING

Fixed 2% risk per trade (R normalization). Position size = (account_risk) / (stop_distance). Start with $10,000 account. No pyramiding. No leverage cap needed since we're tracking R multiples.

## REPLAY LOGIC

1. Load all data into memory
2. Build a global timeline of all 5m bars sorted by close_time
3. Process bars in order. For each bar:
   a. If new day: update cascade tracker for ALL symbols using that day's liq data
   b. If in trade: update lifecycle metrics, check exit conditions (using the trade's decile-specific parameters)
   c. If not in trade: check entry conditions
4. Record every trade with full lifecycle data

## OUTPUT REQUIRED

Run TWO backtests: BASELINE (original exits) and MODIFIED (aggression-decile exits). For each, report:

### 1. Overall Metrics
- Total trades, WR%, total R, profit factor, final equity
- Monthly breakdown (Feb, Mar, Apr)
- Exit reason breakdown (%)

### 2. By Aggression Decile (10 deciles)
For each decile: N, WR%, avg realized R, avg MFE (R), MFE capture %, median bars held, median time to max MFE, exit reason breakdown, % reaching 2R/3R/5R

### 3. Comparison Table
Side-by-side: Baseline vs Modified for each decile and overall

### 4. Key Findings
- Which decile improved most from modified exits?
- Which decile degraded?
- Did total R increase or decrease?
- Did WR% change meaningfully?

## CRITICAL IMPLEMENTATION NOTES

1. **Daily liq update timing**: Update cascade trackers for ALL symbols at the START of each day (before processing any 5m bars for that day). The cascade state for day N is determined by the daily liq data for day N.

2. **Symbol name mapping**: liquidation_history uses `SOLUSDT_PERP.A` format. Strip `_PERP.A` to get the klines symbol name.

3. **Timestamp units**: klines.close_time is in milliseconds. liquidation_history.timestamp is in SECONDS. Convert: `datetime.utcfromtimestamp(timestamp_seconds).strftime('%Y-%m-%d')`.

4. **ret_5d calculation**: Use daily_closes table. For day N, find the close from 6 days prior (not 5 — the original code uses `closes_hist[-6]` which is 6 bars back in daily data, approximately 5 trading days). If no data 6 days back, skip the trade.

5. **Warmup period**: The first 30 days of liq data are warmup. No cascade can activate before day 31. With data starting Jan 1, the earliest possible trade is February. This is expected — do NOT try to generate January trades.

6. **Decile assignment**: Compute aggression score at entry for ALL trades first, THEN assign deciles by ranking. D1 = bottom 10%, D10 = top 10%. Use `pd.qcut` or equivalent with 10 bins.

7. **Interleaved processing**: Bars from different symbols are interleaved in time. Process them in strict close_time order. Each symbol maintains its own state independently.

8. **The modified exit parameters are the ONLY difference** between baseline and modified. Entry logic, cascade detection, confirmations, stop distance — everything else is identical.
