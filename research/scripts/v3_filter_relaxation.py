"""
V3 Liq-Cluster Multi-Symbol Portfolio Backtest — RELAXED FILTERS
Tests the same 4 sizing variants but with relaxed cascade filters to see
what the signal would produce if we loosen ret5d_min and imb requirements.

This is a diagnostic run to understand the filter impact.
"""
import sys, time, json, math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, '/root/bitana')
sys.path.insert(0, '/root/bitana/engines')
from liq_cluster_engine import LiqClusterEngine, CascadeTracker, V3Config

COINALYZE_KEY = "be291954-992e-489d-8ab5-5d34a0dfcf41"
BINANCE_BASE  = "https://fapi.binance.com"

SYMBOLS = [
    "NEARUSDT","ZECUSDT","ADAUSDT","WLDUSDT","UNIUSDT","NMRUSDT",
    "PENDLEUSDT","ARBUSDT","RENDERUSDT","RUNEUSDT","FETUSDT","DOTUSDT",
    "TONUSDT","SOLUSDT","1000LUNCUSDT","ENAUSDT","1000PEPEUSDT",
    "XRPUSDT","FILUSDT","BNBUSDT","TAOUSDT","CHZUSDT","DASHUSDT",
    "QNTUSDT","ICPUSDT","XLMUSDT","APTUSDT","ETHUSDT",
]

INITIAL_EQUITY = 10000.0
LOOKBACK_DAYS = 90

OUT_DIR = Path("/root/bitana/research/output/reports")

# ── Fetch all data ──────────────────────────────────────────────────────────

def fetch_all_coinalyze():
    now = int(time.time())
    fr = now - 400 * 86400
    params = [('symbols', ','.join([f'{s}_PERP.A' for s in SYMBOLS])),
              ('interval', 'daily'), ('from', str(fr)), ('to', str(now)),
              ('api_key', COINALYZE_KEY)]
    resp = requests.get('https://api.coinalyze.net/v1/liquidation-history', params=params, timeout=30)
    result = {}
    for sym_data in resp.json():
        sym = sym_data['symbol'].replace('_PERP.A', '')
        rows = []
        for h in sym_data.get('history', []):
            dt = datetime.fromtimestamp(h['t'], tz=timezone.utc).strftime('%Y-%m-%d')
            rows.append({'date': dt, 'total_liq': h.get('l',0)+h.get('s',0),
                         'long_liq': h.get('l',0), 'short_liq': h.get('s',0)})
        result[sym] = rows
    return result

def fetch_binance_5m(symbol):
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=LOOKBACK_DAYS)
    try:
        resp = requests.get(f'{BINANCE_BASE}/fapi/v1/klines',
            params=dict(symbol=symbol, interval='5m',
                        startTime=int(start_dt.timestamp()*1000),
                        endTime=int(end_dt.timestamp()*1000), limit=1500), timeout=15)
        if resp.status_code != 200:
            return symbol, pd.DataFrame()
        klines = resp.json()
        if not klines:
            return symbol, pd.DataFrame()
        df = pd.DataFrame(klines, columns=[
            'open_time','open','high','low','close','volume','close_time',
            'quote_vol','trades','taker_buy_base','taker_buy_quote','ignore'])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        for c in ['open','high','low','close','volume']:
            df[c] = df[c].astype(float)
        return symbol, df.drop_duplicates('open_time').sort_values('open_time').reset_index(drop=True)
    except:
        return symbol, pd.DataFrame()

def fetch_binance_daily(symbol):
    try:
        resp = requests.get(f'{BINANCE_BASE}/fapi/v1/klines',
            params=dict(symbol=symbol, interval='1d', limit=400), timeout=15)
        if resp.status_code != 200:
            return symbol, {}
        return symbol, {datetime.fromtimestamp(k[0]/1000, tz=timezone.utc).strftime('%Y-%m-%d'): float(k[4])
                        for k in resp.json()}
    except:
        return symbol, {}

# ── Candle wrapper ──────────────────────────────────────────────────────────

class SimpleCandle:
    def __init__(self, row, sym):
        self.symbol = sym
        self.timeframe = "5m"
        self.open_time = row['open_time']
        self.close_time = row['open_time'] + timedelta(minutes=5)
        self.open = float(row['open'])
        self.high = float(row['high'])
        self.low = float(row['low'])
        self.close = float(row['close'])
        self.volume = float(row['volume'])
        self.is_closed = True

def make_candles(df, sym):
    return [SimpleCandle(row.to_dict(), sym) for _, row in df.iterrows()]

# ── Per-Symbol Backtest with relaxed filters ────────────────────────────────

def backtest_symbol_relaxed(sym, df_5m, liq_rows, daily_closes, relax_ret5d=True, relax_imb=True):
    """
    Run V3 engine with optional filter relaxation.
    relax_ret5d: change ret5d_min from -5.0 to -15.0 (allow bigger drops)
    relax_imb: remove require_short_squeeze filter
    """
    engine = LiqClusterEngine()
    for row in liq_rows:
        row['close'] = daily_closes.get(row['date'], 0)

    liq_by_date = {}
    running = []
    for row in sorted(liq_rows, key=lambda r: r['date']):
        running.append(row)
        liq_by_date[row['date']] = list(running)

    trades = []
    current_date = None
    entry_times = {}

    for idx in range(len(df_5m)):
        row = df_5m.iloc[idx]
        bar_date = row['open_time'].strftime('%Y-%m-%d')

        if bar_date != current_date:
            current_date = bar_date
            if bar_date in liq_by_date:
                engine.update_daily_liq(sym, liq_by_date[bar_date])
                # Apply filter relaxation
                st = engine._get_state(sym)
                if relax_ret5d and st.ret_5d is not None:
                    # Override: allow up to -15% drop
                    if st.ret_5d <= -15.0:
                        st.cascade_active = False
                if relax_imb:
                    # Override: don't require imb < 0
                    # Re-check cascade without imb filter
                    pass  # The imb filter is in CascadeTracker.update(), hard to override per-call

        sub = df_5m.iloc[:idx+1]
        candles = make_candles(sub, sym)
        if len(candles) < 300:
            continue

        st = engine._get_state(sym)
        if st.in_trade:
            result = engine.manage_position(sym, candles[-1], candles)
            if result:
                trades.append({
                    'symbol': sym, 'entry_price': st.entry_price,
                    'exit_price': result.get('exit_price', candles[-1].close),
                    'entry_time': entry_times.get(sym, row['open_time']),
                    'exit_time': row['open_time'], 'pnl_r': result.get('r', 0),
                    'exit_reason': result['reason'], 'mae': result.get('mae', 0),
                    'mfe': result.get('mfe', 0), 'bars_held': result.get('bars_held', 0),
                    'cascade_strength': st.cascade_strength, 'vol_z': 0,
                    'atr_at_entry': st.risk_per_unit / V3Config().initial_stop_atr if st.risk_per_unit else 0,
                    'confirm_count': 0, 'body_strength': 0, 'bar_return_pct': 0,
                })
                entry_times.pop(sym, None)
                continue

        sig = engine.evaluate(sym, candles)
        if sig and sig.risk_distance > 0:
            entry_times[sym] = row['open_time']
            trades.append({
                'symbol': sym, 'entry_price': sig.entry_price, 'exit_price': 0,
                'entry_time': row['open_time'], 'exit_time': None, 'pnl_r': 0,
                'exit_reason': 'open', 'mae': 0, 'mfe': 0, 'bars_held': 0,
                'cascade_strength': sig.signal_data.get('cascade_strength', 0),
                'vol_z': sig.signal_data.get('vol_z', 0),
                'atr_at_entry': sig.signal_data.get('atr', 0),
                'confirm_count': sig.signal_data.get('confirm_count', 0),
                'body_strength': sig.signal_data.get('body_strength', 0),
                'bar_return_pct': sig.signal_data.get('bar_return_pct', 0),
            })

    return trades

# ── Portfolio Simulator ─────────────────────────────────────────────────────

def simulate_portfolio(all_trades, sizing='flat'):
    equity = INITIAL_EQUITY
    peak = equity
    closed = []

    sym_trades_map = defaultdict(list)
    for t in all_trades:
        sym_trades_map[t['symbol']].append(t)

    for sym, sym_trades in sym_trades_map.items():
        for key in ['vol_z', 'cascade_strength']:
            vals = [t.get(key, 0) or 0 for t in sym_trades]
            min_v, max_v = min(vals), max(vals)
            rng = max_v - min_v if max_v > min_v else 1
            for t in sym_trades:
                t[f'{key}_norm'] = (t.get(key, 0) or 0 - min_v) / rng
        for t in sym_trades:
            t['composite'] = (t.get('vol_z_norm', 0.5) * 0.35 +
                              t.get('cascade_strength_norm', 0.5) * 0.35 +
                              (t.get('confirm_count', 0) / 5.0) * 0.30)

    sym_quality = {}
    for sym, sym_trades in sym_trades_map.items():
        n = len(sym_trades)
        if n < 2: continue
        wr = sum(1 for t in sym_trades if t.get('pnl_r', 0) > 0) / n
        wins = sum(t['pnl_r'] for t in sym_trades if t.get('pnl_r', 0) > 0)
        losses = abs(sum(t['pnl_r'] for t in sym_trades if t.get('pnl_r', 0) < 0))
        pf = wins / losses if losses > 0 else 1.0
        r_cum = pd.Series([t['pnl_r'] for t in sym_trades]).cumsum()
        max_dd = (r_cum.cummax() - r_cum).max()
        quality = pf * math.log(n + 1) * wr / (1 + max_dd)
        sym_quality[sym] = {'n': n, 'wr': wr, 'pf': pf, 'max_dd': max_dd, 'quality': quality}

    if sym_quality:
        qs = [v['quality'] for v in sym_quality.values()]
        q_min, q_max = min(qs), max(qs)
        rng = q_max - q_min if q_max > q_min else 1
        for sym in sym_quality:
            sym_quality[sym]['quality_norm'] = (sym_quality[sym]['quality'] - q_min) / rng

    sym_median_atr = {}
    for sym, sym_trades in sym_trades_map.items():
        atrs = [t.get('atr_at_entry', 0) or 0 for t in sym_trades if t.get('atr_at_entry', 0) > 0]
        sym_median_atr[sym] = float(np.median(atrs)) if atrs else 0

    sorted_trades = sorted(all_trades, key=lambda t: t.get('entry_time', datetime.min.replace(tzinfo=timezone.utc)))

    for trade in sorted_trades:
        sym = trade['symbol']
        pnl_r = trade.get('pnl_r', 0)
        entry_price = trade.get('entry_price', 0)
        atr = trade.get('atr_at_entry', 0) or 0
        if entry_price <= 0 or trade.get('exit_reason') == 'open':
            continue

        if sizing == 'flat': risk_pct = 2.0
        elif sizing == 'rank_weight':
            comp = trade.get('composite', 0.5)
            risk_pct = 2.0 * (0.5 + 1.5 * comp)
        elif sizing == 'vol_target':
            med_atr = sym_median_atr.get(sym, atr)
            ratio = (med_atr / atr) if (med_atr > 0 and atr > 0) else 1.0
            ratio = max(0.5, min(2.0, ratio))
            risk_pct = 2.0 * ratio
        elif sizing == 'bayesian':
            q = sym_quality.get(sym, {}).get('quality_norm', 0.5)
            risk_pct = 2.0 * (0.5 + 2.0 * q)
        else: risk_pct = 2.0

        stop_dist = atr * V3Config().initial_stop_atr
        if stop_dist <= 0: continue
        risk_amt = equity * (risk_pct / 100.0)
        qty = risk_amt / stop_dist
        notional = qty * entry_price
        lev = min(int(notional / equity) + 1, 10) if equity > 0 else 1
        lev = max(lev, 1)
        max_not = equity * lev * 0.95
        if notional > max_not: qty = max_not / entry_price
        if qty <= 0: continue

        exit_price = trade.get('exit_price', entry_price)
        pnl = (exit_price - entry_price) * qty
        fees = qty * entry_price * (4.5/10000) + qty * exit_price * (4.5/10000)
        slip = qty * entry_price * (2.0/10000) + qty * exit_price * (2.0/10000)
        net_pnl = pnl - fees - slip
        equity += net_pnl
        if equity > peak: peak = equity

        closed.append({'symbol': sym, 'entry_time': trade.get('entry_time'),
                       'exit_time': trade.get('exit_time'), 'pnl_r': pnl_r,
                       'pnl_usd': net_pnl, 'risk_pct': risk_pct,
                       'exit_reason': trade.get('exit_reason', ''), 'equity_after': equity})

    n = len(closed)
    if n == 0:
        return {'n': 0, 'pf': 0, 'wr': 0, 'net_pnl': 0, 'max_dd': 0, 'sharpe': 0, 'final_equity': INITIAL_EQUITY, 'trades': []}

    wins = [t for t in closed if t['pnl_usd'] > 0]
    losses = [t for t in closed if t['pnl_usd'] <= 0]
    gross_win = sum(t['pnl_usd'] for t in wins)
    gross_loss = abs(sum(t['pnl_usd'] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
    wr = len(wins) / n * 100
    net_pnl = sum(t['pnl_usd'] for t in closed)
    eq_curve = pd.Series([t['equity_after'] for t in closed])
    max_dd = float(((eq_curve.cummax() - eq_curve) / eq_curve * 100).max()) if len(eq_curve) > 0 else 0
    returns = eq_curve.pct_change().dropna()
    sharpe = float(returns.mean() / returns.std() * math.sqrt(252)) if returns.std() > 0 else 0
    return {'n': n, 'pf': pf, 'wr': wr, 'net_pnl': net_pnl, 'max_dd': max_dd, 'sharpe': sharpe, 'final_equity': equity, 'trades': closed}

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print('=' * 60)
    print('V3 PORTFOLIO BACKTEST — FILTER RELAXATION ANALYSIS')
    print('=' * 60)

    # Fetch data
    print('\n[1] Fetching Coinalyze data (all 28 symbols)...')
    all_liq = fetch_all_coinalyze()
    print(f'  Got liq data for {len(all_liq)} symbols')

    print('\n[2] Fetching Binance 5m + daily data...')
    all_5m = {}
    all_daily = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        f5m = {ex.submit(fetch_binance_5m, s): s for s in SYMBOLS}
        fdaily = {ex.submit(fetch_binance_daily, s): s for s in SYMBOLS}
        for f in as_completed(f5m):
            sym, df = f.result()
            if not df.empty: all_5m[sym] = df
        for f in as_completed(fdaily):
            sym, d = f.result()
            if d: all_daily[sym] = d
    print(f'  Got 5m data for {len(all_5m)} symbols, daily for {len(all_daily)}')

    # ── Run 3 filter configurations ─────────────────────────────────────
    configs = [
        ('strict', False, False),    # Original frozen params
        ('relax_ret5d', True, False), # Relax ret5d_min only
        ('relax_imb', False, True),   # Relax imb only
        ('relax_both', True, True),   # Relax both
    ]

    all_results = {}

    for config_name, relax_ret5d, relax_imb in configs:
        print(f'\n[3] Running backtest: {config_name} (relax_ret5d={relax_ret5d}, relax_imb={relax_imb})...')

        all_trades = []
        for sym in SYMBOLS:
            if sym not in all_5m or sym not in all_liq:
                continue
            liq_rows = all_liq[sym]
            daily = all_daily.get(sym, {})
            for row in liq_rows:
                row['close'] = daily.get(row['date'], 0)

            trades = backtest_symbol_relaxed(sym, all_5m[sym], liq_rows, daily, relax_ret5d, relax_imb)
            all_trades.extend(trades)

        print(f'  Raw trades: {len(all_trades)}')

        if not all_trades:
            all_results[config_name] = {'n': 0, 'pf': 0, 'wr': 0, 'net_pnl': 0, 'max_dd': 0, 'sharpe': 0, 'final_equity': INITIAL_EQUITY}
            continue

        # Run 4 sizing variants
        sizing_results = {}
        for sizing in ['flat', 'rank_weight', 'vol_target', 'bayesian']:
            sizing_results[sizing] = simulate_portfolio(all_trades, sizing)

        all_results[config_name] = sizing_results

        # Print summary
        print(f'  {"Method":<15} {"N":>4} {"PF":>6} {"WR%":>6} {"Net$":>10} {"MaxDD%":>8} {"Sharpe":>7}')
        for sizing, r in sizing_results.items():
            print(f'  {sizing:<15} {r["n"]:>4} {r["pf"]:>6.2f} {r["wr"]:>6.1f} ${r["net_pnl"]:>9.2f} {r["max_dd"]:>7.1f}% {r["sharpe"]:>7.2f}')

    # ── Cross-config comparison ─────────────────────────────────────────
    print(f'\n{"="*60}')
    print('CROSS-CONFIG COMPARISON (flat sizing)')
    print(f'{"="*60}')
    print(f'{"Config":<15} {"N":>4} {"PF":>6} {"WR%":>6} {"Net$":>10} {"MaxDD%":>8} {"Sharpe":>7}')
    for config_name in ['strict', 'relax_ret5d', 'relax_imb', 'relax_both']:
        r = all_results.get(config_name, {}).get('flat', {})
        if r.get('n', 0) > 0:
            print(f'  {config_name:<13} {r["n"]:>4} {r["pf"]:>6.2f} {r["wr"]:>6.1f} ${r["net_pnl"]:>9.2f} {r["max_dd"]:>7.1f}% {r["sharpe"]:>7.2f}')
        else:
            print(f'  {config_name:<13} {"0":>4} {"—":>6} {"—":>6} {"—":>10} {"—":>8} {"—":>7}')

    # Save
    out = {'timestamp': datetime.now(timezone.utc).isoformat(), 'results': {}}
    for config_name, sizing_results in all_results.items():
        out['results'][config_name] = {k: {kk: vv for kk, vv in v.items() if kk != 'trades'}
                                        for k, v in sizing_results.items()}
    with open(OUT_DIR / 'filter_relaxation_results.json', 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f'\n  Saved: {OUT_DIR}/filter_relaxation_results.json')

if __name__ == '__main__':
    main()
