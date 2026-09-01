#!/usr/bin/env python3
"""Part 5: nyflush k=3 cross-cluster robustness (NULL-bucket era exclusion, weekday-only, per-cluster streak vs per-leg)."""
import sqlite3
from datetime import datetime, timedelta, timezone
con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row
def q(sql,*a): return cur.execute(sql,a).fetchall()
def parse(ts): return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

rows=q("SELECT entry_time,exit_time,pnl_atr,stop_atr,cluster_bucket,btc_trend_state FROM shadow_trades WHERE status='closed' AND strategy='ny_flush_buy_4h' AND side='LONG'")
T=[]
for r in rows:
    if not r['stop_atr']: continue
    T.append(dict(entry=parse(r['entry_time']),exit=parse(r['exit_time']) if r['exit_time'] else None,
                  R=r['pnl_atr']/r['stop_atr'],bucket=r['cluster_bucket'],state=r['btc_trend_state'],
                  wd=r['entry_time'][10:13]))
T=sorted(T,key=lambda x:x['entry'])

def run(trades, per_cluster=False, k=3):
    streak=0; buckets=set(); fired_on_cluster=None; fires=[]
    for t in trades:
        if t['R']>0: streak=0; buckets=set(); fired_on_cluster=None
        else:
            if per_cluster:
                # cluster-level streak: consecutive losses within same bucket count ONCE
                if t['bucket']!=fired_on_cluster: streak+=1
            else:
                streak+=1
            if t['bucket']: buckets.add(t['bucket'])
            if streak>=k and t['exit'] and len(buckets)>=2:
                fwd=[x['R'] for x in trades if t['exit']<x['entry']<=t['exit']+timedelta(hours=48)][:10]
                fires.append((t['exit'],fwd))
                if per_cluster: fired_on_cluster=t['bucket']
    allf=[x for _,f in fires for x in f]
    return len(fires), len(allf), (sum(allf)/len(allf) if allf else 0)

print("ny_flush_buy_4h LONG (n=%d):"%len(T))
n,nf,E = run(T, per_cluster=False); print("  per-LEG   k=3 cross-cluster: ep=%d fwd=%d E=%+.3f"%(n,nf,E))
n,nf,E = run(T, per_cluster=True);  print("  per-CLUSTER k=3 cross-cluster: ep=%d fwd=%d E=%+.3f"%(n,nf,E))
# NULL-bucket era excluded
T2=[t for t in T if t['bucket']]
n,nf,E = run(T2, per_cluster=False); print("  per-LEG, NULL-bucket trades dropped (n=%d): ep=%d fwd=%d E=%+.3f"%(len(T2),n,nf,E))
# weekday only
T3=[t for t in T if t['wd'] not in (' Sat',' Sun')]
n,nf,E = run(T3, per_cluster=False); print("  per-LEG weekday-only (n=%d): ep=%d fwd=%d E=%+.3f"%(len(T3),n,nf,E))
# same-cluster comparator
def run_same(trades, k=3):
    streak=0; buckets=set(); fires=[]
    for t in trades:
        if t['R']>0: streak=0; buckets=set()
        else:
            streak+=1
            if t['bucket']: buckets.add(t['bucket'])
            if streak>=k and t['exit'] and len(buckets)==1:
                fwd=[x['R'] for x in trades if t['exit']<x['entry']<=t['exit']+timedelta(hours=48)][:10]
                fires.append((t['exit'],fwd))
    allf=[x for _,f in fires for x in f]
    return len(fires), len(allf), (sum(allf)/len(allf) if allf else 0)
n,nf,E=run_same(T); print("  per-LEG k=3 SAME-cluster: ep=%d fwd=%d E=%+.3f"%(n,nf,E))
n,nf,E=run_same(T2); print("  per-LEG k=3 SAME-cluster, NULL dropped: ep=%d fwd=%d E=%+.3f"%(n,nf,E))
con.close()
