#!/usr/bin/env python3
"""Part 4: nyflush cross-cluster signal robustness + Aug28 fire-time case study."""
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict

con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row
def q(sql,*a): return cur.execute(sql,a).fetchall()
def parse(ts): return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

def load(book):
    rows=q("SELECT entry_time,exit_time,pnl_atr,stop_atr,cluster_bucket,btc_trend_state,is_weekend FROM shadow_trades WHERE status='closed' AND strategy=? AND side='LONG'",book)
    T=[]
    for r in rows:
        if not r['stop_atr']: continue
        T.append(dict(entry=parse(r['entry_time']),exit=parse(r['exit_time']) if r['exit_time'] else None,
                      R=r['pnl_atr']/r['stop_atr'],bucket=r['cluster_bucket'],state=r['btc_trend_state'],we=r['is_weekend']))
    return sorted(T,key=lambda x:x['entry'])

# ---- nyflush k=3 cross-cluster: concentration + weekend split
T=load('ny_flush_buy_4h')
streak=0; buckets=set(); fires=[]
for t in T:
    if t['R']>0: streak=0; buckets=set()
    else:
        streak+=1
        if t['bucket']: buckets.add(t['bucket'])
        if streak>=3 and t['exit'] and len(buckets)>=2:
            fwd=[x['R'] for x in T if t['exit']<x['entry']<=t['exit']+timedelta(hours=48)][:10]
            fires.append((t['exit'],fwd))
per_day=defaultdict(list)
for ft,f in fires: per_day[ft.date().isoformat()]+=f
allf=[x for _,f in fires for x in f]
print("ny_flush k=3 DIFFERENT-cluster: episodes=%d fwd-trades=%d E=%+.3f"%(len(fires),len(allf),sum(allf)/len(allf)))
days=sorted(per_day.items(), key=lambda kv: sum(kv[1]), reverse=True)
top=days[0]
print("top-day: %s netR=%+.2f of %+.2f total (%.0f%%)"%(top[0],sum(top[1]),sum(allf),100*sum(top[1])/sum(allf)))
print("top 5 days by fwd netR:")
for d,f in days[:5]: print("   %s n=%3d netR=%+.2f"%(d,len(f),sum(f)))
we=[x for _,f in fires for x in f if x]
# weekend tag on the forward trades themselves: rebuild with tag
fwd_tags=[]
for ft,_ in fires:
    for x in T:
        if ft<x['entry']<=ft+timedelta(hours=48):
            fwd_tags.append((x['R'],x['we']))
        if len(fwd_tags)>len(allf)+50: break
wk=[r for r,w in fwd_tags if not w]; wke=[r for r,w in fwd_tags if w]
print("fwd by trade weekend tag: weekday n=%d E=%+.3f | weekend n=%d E=%+.3f"%(len(wk),sum(wk)/len(wk) if wk else 0,len(wke),sum(wke)/len(wke) if wke else 0))

# ---- Aug 28 case study: streak evolution across bf+nyflush, fire times vs live brake 20:05:30Z
print("\nAug 27-29 shadow LONG trades (bf + nyflush), chronological:")
B=load('burst_follow')+load('ny_flush_buy_4h')
B=sorted(B,key=lambda x:x['entry'])
streak=0; buckets=set()
fired=None
for t in B:
    if t['entry']<datetime(2026,8,27,tzinfo=timezone.utc): continue
    if t['R']>0: streak=0; buckets=set()
    else:
        streak+=1
        if t['bucket']: buckets.add(t['bucket'])
    mark=""
    if not fired and streak>=3 and len(buckets)>=2 and t['exit'] and t['R']<0:
        fired=t['exit']; mark=" <== k=3 cross-cluster FIRE"
    tag = 'bf' if any(t is x for x in B) and t['entry'] in [x['entry'] for x in B[:1]] else ''
    print("  %s %s R=%+.2f streak=%d bkt=%d state=%s%s"%(t['entry'].strftime('%m-%d %H:%M'),(t['exit'].strftime('%H:%M') if t['exit'] else '--'),t['R'],streak,len(buckets),t['state'] or '-',mark))
con.close()
