#!/usr/bin/env python3
"""Part 3: cross-cluster loss-streak detector + BTC corrective-path dating + Aug28 case study."""
import sqlite3, json, urllib.request, time
from datetime import datetime, timedelta, timezone

con = sqlite3.connect('file:storage/signal_shadow.db?mode=ro', uri=True)
cur = con.cursor(); cur.row_factory = sqlite3.Row
def q(sql, *a): return cur.execute(sql, a).fetchall()

def parse(ts): return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) if '+' not in ts and ts.endswith('Z')==False else datetime.fromisoformat(ts)

# ============ BTC path dating ============
def fetch(interval, start_ms, end_ms):
    out=[]; s=start_ms
    while s<end_ms:
        url=f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval={interval}&startTime={s}&endTime={end_ms}&limit=1000"
        with urllib.request.urlopen(url,timeout=30) as f: d=json.loads(f.read())
        if not d: break
        out+=d; s=d[-1][6]+1
        if len(d)<1000: break
        time.sleep(0.15)
    return out
s0=int(datetime(2026,7,25,tzinfo=timezone.utc).timestamp()*1000); e0=int(datetime(2026,8,29,tzinfo=timezone.utc).timestamp()*1000)
k1=fetch('1d',s0,e0)
print("="*70); print("B. BTC DAILY CLOSES Jul25-Aug28 (empirical corrective path)")
prev=None
for k in k1:
    ts=datetime.fromtimestamp(k[0]/1000,tz=timezone.utc); o,h,l,c=float(k[1]),float(k[2]),float(k[3]),float(k[4])
    chg=f"{(c/prev-1)*100:+.1f}%" if prev else ""
    print(f"   {ts:%m-%d} O={o:,.0f} H={h:,.0f} L={l:,.0f} C={c:,.0f} {chg}")
    prev=c

# ============ streak detector ============
print("="*70)
print("C. CROSS-CLUSTER LOSS-STREAK DETECTOR (decision = k-th loss EXIT)")
def run_stream(trades, k, label):
    # trades: list of dicts ordered by entry_time with exit_time, R, bucket, state
    T=sorted(trades, key=lambda x: x['entry'])
    signals=[]           # (fire_exit_time, state, streak, buckets, forward list)
    streak=0; buckets=set()
    i=0
    for t in T:
        if t['R']>0:
            streak=0; buckets=set()
        else:
            streak+=1
            if t['bucket']: buckets.add(t['bucket'])
            if streak>=k and t['exit']:
                cond = len(buckets)>=2
                same = len(buckets)==1
                # forward: next 10 trades entering within 48h after exit
                fwd=[x['R'] for x in T if t['exit']<x['entry']<=t['exit']+timedelta(hours=48)][:10]
                signals.append((t['exit'], t['state'], streak, len(buckets), fwd, cond, same))
    # dedupe: keep first fire per losing episode (cond OR same), reset when a win occurs after fire
    return T, signals

def summarize(signals, cond):
    sel=[s for s in signals if s[5]==cond]
    if not sel: return None
    fw=[f for s in sel for f in s[4]]
    lags=[]
    for s in sel:
        pass
    return dict(n_ep=len(sel), n_fwd=len(fw), E=sum(fw)/len(fw) if fw else 0, WR=100*sum(1 for x in fw if x>0)/len(fw) if fw else 0)

for book in ['burst_follow','ny_flush_buy_4h']:
    rows=q("SELECT entry_time, exit_time, pnl_atr, stop_atr, cluster_bucket, btc_trend_state FROM shadow_trades WHERE status='closed' AND strategy=? AND side='LONG'", book)
    trades=[]
    for r in rows:
        if not r['stop_atr']: continue
        trades.append(dict(entry=parse(r['entry_time']), exit=parse(r['exit_time']) if r['exit_time'] else None,
                           R=r['pnl_atr']/r['stop_atr'], bucket=r['cluster_bucket'], state=r['btc_trend_state']))
    allt=[t['R'] for t in trades]
    base=sum(allt)/len(allt)
    print(f"\n-- {book} LONG n={len(trades)} baseline E={base:+.3f} --")
    for k in [2,3]:
        T,sigs=run_stream(trades,k,book)
        for cond,lab in [(True,'DIFFERENT clusters'),(False,'same-cluster  ')]:
            s=summarize(sigs,cond)
            if s: print(f"   k={k} {lab} episodes={s['n_ep']:3d} fwd-trades={s['n_fwd']:4d} fwdE={s['E']:+.3f} fwdWR={s['WR']:.0f}%")
            else: print(f"   k={k} {lab} episodes=0")

# regime lag at cross-cluster k=3 fires for bf
print("\n-- regime lag at k=3 DIFFERENT-cluster fires (burst_follow) --")
rows=q("SELECT entry_time, exit_time, pnl_atr, stop_atr, cluster_bucket, btc_trend_state FROM shadow_trades WHERE status='closed' AND strategy='burst_follow' AND side='LONG'")
trades=[]
for r in rows:
    if not r['stop_atr']: continue
    trades.append(dict(entry=parse(r['entry_time']), exit=parse(r['exit_time']) if r['exit_time'] else None,
                       R=r['pnl_atr']/r['stop_atr'], bucket=r['cluster_bucket'], state=r['btc_trend_state']))
T=sorted(trades,key=lambda x:x['entry'])
streak=0; buckets=set(); fires=[]
for t in T:
    if t['R']>0: streak=0; buckets=set()
    else:
        streak+=1
        if t['bucket']: buckets.add(t['bucket'])
        if streak>=3 and t['exit'] and len(buckets)>=2:
            fires.append((t['exit'], t['state']))
lags=[]
for ft, st in fires:
    nxt=[x['entry'] for x in T if x['entry']>ft and x['state'] and x['state']!=st]
    if nxt: lags.append(((min(nxt)-ft).total_seconds()/3600, st))
import statistics
if lags:
    ls=[l for l,_ in lags]
    print(f"   fires={len(lags)} lag-to-state-flip h: median={statistics.median(ls):.1f} p25={sorted(ls)[len(ls)//4]:.1f} p75={sorted(ls)[3*len(ls)//4]:.1f}")
    from collections import Counter
    print("   state at fire:", Counter(s for _,s in lags).most_common())
con.close()
