#!/usr/bin/env python3
"""Part 2: event-window expectancy + cross-cluster loss-streak detector."""
import sqlite3, json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row

def q(sql, *a): return cur.execute(sql, a).fetchall()

anchors = [(n, datetime.fromisoformat(t)) for n, t in json.load(open('/tmp/anchors.json'))]

BOOKS = ['burst_follow', 'ny_flush_buy_4h']

# ============ A. EVENT-WINDOW EXPECTANCY ============
print("="*70)
print("A. EVENT-WINDOW EXPECTANCY (R = pnl_atr/stop_atr)")
print("="*70)
for book in BOOKS:
    for filt, flabel in [("", "full-closed"), (" AND COALESCE(would_live_accept,0)=1", "live-accept")]:
        trades = q(f"SELECT entry_time, pnl_atr, stop_atr, btc_trend_state FROM shadow_trades WHERE status='closed' AND strategy=? AND side='LONG'{filt}", book)
        T = [(datetime.fromisoformat(t['entry_time']).replace(tzinfo=timezone.utc), t['pnl_atr']/t['stop_atr'], t['btc_trend_state']) for t in trades if t['stop_atr']]
        print(f"\n-- {book} LONG [{flabel}] n={len(T)} baseline E={sum(x[1] for x in T)/len(T):+.3f} --")
        for name, t0 in anchors:
            for lo, hi, lab in [(-6, 0, "pre-6h"), (0, 6, "post-6h"), (6, 24, "post6-24h")]:
                sel = [x[1] for x in T if 0 <= (x[0]-t0).total_seconds()/3600 - lo < hi - lo]
                if sel:
                    print(f"   {name:11s} {lab:10s} n={len(sel):3d} E={sum(sel)/len(sel):+.3f} WR={100*sum(1 for x in sel if x>0)/len(sel):.0f}%")
                else:
                    print(f"   {name:11s} {lab:10s} n=  0")
con.close()
