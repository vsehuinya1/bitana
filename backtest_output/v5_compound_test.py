"""
Proper compounding simulation for V5 with different sizing models.
Replays trades in sequence with realistic position sizing.
"""
import csv
from collections import defaultdict
from datetime import datetime

with open('/root/bitana/backtest_output/v5_full_backtest_trades.csv') as f:
    all_trades = list(csv.DictReader(f))

# Filter: drop D4 and D10
trades = [t for t in all_trades if t['decile'] not in ('4', '10')]
# Sort by entry time
trades.sort(key=lambda t: t['entry_time'])

EQUITY_START = 10000.0

# Per-decile Kelly values (half and full)
HALF_KELLY = {1: 0.009, 2: 0.110, 3: 0.100, 5: 0.184, 6: 0.053, 7: 0.051, 8: 0.160, 9: 0.235}
FULL_KELLY = {k: v*2 for k, v in HALF_KELLY.items()}

def simulate(trades, sizing_model, label):
    equity = EQUITY_START
    peak = equity
    max_dd = 0
    n_wins = 0
    n_losses = 0
    total_r = 0
    monthly = defaultdict(lambda: {'n': 0, 'r': 0, 'eq_start': 0, 'eq_end': 0})
    
    current_month = None
    
    for t in trades:
        month = t['entry_time'][:7]
        if month != current_month:
            if current_month and monthly[current_month]['eq_start'] == 0:
                monthly[current_month]['eq_start'] = equity
            current_month = month
            if monthly[month]['eq_start'] == 0:
                monthly[month]['eq_start'] = equity
        
        decile = int(t['decile'])
        original_r = float(t['pnl_r'])
        original_pct = float(t['risk_pct'])
        
        if original_pct <= 0:
            continue
        
        # Get risk % for this trade
        if sizing_model == "half_kelly":
            risk_pct = HALF_KELLY.get(decile, 0.05)
        elif sizing_model == "full_kelly":
            risk_pct = FULL_KELLY.get(decile, 0.10)
        elif sizing_model == "full_kelly_capped25":
            risk_pct = min(FULL_KELLY.get(decile, 0.10), 0.25)
        elif sizing_model == "flat_2pct":
            risk_pct = 0.02
        elif sizing_model == "flat_4pct":
            risk_pct = 0.04
        else:
            risk_pct = original_pct
        
        # Scale R proportionally to sizing change
        scale = risk_pct / original_pct
        actual_r = original_r * scale
        total_r += actual_r
        
        # PnL in USD: actual_r * (equity * risk_pct) / risk_pct... 
        # Wait. original_r = pnl / risk_per_unit_in_r_terms
        # The backtest computed: pnl_r = pnl_usd / (risk_per_unit * qty)
        # where qty = (equity * original_pct) / risk_per_unit
        # So pnl_r = pnl_usd / (equity * original_pct)
        # Therefore pnl_usd = pnl_r * equity * original_pct
        # With new sizing: pnl_usd_new = actual_r * equity * risk_pct
        # But actual_r = original_r * (risk_pct/original_pct)
        # So pnl_usd_new = original_r * (risk_pct/original_pct) * equity * risk_pct
        #               = original_r * equity * risk_pct^2 / original_pct
        # Hmm, that's not right either.
        
        # Let me think differently. The backtest R is normalized.
        # 1R = 1 unit of risk = equity * risk_pct
        # So pnl_usd = pnl_r * equity * risk_pct
        # With new risk_pct: pnl_usd = actual_r * equity * new_risk_pct
        # where actual_r = original_r * (new_risk_pct / original_pct)  [linear scaling]
        # So pnl_usd = original_r * (new_risk_pct/original_pct) * equity * new_risk_pct
        #            = original_r * equity * new_risk_pct^2 / original_pct
        
        # Actually I think the simplest correct way:
        # The trade made original_r at original_pct risk.
        # If we risked differently, the R-multiple stays the same (it's a property of the trade)
        # but the dollar PnL changes proportionally.
        # pnl_usd = original_r * (equity * new_risk_pct)
        # Because 1R = equity * new_risk_pct with the new sizing
        
        pnl_usd = actual_r * equity * risk_pct  # This is wrong
        
        # Let me just do it simply:
        # original: risk_amount = equity * original_pct, pnl = original_r * risk_amount
        # new: risk_amount_new = equity * risk_pct, pnl_new = original_r * risk_amount_new
        # The R-multiple doesn't change with sizing — it's a property of the trade
        # Only the dollar amount changes
        
        pnl_usd = original_r * equity * risk_pct
        
        equity += pnl_usd
        
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        
        if actual_r > 0:
            n_wins += 1
        elif actual_r < 0:
            n_losses += 1
        
        monthly[month]['n'] += 1
        monthly[month]['r'] += actual_r
        monthly[month]['eq_end'] = equity
    
    n = len(trades)
    wr = n_wins / max(n, 1) * 100
    
    print(f"\n{label}:")
    print(f"  Trades: {n} ({n_wins}W / {n_losses}L)")
    print(f"  WR: {wr:.1f}%")
    print(f"  Total R: {total_r:+.2f}")
    print(f"  Final equity: ${equity:,.2f} (from ${EQUITY_START:,.0f})")
    print(f"  ROI: {(equity/EQUITY_START-1)*100:+.1f}%")
    print(f"  Max DD: {max_dd:.1f}%")
    print(f"  Monthly:")
    for m in sorted(monthly.keys()):
        s = monthly[m]
        print(f"    {m}: {s['n']}t {s['r']:+.2f}R  eq: ${s['eq_start']:,.0f} → ${s['eq_end']:,.0f}")
    
    return equity, max_dd

print("=" * 60)
print("V5 SIZING MODEL COMPARISON (D4+D10 dropped, 156 trades)")
print("=" * 60)

models = [
    ("Half-Kelly (current)", "half_kelly"),
    ("Full Kelly", "full_kelly"),
    ("Full Kelly capped 25%", "full_kelly_capped25"),
    ("Flat 2% (V4 style)", "flat_2pct"),
    ("Flat 4%", "flat_4pct"),
]

results = []
for label, model in models:
    eq, dd = simulate(trades, model, label)
    results.append((label, eq, dd))

print(f"\n{'='*60}")
print("SUMMARY:")
print(f"{'Model':<25} {'Final Equity':>15} {'Max DD':>8} {'ROI':>10}")
for label, eq, dd in results:
    roi = (eq/EQUITY_START-1)*100
    print(f"{label:<25} ${eq:>14,.2f} {dd:>7.1f}% {roi:>+9.1f}%")
