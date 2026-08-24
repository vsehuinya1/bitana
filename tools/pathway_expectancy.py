"""
Pathway Expectancy Analysis:
Are there specific trade pathways with positive expectancy?
If yes, run $100 compounding at max Kelly for each.
"""
import sqlite3, math, statistics
from collections import defaultdict

conn = sqlite3.connect("/root/bitana/storage/v5_forward_test.db")
conn.row_factory = sqlite3.Row
teleconn = sqlite3.connect("/root/bitana/storage/v6_telemetry.db")
teleconn.row_factory = sqlite3.Row

trades = conn.execute("""
    SELECT trade_uuid, symbol, pnl_r, exit_reason, hold_candles, 
           aggression, decile, entry_price, exit_price, side
    FROM trades WHERE exit_time IS NOT NULL ORDER BY entry_time
""").fetchall()
trades = [dict(t) for t in trades]

trade_data = {}
for t in trades:
    bars = teleconn.execute("""
        SELECT bar_index, price, unrealized_r, mae_so_far, mfe_so_far,
               vol_trail_level, atr, consecutive_red
        FROM r_path WHERE trade_uuid=? ORDER BY bar_index
    """, (t['trade_uuid'],)).fetchall()
    if bars:
        trade_data[t['trade_uuid']] = {
            'meta': t,
            'bars': [dict(b) for b in bars]
        }

print(f"Trades with r_path: {len(trade_data)}")

# ── DEFINE PATHWAYS ───────────────────────────────────────────────

def get_pathway(data):
    """
    Classify each trade into a pathway based on observable trajectory features.
    Returns pathway name + key metrics.
    """
    bars = data['bars']
    meta = data['meta']
    pnl = meta['pnl_r']
    is_winner = pnl > 0
    
    max_mfe = max(b['mfe_so_far'] for b in bars)
    max_mae = max(abs(b['mae_so_far']) for b in bars)
    
    # Time to thresholds
    t_10 = t_20 = t_30 = None
    for b in bars:
        if t_10 is None and b['mfe_so_far'] >= 0.10: t_10 = b['bar_index']
        if t_20 is None and b['mfe_so_far'] >= 0.20: t_20 = b['bar_index']
        if t_30 is None and b['mfe_so_far'] >= 0.30: t_30 = b['bar_index']
    
    # Early behavior (first 5 bars)
    early_bars = [b for b in bars if b['bar_index'] <= 5]
    late_bars = [b for b in bars if b['bar_index'] > len(bars) // 2]
    
    mfe_first5 = max(b['mfe_so_far'] for b in early_bars) if early_bars else 0
    mfe_last5 = max(b['mfe_so_far'] for b in late_bars) if late_bars else 0
    
    # MAE recovery
    max_recovery = max(b['unrealized_r'] - b['mae_so_far'] for b in bars)
    
    # MFE velocity late vs early
    if len(early_bars) >= 2:
        mfe_vel_early = (early_bars[-1]['mfe_so_far'] - early_bars[0]['mfe_so_far']) / len(early_bars)
    else:
        mfe_vel_early = 0
    
    if len(late_bars) >= 2:
        mfe_vel_late = (late_bars[-1]['mfe_so_far'] - late_bars[0]['mfe_so_far']) / len(late_bars)
    else:
        mfe_vel_late = 0
    
    # Consecutive deep bleed
    consec_deep = 0
    max_consec = 0
    for b in bars:
        if abs(b['mae_so_far']) > 0.3:
            consec_deep += 1
            max_consec = max(max_consec, consec_deep)
        else:
            consec_deep = 0
    
    return {
        'pnl': pnl, 'is_winner': is_winner, 'exit_reason': meta['exit_reason'],
        'hold': meta['hold_candles'], 'max_mfe': max_mfe, 'max_mae': max_mae,
        't_10': t_10, 't_20': t_20, 't_30': t_30,
        'mfe_first5': mfe_first5, 'mfe_vel_early': mfe_vel_early,
        'mfe_vel_late': mfe_vel_late, 'max_recovery': max_recovery,
        'max_consec_deep': max_consec, 'n_bars': len(bars),
        'aggression': meta['aggression'], 'decile': meta['decile'],
    }

all_paths = {}
for uuid, data in trade_data.items():
    all_paths[uuid] = get_pathway(data)

# ── TEST PATHWAY FILTERS ─────────────────────────────────────────

def test_pathway(name, filter_fn):
    """Test a pathway filter. Return expectancy stats."""
    filtered = [p for p in all_paths.values() if filter_fn(p)]
    if len(filtered) < 5:
        return None
    
    winners = [p for p in filtered if p['is_winner']]
    losers = [p for p in filtered if not p['is_winner']]
    n = len(filtered)
    wr = len(winners) / n
    avg_win = statistics.mean(p['pnl'] for p in winners) if winners else 0
    avg_loss = statistics.mean(p['pnl'] for p in losers) if losers else 0
    expectancy = wr * avg_win + (1 - wr) * avg_loss
    total_r = sum(p['pnl'] for p in filtered)
    
    # Kelly fraction
    if avg_win > 0 and avg_loss != 0:
        kelly = (wr * avg_win - (1 - wr) * abs(avg_loss)) / avg_win
    else:
        kelly = 0
    
    # Compounding @ half-Kelly (capped at 2%)
    half_kelly = min(kelly / 2, 0.02) if kelly > 0 else 0
    eq = 100.0
    for p in filtered:
        if half_kelly > 0:
            eq *= (1 + half_kelly * p['pnl'])
        else:
            eq *= (1 + 0.005 * p['pnl'])  # fallback 0.5%
    
    return {
        'name': name, 'n': n, 'wr': wr,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'expectancy': expectancy, 'total_r': total_r,
        'kelly': kelly, 'half_kelly': half_kelly,
        'eq_100': eq, 'n_winners': len(winners), 'n_losers': len(losers),
    }

# Define pathways to test
pathways = []

# 1. All trades (baseline)
pathways.append(test_pathway("ALL TRADES (baseline)", lambda p: True))

# 2. Expansive ignition: MFE > 0.2R within first 3 bars
pathways.append(test_pathway("FAST IGNITION (MFE>0.2R in 3 bars)", 
    lambda p: p['t_20'] is not None and p['t_20'] <= 3))

# 3. Confirmation reach: MFE > 0.3R within 10 bars
pathways.append(test_pathway("CONFIRMED (MFE>0.3R in 10 bars)",
    lambda p: p['t_30'] is not None and p['t_30'] <= 10))

# 4. Deep recovery: max_recovery > 1.0R
pathways.append(test_pathway("HIGH RECOVERY (>1R from trough)",
    lambda p: p['max_recovery'] > 1.0))

# 5. No deep bleed: max MAE < 0.4R
pathways.append(test_pathway("SHALLOW MAE (never <-0.4R)",
    lambda p: p['max_mae'] < 0.4))

# 6. Sustained MFE: velocity stays positive
pathways.append(test_pathway("SUSTAINED ACCEL (mfe_vel_late > 0)",
    lambda p: p['mfe_vel_late'] > 0))

# 7. Aggression filter
pathways.append(test_pathway("HIGH AGGRESSION (>65)",
    lambda p: p['aggression'] > 65))

# 8. Decile filter (D1-D3 only)
pathways.append(test_pathway("TOP DECILES (1-3)",
    lambda p: p['decile'] <= 3))

# 9. Short hold + winner: fast exits
pathways.append(test_pathway("FAST WINNER (hold < 20 bars)",
    lambda p: p['hold'] < 20 and p['is_winner']))

# 10. Confirmation + no deep bleed (combined)
pathways.append(test_pathway("CONFIRMED + SHALLOW MAE",
    lambda p: p['t_30'] is not None and p['t_30'] <= 10 and p['max_mae'] < 0.5))

# 11. Confirmation + high recovery
pathways.append(test_pathway("CONFIRMED + HIGH RECOVERY",
    lambda p: p['t_30'] is not None and p['t_30'] <= 10 and p['max_recovery'] > 0.8))

# 12. Volatile expansion: max_mfe > 1.5R and early
pathways.append(test_pathway("EXPANSIVE (MFE>1.5R in 15 bars)",
    lambda p: p['max_mfe'] > 1.5 and p['t_30'] is not None and p['t_30'] <= 15))

# 13. No consecutive deep bleed
pathways.append(test_pathway("NO CONSECUTIVE DEEP BLEED (<3 bars)",
    lambda p: p['max_consec_deep'] < 3))

# Print results
print("\n" + "=" * 90)
print("PATHWAY EXPECTANCY ANALYSIS")
print("=" * 90)
print(f"\n  {'Pathway':<40} {'N':>4} {'WR':>5} {'Exp/R':>7} {'TotalR':>7} {'Kelly':>7} {'$100→':>10}")
print("  " + "-" * 90)

positive = []
for pw in pathways:
    if pw is None:
        continue
    kelly_str = f"{pw['kelly']:.1%}" if pw['kelly'] > 0 else "NEG"
    marker = " ✅" if pw['expectancy'] > 0 else ""
    print(f"  {pw['name']:<40} {pw['n']:>4} {pw['wr']:>4.0%} {pw['expectancy']:>+7.4f} {pw['total_r']:>+7.1f} {kelly_str:>7} ${pw['eq_100']:>9.2f}{marker}")
    if pw['expectancy'] > 0:
        positive.append(pw)

print(f"\n  Pathways with positive expectancy: {len(positive)}/{len([p for p in pathways if p])}")

# ── DETAILED: Positive pathways → full Kelly compounding ─────────

# Store filter functions alongside pathway results for compounding
pathway_filters = {
    "ALL TRADES (baseline)": lambda p: True,
    "FAST IGNITION (MFE>0.2R in 3 bars)": lambda p: p['t_20'] is not None and p['t_20'] <= 3,
    "CONFIRMED (MFE>0.3R in 10 bars)": lambda p: p['t_30'] is not None and p['t_30'] <= 10,
    "HIGH RECOVERY (>1R from trough)": lambda p: p['max_recovery'] > 1.0,
    "SHALLOW MAE (never <-0.4R)": lambda p: p['max_mae'] < 0.4,
    "SUSTAINED ACCEL (mfe_vel_late > 0)": lambda p: p['mfe_vel_late'] > 0,
    "HIGH AGGRESSION (>65)": lambda p: p['aggression'] > 65,
    "TOP DECILES (1-3)": lambda p: p['decile'] <= 3,
    "FAST WINNER (hold < 20 bars)": lambda p: p['hold'] < 20 and p['is_winner'],
    "CONFIRMED + SHALLOW MAE": lambda p: p['t_30'] is not None and p['t_30'] <= 10 and p['max_mae'] < 0.5,
    "CONFIRMED + HIGH RECOVERY": lambda p: p['t_30'] is not None and p['t_30'] <= 10 and p['max_recovery'] > 0.8,
    "EXPANSIVE (MFE>1.5R in 15 bars)": lambda p: p['max_mfe'] > 1.5 and p['t_30'] is not None and p['t_30'] <= 15,
    "NO CONSECUTIVE DEEP BLEED (<3 bars)": lambda p: p['max_consec_deep'] < 3,
}

print("\n\n" + "=" * 90)
print("POSITIVE PATHWAYS: $100 COMPOUNDING AT MAX KELLY")
print("=" * 90)

for pw in sorted(positive, key=lambda x: -x['expectancy']):
    kelly = pw['kelly']
    filt_fn = pathway_filters.get(pw['name'], lambda p: True)
    
    # Get filtered PnL series
    filtered_pnls = [p['pnl'] for p in all_paths.values() if filt_fn(p)]
    
    print(f"\n  --- {pw['name']} (n={pw['n']}, WR={pw['wr']:.0%}, Exp={pw['expectancy']:+.4f}R, Kelly={kelly:.1%}) ---")
    
    for frac_name, frac in [("Full Kelly", 1.0), ("Half Kelly", 0.5), ("Quarter Kelly", 0.25), ("Fixed 1%", 0.0), ("Fixed 0.5%", 0.0)]:
        if frac_name == "Fixed 1%":
            risk_per_trade = 0.01
        elif frac_name == "Fixed 0.5%":
            risk_per_trade = 0.005
        else:
            risk_per_trade = min(kelly * frac, 0.05)
        
        if risk_per_trade <= 0:
            continue
            
        eq = 100.0
        peak = eq
        max_dd = 0
        for pnl in filtered_pnls:
            pnl_usd = eq * risk_per_trade * pnl
            eq += pnl_usd
            peak = max(peak, eq)
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
        
        roi = ((eq / 100) - 1) * 100
        print(f"    {frac_name:<16} risk={risk_per_trade:.1%}/trade → ${eq:.2f} ({roi:+.1f}%) MaxDD={max_dd:.1%}")

conn.close()
teleconn.close()
