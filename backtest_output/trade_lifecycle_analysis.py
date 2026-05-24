"""
Trade Lifecycle Behavior Analysis by Aggression Decile
Uses only numpy + sqlite3 (no pandas dependency).
"""
import sqlite3
import numpy as np
import json
from datetime import datetime, timedelta

KLINES_DB = 'backtest_data/klines_5m.db'
LIQ_DB = 'backtest_data/coinalyze_liq.db'
DAILY_DB = 'backtest_data/daily_closes.db'

def load_klines():
    conn = sqlite3.connect(KLINES_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume FROM klines ORDER BY symbol, open_time")
    rows = cursor.fetchall()
    conn.close()

    data = {}
    for row in rows:
        symbol, open_time, close_time, o, h, l, c, v, tbv = row
        if symbol not in data:
            data[symbol] = []
        data[symbol].append({
            'timestamp': close_time, 'open_time': open_time, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'taker_buy_volume': tbv
        })
    return data

def load_liq():
    conn = sqlite3.connect(LIQ_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, timestamp, long_liq, short_liq FROM liquidation_history")
    rows = cursor.fetchall()
    conn.close()

    data = {}
    for row in rows:
        symbol, ts, ll, sl = row
        if symbol not in data:
            data[symbol] = []
        data[symbol].append({'timestamp': ts, 'long_liq': float(ll), 'short_liq': float(sl)})
    return data

def load_daily():
    # daily_closes is in the coinalyze DB
    conn = sqlite3.connect(LIQ_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, date, close FROM daily_closes")
    rows = cursor.fetchall()
    conn.close()

    data = {}
    for row in rows:
        symbol, date, close = row
        if symbol not in data:
            data[symbol] = []
        data[symbol].append({'date': str(date), 'close': float(close)})
    return data

def compute_aggression(sym_bars, bar_idx, lookback=20):
    """Compute aggression score for a specific bar."""
    if bar_idx < lookback or bar_idx >= len(sym_bars):
        return None

    window = sym_bars[bar_idx - lookback: bar_idx + 1]
    n = len(window)
    if n < lookback + 1:
        return None

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
    scores['range_expansion_pctile'] = np.mean(current_range > ranges)

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
    body = abs(c[-1] - o[-1])
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
    range_z = (current_range - np.mean(ranges)) / (np.std(ranges) + 1e-10)
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

def find_signals(klines, liq_data, daily_data):
    """Find all signal bars."""
    signals = []

    # Build symbol mapping: klines name -> liq name
    # klines: SOLUSDT, liq: SOLUSDT_PERP.A
    liq_symbols = set()
    for symbol in liq_data:
        base = symbol.replace('_PERP.A', '')
        liq_symbols.add(base)

    for symbol in klines:
        if symbol == 'BTCUSDT':
            continue

        sym_bars = klines[symbol]
        # Map to liq symbol name
        liq_symbol = symbol + '_PERP.A'
        sym_liq = liq_data.get(liq_symbol, [])

        if len(sym_bars) < 50:
            continue

        n = len(sym_bars)
        closes = np.array([b['close'] for b in sym_bars])
        highs = np.array([b['high'] for b in sym_bars])
        lows = np.array([b['low'] for b in sym_bars])
        volumes = np.array([b['volume'] for b in sym_bars])
        timestamps = [b['timestamp'] for b in sym_bars]

        # ATR
        ranges = highs - lows
        atr_20 = np.zeros(n)
        for i in range(20, n):
            atr_20[i] = np.mean(ranges[i-20:i])

        # Cascade: 3-bar volume > 3x average
        avg_vol_20 = np.zeros(n)
        for i in range(20, n):
            avg_vol_20[i] = np.mean(volumes[i-20:i])

        # Daily closes lookup
        daily_closes = daily_data.get(symbol, [])
        daily_dates = [d['date'] for d in daily_closes]
        daily_prices = [d['close'] for d in daily_closes]

        for i in range(25, n - 2):
            if avg_vol_20[i] <= 0:
                continue
            v3 = volumes[i] + volumes[i+1] + volumes[i+2]
            if v3 <= 3 * avg_vol_20[i] * 3:
                continue

            ts = timestamps[i]

            # ret_5d check
            try:
                ts_dt = datetime.utcfromtimestamp(ts / 1000.0)
                date_5d = ts_dt - timedelta(days=7)
                date_5d_str = date_5d.strftime('%Y-%m-%d')

                closest_idx = None
                for di in range(len(daily_dates) - 1, -1, -1):
                    if daily_dates[di] <= date_5d_str:
                        closest_idx = di
                        break

                if closest_idx is None:
                    continue
                close_5d_ago = daily_prices[closest_idx]
                ret_5d = (closes[i] - close_5d_ago) / close_5d_ago * 100
                if ret_5d < -5:
                    continue
            except:
                continue

            # Liquidation spike check
            try:
                ts_dt = datetime.utcfromtimestamp(ts / 1000.0)
                window_start = int((ts_dt - timedelta(minutes=30)).timestamp() * 1000)
                window_end = int((ts_dt + timedelta(minutes=5)).timestamp() * 1000)

                liq_sum = 0.0
                for lq in sym_liq:
                    if window_start <= lq['timestamp'] <= window_end:
                        liq_sum += lq['long_liq'] + lq['short_liq']

                if liq_sum < 50000:
                    continue
            except:
                continue

            # Compute aggression
            agg = compute_aggression(sym_bars, i)
            if agg is None:
                continue

            signals.append({
                'symbol': symbol,
                'bar_idx': i,
                'timestamp': ts,
                'close': closes[i],
                'atr_20': atr_20[i],
                'aggression': agg,
                'ret_5d': ret_5d,
                'liq_sum': liq_sum,
            })

    return signals

def simulate_trade_lifecycle(signal, klines, max_bars=200):
    """Simulate a single trade and record lifecycle metrics."""
    sym_bars = klines[signal['symbol']]
    entry_idx = signal['bar_idx']
    entry_price = signal['close']
    atr = signal['atr_20']

    if atr <= 0 or entry_idx >= len(sym_bars) - 1:
        return None

    stop_loss = entry_price - 1.5 * atr

    max_price = entry_price
    max_mfe_r = 0.0
    time_to_1r = None
    time_to_2r = None
    time_to_max_mfe = None
    bars_above_1r = 0
    bars_above_2r = 0
    bars_above_3r = 0
    consecutive_red = 0
    max_consecutive_red = 0
    pullback_from_max = 0.0
    expansion_bars = 0
    consolidation_bars = 0
    red_bars = 0
    green_bars = 0
    total_volume = 0.0
    peak_volume = 0.0
    exit_bar = None
    exit_price = None
    exit_reason = None
    bars_held = 0

    for j in range(1, min(max_bars, len(sym_bars) - entry_idx)):
        idx = entry_idx + j
        if idx >= len(sym_bars):
            break

        bar = sym_bars[idx]
        h = bar['high']
        l = bar['low']
        c = bar['close']
        o = bar['open']
        v = bar['volume']

        bars_held = j
        total_volume += v
        peak_volume = max(peak_volume, v)

        bar_range = h - l
        if bar_range > 1.5 * atr:
            expansion_bars += 1
        elif bar_range < 0.5 * atr:
            consolidation_bars += 1

        if c > o:
            green_bars += 1
            consecutive_red = 0
        else:
            red_bars += 1
            consecutive_red += 1
            max_consecutive_red = max(max_consecutive_red, consecutive_red)

        max_price = max(max_price, h)
        mfe_r = (max_price - entry_price) / atr

        if mfe_r > max_mfe_r:
            max_mfe_r = mfe_r
            time_to_max_mfe = j
            pullback_from_max = 0.0
        else:
            pullback_from_max = (max_price - c) / atr

        r_1r = 1.0 * atr
        r_2r = 2.0 * atr
        r_3r = 3.0 * atr

        if c >= entry_price + r_1r and time_to_1r is None:
            time_to_1r = j
        if c >= entry_price + r_2r and time_to_2r is None:
            time_to_2r = j
        if c >= entry_price + r_1r:
            bars_above_1r += 1
        if c >= entry_price + r_2r:
            bars_above_2r += 1
        if c >= entry_price + r_3r:
            bars_above_3r += 1

        # Stop loss
        if l <= stop_loss:
            exit_bar = j
            exit_price = stop_loss
            exit_reason = 'stop_loss'
            break

        # Exit V2: expansion decay (revised)
        if j >= 12 and max_mfe_r >= 1.5 and pullback_from_max >= 0.6 and consecutive_red >= 3:
            exit_bar = j
            exit_price = c
            exit_reason = 'expansion_decay'
            break

        # Vol trail
        trail_level = max_price - 2.0 * atr
        if max_mfe_r >= 2.0 and c < trail_level:
            exit_bar = j
            exit_price = c
            exit_reason = 'vol_trail'
            break

        # Max hold
        if j >= max_bars - 1:
            exit_bar = j
            exit_price = c
            exit_reason = 'max_hold'
            break

    if exit_bar is None:
        exit_bar = bars_held
        last_idx = min(entry_idx + bars_held, len(sym_bars) - 1)
        exit_price = sym_bars[last_idx]['close']
        exit_reason = 'end_of_data'

    realized_r = (exit_price - entry_price) / atr
    mfe_capture = realized_r / max_mfe_r if max_mfe_r > 0 else 0.0
    avg_volume = total_volume / max(bars_held, 1)
    volume_ratio = peak_volume / (avg_volume + 1e-10)

    return {
        'symbol': signal['symbol'],
        'entry_time': signal['timestamp'],
        'entry_price': entry_price,
        'aggression': signal['aggression'],
        'atr': atr,
        'exit_bar': exit_bar,
        'exit_price': exit_price,
        'exit_reason': exit_reason,
        'bars_held': bars_held,
        'realized_r': realized_r,
        'max_mfe_r': max_mfe_r,
        'time_to_1r': time_to_1r or bars_held,
        'time_to_2r': time_to_2r or bars_held,
        'time_to_max_mfe': time_to_max_mfe or bars_held,
        'pullbar_at_exit': pullback_from_max,
        'bars_above_1r': bars_above_1r,
        'bars_above_2r': bars_above_2r,
        'bars_above_3r': bars_above_3r,
        'expansion_bars': expansion_bars,
        'consolidation_bars': consolidation_bars,
        'red_bars': red_bars,
        'green_bars': green_bars,
        'max_consecutive_red': max_consecutive_red,
        'mfe_capture': mfe_capture,
        'avg_volume': avg_volume,
        'peak_volume': peak_volume,
        'volume_ratio': volume_ratio,
    }

def percentile(arr, p):
    """Compute percentile."""
    if not arr:
        return 0
    sorted_arr = sorted(arr)
    k = (len(sorted_arr) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_arr):
        return sorted_arr[-1]
    d0 = sorted_arr[f] * (c - k)
    d1 = sorted_arr[c] * (k - f)
    return d0 + d1

def main():
    print("Loading data...")
    klines = load_klines()
    liq_data = load_liq()
    daily_data = load_daily()
    total_bars = sum(len(v) for v in klines.values())
    print(f"Symbols: {len(klines)}, Total bars: {total_bars}, Liq records: {sum(len(v) for v in liq_data.values())}")

    print("\nFinding signals...")
    signals = find_signals(klines, liq_data, daily_data)
    print(f"Found {len(signals)} signals")

    if not signals:
        print("No signals found!")
        return

    # Assign deciles by aggression
    signals.sort(key=lambda x: x['aggression'])
    n = len(signals)
    decile_size = n / 10.0
    for i, s in enumerate(signals):
        s['decile'] = min(int(i / decile_size) + 1, 10)

    deciles = {}
    for s in signals:
        d = s['decile']
        if d not in deciles:
            deciles[d] = []
        deciles[d].append(s)

    print(f"\nAggression range: {signals[0]['aggression']:.1f} - {signals[-1]['aggression']:.1f}")
    print(f"Decile distribution:")
    for d in sorted(deciles.keys()):
        sigs = deciles[d]
        aggs = [s['aggression'] for s in sigs]
        print(f"  D{d}: {len(sigs)} trades, agg {min(aggs):.1f}-{max(aggs):.1f}")

    print("\nSimulating trade lifecycles...")
    lifecycles = []
    for i, signal in enumerate(signals):
        if i % 50 == 0:
            print(f"  {i}/{len(signals)}...")
        lc = simulate_trade_lifecycle(signal, klines)
        if lc:
            lc['decile'] = signal['decile']
            lifecycles.append(lc)

    print(f"\nSimulated {len(lifecycles)} trade lifecycles")

    # === BEHAVIORAL ANALYSIS BY DECILE ===
    print("\n" + "=" * 110)
    print("TRADE LIFECYCLE BEHAVIOR BY AGGRESSION DECILE")
    print("=" * 110)

    lc_by_decile = {}
    for lc in lifecycles:
        d = lc['decile']
        if d not in lc_by_decile:
            lc_by_decile[d] = []
        lc_by_decile[d].append(lc)

    all_deciles = sorted(lc_by_decile.keys())

    # Header
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
            data = lc_by_decile[d]
            v = [lc[metric] for lc in data]
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

        # Trend arrow
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
        all_reasons.add(lc['exit_reason'])
    all_reasons = sorted(all_reasons)

    print(f"{'Exit Reason':<20}", end='')
    for d in all_deciles:
        print(f"{'D' + str(d):>10}", end='')
    print()
    print("-" * 110)
    for reason in all_reasons:
        print(f"{reason:<20}", end='')
        for d in all_deciles:
            data = lc_by_decile[d]
            pct = sum(1 for lc in data if lc['exit_reason'] == reason) / len(data)
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
            data = lc_by_decile[d]
            pct = sum(1 for lc in data if lc['max_mfe_r'] >= thresh) / len(data)
            print(f"{pct:>9.1%}", end='')
        print()

    # Archetype signatures
    print(f"\n{'ARCHETYPE SIGNATURES':^110}")
    print("=" * 110)

    for d in all_deciles:
        data = lc_by_decile[d]
        n_d = len(data)
        sigs = deciles.get(d, [])
        agg_min = min(s['aggression'] for s in sigs) if sigs else 0
        agg_max = max(s['aggression'] for s in sigs) if sigs else 0

        avg_duration = np.mean([lc['bars_held'] for lc in data])
        avg_mfe = np.mean([lc['max_mfe_r'] for lc in data])
        avg_capture = np.mean([lc['mfe_capture'] for lc in data])
        pct_above_2r = sum(1 for lc in data if lc['max_mfe_r'] >= 2.0) / n_d
        pct_above_3r = sum(1 for lc in data if lc['max_mfe_r'] >= 3.0) / n_d
        avg_expansion = np.mean([lc['expansion_bars'] for lc in data])
        avg_consolidation = np.mean([lc['consolidation_bars'] for lc in data])
        median_time_to_max = percentile([lc['time_to_max_mfe'] for lc in data], 50)
        stop_pct = sum(1 for lc in data if lc['exit_reason'] == 'stop_loss') / n_d
        decay_pct = sum(1 for lc in data if lc['exit_reason'] == 'expansion_decay') / n_d
        trail_pct = sum(1 for lc in data if lc['exit_reason'] == 'vol_trail') / n_d
        max_hold_pct = sum(1 for lc in data if lc['exit_reason'] == 'max_hold') / n_d

        print(f"\n  D{d} ({n_d} trades) | Aggression {agg_min:.0f}-{agg_max:.0f}")
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
    with open('backtest_output/trade_lifecycle_raw.json', 'w') as f:
        # Convert numpy types
        clean = []
        for lc in lifecycles:
            cl = {}
            for k, v in lc.items():
                if isinstance(v, (np.floating, np.integer)):
                    cl[k] = float(v)
                else:
                    cl[k] = v
            clean.append(cl)
        json.dump(clean, f, indent=2, default=str)

    print(f"\nSaved: backtest_output/trade_lifecycle_raw.json")

if __name__ == '__main__':
    main()
