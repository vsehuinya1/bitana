"""
Backtest cascade filter variations using the original agg_exit_v2_backtest.py infrastructure.
Only the cascade tracker is modified — everything else stays the same.
"""
import csv, json, sqlite3, numpy as np
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import sys, copy

sys.path.insert(0, '/root/bitana/backtest_output')

# Import everything from the original backtest
from agg_exit_v2_backtest import (
    ALL_SYMBOLS, BTC_SYMBOL, CFG, V3Config,
    CascadeTracker, compute_aggression, run_backtest,
    load_all_data, _atr, _ema, _z_score
)

# V4 exit params (7-tuple with struct_lookback)
EXIT_PARAMS_V4 = {
    1:  (3.0, 99999, 999.0, 999.0, 99, 500, 48),
    2:  (3.0, 99999, 999.0, 999.0, 99, 500, 48),
    3:  (2.0, 99999, 999.0, 999.0, 99, 288, 24),
    4:  (2.0, 15, 1.5, 0.6, 3, 288, 12),
    5:  (2.0, 15, 1.5, 0.6, 3, 288, 12),
    6:  (2.0, 12, 1.5, 0.6, 3, 288, 12),
    7:  (2.5, 20, 2.0, 0.8, 4, 358, 18),
    8:  (2.5, 20, 2.0, 0.8, 4, 358, 18),
    9:  (1.5, 8,  1.5, 0.5, 3, 100, 8),
    10: (1.5, 8,  1.5, 0.5, 3, 100, 8),
}

# V4 decile boundaries (from full 386-trade baseline)
DECILE_BOUNDARIES = [68.2, 71.4, 73.6, 75.2, 77.2, 78.8, 80.8, 82.5, 84.1, 90.4]

def score_to_decile_v4(score):
    for i, b in enumerate(DECILE_BOUNDARIES):
        if score <= b:
            return i + 1
    return 10


class CascadeTrackerV4(CascadeTracker):
    """Extended cascade tracker with configurable percentile and min strength."""
    def __init__(self, liq_percentile=0.90, min_strength=0.0):
        super().__init__()
        self._liq_percentile = liq_percentile
        self._min_strength = min_strength

    def update(self, daily_row):
        self._liq_history.append(daily_row)
        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0
        liqs = [r["total_liq"] for r in self._liq_history]
        if len(liqs) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0
        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p = np.percentile(lookback, self._liq_percentile * 100)
        if p <= 0:
            return False, 0.0, 0.0, 0.0
        cascade_active = False
        for i in range(CFG.liq_window + 1):
            idx = -(i + 1)
            if abs(idx) <= len(liqs) and liqs[idx] > p:
                cascade_active = True
                break
        strength = liqs[-1] / p if p > 0 else 0

        # NEW: min_strength filter
        if self._min_strength > 0 and strength < self._min_strength:
            cascade_active = False

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


def run_backtest_v4(klines, daily_liq, daily_closes, liq_percentile=0.90, min_strength=0.0):
    """
    Run backtest with V4 cascade tracker, V4 decile-specific exits, and V4 decile boundaries.
    This is a modified version of the original run_backtest() from agg_exit_v2_backtest.py.
    """
    daily_liq_by_date = {}
    for sym in daily_liq:
        daily_liq_by_date[sym] = {}
        for row in daily_liq[sym]:
            daily_liq_by_date[sym][row['date']] = row

    states = {}
    cascade_trackers = {}
    sym_candles = defaultdict(list)
    current_trades = {}
    lifecycles = []
    last_date = None

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
                'cascade_active': False, 'cascade_strength': 0.0,
                'liq_direction_imb': 0.0, 'ret_5d': 0.0,
                'last_cascade_state': False, 'in_trade': False,
                'entry_price': 0, 'risk_per_unit': 0, 'bars_held': 0,
                'partial_taken': False, 'best_price': 0, 'vol_trail': 0,
                'struct_trail': 0, 'mae': 0, 'mfe': 0,
                'aggression': 0, 'decile': 5, 'consecutive_red': 0,
            }
            cascade_trackers[sym] = CascadeTrackerV4(liq_percentile, min_strength)

        st = states[sym]
        candle_dt = datetime.fromtimestamp(bar['close_time'] / 1000, tz=timezone.utc)
        today_str = candle_dt.strftime("%Y-%m-%d")

        # Daily liq update
        if today_str != last_date:
            for s in ALL_SYMBOLS:
                if s in daily_liq_by_date and today_str in daily_liq_by_date[s]:
                    row = daily_liq_by_date[s][today_str]
                    if s in cascade_trackers:
                        ca, stg, imb, r5 = cascade_trackers[s].update(row)
                        states[s]['cascade_active'] = ca
                        states[s]['cascade_strength'] = stg
                        states[s]['liq_direction_imb'] = imb
                        states[s]['ret_5d'] = r5

        if sym == BTC_SYMBOL:
            continue

        last_date = today_str
        sym_candles[sym].append(bar)
        if len(sym_candles[sym]) > 200:
            sym_candles[sym] = sym_candles[sym][-200:]

        # ── Manage existing trade ──
        if sym in current_trades:
            tc = current_trades[sym]
            tc['bars_held'] += 1

            price = bar['close']
            if bar['high'] > tc['best_price']:
                tc['best_price'] = bar['high']

            current_r = (price - tc['entry_price']) / tc['risk_per_unit'] if tc['risk_per_unit'] > 0 else 0
            low_r = (bar['low'] - tc['entry_price']) / tc['risk_per_unit'] if tc['risk_per_unit'] > 0 else 0
            high_r = (bar['high'] - tc['entry_price']) / tc['risk_per_unit'] if tc['risk_per_unit'] > 0 else 0

            if low_r < tc['mae']:
                tc['mae'] = low_r
            if high_r > tc['mfe']:
                tc['mfe'] = high_r

            if price <= bar['open']:
                tc['consecutive_red'] += 1
            else:
                tc['consecutive_red'] = 0

            candles = sym_candles[sym]
            h = np.array([c['high'] for c in candles[-50:]])
            l = np.array([c['low'] for c in candles[-50:]])
            c = np.array([c['close'] for c in candles[-50:]])
            atr = _atr(h, l, c, CFG.atr_period)

            decile = tc['decile']
            trail_atr, decay_bars, decay_mfe, decay_pullback, decay_red, max_hold, struct_lb = EXIT_PARAMS_V4.get(
                decile, (2.0, 8, 1.5, 0.3, 99, 288, 12)
            )

            # Stop loss
            stop_price = tc['entry_price'] - tc['risk_per_unit']
            if bar['low'] <= stop_price:
                tc['exit_reason'] = 'stop_loss'
                tc['exit_price'] = stop_price
                tc['pnl_r'] = (stop_price - tc['entry_price']) / tc['risk_per_unit']
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['stopped_in_window'] = True
                st['cooldown'] = CFG.cooldown_bars
                continue

            # Partial TP
            if not tc['partial_taken'] and high_r >= CFG.partial_r:
                tc['partial_taken'] = True
                tc['rpnl'] += (bar['high'] - tc['entry_price']) * tc['quantity'] * CFG.partial_fraction
                tc['quantity'] *= (1 - CFG.partial_fraction)

            # Vol trail
            new_vol_trail = price - atr * trail_atr
            if new_vol_trail > tc['vol_trail']:
                tc['vol_trail'] = new_vol_trail
            if tc['vol_trail'] > tc['entry_price'] and bar['low'] <= tc['vol_trail']:
                tc['exit_reason'] = 'vol_trail'
                tc['exit_price'] = tc['vol_trail']
                tc['pnl_r'] = (tc['vol_trail'] - tc['entry_price']) / tc['risk_per_unit']
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                continue

            # Struct trail
            if len(candles) >= struct_lb:
                swing_low = min(c['low'] for c in candles[-struct_lb:])
                if swing_low > tc['struct_trail']:
                    tc['struct_trail'] = swing_low
                if tc['struct_trail'] > tc['entry_price'] and bar['low'] <= tc['struct_trail']:
                    tc['exit_reason'] = 'struct_trail'
                    tc['exit_price'] = tc['struct_trail']
                    tc['pnl_r'] = (tc['struct_trail'] - tc['entry_price']) / tc['risk_per_unit']
                    lifecycles.append(tc)
                    del current_trades[sym]
                    st['in_trade'] = False
                    st['cooldown'] = CFG.cooldown_bars
                    continue

            # Expansion decay
            if decay_bars < 99999:
                if tc['bars_held'] >= decay_bars and current_r > 0.5:
                    peak_r = (tc['best_price'] - tc['entry_price']) / tc['risk_per_unit']
                    if peak_r >= decay_mfe and tc['consecutive_red'] >= decay_red:
                        pullback_r = (tc['best_price'] - price) / tc['risk_per_unit']
                        if pullback_r >= decay_pullback:
                            tc['exit_reason'] = 'expansion_decay'
                            tc['exit_price'] = price
                            tc['pnl_r'] = current_r
                            lifecycles.append(tc)
                            del current_trades[sym]
                            st['in_trade'] = False
                            st['cooldown'] = CFG.cooldown_bars
                            continue

            # Time stop
            if tc['bars_held'] >= max_hold:
                tc['exit_reason'] = 'time_stop'
                tc['exit_price'] = price
                tc['pnl_r'] = current_r
                lifecycles.append(tc)
                del current_trades[sym]
                st['in_trade'] = False
                st['cooldown'] = CFG.cooldown_bars
                continue

        # ── Entry logic (same as original) ──
        if st['cooldown'] > 0:
            st['cooldown'] -= 1
            continue
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

        entry_price = bar['close']
        stop_distance = atr * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance
        aggression = compute_aggression(candles)
        decile = score_to_decile_v4(aggression)

        st['in_trade'] = True
        st['entry_price'] = entry_price
        st['cooldown'] = CFG.cooldown_bars

        current_trades[sym] = {
            'symbol': sym,
            'entry_time': bar['close_time'],
            'entry_price': entry_price,
            'stop_price': stop_price,
            'risk_per_unit': stop_distance,
            'bars_held': 0,
            'partial_taken': False,
            'best_price': entry_price,
            'vol_trail': 0,
            'struct_trail': 0,
            'mae': 0,
            'mfe': 0,
            'consecutive_red': 0,
            'aggression': aggression,
            'decile': decile,
            'quantity': 1.0,
            'rpnl': 0.0,
            'cascade_strength': st['cascade_strength'],
            'confirm_count': confirm_count,
        }

    return lifecycles


def analyze_results(trades, label):
    if not trades:
        print(f"{label}: NO TRADES")
        return None

    n = len(trades)
    wins = [t for t in trades if t['pnl_r'] > 0]
    losses = [t for t in trades if t['pnl_r'] <= 0]
    wr = len(wins) / n * 100
    total_r = sum(t['pnl_r'] for t in trades)
    gross_win = sum(t['pnl_r'] for t in wins) if wins else 0
    gross_loss = abs(sum(t['pnl_r'] for t in losses)) if losses else 0.001
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')

    by_decile = defaultdict(list)
    for t in trades:
        by_decile[t['decile']].append(t['pnl_r'])

    by_sym = defaultdict(lambda: {'n': 0, 'r': 0})
    for t in trades:
        by_sym[t['symbol']]['n'] += 1
        by_sym[t['symbol']]['r'] += t['pnl_r']

    near_trades = [t for t in trades if t['symbol'] == 'NEARUSDT']

    print(f"\n{'='*70}")
    print(f"{label}")
    print(f"{'='*70}")
    print(f"Trades: {n}  WR: {wr:.1f}%  Total R: {total_r:+.2f}  PF: {pf:.2f}")
    print(f"NEARUSDT: {len(near_trades)} trades, {sum(t['pnl_r'] for t in near_trades):+.2f}R")

    print(f"\nBy decile:")
    for d in sorted(by_decile.keys()):
        rs = by_decile[d]
        d_wr = len([r for r in rs if r > 0]) / len(rs) * 100
        print(f"  D{d}: {len(rs):3}tr  WR={d_wr:.0f}%  R={sum(rs):+7.2f}  avg={sum(rs)/len(rs):+.3f}")

    print(f"\nTop/bottom symbols:")
    ranked = sorted(by_sym.items(), key=lambda x: -x[1]['r'])
    for sym, s in ranked[:5]:
        print(f"  {sym:15} {s['n']:3}t  {s['r']:+7.2f}R")
    print(f"  ...")
    for sym, s in ranked[-3:]:
        print(f"  {sym:15} {s['n']:3}t  {s['r']:+7.2f}R")

    reasons = defaultdict(lambda: {'n': 0, 'r': 0})
    for t in trades:
        reasons[t.get('exit_reason', 'unknown')]['n'] += 1
        reasons[t.get('exit_reason', 'unknown')]['r'] += t['pnl_r']
    print(f"\nExit reasons:")
    for r, s in sorted(reasons.items(), key=lambda x: -x[1]['n']):
        print(f"  {r:20} {s['n']:3}t  {s['r']:+7.2f}R")

    return {
        'n': n, 'wr': wr, 'total_r': total_r, 'pf': pf,
        'near_n': len(near_trades), 'near_r': sum(t['pnl_r'] for t in near_trades),
        'by_decile': {d: {'n': len(rs), 'r': sum(rs)} for d, rs in by_decile.items()},
        'by_sym': dict(by_sym),
    }


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

print("Loading data...")
klines, daily_liq, daily_closes = load_all_data()
print(f"Loaded: {sum(len(v) for v in klines.values())} klines, {len(daily_liq)} symbols liq data")

configs = [
    (0.90, 0.00, "Baseline (p90, no min_strength)"),
    (0.95, 0.00, "Stricter percentile (p95)"),
    (0.90, 0.10, "Min strength=0.10"),
    (0.95, 0.10, "Both p95 + min_strength=0.10"),
]

results = {}
for percentile, min_str, label in configs:
    print(f"\nRunning: {label}...")
    trades = run_backtest_v4(klines, daily_liq, daily_closes, percentile, min_str)
    results[label] = analyze_results(trades, label)

# Comparison
print(f"\n{'='*70}")
print("COMPARISON SUMMARY")
print(f"{'='*70}")
print(f"{'Config':<45} {'Trades':<8} {'WR%':<8} {'Total R':<10} {'PF':<8} {'NEAR R':<10}")
print("-" * 90)
for percentile, min_str, label in configs:
    r = results[label]
    if r:
        print(f"{label:<45} {r['n']:<8} {r['wr']:<8.1f} {r['total_r']:<+10.2f} {r['pf']:<8.2f} {r['near_r']:<+10.2f}")
