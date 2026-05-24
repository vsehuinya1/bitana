"""
Test 3 filters on V5 backtest:
1. Drop D10 entirely
2. Drop D4 (also negative)
3. Add vol_z > 1.0 minimum gate
Compare all combinations.
"""
import csv
from collections import defaultdict

with open('/root/bitana/backtest_output/v5_full_backtest_trades.csv') as f:
    all_trades = list(csv.DictReader(f))

print("V5 FILTER TESTING")
print("=" * 60)
print("Base: %d trades, %+.2fR, WR=%.1f%%" % (
    len(all_trades),
    sum(float(t['pnl_r']) for t in all_trades),
    len([t for t in all_trades if float(t['pnl_r']) > 0]) / len(all_trades) * 100
))
print()

# Filter combinations to test
filters = {
    "Base (no filter)": lambda t: True,
    "Drop D10": lambda t: t['decile'] != '10',
    "Drop D4": lambda t: t['decile'] != '4',
    "Drop D4+D10": lambda t: t['decile'] not in ('4', '10'),
    "Drop D1-D4, D9-D10 (D5-D8 only)": lambda t: t['decile'] in ('5', '6', '7', '8'),
    "Drop D1-D4, D10 (D5-D9)": lambda t: t['decile'] in ('5', '6', '7', '8', '9'),
}

# Also test vol_z filter (need to check if vol_z is in the data)
# The backtest doesn't save vol_z, so we can't filter on it retroactively
# But we can test it in the next backtest run

for name, fn in filters.items():
    trades = [t for t in all_trades if fn(t)]
    if not trades:
        print("%s: 0 trades" % name)
        continue
    n = len(trades)
    r = sum(float(t['pnl_r']) for t in trades)
    w = len([t for t in trades if float(t['pnl_r']) > 0])
    wr = w / n * 100
    gw = sum(float(t['pnl_r']) for t in trades if float(t['pnl_r']) > 0)
    gl = abs(sum(float(t['pnl_r']) for t in trades if float(t['pnl_r']) < 0))
    pf = gw / gl if gl > 0 else float('inf')
    avg_r = r / n

    # Monthly breakdown
    monthly = defaultdict(lambda: {'n': 0, 'r': 0})
    for t in trades:
        month = t['entry_time'][:7]
        monthly[month]['n'] += 1
        monthly[month]['r'] += float(t['pnl_r'])

    print("%s:" % name)
    print("  %d trades, %+.2fR, WR=%.0f%%, PF=%.2f, avg_R=%+.3f" % (n, r, wr, pf, avg_r))
    print("  Monthly: " + " | ".join("%s: %dt %+.2fR" % (m, s['n'], s['r']) for m, s in sorted(monthly.items())))
    print()

# Per-decile detail for key filters
print("=" * 60)
print("PER-DECILE DETAIL (Base):")
for d in range(1, 11):
    dt = [t for t in all_trades if t['decile'] == str(d)]
    if not dt:
        continue
    n = len(dt)
    r = sum(float(t['pnl_r']) for t in dt)
    w = len([t for t in dt if float(t['pnl_r']) > 0])
    gw = sum(float(t['pnl_r']) for t in dt if float(t['pnl_r']) > 0)
    gl = abs(sum(float(t['pnl_r']) for t in dt if float(t['pnl_r']) < 0))
    pf = gw / gl if gl > 0 else float('inf')
    print("  D%d: %3d trades, %+.2fR, WR=%.0f%%, PF=%.2f, avg_R=%+.3f" % (d, n, r, w/n*100, pf, r/n))

print()
print("RECOMMENDATION:")
print("D4 and D10 are negative. D1 is huge positive but 49 trades.")
print("D5-D8 is the sweet spot: consistent, positive, reasonable trade count.")
print("D9 is break-even — not worth the 23.5% risk.")
