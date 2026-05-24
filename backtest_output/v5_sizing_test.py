"""
Test 3 sizing models on V5 (with D4+D10 dropped):
1. Half-Kelly (current)
2. Full Kelly
3. Flat 2% per trade (V4 style)
"""
import csv
from collections import defaultdict

with open('/root/bitana/backtest_output/v5_full_backtest_trades.csv') as f:
    all_trades = list(csv.DictReader(f))

# Filter: drop D4 and D10
trades = [t for t in all_trades if t['decile'] not in ('4', '10')]

print("V5 SIZING MODEL COMPARISON (D4+D10 dropped)")
print("=" * 60)
print("Base trades: %d" % len(trades))
print()

# Current half-Kelly R
half_kelly_r = sum(float(t['pnl_r']) for t in trades)
print("Half-Kelly (current): %+.2fR" % half_kelly_r)

# Full Kelly = 2x half-Kelly
# Since R is proportional to position size, full Kelly = 2x R
full_kelly_r = half_kelly_r * 2
print("Full Kelly (2x size): %+.2fR" % full_kelly_r)

# Flat 2% — need to recalculate based on each trade's risk_pct
# R is already normalized, so flat sizing means each trade gets same risk
# Current: each trade gets risk_pct * equity risk
# Flat 2%: each trade gets 2% * equity risk
# Ratio: 0.02 / risk_pct for each trade

flat_r = 0
for t in trades:
    risk_pct = float(t['risk_pct'])
    if risk_pct > 0:
        # Scale R proportionally
        scale = 0.02 / risk_pct
        flat_r += float(t['pnl_r']) * scale

print("Flat 2%% (V4 style): %+.2fR" % flat_r)

# Also test: what if we use full Kelly but cap at 25%?
capped_r = 0
for t in trades:
    risk_pct = float(t['risk_pct'])
    # Full Kelly = 2x half-Kelly
    fk_pct = risk_pct * 2
    # Cap at 25%
    capped_pct = min(fk_pct, 0.25)
    if risk_pct > 0:
        scale = capped_pct / risk_pct
        capped_r += float(t['pnl_r']) * scale

print("Full Kelly capped 25%%: %+.2fR" % capped_r)

print()
print("=" * 60)
print("PER-DECILE SIZING:")
print("%-6s %8s %8s %8s %8s" % ("Decile", "Half-K", "Full-K", "Flat 2%", "FK cap25"))
for d in range(1, 11):
    if d in (4, 10):
        continue
    dt = [t for t in trades if t['decile'] == str(d)]
    if not dt:
        continue
    n = len(dt)
    hk_r = sum(float(t['pnl_r']) for t in dt)
    fk_r = hk_r * 2
    f_r = sum(float(t['pnl_r']) * (0.02 / float(t['risk_pct'])) for t in dt if float(t['risk_pct']) > 0)
    c_r = sum(float(t['pnl_r']) * (min(float(t['risk_pct'])*2, 0.25) / float(t['risk_pct'])) for t in dt if float(t['risk_pct']) > 0)
    print("D%-5d %8.2f %8.2f %8.2f %8.2f" % (d, hk_r, fk_r, f_r, c_r))

print()
print("=" * 60)
print("COMPOUNDING COMPARISON ($10,000 start):")
print()

def compound(trades, sizing_fn):
    """Simulate compounding with a given sizing function."""
    equity = 10000.0
    peak = equity
    max_dd = 0
    for t in trades:
        risk_pct = sizing_fn(t)
        # R is already in risk units, scale by risk_pct / original_risk_pct
        original_r = float(t['pnl_r'])
        original_pct = float(t['risk_pct'])
        if original_pct <= 0:
            continue
        # PnL in R units * risk_amount
        risk_amount = equity * risk_pct
        pnl = original_r * (risk_amount / (original_pct * equity)) if equity > 0 else 0
        # Simplified: pnl = original_r * (risk_pct / original_pct) * equity... no
        # Actually original_r is already normalized to 1R = risk_per_unit
        # So pnl_usd = original_r * risk_per_unit * qty
        # And qty = (equity * risk_pct) / risk_per_unit
        # So pnl_usd = original_r * risk_per_unit * (equity * risk_pct / risk_per_unit)
        #            = original_r * equity * risk_pct
        # Wait no, original_r was computed with the original risk_pct
        # Let me just scale proportionally
        scale = risk_pct / original_pct
        pnl_usd = original_r * scale * equity  # This is wrong too
        
        # Actually the simplest: original_r is R-multiple at original risk_pct
        # If we change risk_pct, PnL scales linearly
        # pnl_usd = original_r * (equity * original_pct) * (risk_pct / original_pct)
        #         = original_r * equity * risk_pct
        # No wait. Let me think again.
        # original_r = pnl / (risk_per_unit * qty) where qty = equity * original_pct / risk_per_unit
        # So original_r = pnl / (equity * original_pct)
        # Therefore pnl = original_r * equity * original_pct
        # With new risk_pct: pnl_new = original_r * equity * risk_pct
        pnl_usd = original_r * equity * risk_pct
        equity += pnl_usd
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return equity, max_dd

# Half-Kelly compounding
def hk_sizing(t):
    return float(t['risk_pct'])

# Full Kelly = 2x
def fk_sizing(t):
    return min(float(t['risk_pct']) * 2, 0.50)  # cap at 50%

# Flat 2%
def flat_sizing(t):
    return 0.02

# Per-decile full Kelly (uncapped)
def fk_uncapped_sizing(t):
    return float(t['risk_pct']) * 2

for name, fn in [("Half-Kelly", hk_sizing), ("Full Kelly (capped 50%)", fk_sizing), ("Flat 2%", flat_sizing), ("Full Kelly (uncapped)", fk_uncapped_sizing)]:
    eq, dd = compound(trades, fn)
    print("%-25s: $%10.2f (DD: %.1f%%)" % (name, eq, dd))

print()
print("NOTE: These are approximate — actual compounding depends on")
print("the sequence of wins/losses, not just total R.")
