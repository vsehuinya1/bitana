#!/usr/bin/env python3
"""sl_opt.py — tighten-SL sweep on the GATE-SURVIVING population.
Method: counterfactual stop at k ATR -> if in-life adverse excursion reached k
before the original exit, trade reprints at -k ATR-units; else keeps original pnl.
Uses run_mae_atr (in-life extreme, entry-referenced ATR units) — decision-time-safe
for exit sims (no post-exit data). Read-only."""
import sqlite3

con = sqlite3.connect('file:/root/bitana/storage/signal_shadow.db?mode=ro', uri=True)
c = con.cursor()
avg_rv = c.execute("SELECT AVG(btc_realized_vol_24h) FROM shadow_trades WHERE status='closed' AND btc_realized_vol_24h IS NOT NULL").fetchone()[0]
q1 = avg_rv*0.7

# sign convention check for run_mae_atr
mn, mx, negs = c.execute("SELECT MIN(run_mae_atr),MAX(run_mae_atr),SUM(CASE WHEN run_mae_atr<0 THEN 1 ELSE 0 END) "
                         "FROM shadow_trades WHERE status='closed'").fetchone()

GATE = """status='closed' AND pnl_atr IS NOT NULL AND run_mae_atr IS NOT NULL
AND NOT (side='SHORT' AND (btc_adx>=35 OR btc_realized_vol_24h<=?))
AND NOT (strategy='burst_follow' AND side='SHORT')
AND NOT (strategy LIKE 'asia_pump%' AND side='SHORT' AND funding_rate_symbol>=0.0001)
AND NOT (side='LONG' AND session='late')"""

def sweep(tag, extra="", args=()):
    rows = c.execute(f"SELECT pnl_atr, run_mae_atr FROM shadow_trades WHERE {GATE} {extra}",
                     (q1,)+args).fetchall()
    n = len(rows)
    base_E = sum(p for p,_ in rows)/n
    base_sum = sum(p for p,_ in rows)
    print(f"\n--- {tag} ---")
    print(f"untightened: n={n} sum={base_sum:+.1f} E={base_E:+.4f}")
    print(f"{'stop_k':>7} {'stopped%':>9} {'E':>9} {'dE':>9} {'sum':>9} {'WR':>7}")
    best=(None,-9e9)
    for k in (0.5,0.75,1.0,1.5,2.0,2.5,3.0,4.0,5.0,6.0,7.5,10.0):
        S=0.0; ns=0
        for p,m in rows:
            mae=abs(m)
            if mae>=k: S+=-k; ns+=1
            else:      S+=p
        E=S/n
        print(f"{k:>7.2f} {ns/n*100:>8.1f}% {E:>+9.4f} {E-base_E:>+9.4f} {S:>+9.1f} {(lambda w,t: w/t)(sum(1 for p,m in rows if (abs(m)>=k and True)) , n)*0+sum(1 for p,m in rows if ((-k) if abs(m)>=k else p)>0)/n:>7.4f}")
        if E>best[1]: best=(k,E)
    print(f"BEST static stop: k={best[0]} ATR -> E={best[1]:+.4f} (vs {base_E:+.4f})")

sweep("ALL gate-survivors")
sweep("gate-survivors LONG", "AND side='LONG'")
sweep("gate-survivors SHORT", "AND side='SHORT'")
sweep("would-live gate-survivors", "AND would_live_accept=1")
print(f"\nrun_mae_atr convention: min={mn}, max={mx}, negatives={negs} (abs used)")
con.close()
