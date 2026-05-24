"""
Analyze D1 stop losses in backtest to understand the chop problem.
Key question: do wider stops help D1 in chop, or do they just reduce trail profits?
"""
import csv
import numpy as np
from collections import defaultdict

with open("/root/bitana/backtest_output/v5_full_backtest_trades.csv") as f:
    reader = csv.DictReader(f)
    all_trades = list(reader)

d1_trades = [t for t in all_trades if int(t["decile"]) == 1]
d1_stops = [t for t in d1_trades if "stop" in t["exit_reason"]]
d1_wins = [t for t in d1_trades if float(t["pnl_r"]) > 0]

print(f"D1 trades: {len(d1_trades)}")
print(f"D1 stops: {len(d1_stops)} ({len(d1_stops)/len(d1_trades)*100:.0f}%)")
print(f"D1 wins: {len(d1_wins)} ({len(d1_wins)/len(d1_trades)*100:.0f}%)")
print(f"D1 net R: {sum(float(t['pnl_r']) for t in d1_trades):+.1f}")
print(f"D1 avg win: {np.mean([float(t['pnl_r']) for t in d1_wins]):+.3f}R")
print(f"D1 avg loss: {np.mean([float(t['pnl_r']) for t in d1_stops]):+.3f}R")

# What are D1 stops like?
print(f"\nD1 stop details:")
for t in d1_stops[:15]:
    print(f"  {t['symbol']:>10} entry={float(t['entry_price']):>10.4f} stop_dist={float(t['stop_dist']):>8.4f} "
          f"stop%={float(t['stop_dist'])/float(t['entry_price'])*100:>5.2f}% held={t['hold_candles']:>4}c "
          f"mae={float(t['mae']):>+5.2f} mfe={float(t['mfe']):>+5.2f}")

# How many stops had MFE > 0 (price went up first, then came back)?
stops_with_mfe = [t for t in d1_stops if float(t["mfe"]) > 0]
print(f"\nD1 stops where price went positive first: {len(stops_with_mfe)}/{len(d1_stops)} ({len(stops_with_mfe)/len(d1_stops)*100:.0f}%)")
for t in stops_with_mfe[:10]:
    print(f"  {t['symbol']:>10} mfe={float(t['mfe']):>5.2f}R mae={float(t['mae']):>5.2f}R held={t['hold_candles']:>4}c")

# How many stops were "quick" (held < 50 candles)?
quick_stops = [t for t in d1_stops if int(t["hold_candles"]) < 50]
print(f"\nQuick stops (<50c): {len(quick_stops)}/{len(d1_stops)} ({len(quick_stops)/len(d1_stops)*100:.0f}%)")

# What's the average stop distance?
stop_dists = [float(t["stop_dist"])/float(t["entry_price"])*100 for t in d1_stops]
print(f"\nD1 stop distance: mean={np.mean(stop_dists):.2f}% median={np.median(stop_dists):.2f}% min={np.min(stop_dists):.2f}% max={np.max(stop_dists):.2f}%")

# Compare to D8/D9 stops
d89_stops = [t for t in all_trades if int(t["decile"]) in [8,9] and "stop" in t["exit_reason"]]
d89_stop_dists = [float(t["stop_dist"])/float(t["entry_price"])*100 for t in d89_stops]
print(f"\nD8/9 stop distance: mean={np.mean(d89_stop_dists):.2f}% median={np.median(d89_stop_dists):.2f}%")

# Key insight: D1 vol_trail_atr=3.0 vs D8 vol_trail_atr=2.5 vs D9 vol_trail_atr=1.5
# D1 has the WIDEST trail but still loses because the stop is too tight
# The stop distance is driven by initial_stop_atr=2.5, same for all deciles
# But D1's 3.0 ATR trail means it needs price to move 3.0 ATR above entry before trail activates
# In chop, price never gets that far

print(f"\n\nKEY INSIGHT:")
print(f"D1 stop distance: {np.mean(stop_dists):.2f}% (2.5 × ATR)")
print(f"D1 vol_trail_atr: 3.0 (needs price to move 3.0×ATR above entry)")
print(f"In chop: price moves < 1×ATR → stop at -1.0R, trail never activates")
print(f"Wider stop (3.0×ATR) = {np.mean(stop_dists)*3.0/2.5:.2f}% → survives more chop")
print(f"But backtest showed wider stops reduce net R because trail profits drop")

# Let's check: what % of D1 wins came from vol_trail vs struct_trail vs time_stop
d1_win_reasons = defaultdict(list)
for t in d1_wins:
    d1_win_reasons[t["exit_reason"]].append(float(t["pnl_r"]))
print(f"\nD1 win reasons:")
for reason, pnls in sorted(d1_win_reasons.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {reason:>15}: {len(pnls):>3} wins, avg={np.mean(pnls):+.3f}R, total={sum(pnls):+.1f}R")

# What about D1 stops — how many were "unlucky" (MFE > 1R then stopped)?
unlucky = [t for t in d1_stops if float(t["mfe"]) > 1.0]
print(f"\nD1 stops with MFE > 1R (unlucky): {len(unlucky)}/{len(d1_stops)} ({len(unlucky)/len(d1_stops)*100:.0f}%)")
