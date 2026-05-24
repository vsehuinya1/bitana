import csv
import numpy as np
from collections import defaultdict

with open("/root/bitana/backtest_output/v5_full_backtest_trades.csv") as f:
    reader = csv.DictReader(f)
    all_trades = list(reader)

print("Columns:", list(all_trades[0].keys()))

d1_trades = [t for t in all_trades if int(t["decile"]) == 1]
d1_stops = [t for t in d1_trades if "stop" in t["exit_reason"]]
d1_non_stops = [t for t in d1_trades if "stop" not in t["exit_reason"]]

print(f"\nD1 trades: {len(d1_trades)}")
print(f"D1 stops: {len(d1_stops)} ({len(d1_stops)/len(d1_trades)*100:.0f}%)")
print(f"D1 non-stops: {len(d1_non_stops)} ({len(d1_non_stops)/len(d1_trades)*100:.0f}%)")
print(f"D1 net R: {sum(float(t['pnl_r']) for t in d1_trades):+.1f}")

if d1_non_stops:
    print(f"D1 avg non-stop: {np.mean([float(t['pnl_r']) for t in d1_non_stops]):+.3f}R")
if d1_stops:
    print(f"D1 avg stop: {np.mean([float(t['pnl_r']) for t in d1_stops]):+.3f}R")

# Stop distances
if d1_stops:
    stop_dists = [float(t["stop_dist"])/float(t["entry_price"])*100 for t in d1_stops]
    print(f"\nD1 stop distance: mean={np.mean(stop_dists):.2f}% min={np.min(stop_dists):.2f}% max={np.max(stop_dists):.2f}%")
    
    print(f"\nD1 stop details (first 15):")
    for t in d1_stops[:15]:
        pct = float(t["stop_dist"])/float(t["entry_price"])*100
        print(f"  {t['symbol']:>10} entry={float(t['entry_price']):>10.4f} stop={pct:>5.2f}% held={t['hold_candles']:>4}c mae={float(t['mae']):>+5.2f} mfe={float(t['mfe']):>+5.2f}")

# Non-stop wins by reason
d1_win_reasons = defaultdict(list)
for t in d1_non_stops:
    d1_win_reasons[t["exit_reason"]].append(float(t["pnl_r"]))
print(f"\nD1 win reasons:")
for reason, pnls in sorted(d1_win_reasons.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {reason:>15}: {len(pnls):>3} wins, avg={np.mean(pnls):+.3f}R, total={sum(pnls):+.1f}R")

# How many stops had MFE > 0?
if d1_stops:
    stops_with_mfe = [t for t in d1_stops if float(t["mfe"]) > 0]
    print(f"\nD1 stops where price went positive first: {len(stops_with_mfe)}/{len(d1_stops)} ({len(stops_with_mfe)/max(len(d1_stops),1)*100:.0f}%)")
    for t in stops_with_mfe[:10]:
        pct = float(t["stop_dist"])/float(t["entry_price"])*100
        print(f"  {t['symbol']:>10} stop={pct:>5.2f}% mfe={float(t['mfe']):>5.2f}R mae={float(t['mae']):>5.2f}R held={t['hold_candles']:>4}c")

# Decile comparison
print(f"\n\nAll deciles comparison:")
for d in sorted(set(int(t["decile"]) for t in all_trades)):
    sub = [t for t in all_trades if int(t["decile"]) == d]
    stops = [t for t in sub if "stop" in t["exit_reason"]]
    net_r = sum(float(t["pnl_r"]) for t in sub)
    wr = len([t for t in sub if float(t["pnl_r"]) > 0]) / len(sub) * 100 if sub else 0
    stop_pct = len(stops) / len(sub) * 100 if sub else 0
    avg_stop = np.mean([float(t["stop_dist"])/float(t["entry_price"])*100 for t in stops]) if stops else 0
    print(f"  D{d}: {len(sub):>3}t WR={wr:>3.0f}% NetR={net_r:>+6.1f} Stop%={stop_pct:>3.0f}% AvgStop={avg_stop:>4.2f}%")
