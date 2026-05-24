"""
Portfolio sizing analysis on existing liq_v2 backtest trades (78 trades).
Tests 4 sizing variants on the actual trade data.
"""
import sys, json, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, '/root/bitana')
sys.path.insert(0, '/root/bitana/engines')
from liq_cluster_engine import V3Config

INITIAL_EQUITY = 10000.0
OUT_DIR = Path("/root/bitana/research/output/reports")

# ── Load existing trades ────────────────────────────────────────────────────

liq_v2 = pd.read_parquet(OUT_DIR / 'liq_v2_trades.parquet')
liq_v2['entry_dt'] = pd.to_datetime(liq_v2['entry_dt'])
liq_v2['exit_dt'] = pd.to_datetime(liq_v2['exit_time'], unit='ns', utc=True)
liq_v2['month'] = liq_v2['entry_dt'].dt.to_period('M')
liq_v2 = liq_v2.sort_values('entry_dt').reset_index(drop=True)

print('=' * 60)
print('PORTFOLIO SIZING ANALYSIS — Liq V2 Trades (78 trades)')
print(f'  Period: {liq_v2["entry_dt"].min().date()} to {liq_v2["entry_dt"].max().date()}')
print('=' * 60)

# ── Compute composite scores ────────────────────────────────────────────────

for key in ['vol_z', 'cascade_strength']:
    vals = liq_v2[key].fillna(0)
    min_v, max_v = vals.min(), vals.max()
    rng = max_v - min_v if max_v > min_v else 1
    liq_v2[f'{key}_norm'] = (vals - min_v) / rng

liq_v2['composite'] = (liq_v2['vol_z_norm'] * 0.35 +
                        liq_v2['cascade_strength_norm'] * 0.35 +
                        (liq_v2['body_strength'].fillna(0) / 1.0) * 0.30)

# ── Portfolio Simulator ─────────────────────────────────────────────────────

def simulate_portfolio(trades_df, sizing='flat'):
    equity = INITIAL_EQUITY
    peak = equity
    closed = []

    for _, trade in trades_df.iterrows():
        pnl_r = trade.get('r', 0)
        entry_price = trade.get('entry_price', 0)
        atr = trade.get('atr', 0) or 0
        if entry_price <= 0: continue

        if sizing == 'flat': risk_pct = 2.0
        elif sizing == 'rank_weight':
            comp = trade.get('composite', 0.5)
            risk_pct = 2.0 * (0.5 + 1.5 * comp)
        elif sizing == 'vol_target':
            median_atr = trades_df['atr'].median()
            ratio = (median_atr / atr) if (median_atr > 0 and atr > 0) else 1.0
            ratio = max(0.5, min(2.0, ratio))
            risk_pct = 2.0 * ratio
        elif sizing == 'bayesian':
            # Single symbol, so quality = 1.0 (no cross-symbol differentiation)
            risk_pct = 2.0
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

        closed.append({'entry_dt': trade['entry_dt'], 'exit_dt': trade['exit_dt'],
                       'pnl_r': pnl_r, 'pnl_usd': net_pnl, 'risk_pct': risk_pct,
                       'exit_reason': trade.get('exit_reason', ''),
                       'equity_after': equity, 'composite': trade.get('composite', 0.5)})

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

# ── Run 4 variants ──────────────────────────────────────────────────────────

results = {}
for sizing in ['flat', 'rank_weight', 'vol_target']:
    results[sizing] = simulate_portfolio(liq_v2, sizing)

# ── Report ──────────────────────────────────────────────────────────────────

print(f'\n{"Method":<15} {"N":>4} {"PF":>6} {"WR%":>6} {"Net$":>10} {"MaxDD%":>8} {"Sharpe":>7} {"FinalEq":>10}')
print('-' * 80)
for sizing, r in results.items():
    print(f'{sizing:<15} {r["n"]:>4} {r["pf"]:>6.2f} {r["wr"]:>6.1f} ${r["net_pnl"]:>9.2f} {r["max_dd"]:>7.1f}% {r["sharpe"]:>7.2f} ${r["final_equity"]:>9.2f}')

flat_r = results['flat']
print(f'\nImprovement vs flat:')
for sizing, r in results.items():
    if sizing == 'flat': continue
    print(f'  {sizing:<15} PF {r["pf"]-flat_r["pf"]:>+.2f}  Sharpe {r["sharpe"]-flat_r["sharpe"]:>+.2f}  '
          f'MaxDD {r["max_dd"]-flat_r["max_dd"]:>+.1f}%  PnL ${r["net_pnl"]-flat_r["net_pnl"]:>+9.2f}')

# ── Monthly breakdown ──────────────────────────────────────────────────────

print(f'\n{"="*60}')
print('MONTHLY BREAKDOWN')
print(f'{"="*60}')
for sizing in ['flat', 'rank_weight', 'vol_target']:
    trades = results[sizing]['trades']
    if not trades: continue
    tdf = pd.DataFrame(trades)
    tdf['month'] = tdf['entry_dt'].dt.to_period('M')
    monthly = tdf.groupby('month').agg(n=('pnl_usd','count'), net=('pnl_usd','sum'),
                                        wr=('pnl_usd', lambda x: (x>0).mean()*100)).round(2)
    print(f'\n  {sizing}:')
    for period, row in monthly.iterrows():
        print(f'    {period}: {row["n"]:>3}t  ${row["net"]:>9.2f}  WR {row["wr"]:>5.1f}%')

# ── Exit reason analysis ────────────────────────────────────────────────────

print(f'\n{"="*60}')
print('EXIT REASON ANALYSIS')
print(f'{"="*60}')
for reason in liq_v2['exit_reason'].value_counts().index:
    rt = liq_v2[liq_v2['exit_reason'] == reason]
    n = len(rt)
    wr = (rt['pnl_net'] > 0).mean() * 100
    net = rt['pnl_net'].sum()
    print(f'  {reason:<20} {n:>3}t  WR {wr:>5.1f}%  Net ${net:>9.2f}')

# ── Signal quality ──────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print('SIGNAL QUALITY — Winners vs Losers')
print(f'{"="*60}')
winners = liq_v2[liq_v2['pnl_net'] > 0]
losers = liq_v2[liq_v2['pnl_net'] <= 0]
for col in ['cascade_strength', 'vol_z', 'body_strength', 'bar_return_pct', 'atr']:
    if col in liq_v2.columns:
        w = winners[col].mean() if len(winners) > 0 else 0
        l = losers[col].mean() if len(losers) > 0 else 0
        pooled = np.sqrt((winners[col].var() + losers[col].var()) / 2) if len(winners) > 1 and len(losers) > 1 else 1
        d = (w - l) / pooled if pooled > 0 else 0
        print(f'  {col:<20} W={w:>8.3f}  L={l:>8.3f}  diff={w-l:>+8.3f}  d={d:>+.2f}')

# ── Save ────────────────────────────────────────────────────────────────────

out = {'timestamp': datetime.now(timezone.utc).isoformat(),
       'results': {k: {kk: vv for kk, vv in v.items() if kk != 'trades'} for k, v in results.items()}}
with open(OUT_DIR / 'sizing_analysis_results.json', 'w') as f:
    json.dump(out, f, indent=2, default=str)
print(f'\nSaved: {OUT_DIR}/sizing_analysis_results.json')
