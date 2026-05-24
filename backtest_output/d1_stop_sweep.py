"""
Targeted analysis: re-trace the 69 D1 backtest trades with different stops.
Read each trade's entry candle, then replay the exit with 2.5x, 3.0x, 3.5x ATR stops.
"""
import csv
import sqlite3
from collections import Counter

# Load backtest trades
with open("/root/bitana/backtest_output/v5_full_backtest_trades.csv") as f:
    reader = csv.DictReader(f)
    all_trades = list(reader)

d1_trades = [t for t in all_trades if int(t["decile"]) == 1]
print(f"D1 trades: {len(d1_trades)}")

# Load klines for the D1 symbols
db = sqlite3.connect("/root/bitana/backtest_data/klines_5m.db")

def load_candles(symbol, start_ms, end_ms):
    cur = db.cursor()
    cur.execute("SELECT open_time, close_time, open, high, low, close, volume FROM klines "
                "WHERE symbol=? AND open_time >= ? AND open_time <= ? ORDER BY open_time",
                (symbol, start_ms, end_ms))
    return cur.fetchall()

def replay_exit(candles, entry_idx, entry_price, stop_price, max_hold):
    """Replay candles after entry. Return (exit_reason, pnl_r, hold_candles)."""
    if entry_idx + 1 >= len(candles):
        return "time_stop", 0, 0
    
    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        return "time_stop", 0, 0
    
    best_price = entry_price
    trailing_stop = None
    
    for i in range(entry_idx + 1, len(candles)):
        hold = i - entry_idx
        if hold >= max_hold:
            close = candles[i][5]
            r = (close - entry_price) / risk_per_unit
            return "time_stop", r, hold
        
        high = candles[i][3]
        low = candles[i][4]
        close = candles[i][5]
        
        if high > best_price:
            best_price = high
        
        # Stop hit?
        if low <= stop_price:
            r = (stop_price - entry_price) / risk_per_unit
            return "stop_loss", r, hold
        
        # Vol trail (3.0 ATR for D1) — simplified
        
    # Held to end
    close = candles[-1][5]
    r = (close - entry_price) / risk_per_unit
    return "time_stop", r, len(candles) - entry_idx - 1

# For each D1 trade, find the entry candle index and replay with different stops
print(f"\n{'#':>3} | {'Symbol':>10} | {'Entry':>8} | {'Orig':>6} | {'2.5x':>6} | {'3.0x':>6} | {'3.5x':>6} | {'OrigR':>6} | {'2.5xR':>6} | {'3.0xR':>6} | {'3.5xR':>6}")
print("-" * 100)

results = {"2.5x": {"stops": 0, "net_r": 0}, "3.0x": {"stops": 0, "net_r": 0}, "3.5x": {"stops": 0, "net_r": 0}}

for t in d1_trades[:20]:  # First 20 for speed
    sym = t["symbol"]
    entry_time = t["entry_time"]
    entry_price = float(t["entry_price"])
    orig_stop_dist = float(t["stop_dist"])
    orig_r = float(t["pnl_r"])
    
    # Find entry candle
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(entry_time.replace("+00:00", "+00:00"))
    entry_ms = int(dt.timestamp() * 1000)
    
    # Load candles around entry (±500 candles)
    candles = load_candles(sym, entry_ms - 500*300000, entry_ms + 600*300000)
    
    # Find entry candle
    entry_idx = None
    for i, c in enumerate(candles):
        if c[1] == entry_ms:  # close_time == entry_time
            entry_idx = i
            break
    
    if entry_idx is None:
        print(f"  {'?':>3} | {sym:>10} | entry candle not found")
        continue
    
    # Calculate ATR at entry
    import numpy as np
    if entry_idx >= 14:
        trs = []
        for j in range(entry_idx - 13, entry_idx + 1):
            if j > 0:
                tr = max(candles[j][3] - candles[j][4], abs(candles[j][3] - candles[j-1][5]), abs(candles[j][4] - candles[j-1][5]))
                trs.append(tr)
        atr = np.mean(trs) if trs else orig_stop_dist / 2.5
    else:
        atr = orig_stop_dist / 2.5
    
    dist_25 = atr * 2.5
    dist_30 = atr * 3.0
    dist_35 = atr * 3.5
    
    stop_25 = entry_price - dist_25
    stop_30 = entry_price - dist_30
    stop_35 = entry_price - dist_35
    
    max_hold = 500  # D1 max_hold_bars
    
    _, pnl_25, _ = replay_exit(candles, entry_idx, entry_price, stop_25, max_hold)
    _, pnl_30, _ = replay_exit(candles, entry_idx, entry_price, stop_30, max_hold)
    _, pnl_35, _ = replay_exit(candles, entry_idx, entry_price, stop_35, max_hold)
    
    is_stop_25 = pnl_25 < -0.5
    is_stop_30 = pnl_30 < -0.5
    is_stop_35 = pnl_35 < -0.5
    
    print(f"  {int(t['id']):>3} | {sym:>10} | {entry_price:>8.4f} | {orig_stop_dist:>6.4f} | {dist_25:>6.4f} | {dist_30:>6.4f} | {dist_35:>6.4f} | {orig_r:>+6.3f} | {pnl_25:>+6.3f} | {pnl_30:>+6.3f} | {pnl_35:>+6.3f}")
    
    for mult, pnl, is_stop in [("2.5x", pnl_25, is_stop_25), ("3.0x", pnl_30, is_stop_30), ("3.5x", pnl_35, is_stop_35)]:
        results[mult]["net_r"] += pnl
        if is_stop:
            results[mult]["stops"] += 1

print(f"\n\nResults for {min(20, len(d1_trades))} D1 trades:")
for mult, data in results.items():
    print(f"  {mult}: {data['stops']} stops, Net R: {data['net_r']:+.2f}")
    print(f"    Stop avg: {data['net_r']/max(data['stops'],1):+.3f}R per stop")
