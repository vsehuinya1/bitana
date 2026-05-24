"""
V5 Backtest: Filter existing full_stack results to D5-D8 only.
Much faster — reuses the V4 backtest output with V5 filters applied.
"""
import csv, numpy as np
from collections import defaultdict, Counter

with open('backtest_output/final_full_stack_trades.csv') as f:
    all_trades = list(csv.DictReader(f))

print(f"V4 full_stack total: {len(all_trades)} trades")

# V5 filter: D5-D8 only (aggression score 0.754 - 0.825 in 0-1 scale)
v5_trades = [t for t in all_trades if 0.754 <= float(t.get('aggression_score',0)) <= 0.825]

print(f"V5 D5-D8 filtered: {len(v5_trades)} trades ({len(v5_trades)/len(all_trades)*100:.1f}% of total)")

if not v5_trades:
    print("No trades match D5-D8 filter")
    exit()

pnls = [float(t['pnl_r']) for t in v5_trades]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]
wr = len(wins)/max(len(wins)+len(losses),1)*100
gw = sum(wins); gl = abs(sum(losses))
pf = gw/gl if gl > 0 else float('inf')

print(f"\n{'='*60}")
print(f"V5 D5-D8 BACKTEST RESULTS")
print(f"{'='*60}")
print(f"Trades: {len(v5_trades)}")
print(f"WR: {wr:.1f}% ({len(wins)}W/{len(losses)}L)")
print(f"Total R: {sum(pnls):+.2f}R")
print(f"Avg R: {np.mean(pnls):+.3f}R")
print(f"PF: {pf:.2f}")

for th in [1,2,3,5,10]:
    c = sum(1 for p in pnls if p >= th)
    print(f">={th}R: {c}/{len(v5_trades)} = {c/len(v5_trades)*100:.1f}%")

# By decile
print(f"\n=== BY DECILE ===")
for d in [5,6,7,8]:
    dt = [t for t in v5_trades if int(t.get('aggression_decile',0)) == d]
    if dt:
        dp = [float(t['pnl_r']) for t in dt]
        dw = [p for p in dp if p>0]; dl = [p for p in dp if p<0]
        print(f"D{d}: {len(dt)} tr, WR {len(dw)/max(len(dw)+len(dl),1)*100:.1f}%, {sum(dp):+.2f}R, PF {sum(dw)/max(abs(sum(dl)),1):.2f}")

# By symbol
print(f"\n=== BY SYMBOL ===")
sp = defaultdict(lambda:{'n':0,'p':0.0})
for t in v5_trades:
    sp[t['symbol']]['n'] += 1
    sp[t['symbol']]['p'] += float(t['pnl_r'])
for s,d in sorted(sp.items(), key=lambda x: x[1]['p'], reverse=True):
    print(f"  {s:<15} {d['n']:3d} tr  {d['p']:+7.2f}R")

# Exit reasons
print(f"\n=== EXIT REASONS ===")
for r,c in Counter(t.get('exit_reason','') for t in v5_trades).most_common():
    a = np.mean([float(t['pnl_r']) for t in v5_trades if t.get('exit_reason')==r])
    print(f"  {r}: {c} tr, avg {a:+.2f}R")

# Top/bottom
print(f"\n=== TOP 15 WINNERS ===")
for t in sorted(v5_trades, key=lambda x: float(x['pnl_r']), reverse=True)[:15]:
    print(f"  D{t.get('aggression_decile','?')} {t['symbol']:<15} {float(t['pnl_r']):+6.2f}R  agg={t.get('aggression_score','0')}  hold={t.get('hold','0'):>3}  {t.get('exit_reason','')}")

print(f"\n=== BOTTOM 10 LOSERS ===")
for t in sorted(v5_trades, key=lambda x: float(x['pnl_r']))[:10]:
    print(f"  D{t.get('aggression_decile','?')} {t['symbol']:<15} {float(t['pnl_r']):+6.2f}R  agg={t.get('aggression_score','0')}  hold={t.get('hold','0'):>3}  {t.get('exit_reason','')}")

# Compound from $100 at different risk levels
print(f"\n=== COMPOUND $100 ===")
for risk_pct in [2,3,4,5,6,8,10]:
    eq = 100.0; peak = eq; max_dd = 0
    for t in v5_trades:
        risk = eq * (risk_pct/100)
        pnl = float(t['pnl_r']) * risk
        eq += pnl
        peak = max(peak, eq)
        dd = (peak-eq)/peak*100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    print(f"  {risk_pct}% risk: ${eq:>12,.2f} ({(eq-100)/100*100:+.0f}% ROI)  maxDD={max_dd:.1f}%")

# Compare to V4 full_stack
print(f"\n=== V5 vs V4 COMPARISON ===")
v4_pnls = [float(t['pnl_r']) for t in all_trades]
print(f"  V4 all: {len(all_trades)} tr, {sum(v4_pnls):+.2f}R, PF {sum(p for p in v4_pnls if p>0)/max(abs(sum(p for p in v4_pnls if p<0)),1):.2f}")
print(f"  V5 D5-D8: {len(v5_trades)} tr, {sum(pnls):+.2f}R, PF {pf:.2f}")
print(f"  Trade count: {len(v5_trades)/len(all_trades)*100:.1f}% of V4")
print(f"  R captured: {sum(pnls)/sum(v4_pnls)*100:.1f}% of V4 total R")

# Save
with open('backtest_output/v5_d5d8_trades.csv','w',newline='') as f:
    w = csv.DictWriter(f, fieldnames=v5_trades[0].keys())
    w.writeheader()
    w.writerows(v5_trades)
print(f"\nSaved to backtest_output/v5_d5d8_trades.csv")
