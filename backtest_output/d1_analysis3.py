import csv
import numpy as np
from collections import defaultdict

with open("/root/bitana/backtest_output/v5_full_backtest_trades.csv") as f:
    reader = csv.DictReader(f)
    all_trades = list(reader)

d1_trades = [t for t in all_trades if int(t["decile"]) == 1]
d1_stops = [t for t in d1_trades if "stop" in t["exit_reason"]]
d1_non_stops = [t for t in d1_trades if "stop" not in t["exit_reason"]]

# Key question: do wider stops help?
# D1 stops: 18 losses at avg -1.069R = -19.2R total
# D1 wins: 51 wins at avg +1.770R = +90.3R total
# Net: +71.0R

# If stops were wider (3.0 ATR instead of 2.5):
# - Stop distance increases by 20% (3.0/2.5 = 1.2x)
# - Some stops that were -1.0R would become wins (price dips less than 3.0 ATR)
# - But the avg loss per stop would still be ~-1.0R (slightly more due to wider)
# - The real question: how many of the 18 stops would have survived?

# Let's look at the MAE of stops — how far did price go below entry?
print("D1 stop analysis — how far did price go below entry?")
for t in d1_stops:
    mae = float(t["mae"])
    mfe = float(t["mfe"])
    held = int(t["candles_held"])
    entry_atr = float(t["entry_atr"])
    stop_dist = float(t["stop_dist"])
    stop_atr_mult = stop_dist / entry_atr if entry_atr > 0 else 0
    mae_atr = abs(mae) * stop_dist / stop_dist if stop_dist > 0 else 0  # mae is in R units
    # mae is in R units (already normalized by risk_per_unit)
    # So mae=-0.5 means price went 0.5×risk_per_unit below entry
    # risk_per_unit = stop_dist = 2.5×ATR
    # So mae=-0.5 in R = 0.5 × 2.5 ATR = 1.25 ATR below entry
    mae_atr_units = abs(float(t["mae"])) * 2.5  # convert R to ATR units
    
    print(f"  {t['symbol']:>10} mae={mae:>5.2f}R ({mae_atr_units:>4.1f}×ATR) mfe={mfe:>5.2f}R held={held:>4}c "
          f"stop={stop_dist/float(t['entry_price'])*100:>4.2f}%")

# Key: if stop was at 3.0×ATR instead of 2.5×ATR, which stops would have survived?
# stop_dist_3.0 = entry_atr * 3.0
# For the stop to NOT hit: MAE must be < 3.0×ATR (in price terms)
# MAE is in R units. 1 R = 2.5×ATR. So MAE of -1.0R = 2.5×ATR below entry
# With 3.0×ATR stop: need MAE > -3.0 ATR = -3.0/2.5 R = -1.2R
# ALL current stops have MAE >= -1.0R (by definition, they hit the stop)
# But the question is: would price have kept going down, or would it have reversed?

# Actually, the real question is simpler:
# With 3.0×ATR stop, the stop is 20% further away
# Stops that were triggered at -1.0R (2.5×ATR) would need to go to -1.2R (3.0×ATR) to trigger
# If price reversed before hitting -1.2R, the trade survives

# Let's check: of the 18 D1 stops, how many had MAE worse than -1.2R?
# (i.e., price went more than 1.2×risk_per_unit below entry before the stop hit)
# Actually, the stop IS at -1.0R. So MAE for all stops is approximately -1.0R.
# The question is whether price would have kept going or reversed.

# Better approach: look at MFE for stops
# If MFE > 0, price went up first, then came back down to stop
# These are the "unlucky" ones where a wider stop might have helped

print(f"\n\nD1 stops with MFE > 0 (price went up first, then stopped):")
stops_positive_mfe = [t for t in d1_stops if float(t["mfe"]) > 0]
print(f"  Count: {len(stops_positive_mfe)}/{len(d1_stops)} ({len(stops_positive_mfe)/max(len(d1_stops),1)*100:.0f}%)")
for t in stops_positive_mfe:
    print(f"  {t['symbol']:>10} mfe={float(t['mfe']):>5.2f}R mae={float(t['mae']):>5.2f}R held={t['candles_held']:>4}c")

# D1 stops with MFE = 0 (price never went above entry — pure chop)
stops_zero_mfe = [t for t in d1_stops if float(t["mfe"]) <= 0]
print(f"\nD1 stops with MFE <= 0 (price never went above entry — pure chop):")
print(f"  Count: {len(stops_zero_mfe)}/{len(d1_stops)} ({len(stops_zero_mfe)/max(len(d1_stops),1)*100:.0f}%)")
for t in stops_zero_mfe:
    print(f"  {t['symbol']:>10} mfe={float(t['mfe']):>5.2f}R mae={float(t['mae']):>5.2f}R held={t['candles_held']:>4}c")

# Summary
print(f"\n\nSUMMARY:")
print(f"  D1 stops: {len(d1_stops)}")
print(f"  Stops with MFE > 0 (wider stop might help): {len(stops_positive_mfe)} ({len(stops_positive_mfe)/max(len(d1_stops),1)*100:.0f}%)")
print(f"  Stops with MFE <= 0 (pure chop, wider stop won't help): {len(stops_zero_mfe)} ({len(stops_zero_mfe)/max(len(d1_stops),1)*100:.0f}%)")
print(f"  D1 stop net R: {sum(float(t['pnl_r']) for t in d1_stops):+.1f}R")
print(f"  If wider stops saved ALL MFE>0 stops: +{sum(float(t['mfe']) for t in stops_positive_mfe):+.1f}R recovered")
print(f"  But wider stops also reduce trail profits on wins...")

# What about the win side? How many wins are vol_trail vs struct_trail vs time_stop?
d1_win_reasons = defaultdict(lambda: {"count": 0, "total_r": 0})
for t in d1_non_stops:
    r = t["exit_reason"]
    d1_win_reasons[r]["count"] += 1
    d1_win_reasons[r]["total_r"] += float(t["pnl_r"])

print(f"\n  D1 win reasons:")
for reason, data in sorted(d1_win_reasons.items(), key=lambda x: x[1]["total_r"], reverse=True):
    print(f"    {reason:>15}: {data['count']:>3}t total={data['total_r']:+.1f}R avg={data['total_r']/data['count']:+.3f}R")

# The key number: vol_trail wins
vol_trail_wins = d1_win_reasons.get("vol_trail", {}).get("count", 0)
struct_trail_wins = d1_win_reasons.get("struct_trail", {}).get("count", 0)
time_stop_wins = d1_win_reasons.get("time_stop", {}).get("count", 0)
print(f"\n  vol_trail: {vol_trail_wins}t  struct_trail: {struct_trail_wins}t  time_stop: {time_stop_wins}t")
print(f"  vol_trail is the BIG money maker for D1 (wide 3.0 ATR trail catches trends)")
print(f"  If we widen the stop and the trade survives chop, vol_trail can still trigger")
print(f"  But if we widen the stop and it still stops out, we lose MORE per stop")
