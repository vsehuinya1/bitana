"""
V3 Liq-Cluster Backtest: Baseline vs Aggression-Modified Exits
Two layers, full lifecycle tracking, compound growth + Kelly analysis.
"""
import csv, math, sqlite3, json, numpy as np
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════════════
# CONFIG
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
# EXIT PARAMETERS BY DECILE
# ═══════════════════════════════════════════════════════════════════════

# Format: (trail_atr, decay_bars_min, decay_mfe_min, decay_pullback_atr, decay_consec_red, max_hold)
EXIT_PARAMS = {
    1:  (3.0, 99999, 999, 999, 99, 500),   # D1: wide trail, no decay, long hold
    2:  (3.0, 99999, 999, 999, 99, 500),   # D2: wide trail, no decay, long hold
    3:  (2.0, 12, 1.5, 0.6, 3, 288),       # D3: standard
    4:  (2.0, 12, 1.5, 0.6, 3, 288),       # D4: standard
    5:  (2.0, 12, 1.5, 0.6, 3, 288),       # D5: standard
    6:  (2.0, 12, 1.5, 0.6, 3, 288),       # D6: standard
    7:  (2.5, 20, 2.0, 0.8, 4, 358),       # D7: wide trail, suppressed decay
    8:  (2.5, 20, 2.0, 0.8, 4, 358),       # D8: wide trail, suppressed decay
    9:  (1.5, 6,  1.0, 0.4, 2, 100),       # D9: tight trail, aggressive decay
    10: (1.5, 6,  1.0, 0.4, 2, 100),       # D10: tight trail, aggressive decay
}

# ═══════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════

def load_all_data():
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

    conn = sqlite3.connect('backtest_data/coinalyze_liq.db')
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, timestamp, long_liq, short_liq FROM liquidation_history")
    liq_rows = cursor.fetchall()
    cursor.execute("SELECT symbol, date, close FROM daily_closes")
    daily_rows = cursor.fetchall()
    conn.close()

    daily_liq = defaultdict(list)
    for symbol, ts, ll, sl in liq_rows:
        base = symbol.replace('_PERP.A', '')
        dt = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
        daily_liq[base].append({
            'date': dt, 'long_liq': float(ll), 'short_liq': float(sl),
            'total_liq': float(ll) + float(sl), 'close': 0.0,
        })
    for sym in daily_liq:
        daily_liq[sym].sort(key=lambda x: x['date'])

    daily_closes = defaultdict(dict)
    for symbol, date, close in daily_rows:
        daily_closes[symbol][str(date)] = float(close)

    for sym in daily_liq:
        for row in daily_liq[sym]:
            d = row['date']
            if sym in daily_closes and d in daily_closes[sym]:
                row['close'] = daily_closes[sym][d]

    return klines, daily_liq, daily_closes

# ═══════════════════════════════════════════════════════════════════════
# ENGINE COMPONENTS
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
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
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
        imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total if total > 0 else 0.0
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

def compute_aggression(candles_5m):
    if len(candles_5m) < 25:
        return 0.0
    lookback = 20
    window = candles_5m[-(lookback + 1):]
    c = np.array([w['close'] for w in window])
    h = np.array([w['high'] for w in window])
    l = np.array([w['low'] for w in window])
    v = np.array([w['volume'] for w in window])
    o = np.array([w['open'] for w in window])
    scores = {}
    mid = (h + l) / 2
    denom = h - l
    denom[denom == 0] = 1e-10
    taker_imb = (c - mid) / denom
    recent_imb = taker_imb[-1]
    hist_imb = taker_imb[:-1]
    std_hist = np.std(hist_imb)
    scores['taker_imb_z'] = (recent_imb - np.mean(hist_imb)) / (std_hist + 1e-10)
    diffs = np.diff(c)
    sign = np.sign(diffs[-1])
    persistence = 0
    for d in reversed(diffs):
        if np.sign(d) == sign:
            persistence += 1
        else:
            break
    scores['delta_persistence'] = persistence / lookback
    vol_short = np.mean(v[-5:])
    vol_long = np.mean(v[:-5]) + 1e-10
    scores['oi_acceleration'] = (vol_short - vol_long) / vol_long
    ranges = h[:-1] - l[:-1]
    current_range = h[-1] - l[-1]
    scores['range_expansion_pctile'] = np.mean(current_range > ranges) if len(ranges) > 0 else 0.5
    vol_3 = np.sum(v[-3:])
    vol_10 = np.sum(v[-10:]) + 1e-10
    scores['volume_concentration'] = vol_3 / vol_10
    range_hl = h[-1] - l[-1]
    if range_hl > 0:
        scores['clv'] = (c[-1] - l[-1]) / range_hl * 2 - 1
    else:
        scores['clv'] = 0
    upper_wick = h[-1] - max(c[-1], o[-1])
    lower_wick = min(c[-1], o[-1]) - l[-1]
    total_range = h[-1] - l[-1]
    if total_range > 0:
        scores['wick_rejection'] = (lower_wick / total_range) if c[-1] > o[-1] else (upper_wick / total_range)
    else:
        scores['wick_rejection'] = 0
    avg_range = np.mean(ranges) + 1e-10
    scores['spread_expansion'] = (current_range - avg_range) / avg_range
    scores['velocity'] = (c[-1] - c[0]) / (np.mean(ranges) * np.sqrt(lookback) + 1e-10)
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
    return max(0, min(100, (composite + 2) / 4 * 100))

# ═══════════════════════════════════════════════════════════════════════
# REPLAY ENGINE
# ═══════════════════════════════════════════════════════════════════════

def run_backtest(klines, daily_liq, daily_closes, use_modified_exits=False, decile_boundaries=None):
    """
    Replay the full engine.
    If use_modified_exits=True, apply decile-specific exit parameters.
    decile_boundaries: dict mapping decile -> {min, max} aggression scores.
    Returns list of trade dicts.
    """
    daily_liq_by_date = {}
    for sym in daily_liq:
        daily_liq_by_date[sym] = {}
        for row in daily_liq[sym]:
            daily_liq_by_date[sym][row['date']] = row

    states = {}
    cascade_trackers = {}
    sym_candles = defaultdict(list)
    current_trades = {}  # symbol -> {entry_price, stop_price, atr, bars_held, best_price, consecutive_red, decile, aggression}
    lifecycles = []
    last_date = None

    # Build global timeline
    all_bars = []
    for sym in ALL_SYMBOLS + [BTC_SYMBOL]:
        if sym not in klines:
            continue
        for bar in klines[sym]:
            all_bars.append((sym, bar))
    all_bars.sort(key=lambda x: x[1]['close_time'])

    for idx, (sym, bar) in enumerate(all_bars):
        if sym not in states:
            states[sym] = {
                'cooldown': 0, 'stopped_in_window': False,
                'cascade_active': False, 'last_cascade_state': False,
                'in_trade': False, 'entry_price': 0, 'stop_price': 0,
                'atr': 0, 'bars_held': 0, 'best_price': 0,
                'partial_taken': False, 'aggression': 0, 'decile': 0,
            }
            cascade_trackers[sym] = CascadeTracker()

        st = states[sym]
        ct = cascade_trackers[sym]
        sym_candles[sym].append(bar)
        if len(sym_candles[sym]) > 200:
            sym_candles[sym] = sym_candles[sym][-200:]

        # Daily liq update
        bar_date = datetime.utcfromtimestamp(bar['close_time'] / 1000).strftime('%Y-%m-%d')
        if bar_date != last_date:
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
                            'partial_taken': False, 'aggression': 0, 'decile': 0,
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
            c = bar['close']
            o = bar['open']
            h = bar['high']
            l = bar['low']

            # Update consecutive red
            if c <= o:
                tc['consecutive_red'] += 1
            else:
                tc['consecutive_red'] = 0

            best = max(tc['best_price'], h)
            tc['best_price'] = best
            atr = tc['atr']
            entry = tc['entry_price']
            mfe_r = (best - entry) / atr
            pullback = (best - c) / atr

            # Get exit params for this trade's decile
            decile = tc['decile']
            if use_modified_exits:
                trail_atr, decay_bars, decay_mfe, decay_pullback, decay_red, max_hold = EXIT_PARAMS[decile]
            else:
                # Baseline: same for all deciles
                trail_atr, decay_bars, decay_mfe, decay_pullback, decay_red, max_hold = 2.0, 8, 1.5, 0.3, 99, 288

            # Stop loss
            if l <= tc['stop_price']:
                realized_r = (tc['stop_price'] - entry) / atr
                lifecycles.append(_make_trade_record(tc, bar['close_time'], tc['stop_price'], 'stop_loss', realized_r, st['bars_held']))
                del current_trades[sym]
                st['in_trade'] = False
                st['stopped_in_window'] = True
                st['cooldown'] = CFG.cooldown_bars
                continue

            # Expansion decay
            if (st['bars_held'] >= decay_bars and mfe_r >= decay_mfe and
                pullback >= decay_pullback and tc['consecutive_red'] >= decay_red):
                lifecycles.append(_make_trade_record(tc, bar['close_time'], c, 'expansion_decay', (c - entry) / atr, st['bars_held']))
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                continue

            # Vol trail
            trail_level = best - trail_atr * atr
            if mfe_r >= 2.0 and c < trail_level:
                lifecycles.append(_make_trade_record(tc, bar['close_time'], c, 'vol_trail', (c - entry) / atr, st['bars_held']))
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                continue

            # Max hold
            if st['bars_held'] >= max_hold:
                lifecycles.append(_make_trade_record(tc, bar['close_time'], c, 'max_hold', (c - entry) / atr, st['bars_held']))
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                continue

        # Cooldown
        if st['cooldown'] > 0:
            st['cooldown'] -= 1

        # Entry check
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

        closes = np.array([cnd['close'] for cnd in candles])
        highs = np.array([cnd['high'] for cnd in candles])
        lows = np.array([cnd['low'] for cnd in candles])
        volumes = np.array([cnd['volume'] for cnd in candles])

        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            continue

        ema = _ema(closes, CFG.ema_period)
        if len(highs) > CFG.range_lookback:
            range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, CFG.z_lookback)
        taker_buys = np.array([cnd['taker_buy_volume'] for cnd in candles])
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

        entry_price = bar['close']
        stop_distance = atr * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance
        aggression = compute_aggression(candles)

        st['in_trade'] = True
        st['entry_price'] = entry_price
        st['stop_price'] = stop_price
        st['atr'] = atr
        st['bars_held'] = 0
        st['best_price'] = entry_price
        st['aggression'] = aggression

        current_trades[sym] = {
            'symbol': sym,
            'entry_time': bar['close_time'],
            'entry_price': entry_price,
            'stop_price': stop_price,
            'atr': atr,
            'aggression': aggression,
            'decile': score_to_decile(aggression, decile_boundaries),
            'best_price': entry_price,
            'consecutive_red': 0,
            'confirmations': confirm_count,
        }

    # Close any open trades
    for sym, tc in current_trades.items():
        if sym in klines and len(klines[sym]) > 0:
            last_bar = klines[sym][-1]
            realized_r = (last_bar['close'] - tc['entry_price']) / tc['atr']
            lifecycles.append(_make_trade_record(tc, last_bar['close_time'], last_bar['close'], 'end_of_data', realized_r, 0))

    return lifecycles

def _make_trade_record(tc, exit_time, exit_price, exit_reason, realized_r, bars_held):
    mfe_r = (tc['best_price'] - tc['entry_price']) / tc['atr']
    return {
        'symbol': tc['symbol'],
        'entry_time': tc['entry_time'],
        'exit_time': exit_time,
        'entry_price': tc['entry_price'],
        'exit_price': exit_price,
        'atr': tc['atr'],
        'aggression': tc['aggression'],
        'decile': tc['decile'],
        'exit_reason': exit_reason,
        'realized_r': realized_r,
        'max_mfe_r': mfe_r,
        'mfe_capture': realized_r / mfe_r if mfe_r > 0 else 0,
        'bars_held': bars_held,
        'confirmations': tc['confirmations'],
    }

def assign_deciles(trades):
    """Assign deciles by aggression score ranking. Returns decile boundaries."""
    if not trades:
        return trades, {}
    trades_sorted = sorted(trades, key=lambda x: x['aggression'])
    n = len(trades_sorted)
    decile_size = n / 10.0
    boundaries = {}
    for i, t in enumerate(trades_sorted):
        d = min(int(i / decile_size) + 1, 10)
        t['decile'] = d
        if d not in boundaries:
            boundaries[d] = {'min': t['aggression'], 'max': t['aggression']}
        boundaries[d]['max'] = t['aggression']
    return trades, boundaries

def score_to_decile(score, boundaries):
    """Map an aggression score to a decile using pre-computed boundaries."""
    if not boundaries:
        return 5  # fallback
    for d in range(1, 11):
        if d in boundaries:
            b = boundaries[d]
            if score <= b['max'] + 0.001:
                return d
    return 10

# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

def percentile(arr, p):
    clean = [x for x in arr if x is not None]
    if not clean:
        return 0
    s = sorted(clean)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] * (c - k) + s[c] * (k - f)

def analyze(trades, label):
    if not trades:
        print(f"\n{label}: NO TRADES")
        return {}

    n = len(trades)
    winners = [t for t in trades if t['realized_r'] > 0]
    losers = [t for t in trades if t['realized_r'] <= 0]
    wr = len(winners) / n * 100
    total_r = sum(t['realized_r'] for t in trades)
    avg_r = total_r / n
    pf = sum(t['realized_r'] for t in winners) / abs(sum(t['realized_r'] for t in losers)) if losers else float('inf')

    # Monthly
    monthly = {}
    for t in trades:
        dt = datetime.utcfromtimestamp(t['entry_time'] / 1000)
        m = dt.strftime('%Y-%m')
        if m not in monthly:
            monthly[m] = {'n': 0, 'r': 0}
        monthly[m]['n'] += 1
        monthly[m]['r'] += t['realized_r']

    # Exit reasons
    exit_reasons = {}
    for t in trades:
        r = t['exit_reason']
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # By decile
    by_decile = defaultdict(list)
    for t in trades:
        by_decile[t['decile']].append(t)

    # Streaks
    results = ['W' if t['realized_r'] > 0 else 'L' for t in sorted(trades, key=lambda x: x['entry_time'])]
    max_win_streak = 0
    max_loss_streak = 0
    cur_win = 0
    cur_loss = 0
    for r in results:
        if r == 'W':
            cur_win += 1
            cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    # Compound growth from $100
    equity = 100.0
    peak_equity = 100.0
    max_dd = 0.0
    equity_curve = [100.0]
    for t in sorted(trades, key=lambda x: x['entry_time']):
        # Risk 2% of current equity per trade
        risk_usd = equity * 0.02
        stop_dist = t['atr'] * CFG.initial_stop_atr
        if stop_dist > 0:
            pnl = risk_usd * t['realized_r']  # R multiples × risk
        else:
            pnl = 0
        equity += pnl
        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        max_dd = max(max_dd, dd)

    final_equity = equity
    total_roi = (final_equity - 100) / 100 * 100

    # Kelly fraction
    if winners and losers:
        avg_win = sum(t['realized_r'] for t in winners) / len(winners)
        avg_loss = abs(sum(t['realized_r'] for t in losers) / len(losers))
        win_prob = len(winners) / n
        kelly = (win_prob * avg_win - (1 - win_prob) * avg_loss) / avg_win if avg_win > 0 else 0
        kelly = max(0, kelly)
    else:
        kelly = 0
        avg_win = 0
        avg_loss = 0

    # Print
    print(f"\n{'='*100}")
    print(f"  {label}")
    print(f"{'='*100}")
    print(f"  Trades: {n} | WR: {wr:.1f}% | Total R: {total_r:+.1f} | Avg R: {avg_r:+.3f} | PF: {pf:.2f}")
    print(f"  Final Equity ($100 start): ${final_equity:,.2f} | ROI: {total_roi:+.1f}%")
    print(f"  Max Drawdown: {max_dd:.1f}% | Max Win Streak: {max_win_streak} | Max Loss Streak: {max_loss_streak}")
    print(f"  Kelly Fraction: {kelly:.1%} (half-Kelly: {kelly/2:.1%})")
    if winners and losers:
        print(f"  Avg Win: {avg_win:+.2f}R | Avg Loss: {-avg_loss:+.2f}R | Win/Loss Ratio: {avg_win/avg_loss:.2f}")

    print(f"\n  Monthly:")
    for m in sorted(monthly.keys()):
        mm = monthly[m]
        print(f"    {m}: {mm['n']} trades, {mm['r']:+.1f}R")

    print(f"\n  Exit Reasons:")
    for r, c in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"    {r}: {c} ({c/n*100:.1f}%)")

    print(f"\n  By Decile:")
    print(f"  {'Dec':>4} {'N':>4} {'WR%':>6} {'Avg R':>7} {'MFE':>7} {'Capture':>8} {'Stops%':>7} {'Trail%':>7} {'Decay%':>7}")
    print(f"  {'-'*60}")
    for d in sorted(by_decile.keys()):
        dt = by_decile[d]
        dn = len(dt)
        dw = sum(1 for t in dt if t['realized_r'] > 0)
        dwr = dw / dn * 100
        davg = sum(t['realized_r'] for t in dt) / dn
        dmfe = sum(t['max_mfe_r'] for t in dt) / dn
        dcapt = sum(t['mfe_capture'] for t in dt if t['mfe_capture'] is not None) / dn
        dstops = sum(1 for t in dt if t['exit_reason'] == 'stop_loss') / dn * 100
        dtrail = sum(1 for t in dt if t['exit_reason'] == 'vol_trail') / dn * 100
        ddecay = sum(1 for t in dt if t['exit_reason'] == 'expansion_decay') / dn * 100
        print(f"  D{d:>3} {dn:>4} {dwr:>5.1f}% {davg:>+7.2f} {dmfe:>6.2f}R {dcapt:>7.1%} {dstops:>6.1f}% {dtrail:>6.1f}% {ddecay:>6.1f}%")

    return {
        'n': n, 'wr': wr, 'total_r': total_r, 'pf': pf,
        'final_equity': final_equity, 'roi': total_roi, 'max_dd': max_dd,
        'kelly': kelly, 'max_win_streak': max_win_streak, 'max_loss_streak': max_loss_streak,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'by_decile': by_decile, 'monthly': monthly, 'equity_curve': equity_curve,
    }

def compare(baseline, modified):
    print(f"\n{'='*100}")
    print(f"  COMPARISON: BASELINE vs MODIFIED")
    print(f"{'='*100}")
    print(f"  {'Metric':<30} {'Baseline':>15} {'Modified':>15} {'Delta':>15}")
    print(f"  {'-'*75}")
    metrics = [
        ('Trades', 'n', '{:.0f}'),
        ('Win Rate', 'wr', '{:.1f}%'),
        ('Total R', 'total_r', '{:+.1f}'),
        ('Profit Factor', 'pf', '{:.2f}'),
        ('Final Equity ($100)', 'final_equity', '${:,.2f}'),
        ('ROI', 'roi', '{:+.1f}%'),
        ('Max Drawdown', 'max_dd', '{:.1f}%'),
        ('Kelly Fraction', 'kelly', '{:.1%}'),
        ('Max Win Streak', 'max_win_streak', '{:.0f}'),
        ('Max Loss Streak', 'max_loss_streak', '{:.0f}'),
        ('Avg Win (R)', 'avg_win', '{:+.2f}'),
        ('Avg Loss (R)', 'avg_loss', '{:+.2f}'),
    ]
    for label, key, fmt in metrics:
        bv = baseline.get(key, 0)
        mv = modified.get(key, 0)
        dv = mv - bv
        if 'equity' in key:
            print(f"  {label:<30} {fmt.format(bv):>15} {fmt.format(mv):>15} {dv:>+14.2f}")
        elif 'roi' in key or 'dd' in key:
            print(f"  {label:<30} {fmt.format(bv):>15} {fmt.format(mv):>15} {dv:>+14.1f}")
        elif 'kelly' in key:
            print(f"  {label:<30} {fmt.format(bv):>15} {fmt.format(mv):>15} {dv:>+14.1%}")
        else:
            try:
                print(f"  {label:<30} {fmt.format(bv):>15} {fmt.format(mv):>15} {dv:>+15}")
            except:
                print(f"  {label:<30} {str(bv):>15} {str(mv):>15}")

    # By decile comparison
    print(f"\n  By Decile — Total R:")
    print(f"  {'Decile':>8} {'Baseline R':>12} {'Modified R':>12} {'Delta':>10} {'Baseline WR':>13} {'Modified WR':>13}")
    print(f"  {'-'*70}")
    for d in range(1, 11):
        bdec = baseline.get('by_decile', {}).get(d, [])
        mdec = modified.get('by_decile', {}).get(d, [])
        br = sum(t['realized_r'] for t in bdec) if bdec else 0
        mr = sum(t['realized_r'] for t in mdec) if mdec else 0
        bwr = sum(1 for t in bdec if t['realized_r'] > 0) / len(bdec) * 100 if bdec else 0
        mwr = sum(1 for t in mdec if t['realized_r'] > 0) / len(mdec) * 100 if mdec else 0
        print(f"  D{d:>7} {br:>+12.1f} {mr:>+12.1f} {mr-br:>+10.1f} {bwr:>12.1f}% {mwr:>12.1f}%")

def main():
    print("Loading data...")
    klines, daily_liq, daily_closes = load_all_data()
    print(f"Symbols: {len(klines)}, Total bars: {sum(len(v) for v in klines.values())}")

    # Run baseline first to get decile boundaries
    print("\n[1/2] Running BASELINE backtest...")
    baseline_trades = run_backtest(klines, daily_liq, daily_closes, use_modified_exits=False)
    baseline_trades, decile_boundaries = assign_deciles(baseline_trades)
    print(f"  Baseline trades: {len(baseline_trades)}")
    print(f"  Decile boundaries:")
    for d in sorted(decile_boundaries.keys()):
        b = decile_boundaries[d]
        print(f"    D{d}: {b['min']:.1f} - {b['max']:.1f}")

    # Run modified using baseline decile boundaries
    print("\n[2/2] Running MODIFIED backtest...")
    modified_trades = run_backtest(klines, daily_liq, daily_closes, use_modified_exits=True, decile_boundaries=decile_boundaries)
    modified_trades, _ = assign_deciles(modified_trades)
    print(f"  Modified trades: {len(modified_trades)}")

    # Analyze
    b = analyze(baseline_trades, "BASELINE (Original Exits)")
    m = analyze(modified_trades, "MODIFIED (Aggression-Decile Exits)")

    # Compare
    compare(b, m)

    # Save
    with open('backtest_output/agg_exit_v2_comparison.json', 'w') as f:
        json.dump({
            'baseline': [{k: v for k, v in t.items()} for t in baseline_trades],
            'modified': [{k: v for k, v in t.items()} for t in modified_trades],
            'decile_boundaries': {str(k): v for k, v in decile_boundaries.items()},
        }, f, indent=2, default=str)
    print(f"\nSaved: backtest_output/agg_exit_v2_comparison.json")

if __name__ == '__main__':
    main()
