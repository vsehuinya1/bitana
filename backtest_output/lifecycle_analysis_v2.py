"""
Trade Lifecycle Behavior Analysis by Aggression Decile
Uses the SAME engine logic as v3_comprehensive_backtest.py
Tracks behavioral metrics per trade, grouped by aggression decile.
"""
import csv
import math
import sqlite3
import json
import numpy as np
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════════════
# Config (must match v3_comprehensive_backtest.py)
# ═══════════════════════════════════════════════════════════════════════

SYMBOLS_TIER_A = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT",
]
SYMBOLS_TIER_B = [
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT",
    "DASHUSDT", "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]
ALL_SYMBOLS = SYMBOLS_TIER_A + SYMBOLS_TIER_B
BTC_SYMBOL = "BTCUSDT"

class V3Config:
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

CFG = V3Config()

# ═══════════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════════

def load_all_data():
    """Load klines, daily liq, and daily closes."""
    # Klines
    conn = sqlite3.connect('backtest_data/klines_5m.db')
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume FROM klines ORDER BY symbol, open_time")
    klines_rows = cursor.fetchall()
    conn.close()

    klines = defaultdict(list)
    for row in klines_rows:
        symbol, open_time, close_time, o, h, l, c, v, tbv = row
        klines[symbol].append({
            'open_time': open_time, 'close_time': close_time,
            'open': float(o), 'high': float(h), 'low': float(l),
            'close': float(c), 'volume': float(v), 'taker_buy_volume': float(tbv),
        })

    # Daily liq (from coinalyze DB)
    conn = sqlite3.connect('backtest_data/coinalyze_liq.db')
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, timestamp, long_liq, short_liq FROM liquidation_history")
    liq_rows = cursor.fetchall()

    # Daily closes (from coinalyze DB)
    cursor.execute("SELECT symbol, date, close FROM daily_closes")
    daily_rows = cursor.fetchall()
    conn.close()

    # Parse daily liq - symbol format: SOLUSDT_PERP.A, timestamp is unix seconds
    daily_liq = defaultdict(list)
    for symbol, ts, ll, sl in liq_rows:
        base = symbol.replace('_PERP.A', '')
        dt = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
        daily_liq[base].append({
            'date': dt, 'long_liq': float(ll), 'short_liq': float(sl),
            'total_liq': float(ll) + float(sl), 'close': 0.0,
        })
    # Sort by date
    for sym in daily_liq:
        daily_liq[sym].sort(key=lambda x: x['date'])

    # Parse daily closes - symbol format: SOLUSDT
    daily_closes = defaultdict(dict)
    for symbol, date, close in daily_rows:
        daily_closes[symbol][str(date)] = float(close)

    # Add close prices to daily_liq
    for sym in daily_liq:
        for row in daily_liq[sym]:
            d = row['date']
            if sym in daily_closes and d in daily_closes[sym]:
                row['close'] = daily_closes[sym][d]

    return klines, daily_liq, daily_closes

# ═══════════════════════════════════════════════════════════════════════
# Engine Components (from v3_comprehensive_backtest.py)
# ═══════════════════════════════════════════════════════════════════════

def _ema(values, period):
    if len(values) < period:
        return values[-1] if values else 0.0
    alpha = 2.0 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = alpha * v + (1 - alpha) * result
    return result

def _atr(highs, lows, closes, period):
    if len(highs) < 2:
        return highs[0] - lows[0] if len(highs) else 0.0
    tr = np.empty(len(highs))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return _ema(tr, period)

def _z_score(values, lookback):
    if len(values) < 20:
        return 0.0
    window = values[-lookback:] if len(values) >= lookback else values
    mean = np.mean(window)
    std = np.std(window)
    if std < 1e-12:
        return 0.0
    return (values[-1] - mean) / std

class CascadeTracker:
    def __init__(self):
        self._liq_history = deque(maxlen=CFG.liq_lookback + 5)

    def update(self, daily_row):
        self._liq_history.append(daily_row)

        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        liqs = [r["total_liq"] for r in self._liq_history]
        if len(liqs) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p90 = np.percentile(lookback, CFG.liq_percentile * 100)
        if p90 <= 0:
            return False, 0.0, 0.0, 0.0

        cascade_active = False
        for i in range(CFG.liq_window + 1):
            idx = -(i + 1)
            if abs(idx) <= len(liqs) and liqs[idx] > p90:
                cascade_active = True
                break

        strength = liqs[-1] / p90 if p90 > 0 else 0

        last = self._liq_history[-1]
        total = last.get("total_liq", 0)
        if total > 0:
            imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total
        else:
            imb = 0.0

        closes_hist = [r.get("close", 0) for r in self._liq_history]
        if len(closes_hist) >= 6 and closes_hist[-6] > 0:
            ret_5d = ((closes_hist[-1] / closes_hist[-6]) - 1) * 100
        else:
            ret_5d = 0.0

        if CFG.require_short_squeeze and imb >= 0:
            cascade_active = False
        if CFG.ret5d_min is not None and ret_5d <= CFG.ret5d_min:
            cascade_active = False

        return cascade_active, strength, imb, ret_5d

# ═══════════════════════════════════════════════════════════════════════
# Aggression Score (10 components)
# ═══════════════════════════════════════════════════════════════════════

def compute_aggression(candles_5m):
    """Compute 10-component aggression score at the latest bar."""
    if len(candles_5m) < 25:
        return 0.0

    lookback = 20
    window = candles_5m[-(lookback + 1):]
    n = len(window)

    c = np.array([w['close'] for w in window])
    h = np.array([w['high'] for w in window])
    l = np.array([w['low'] for w in window])
    v = np.array([w['volume'] for w in window])
    o = np.array([w['open'] for w in window])

    scores = {}

    # 1. Taker imbalance z-score
    mid = (h + l) / 2
    denom = h - l
    denom[denom == 0] = 1e-10
    taker_imb = (c - mid) / denom
    recent_imb = taker_imb[-1]
    hist_imb = taker_imb[:-1]
    std_hist = np.std(hist_imb)
    scores['taker_imb_z'] = (recent_imb - np.mean(hist_imb)) / (std_hist + 1e-10)

    # 2. Delta persistence
    diffs = np.diff(c)
    sign = np.sign(diffs[-1])
    persistence = 0
    for d in reversed(diffs):
        if np.sign(d) == sign:
            persistence += 1
        else:
            break
    scores['delta_persistence'] = persistence / lookback

    # 3. Volume acceleration (OI proxy)
    vol_short = np.mean(v[-5:])
    vol_long = np.mean(v[:-5]) + 1e-10
    scores['oi_acceleration'] = (vol_short - vol_long) / vol_long

    # 4. Range expansion percentile
    ranges = h[:-1] - l[:-1]
    current_range = h[-1] - l[-1]
    scores['range_expansion_pctile'] = np.mean(current_range > ranges) if len(ranges) > 0 else 0.5

    # 5. Volume concentration
    vol_3 = np.sum(v[-3:])
    vol_10 = np.sum(v[-10:]) + 1e-10
    scores['volume_concentration'] = vol_3 / vol_10

    # 6. CLV
    range_hl = h[-1] - l[-1]
    if range_hl > 0:
        scores['clv'] = (c[-1] - l[-1]) / range_hl * 2 - 1
    else:
        scores['clv'] = 0

    # 7. Wick rejection
    upper_wick = h[-1] - max(c[-1], o[-1])
    lower_wick = min(c[-1], o[-1]) - l[-1]
    total_range = h[-1] - l[-1]
    if total_range > 0:
        if c[-1] > o[-1]:
            scores['wick_rejection'] = lower_wick / total_range
        else:
            scores['wick_rejection'] = upper_wick / total_range
    else:
        scores['wick_rejection'] = 0

    # 8. Spread expansion
    avg_range = np.mean(ranges) + 1e-10
    scores['spread_expansion'] = (current_range - avg_range) / avg_range

    # 9. Velocity
    scores['velocity'] = (c[-1] - c[0]) / (np.mean(ranges) * np.sqrt(lookback) + 1e-10)

    # 10. Cascade intensity
    vol_z = (v[-1] - np.mean(v[:-1])) / (np.std(v[:-1]) + 1e-10)
    range_z = (current_range - np.mean(ranges)) / (np.std(ranges) + 1e-10) if len(ranges) > 0 and np.std(ranges) > 0 else 0
    scores['cascade_intensity'] = (vol_z + range_z) / 2

    weights = {
        'taker_imb_z': 0.10, 'delta_persistence': 0.10, 'oi_acceleration': 0.08,
        'range_expansion_pctile': 0.15, 'volume_concentration': 0.10, 'clv': 0.07,
        'wick_rejection': 0.08, 'spread_expansion': 0.10, 'velocity': 0.07,
        'cascade_intensity': 0.15,
    }
    composite = sum(scores.get(k, 0) * w for k, w in weights.items())
    composite = max(0, min(100, (composite + 2) / 4 * 100))
    return composite

# ═══════════════════════════════════════════════════════════════════════
# Lifecycle Tracker
# ═══════════════════════════════════════════════════════════════════════

class TradeLifecycle:
    """Records full lifecycle metrics for a single trade."""
    def __init__(self, symbol, entry_price, stop_price, atr, aggression, entry_time):
        self.symbol = symbol
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.atr = atr
        self.aggression = aggression
        self.entry_time = entry_time

        self.bars_held = 0
        self.max_price = entry_price
        self.min_price = entry_price
        self.max_mfe_r = 0.0
        self.time_to_1r = None
        self.time_to_2r = None
        self.time_to_max_mfe = None
        self.pullbar_at_max_mfe = 0.0
        self.bars_above_1r = 0
        self.bars_above_2r = 0
        self.bars_above_3r = 0
        self.expansion_bars = 0
        self.consolidation_bars = 0
        self.red_bars = 0
        self.green_bars = 0
        self.max_consecutive_red = 0
        self.consecutive_red = 0
        self.total_volume = 0.0
        self.peak_volume = 0.0
        self.exit_price = None
        self.exit_reason = None
        self.realized_r = None
        self.mfe_capture = None
        self.volume_ratio = None

    def update(self, bar):
        """Update lifecycle with a new bar. Returns True if trade should continue."""
        self.bars_held += 1
        h = bar['high']
        l = bar['low']
        c = bar['close']
        o = bar['open']
        v = bar['volume']

        self.total_volume += v
        self.peak_volume = max(self.peak_volume, v)

        bar_range = h - l
        if bar_range > 1.5 * self.atr:
            self.expansion_bars += 1
        elif bar_range < 0.5 * self.atr:
            self.consolidation_bars += 1

        if c > o:
            self.green_bars += 1
            self.consecutive_red = 0
        else:
            self.red_bars += 1
            self.consecutive_red += 1
            self.max_consecutive_red = max(self.max_consecutive_red, self.consecutive_red)

        self.max_price = max(self.max_price, h)
        self.min_price = min(self.min_price, l)

        mfe_r = (self.max_price - self.entry_price) / self.atr
        if mfe_r > self.max_mfe_r:
            self.max_mfe_r = mfe_r
            self.time_to_max_mfe = self.bars_held
            self.pullbar_at_max_mfe = 0.0
        else:
            self.pullbar_at_max_mfe = (self.max_price - c) / self.atr

        r_1r = 1.0 * self.atr
        r_2r = 2.0 * self.atr
        r_3r = 3.0 * self.atr

        if c >= self.entry_price + r_1r and self.time_to_1r is None:
            self.time_to_1r = self.bars_held
        if c >= self.entry_price + r_2r and self.time_to_2r is None:
            self.time_to_2r = self.bars_held
        if c >= self.entry_price + r_1r:
            self.bars_above_1r += 1
        if c >= self.entry_price + r_2r:
            self.bars_above_2r += 1
        if c >= self.entry_price + r_3r:
            self.bars_above_3r += 1

        return True

    def exit_trade(self, price, reason):
        self.exit_price = price
        self.exit_reason = reason
        self.realized_r = (price - self.entry_price) / self.atr
        self.mfe_capture = self.realized_r / self.max_mfe_r if self.max_mfe_r > 0 else 0.0
        avg_vol = self.total_volume / max(self.bars_held, 1)
        self.volume_ratio = self.peak_volume / (avg_vol + 1e-10)

    def to_dict(self):
        return {
            'symbol': self.symbol,
            'entry_time': self.entry_time,
            'entry_price': self.entry_price,
            'aggression': self.aggression,
            'atr': self.atr,
            'bars_held': self.bars_held,
            'realized_r': self.realized_r if self.realized_r is not None else 0,
            'max_mfe_r': self.max_mfe_r,
            'mfe_capture': self.mfe_capture if self.mfe_capture is not None else 0,
            'time_to_1r': self.time_to_1r if self.time_to_1r is not None else self.bars_held,
            'time_to_2r': self.time_to_2r if self.time_to_2r is not None else self.bars_held,
            'time_to_max_mfe': self.time_to_max_mfe if self.time_to_max_mfe is not None else self.bars_held,
            'pullbar_at_max_mfe': self.pullbar_at_max_mfe,
            'bars_above_1r': self.bars_above_1r,
            'bars_above_2r': self.bars_above_2r,
            'bars_above_3r': self.bars_above_3r,
            'expansion_bars': self.expansion_bars,
            'consolidation_bars': self.consolidation_bars,
            'red_bars': self.red_bars,
            'green_bars': self.green_bars,
            'max_consecutive_red': self.max_consecutive_red,
            'avg_volume': self.total_volume / max(self.bars_held, 1),
            'peak_volume': self.peak_volume,
            'volume_ratio': self.volume_ratio if self.volume_ratio is not None else 0,
            'exit_reason': self.exit_reason,
        }

# ═══════════════════════════════════════════════════════════════════════
# Main Replay Engine
# ═══════════════════════════════════════════════════════════════════════

def run_lifecycle_analysis():
    print("Loading data...")
    klines, daily_liq, daily_closes = load_all_data()

    total_bars = sum(len(v) for v in klines.values())
    print(f"Symbols: {len(klines)}, Total 5m bars: {total_bars}")
    for sym in sorted(daily_liq.keys())[:5]:
        print(f"  {sym}: {len(daily_liq[sym])} daily liq records")

    # Build date index for daily liq updates
    # For each symbol, map date -> daily_liq_row
    daily_liq_by_date = {}
    for sym in daily_liq:
        daily_liq_by_date[sym] = {}
        for row in daily_liq[sym]:
            daily_liq_by_date[sym][row['date']] = row

    # Track state per symbol
    states = {}
    cascade_trackers = {}
    lifecycles = []
    current_trades = {}  # symbol -> TradeLifecycle

    # Build a global timeline of all 5m bars across all symbols
    # Group by date for daily liq updates
    all_bars = []
    for sym in ALL_SYMBOLS + [BTC_SYMBOL]:
        if sym not in klines:
            continue
        for bar in klines[sym]:
            all_bars.append((sym, bar))

    # Sort by close_time
    all_bars.sort(key=lambda x: x[1]['close_time'])

    print(f"\nTotal bars to process: {len(all_bars)}")

    # Process bar by bar
    sym_bar_count = defaultdict(int)
    sym_candles = defaultdict(list)  # rolling window of 5m candles per symbol
    last_date = None
    trade_count = 0

    for idx, (sym, bar) in enumerate(all_bars):
        if idx % 50000 == 0:
            print(f"  Processing bar {idx}/{len(all_bars)}... ({trade_count} trades found)")

        # Get or create state
        if sym not in states:
            states[sym] = {
                'cooldown': 0, 'stopped_in_window': False,
                'cascade_active': False, 'last_cascade_state': False,
                'in_trade': False, 'entry_price': 0, 'stop_price': 0,
                'atr': 0, 'bars_held': 0, 'best_price': 0,
                'partial_taken': False, 'aggression': 0,
            }
            cascade_trackers[sym] = CascadeTracker()

        st = states[sym]
        ct = cascade_trackers[sym]

        # Add candle to rolling window
        sym_candles[sym].append(bar)
        if len(sym_candles[sym]) > 200:
            sym_candles[sym] = sym_candles[sym][-200:]

        # Check if new day - update daily liq
        bar_date = datetime.utcfromtimestamp(bar['close_time'] / 1000).strftime('%Y-%m-%d')
        if bar_date != last_date:
            # Update daily liq for ALL symbols at start of each day
            for s in ALL_SYMBOLS:
                if s in daily_liq_by_date and bar_date in daily_liq_by_date[s]:
                    daily_row = daily_liq_by_date[s][bar_date]
                    if s not in cascade_trackers:
                        cascade_trackers[s] = CascadeTracker()
                    cascade_active, strength, imb, ret_5d = cascade_trackers[s].update(daily_row)
                    if s not in states:
                        states[s] = {
                            'cooldown': 0, 'stopped_in_window': False,
                            'cascade_active': False, 'last_cascade_state': False,
                            'in_trade': False, 'entry_price': 0, 'stop_price': 0,
                            'atr': 0, 'bars_held': 0, 'best_price': 0,
                            'partial_taken': False, 'aggression': 0,
                        }
                    sst = states[s]
                    if cascade_active and not sst['last_cascade_state']:
                        sst['stopped_in_window'] = False
                    sst['last_cascade_state'] = cascade_active
                    sst['cascade_active'] = cascade_active
            last_date = bar_date

        # Manage existing trade
        if st['in_trade'] and sym in current_trades:
            st['bars_held'] += 1
            tc = current_trades[sym]
            tc.update(bar)

            # Check stop loss
            if bar['low'] <= st['stop_price']:
                tc.exit_trade(st['stop_price'], 'stop_loss')
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['stopped_in_window'] = True
                st['cooldown'] = CFG.cooldown_bars
                trade_count += 1
                continue

            # Check exit conditions (V2 revised)
            atr = st['atr']
            entry = st['entry_price']
            best = max(st['best_price'], bar['high'])
            st['best_price'] = best
            mfe_r = (best - entry) / atr
            pullback = (best - bar['close']) / atr

            # Expansion decay (revised - more conservative)
            if (st['bars_held'] >= 12 and mfe_r >= 1.5 and
                pullback >= 0.6 and tc.consecutive_red >= 3):
                tc.exit_trade(bar['close'], 'expansion_decay')
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                trade_count += 1
                continue

            # Vol trail
            trail_level = best - 2.0 * atr
            if mfe_r >= 2.0 and bar['close'] < trail_level:
                tc.exit_trade(bar['close'], 'vol_trail')
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                trade_count += 1
                continue

            # Max hold
            if st['bars_held'] >= CFG.max_hold_bars:
                tc.exit_trade(bar['close'], 'max_hold')
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                trade_count += 1
                continue

        # Update cooldown
        if st['cooldown'] > 0:
            st['cooldown'] -= 1

        # Check for new entry
        if st['in_trade'] or not st['cascade_active']:
            continue
        if st['cooldown'] > 0:
            continue
        if CFG.no_reentry_after_stop and st['stopped_in_window']:
            continue

        candles = sym_candles[sym]
        n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
        if len(candles) < n_needed:
            continue

        closes = np.array([c['close'] for c in candles])
        highs = np.array([c['high'] for c in candles])
        lows = np.array([c['low'] for c in candles])
        volumes = np.array([c['volume'] for c in candles])

        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            continue

        ema = _ema(closes, CFG.ema_period)

        if len(highs) > CFG.range_lookback:
            range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, CFG.z_lookback)

        taker_buys = np.array([c['taker_buy_volume'] for c in candles])
        has_taker = taker_buys[-1] > 0
        if has_taker:
            taker_sells = volumes - taker_buys
            totals = taker_buys + taker_sells
            safe_totals = np.where(totals > 0, totals, 1.0)
            imb_raw = (taker_buys - taker_sells) / safe_totals
            imb_z = _z_score(imb_raw, CFG.z_lookback)
        else:
            imb_z = 0.0

        candle_range = bar['high'] - bar['low']
        candle_body = abs(bar['close'] - bar['open'])
        body_strength = candle_body / candle_range if candle_range > 0 else 0
        bar_return_pct = ((bar['close'] - bar['open']) / bar['open'] * 100) if bar['open'] > 0 else 0

        confirmations = {
            "breakout": bar['close'] > range_high,
            "imb": imb_z > CFG.imb_z_threshold if has_taker else False,
            "vol": vol_z > CFG.vol_z_threshold,
            "body": body_strength > CFG.body_strength_min,
            "impulse": bar_return_pct > CFG.impulse_min_pct,
            "momentum": bar['close'] > ema,
        }
        confirm_count = sum(1 for v in confirmations.values() if v)

        if confirm_count < CFG.min_confirmations:
            continue

        # Entry!
        entry_price = bar['close']
        stop_distance = atr * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance

        # Compute aggression
        aggression = compute_aggression(candles)

        st['in_trade'] = True
        st['entry_price'] = entry_price
        st['stop_price'] = stop_price
        st['atr'] = atr
        st['bars_held'] = 0
        st['best_price'] = entry_price
        st['partial_taken'] = False
        st['aggression'] = aggression

        current_trades[sym] = TradeLifecycle(
            sym, entry_price, stop_price, atr, aggression, bar['close_time']
        )

    # Close any open trades
    for sym, tc in current_trades.items():
        if tc.exit_price is None:
            tc.exit_trade(bar['close'], 'end_of_data')
            lifecycles.append(tc)

    print(f"\nTotal trades: {len(lifecycles)}")
    return lifecycles

# ═══════════════════════════════════════════════════════════════════════
# Analysis & Output
# ═══════════════════════════════════════════════════════════════════════

def percentile(arr, p):
    if not arr:
        return 0
    # Filter out None values
    clean = [x for x in arr if x is not None]
    if not clean:
        return 0
    sorted_arr = sorted(clean)
    k = (len(sorted_arr) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_arr):
        return sorted_arr[-1]
    return sorted_arr[f] * (c - k) + sorted_arr[c] * (k - f)

def analyze_by_decile(lifecycles):
    """Analyze trade lifecycle behavior by aggression decile."""
    if not lifecycles:
        print("No lifecycles to analyze!")
        return

    # Assign deciles
    lifecycles.sort(key=lambda x: x.aggression)
    n = len(lifecycles)
    decile_size = n / 10.0
    for i, lc in enumerate(lifecycles):
        lc.decile = min(int(i / decile_size) + 1, 10)

    # Group by decile
    by_decile = defaultdict(list)
    for lc in lifecycles:
        by_decile[lc.decile].append(lc)

    all_deciles = sorted(by_decile.keys())

    print("\n" + "=" * 110)
    print("TRADE LIFECYCLE BEHAVIOR BY AGGRESSION DECILE")
    print("=" * 110)

    # Decile summary
    print(f"\n{'Decile':<8} {'N':>5} {'Agg Range':>18} {'Avg Agg':>10}")
    print("-" * 45)
    for d in all_deciles:
        data = by_decile[d]
        aggs = [lc.aggression for lc in data]
        print(f"  D{d:<5} {len(data):>5} {min(aggs):.1f} - {max(aggs):.1f}      {np.mean(aggs):.1f}")

    # Metrics table
    print(f"\n{'Metric':<30}", end='')
    for d in all_deciles:
        print(f"{'D' + str(d):>10}", end='')
    print(f"{'Trend':>10}")
    print("-" * (30 + 10 * len(all_deciles) + 10))

    metrics = [
        ('bars_held', 'Avg Bars Held', False),
        ('realized_r', 'Avg Realized R', False),
        ('max_mfe_r', 'Avg Max MFE (R)', False),
        ('mfe_capture', 'MFE Capture %', False),
        ('time_to_1r', 'Med Bars to 1R', True),
        ('time_to_2r', 'Med Bars to 2R', True),
        ('time_to_max_mfe', 'Med Bars to Max MFE', True),
        ('expansion_bars', 'Avg Expansion Bars', False),
        ('consolidation_bars', 'Avg Consolidation Bars', False),
        ('max_consecutive_red', 'Med Consec Red', True),
        ('green_bars', 'Avg Green Bars', False),
        ('red_bars', 'Avg Red Bars', False),
        ('volume_ratio', 'Peak/Avg Volume', False),
    ]

    for metric, label, is_median in metrics:
        print(f"{label:<30}", end='')
        vals = []
        for d in all_deciles:
            data = by_decile[d]
            v = []
            for lc in data:
                val = getattr(lc, metric)
                if val is not None:
                    v.append(val)
            if is_median:
                val = percentile(v, 50)
            else:
                val = np.mean(v) if v else 0
            vals.append(val)
            if metric == 'mfe_capture':
                print(f"{val:>9.1%}", end='')
            elif metric in ['realized_r', 'max_mfe_r', 'volume_ratio']:
                print(f"{val:>10.2f}", end='')
            else:
                print(f"{val:>10.1f}", end='')

        first_half = np.mean(vals[:5])
        second_half = np.mean(vals[5:])
        if second_half > first_half * 1.05:
            trend = "  ↑"
        elif second_half < first_half * 0.95:
            trend = "  ↓"
        else:
            trend = "  →"
        print(f"{trend:>10}")

    # Exit reason breakdown
    print(f"\n{'Exit Reason Breakdown by Decile':^110}")
    print("-" * 110)
    all_reasons = set()
    for lc in lifecycles:
        if lc.exit_reason:
            all_reasons.add(lc.exit_reason)
    all_reasons = sorted(all_reasons)

    print(f"{'Exit Reason':<20}", end='')
    for d in all_deciles:
        print(f"{'D' + str(d):>10}", end='')
    print()
    print("-" * 110)
    for reason in all_reasons:
        print(f"{reason:<20}", end='')
        for d in all_deciles:
            data = by_decile[d]
            pct = sum(1 for lc in data if lc.exit_reason == reason) / len(data)
            print(f"{pct:>9.1%}", end='')
        print()

    # MFE distribution
    print(f"\n{'MFE Distribution by Decile':^110}")
    print("-" * 110)
    mfe_thresholds = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    print(f"{'MFE Threshold':<20}", end='')
    for d in all_deciles:
        print(f"{'D' + str(d):>10}", end='')
    print()
    print("-" * 110)
    for thresh in mfe_thresholds:
        print(f"{'>' + str(thresh) + 'R':<20}", end='')
        for d in all_deciles:
            data = by_decile[d]
            pct = sum(1 for lc in data if lc.max_mfe_r >= thresh) / len(data)
            print(f"{pct:>9.1%}", end='')
        print()

    # Archetype signatures
    print(f"\n{'ARCHETYPE SIGNATURES':^110}")
    print("=" * 110)

    for d in all_deciles:
        data = by_decile[d]
        n_d = len(data)
        aggs = [lc.aggression for lc in data]

        avg_duration = np.mean([lc.bars_held for lc in data])
        avg_mfe = np.mean([lc.max_mfe_r for lc in data])
        avg_capture = np.mean([lc.mfe_capture for lc in data if lc.mfe_capture is not None])
        pct_above_2r = sum(1 for lc in data if lc.max_mfe_r >= 2.0) / n_d
        pct_above_3r = sum(1 for lc in data if lc.max_mfe_r >= 3.0) / n_d
        avg_expansion = np.mean([lc.expansion_bars for lc in data])
        avg_consolidation = np.mean([lc.consolidation_bars for lc in data])
        median_time_to_max = percentile([lc.time_to_max_mfe for lc in data], 50)
        stop_pct = sum(1 for lc in data if lc.exit_reason == 'stop_loss') / n_d
        decay_pct = sum(1 for lc in data if lc.exit_reason == 'expansion_decay') / n_d
        trail_pct = sum(1 for lc in data if lc.exit_reason == 'vol_trail') / n_d
        max_hold_pct = sum(1 for lc in data if lc.exit_reason == 'max_hold') / n_d

        print(f"\n  D{d} ({n_d} trades) | Aggression {min(aggs):.0f}-{max(aggs):.0f}")
        print(f"  {'─' * 80}")
        print(f"  Duration:     {avg_duration:.0f} bars avg | {median_time_to_max:.0f} bars to max MFE")
        print(f"  MFE:          {avg_mfe:.2f}R avg | {pct_above_2r:.0%} reach 2R+ | {pct_above_3r:.0%} reach 3R+")
        print(f"  Capture:      {avg_capture:.1%} of max MFE realized")
        print(f"  Vol Profile:  {avg_expansion:.1f} expansion bars | {avg_consolidation:.1f} consolidation bars")
        print(f"  Exits:        {stop_pct:.0%} stop | {decay_pct:.0%} decay | {trail_pct:.0%} trail | {max_hold_pct:.0%} max_hold")

        # Archetype classification
        if avg_duration < 15 and avg_mfe < 1.5:
            archetype = "QUICK FLUSH — fast expansion, limited follow-through"
        elif avg_duration > 40 and pct_above_2r > 0.5 and avg_capture > 0.6:
            archetype = "STRUCTURAL SQUEEZE — persistent, deep continuation"
        elif avg_mfe > 2.5 and avg_capture < 0.4:
            archetype = "CLIMAX EXHAUSTION — large MFE but poor capture, reversal-prone"
        elif avg_consolidation > avg_expansion and avg_mfe > 1.5:
            archetype = "GRINDER — slow consolidation then continuation"
        elif stop_pct > 0.3:
            archetype = "NOISY — high stop rate, unreliable"
        elif max_hold_pct > 0.2:
            archetype = "SLOW BURN — frequently hits max hold, needs longer windows"
        else:
            archetype = "STANDARD — moderate duration and capture"
        print(f"  Archetype:    {archetype}")

    # Save raw data
    output = []
    for lc in lifecycles:
        d = lc.to_dict()
        d['decile'] = getattr(lc, 'decile', 0)
        output.append(d)

    with open('backtest_output/trade_lifecycle_by_decile.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nSaved: backtest_output/trade_lifecycle_by_decile.json")

def main():
    lifecycles = run_lifecycle_analysis()
    analyze_by_decile(lifecycles)

if __name__ == '__main__':
    main()
