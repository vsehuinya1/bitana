#!/usr/bin/env python3
"""Policy sim: cross-cluster streak brake on ny_flush_buy_4h LONG — full history walk, kept vs skipped."""
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict
con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row
def q(sql,*a): return cur.execute(sql,a).fetchall()
def parse(ts): return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

def load(book, live_only=False):
    filt = " AND COALESCE(would_live_accept,0)=1" if live_only else ""
    rows=q("SELECT entry_time,exit_time,pnl_atr,stop_atr,cluster_bucket,btc_trend_state FROM shadow_trades WHERE status='closed' AND strategy=? AND side='LONG'"+filt, book)
    T=[]
    for r in rows:
        if not r['stop_atr']: continue
        T.append(dict(entry=parse(r['entry_time']),exit=parse(r['exit_time']) if r['exit_time'] else None,
                      R=r['pnl_atr']/r['stop_atr'],bucket=r['cluster_bucket'],state=r['btc_trend_state']))
    return sorted(T,key=lambda x:x['entry'])

def policy(T, k=3, block_h=48, cross=True):
    """Walk: streak of consecutive losses (kept trades only). Fire when streak>=k and
    distinct buckets>=2 (cross=True) or ==1 (cross=False). Block block_h from fire exit.
    Skipped trades do not touch streak. Re-fire extends block."""
    streak=0; buckets=set(); blocked_until=None
    fires=[]; skipped=[]; kept=[]
    for t in T:
        if blocked_until and t['entry'] <= blocked_until:
            skipped.append(t); continue
        blocked_until=None
        kept.append(t)
        if t['R']>0: streak=0; buckets=set()
        else:
            streak+=1
            if t['bucket']: buckets.add(t['bucket'])
            cond = (len(buckets)>=2) if cross else (len(buckets)==1)
            if streak>=k and t['exit'] and cond:
                blocked_until = t['exit']+timedelta(hours=block_h)
                fires.append((t['exit'], t['state'], streak, len(buckets)))
    return fires, skipped, kept

def report(label, T, k, bh, cross):
    fires, skipped, kept = policy(T,k,bh,cross)
    bR=sum(t['R'] for t in T); pR=sum(t['R'] for t in kept); sR=sum(t['R'] for t in skipped)
    sE=sR/len(skipped) if skipped else 0
    print(f"{label:44s} fires={len(fires):3d} skipped={len(skipped):4d} skippedE={sE:+.3f} | book {bR:+.1f}R/n={len(T)} -> kept {pR:+.1f}R/n={len(kept)}  dR={pR-bR:+.1f}")
    return fires, skipped

T = load('ny_flush_buy_4h')
base = sum(t['R'] for t in T)
print(f"ny_flush_buy_4h LONG full closed: n={len(T)} totalR={base:+.2f} E={base/len(T):+.3f}")
print("-- cross-cluster gate grid --")
for k in [2,3]:
    for bh in [24,48]:
        report(f"k={k} block={bh}h cross", T, k, bh, True)
print("-- comparators --")
report("k=3 block=48h per-leg ANY-cluster (current-style)", T, 3, 48, None if False else True)  # placeholder replaced below
# per-leg any-cluster: cond always true at streak>=k
def policy_anyleg(T,k,bh):
    streak=0; blocked_until=None; fires=[]; skipped=[]; kept=[]
    for t in T:
        if blocked_until and t['entry']<=blocked_until: skipped.append(t); continue
        blocked_until=None; kept.append(t)
        if t['R']>0: streak=0
        else:
            streak+=1
            if streak>=k and t['exit']:
                blocked_until=t['exit']+timedelta(hours=bh); fires.append((t['exit'],t['state'],streak))
    return fires,skipped,kept
for k in [3,5]:
    f,s,kp = policy_anyleg(T,k,48)
    sR=sum(t['R'] for t in s)
    print(f"{'per-leg k=%d block=48h (any clusters)'%k:44s} fires={len(f):3d} skipped={len(s):4d} skippedE={sR/len(s) if s else 0:+.3f} | kept {sum(t['R'] for t in kp):+.1f}R  dR={sum(t['R'] for t in kp)-base:+.1f}")

# monthly breakdown of best cross variant
print("-- monthly skipped breakdown (k=3 48h cross) --")
fires, skipped, kept = policy(T,3,48,True)
mon=defaultdict(lambda:[0,0.0])
for t in skipped:
    m=t['entry'].strftime('%Y-%m'); mon[m][0]+=1; mon[m][1]+=t['R']
for m in sorted(mon): print(f"   {m}: skipped n={mon[m][0]:3d} sumR={mon[m][1]:+.2f}")
# top skipped days
days=defaultdict(float)
for t in skipped: days[t['entry'].date().isoformat()]+=t['R']
top=sorted(days.items(), key=lambda kv: kv[1])[:5]
print("   worst skipped days (R we avoided):", [(d,round(v,2)) for d,v in top])
topw=sorted(days.items(), key=lambda kv: -kv[1])[:3]
print("   best skipped days (R we lost):   ", [(d,round(v,2)) for d,v in topw])
# state at fires
from collections import Counter
print("   state at fires:", Counter(f[1] for f in fires).most_common())

# live-accept variant
TL = load('ny_flush_buy_4h', live_only=True)
print(f"\nlive-accept variant: n={len(TL)} totalR={sum(t['R'] for t in TL):+.2f}")
report("k=3 block=48h cross [live-accept]", TL, 3, 48, True)

# Friday case
print("\n-- Friday Aug 28: skipped by policy (k=3 48h cross) --")
for t in skipped:
    if t['entry']>=datetime(2026,8,28,tzinfo=timezone.utc) and t['entry']<datetime(2026,8,29,tzinfo=timezone.utc):
        print(f"   skip {t['entry']:%m-%d %H:%M} R={t['R']:+.2f} state={t['state']}")
fri_fires=[f for f in fires if datetime(2026,8,27,tzinfo=timezone.utc)<=f[0]<datetime(2026,8,29,tzinfo=timezone.utc)]
print("   fires Thu-Fri:", [(f[0].strftime('%m-%d %H:%M'), f[1], f[2], f[3]) for f in fri_fires])
con.close()
