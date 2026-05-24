"""
V4 Backtest Verification
Runs the actual V4 engine (LiqClusterEngineV4) on historical data
and compares against V3 baseline.
"""
import csv, math, sqlite3, json, numpy as np
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.liq_cluster_engine import LiqClusterEngine, CFG as V3_CFG
from engines.liq_cluster_engine_v4 import LiqClusterEngineV4, CFG as V4_CFG, EXIT_PARAMS_BY_DECILE

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
# ADAPTER: Convert dict candles to Candle objects for the engine
# ═══════════════════════════════════════════════════════════════════════

class SimpleCandle:
    """Minimal Candle-compatible object."""
    def __init__(self, d):
        self.open = d['open']
        self.high = d['high']
        self.low = d['low']
        self.close = d['close']
        self.volume = d['volume']
        self.taker_buy_volume = d.get('taker_buy_volume', 0)
        self.open_time = d['open_time']
        self.close_time = d['close_time']

def to_candles(dict_candles):
    return [SimpleCandle(d) for d in dict_candles]


# ═══════════════════════════════════════════════════════════════════════
# REPLAY ENGINE (uses actual LiqClusterEngine / LiqClusterEngineV4)
# ═══════════════════════════════════════════════════════════════════════

ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT",
    "DASHUSDT", "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]

def run_engine(engine, klines, daily_liq, daily_closes, label):
    """Run an engine (V3 or V4) through historical data."""
    daily_liq_by_date = {}
    for sym in daily_liq:
        daily_liq_by_date[sym] = {}
        for row in daily_liq[sym]:
            daily_liq_by_date[sym][row['date']] = row

    sym_candles = defaultdict(list)
    trades = []
    last_date = None

    # Build global timeline
    all_bars = []
    for sym in ALL_SYMBOLS:
        if sym not in klines:
            continue
        for bar in klines[sym]:
            all_bars.append((sym, bar))
    all_bars.sort(key=lambda x: x[1]['close_time'])

    for idx, (sym, bar) in enumerate(all_bars):
        bar_date = datetime.utcfromtimestamp(bar['close_time'] / 1000).strftime('%Y-%m-%d')

        # Daily liq update
        if bar_date != last_date:
            for s in ALL_SYMBOLS:
                if s in daily_liq_by_date and bar_date in daily_liq_by_date[s]:
                    engine.update_daily_liq(s, [daily_liq_by_date[s][bar_date]])
            last_date = bar_date

        # Add candle
        sym_candles[sym].append(bar)
        if len(sym_candles[sym]) > 200:
            sym_candles[sym] = sym_candles[sym][-200:]

        candles = to_candles(sym_candles[sym])

        # Manage existing position
        st = engine._get_state(sym)
        if st.in_trade:
            result = engine.manage_position(sym, candles[-1], candles)
            if result and result['action'] == 'close':
                trades.append({
                    'symbol': sym,
                    'entry_time': int(st.entry_price * 1000),  # placeholder
                    'exit_time': bar['close_time'],
                    'entry_price': st.entry_price,
                    'exit_price': result.get('exit_price', bar['close']),
                    'realized_r': result['r'],
                    'max_mfe_r': result['mfe'] / st.risk_per_unit if st.risk_per_unit > 0 else 0,
                    'exit_reason': result['reason'],
                    'bars_held': result['bars_held'],
                    'aggression': getattr(st, 'aggression_score', 0),
                    'decile': getattr(st, 'decile', 0),
                })
                continue
            # Handle partial — position stays open
            if result and result['action'] == 'partial':
                pass  # position continues

        # Check entry
        sig = engine.evaluate(sym, candles)
        if sig is not None:
            pass  # engine already set in_trade state

    return trades


# ═══════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

def analyze(trades, label):
    n = len(trades)
    if n == 0:
        print(f"\n{label}: NO TRADES")
        return {}

    winners = [t for t in trades if t['realized_r'] > 0]
    losers = [t for t in trades if t['realized_r'] <= 0]
    wr = len(winners) / n * 100
    total_r = sum(t['realized_r'] for t in trades)
    avg_r = total_r / n
    pf = sum(t['realized_r'] for t in winners) / abs(sum(t['realized_r'] for t in losers)) if losers else float('inf')
    avg_win = sum(t['realized_r'] for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t['realized_r'] for t in losers) / len(losers) if losers else 0

    # Compound growth
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    for t in trades:
        equity *= (1 + 0.02 * t['realized_r'])
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    # Kelly
    p = len(winners) / n
    q = 1 - p
    b = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    kelly = max(0, (b * p - q) / b) if b > 0 else 0

    # Streaks
    results = ['W' if t['realized_r'] > 0 else 'L' for t in trades]
    max_win = max_loss = cur_win = cur_loss = 0
    for r in results:
        if r == 'W':
            cur_win += 1
            cur_loss = 0
            max_win = max(max_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_loss = max(max_loss, cur_loss)

    # Monthly
    monthly = {}
    for t in trades:
        dt = datetime.utcfromtimestamp(t['exit_time'] / 1000)
        m = dt.strftime('%Y-%m')
        if m not in monthly:
            monthly[m] = {'n': 0, 'r': 0}
        monthly[m]['n'] += 1
        monthly[m]['r'] += t['realized_r']

    # By decile
    by_decile = defaultdict(list)
    for t in trades:
        by_decile[t.get('decile', 0)].append(t)

    # Exit reasons
    reasons = {}
    for t in trades:
        r = t['exit_reason']
        reasons[r] = reasons.get(r, 0) + 1

    print(f"\n{'='*90}")
    print(f"  {label}")
    print(f"{'='*90}")
    print(f"  Trades: {n} | WR: {wr:.1f}% | Total R: {total_r:+.1f} | PF: {pf:.2f}")
    print(f"  Avg Win: {avg_win:+.2f}R | Avg Loss: {avg_loss:+.2f}R | W/L: {abs(avg_win/avg_loss):.2f}")
    print(f"  Compound $100 → ${equity:,.2f} (ROI: {(equity-100):+.0f}%) | Max DD: {max_dd:.1f}%")
    print(f"  Kelly: {kelly:.1%} | Max Win Streak: {max_win} | Max Loss Streak: {max_loss}")

    print(f"\n  Monthly:")
    for m in sorted(monthly.keys()):
        mm = monthly[m]
        print(f"    {m}: {mm['n']} trades, {mm['r']:+.1f}R")

    print(f"\n  Exit Reasons:")
    for r, c in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"    {r}: {c} ({c/n*100:.1f}%)")

    print(f"\n  By Decile:")
    print(f"  {'Dec':>4} {'N':>4} {'WR%':>6} {'Avg R':>7} {'Stops%':>7} {'Trail%':>7} {'Decay%':>7}")
    print(f"  {'-'*45}")
    for d in sorted(by_decile.keys()):
        dt = by_decile[d]
        dn = len(dt)
        dw = sum(1 for t in dt if t['realized_r'] > 0)
        davg = sum(t['realized_r'] for t in dt) / dn
        dstops = sum(1 for t in dt if t['exit_reason'] == 'stop_loss') / dn * 100
        dtrail = sum(1 for t in dt if t['exit_reason'] == 'vol_trail') / dn * 100
        ddecay = sum(1 for t in dt if t['exit_reason'] == 'expansion_decay') / dn * 100
        print(f"  D{d:>3} {dn:>4} {dw/dn*100:>5.1f}% {davg:>+7.2f} {dstops:>6.1f}% {dtrail:>6.1f}% {ddecay:>6.1f}%")

    return {
        'n': n, 'wr': wr, 'total_r': total_r, 'pf': pf,
        'equity': equity, 'max_dd': max_dd, 'kelly': kelly,
        'max_win_streak': max_win, 'max_loss_streak': max_loss,
        'avg_win': avg_win, 'avg_loss': avg_loss,
    }


def main():
    print("Loading data...")
    klines, daily_liq, daily_closes = load_all_data()
    print(f"Symbols: {len(klines)}, Total bars: {sum(len(v) for v in klines.values())}")

    # V3
    print("\n[1/2] Running V3 engine...")
    v3_engine = LiqClusterEngine()
    v3_trades = run_engine(v3_engine, klines, daily_liq, daily_closes, "V3")
    print(f"  V3 trades: {len(v3_trades)}")

    # V4
    print("\n[2/2] Running V4 engine...")
    v4_engine = LiqClusterEngineV4()
    v4_trades = run_engine(v4_engine, klines, daily_liq, daily_closes, "V4")
    print(f"  V4 trades: {len(v4_trades)}")

    # Analyze
    b = analyze(v3_trades, "V3 BASELINE (LiqClusterEngine)")
    m = analyze(v4_trades, "V4 MODIFIED (LiqClusterEngineV4)")

    # Comparison
    print(f"\n{'='*90}")
    print(f"  COMPARISON: V3 vs V4")
    print(f"{'='*90}")
    print(f"  {'Metric':<30} {'V3':>12} {'V4':>12} {'Delta':>12}")
    print(f"  {'-'*60}")
    for label, key in [('Trades', 'n'), ('Win Rate', 'wr'), ('Total R', 'total_r'),
                        ('Profit Factor', 'pf'), ('Final Equity', 'equity'),
                        ('Max Drawdown', 'max_dd'), ('Kelly', 'kelly'),
                        ('Max Win Streak', 'max_win_streak'), ('Max Loss Streak', 'max_loss_streak')]:
        bv = b.get(key, 0)
        mv = m.get(key, 0)
        dv = mv - bv
        if key == 'kelly':
            print(f"  {label:<30} {bv:>11.1%} {mv:>11.1%} {dv:>+11.1%}")
        elif key in ['wr', 'max_dd']:
            print(f"  {label:<30} {bv:>11.1f}% {mv:>11.1f}% {dv:>+11.1f}%")
        elif key == 'equity':
            print(f"  {label:<30} ${bv:>10,.2f} ${mv:>10,.2f} ${dv:>+10,.2f}")
        elif key == 'total_r':
            print(f"  {label:<30} {bv:>+12.1f} {mv:>+12.1f} {dv:>+12.1f}")
        elif key == 'pf':
            print(f"  {label:<30} {bv:>12.2f} {mv:>12.2f} {dv:>+12.2f}")
        else:
            print(f"  {label:<30} {bv:>12} {mv:>12} {dv:>+12}")

    # Save
    with open('backtest_output/v3_v4_comparison.json', 'w') as f:
        json.dump({
            'v3': [{k: v for k, v in t.items()} for t in v3_trades],
            'v4': [{k: v for k, v in t.items()} for t in v4_trades],
        }, f, indent=2, default=str)
    print(f"\nSaved: backtest_output/v3_v4_comparison.json")


if __name__ == '__main__':
    main()
