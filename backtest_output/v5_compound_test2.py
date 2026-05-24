"""
Proper compounding simulation.
Key insight: pnl_r is already normalized. 1R = the risk amount for that trade.
So pnl_usd = pnl_r * risk_amount, where risk_amount = equity * risk_pct.
But the R doesn't change with sizing — only the dollar amount does.
"""
import csv
from collections import defaultdict

with open('/root/bitana/backtest_output/v5_full_backtest_trades.csv') as f:
    all_trades = list(csv.DictReader(f))

trades = [t for t in all_trades if t['decile'] not in ('4', '10')]
trades.sort(key=lambda t: t['entry_time'])

EQUITY_START = 10000.0

HALF_KELLY = {1: 0.009, 2: 0.110, 3: 0.100, 5: 0.184, 6: 0.053, 7: 0.051, 8: 0.160, 9: 0.235}
FULL_KELLY = {k: v*2 for k, v in HALF_KELLY.items()}

def simulate(trades, get_risk_pct, label):
    equity = EQUITY_START
    peak = equity
    max_dd = 0
    n_wins = n_losses = 0
    monthly = defaultdict(lambda: {'n': 0, 'r': 0})
    
    for t in trades:
        decile = int(t['decile'])
        original_r = float(t['pnl_r'])
        original_pct = float(t['risk_pct'])
        
        if original_pct <= 0:
            continue
        
        risk_pct = get_risk_pct(decile)
        
        # The trade's R-multiple is a property of the market move, not our sizing.
        # If the trade made +2R at 5% risk, it makes +2R at any risk level.
        # The dollar amount changes: pnl = R * (equity * risk_pct)
        # But wait — the backtest already computed R based on the original risk_pct.
        # The R is: pnl_usd / (equity * original_pct)
        # So pnl_usd = R * equity * original_pct... no.
        # 
        # Let me think about this more carefully.
        # In the backtest:
        #   risk_amount = equity * original_pct  (e.g. 10000 * 0.05 = 500)
        #   qty = risk_amount / risk_per_unit
        #   pnl_usd = (exit - entry) * qty - fees
        #   pnl_r = pnl_usd / risk_amount  (how many risk units did we make?)
        #
        # So pnl_r is independent of position size. It's a property of the trade.
        # If we change risk_pct:
        #   new_risk_amount = equity * new_risk_pct
        #   new_qty = new_risk_amount / risk_per_unit
        #   new_pnl_usd = (exit - entry) * new_qty - fees
        #   new_pnl_r = new_pnl_usd / new_risk_amount
        #              = (exit - entry) * new_qty / new_risk_amount - fees/new_risk_amount
        #              = (exit - entry) / risk_per_unit - fees/new_risk_amount
        # 
        # Hmm, fees complicate this. But roughly:
        #   new_pnl_r ≈ original_r * (original_pct / new_risk_pct) * (new_risk_pct / original_pct)
        #            = original_r  (if fees are negligible)
        #
        # Actually the simplest correct way:
        # pnl_r is approximately constant regardless of sizing (for small fee effects)
        # pnl_usd = pnl_r * equity * risk_pct
        
        # So the correct formula is:
        pnl_usd = original_r * equity * risk_pct
        
        # Wait, that's what I had before. Let me verify with an example:
        # Trade makes +2R. Equity = 10000. Risk = 5%.
        # risk_amount = 500. pnl_usd = 2 * 500 = 1000. Correct.
        # With 10% risk: risk_amount = 1000. pnl_usd = 2 * 1000 = 2000. Correct.
        # So pnl_usd = pnl_r * equity * risk_pct IS correct.
        
        # The issue before was that I was also scaling R, which was wrong.
        # R doesn't scale with position size. Only dollar PnL does.
        
        equity += pnl_usd
        
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        
        if original_r > 0:
            n_wins += 1
        elif original_r < 0:
            n_losses += 1
        
        month = t['entry_time'][:7]
        monthly[month]['n'] += 1
        monthly[month]['r'] += original_r  # R doesn't change with sizing
    
    n = len(trades)
    wr = n_wins / max(n, 1) * 100
    total_r = sum(float(t['pnl_r']) for t in trades)
    
    print(f"\n{label}:")
    print(f"  Trades: {n} ({n_wins}W / {n_losses}L), WR: {wr:.1f}%")
    print(f"  Total R (sum of trade R): {total_r:+.2f}")
    print(f"  Final equity: ${equity:,.2f} (from ${EQUITY_START:,.0f})")
    print(f"  ROI: {(equity/EQUITY_START-1)*100:+.1f}%")
    print(f"  Max DD: {max_dd:.1f}%")
    print(f"  Monthly:")
    for m in sorted(monthly.keys()):
        s = monthly[m]
        print(f"    {m}: {s['n']}t {s['r']:+.2f}R")
    
    return equity, max_dd

print("=" * 60)
print("V5 SIZING COMPARISON (D4+D10 dropped, 156 trades)")
print("=" * 60)

models = [
    ("Half-Kelly (current)", lambda d: HALF_KELLY.get(d, 0.05)),
    ("Full Kelly", lambda d: FULL_KELLY.get(d, 0.10)),
    ("Full Kelly capped 25%", lambda d: min(FULL_KELLY.get(d, 0.10), 0.25)),
    ("Flat 2% (V4 style)", lambda d: 0.02),
    ("Flat 4%", lambda d: 0.04),
    ("Flat 5%", lambda d: 0.05),
]

results = []
for label, fn in models:
    eq, dd = simulate(trades, fn, label)
    results.append((label, eq, dd))

print(f"\n{'='*60}")
print("SUMMARY:")
print(f"{'Model':<25} {'Final Equity':>15} {'Max DD':>8} {'ROI':>10}")
for label, eq, dd in results:
    roi = (eq/EQUITY_START-1)*100
    print(f"{label:<25} ${eq:>14,.2f} {dd:>7.1f}% {roi:>+9.1f}%")
