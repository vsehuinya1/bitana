#!/usr/bin/env python3
"""
Brake-sizing counterfactual simulation (Item 3).

Question: Does 18-21% per-trade risk avoid daily-brake trips while preserving winners?
Friday case: 10 losses → 53.7% on a 40% limit

Walk-forward sim on full shadow: trips avoided vs winners blocked.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DB = 'file:storage/signal_shadow.db?mode=ro'
con = sqlite3.connect(DB, uri=True)
cur = con.cursor()
cur.row_factory = sqlite3.Row

# Live config params
DAILY_LOSS_LIMIT_PCT = 0.60  # 60% (current live config)
RISK_PCT_BASE = 0.15  # 15% base risk per trade (from config)
RISK_PCT_REDUCED = 0.12  # 12% reduced risk (config: reduced_risk_pct = 21%, but that's different)
CONSEC_THRESHOLD = 3
CONSEC_REDUCED_TRADES = 5

# Test scenarios: per-trade risk pct
SCENARIOS = [
    ("base_15pct", 0.15),
    ("test_18pct", 0.18),
    ("test_21pct", 0.21),
    ("test_24pct", 0.24),
]

def load_trades():
    """Load all closed shadow trades with R and entry time."""
    rows = cur.execute("""
        SELECT entry_time, pnl_atr, stop_atr, strategy, side, would_live_accept
        FROM shadow_trades
        WHERE status='closed' AND stop_atr IS NOT NULL AND stop_atr > 0
        ORDER BY entry_time
    """).fetchall()
    
    trades = []
    for r in rows:
        t = datetime.fromisoformat(r['entry_time']).replace(tzinfo=timezone.utc)
        R = r['pnl_atr'] / r['stop_atr']
        trades.append({
            'time': t,
            'R': R,
            'strategy': r['strategy'],
            'side': r['side'],
            'live_accept': bool(r['would_live_accept']),
            'date': t.strftime('%Y-%m-%d'),
        })
    return trades

def simulate(trades, risk_pct_per_trade, daily_limit_pct=DAILY_LOSS_LIMIT_PCT):
    """
    Simulate daily P&L with given per-trade risk pct.
    Returns: (total_R, daily_brake_days, trades_taken, trades_blocked, equity_curve)
    """
    daily_loss = defaultdict(float)
    daily_trades = defaultdict(list)
    
    # Track consecutive losses per the new per-bucket logic (simplified: global for now)
    # But for daily brake, we just need daily realized loss
    total_R = 0.0
    trades_taken = 0
    trades_blocked = 0
    equity = 1.0
    equity_curve = []
    
    for tr in trades:
        date = tr['date']
        R = tr['R']
        
        # Check if daily brake would block this trade
        if daily_loss[date] >= daily_limit_pct:
            trades_blocked += 1
            continue
        
        # Take the trade
        pnl_pct = R * risk_pct_per_trade
        daily_loss[date] += max(0, -pnl_pct)  # Only losses count toward daily limit
        total_R += R
        trades_taken += 1
        equity *= (1 + pnl_pct)
        equity_curve.append((tr['time'], equity))
        
        # Track daily trade count
        daily_trades[date].append(R)
    
    # Count brake days
    brake_days = sum(1 for d, loss in daily_loss.items() if loss >= daily_limit_pct)
    
    return {
        'total_R': total_R,
        'brake_days': brake_days,
        'trades_taken': trades_taken,
        'trades_blocked': trades_blocked,
        'final_equity': equity,
        'daily_loss': dict(daily_loss),
        'equity_curve': equity_curve,
    }

def main():
    trades = load_trades()
    print(f"Loaded {len(trades)} closed shadow trades")
    print(f"Date range: {trades[0]['time']} to {trades[-1]['time']}")
    print(f"Daily limit: {DAILY_LOSS_LIMIT_PCT:.0%}")
    print(f"Consecutive loss threshold: {CONSEC_THRESHOLD}, reduced trades: {CONSEC_REDUCED_TRADES}")
    print()
    
    # Run scenarios
    results = {}
    for name, risk_pct in SCENARIOS:
        res = simulate(trades, risk_pct)
        results[name] = res
        print(f"{name:15s} | risk={risk_pct:.0%} | total_R={res['total_R']:+.1f} | "
              f"brake_days={res['brake_days']:3d} | taken={res['trades_taken']:5d} | "
              f"blocked={res['trades_blocked']:5d} | equity={res['final_equity']:.3f}")
    
    print()
    print("=" * 80)
    print("COUNTERFACTUAL: Friday-type day analysis (10 losses in a day)")
    print("=" * 80)
    
    # Find worst days
    for name, risk_pct in SCENARIOS:
        res = results[name]
        worst_day = max(res['daily_loss'].items(), key=lambda x: x[1]) if res['daily_loss'] else ('none', 0)
        print(f"{name:15s} | worst day loss: {worst_day[1]:.1%} on {worst_day[0]}")
    
    # Analyze the specific Friday mentioned (Aug 22 or 25?)
    print()
    print("Day-by-day for key dates:")
    for name, risk_pct in SCENARIOS:
        res = results[name]
        for date in ['2026-08-22', '2026-08-25', '2026-08-28']:
            loss = res['daily_loss'].get(date, 0)
            print(f"  {name:15s} {date}: daily_loss={loss:.1%} {'BRAKE' if loss >= DAILY_LOSS_LIMIT_PCT else ''}")

if __name__ == '__main__':
    main()