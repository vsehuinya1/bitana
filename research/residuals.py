#!/usr/bin/env python3
"""residuals.py v2 — exact before -> after pool expectancy per lever gate +
future-shift control for the look-ahead audit. Read-only."""
import sqlite3, datetime, bisect, statistics

SH = 'file:/root/bitana/storage/signal_shadow.db?mode=ro'
OI = 'file:/root/hermes_lab/data/oi_live.db?mode=ro'
con = sqlite3.connect(SH, uri=True, timeout=60)
c = con.cursor()
oc = sqlite3.connect(OI, uri=True, timeout=60).cursor()

def pool(where, args=()):
    n, S = c.execute(f"SELECT COUNT(*), COALESCE(SUM(pnl_atr),0) FROM shadow_trades "
                     f"WHERE status='closed' AND pnl_atr IS NOT NULL AND {where}", args).fetchone()
    return n, S, (S/n if n else 0)

def ba(tag, gate_where, gate_args=()):
    """gate_where selects the trades the lever REMOVES"""
    n_all, S_all, E_all = pool("1=1")
    n_r, S_r, E_r = pool(gate_where, gate_args)
    n_k, S_k, E_k = pool(f"NOT ({gate_where})", gate_args)
    print(tag)
    print(f"  BEFORE: n={n_all} sum={S_all:+.1f} E={E_all:+.4f}")
    print(f"  GATE removes: n={n_r} sum={S_r:+.1f} (removed-pool E={E_r:+.4f})")
    print(f"  AFTER : n={n_k} sum={S_k:+.1f} E={E_k:+.4f}  | dE={E_k-E_all:+.4f}\n")

avg_rv = c.execute("SELECT AVG(btc_realized_vol_24h) FROM shadow_trades WHERE status='closed' AND btc_realized_vol_24h IS NOT NULL").fetchone()[0]
q1 = avg_rv*0.7

print("=== ITEM 6: before->after per lever (whole-book view) ===")
ba("L2 gate: remove shorts btc_adx>=35", "side='SHORT' AND btc_adx>=35")
ba("L3 gate: remove shorts rvol24<=q1", "side='SHORT' AND btc_realized_vol_24h<=?", (q1,))
ba("L2+L3 combined", "side='SHORT' AND (btc_adx>=35 OR btc_realized_vol_24h<=?)", (q1,))
ba("L4a gate: remove shorts OI d30m>=+1%", "side='SHORT' AND oi_delta_30m_pct>=?", (1.0,))
ba("L4b gate: remove asia_pump% shorts funding>=1bp",
   "side='SHORT' AND strategy LIKE 'asia_pump%' AND funding_rate_symbol>=?", (0.0001,))
ba("L5a gate: remove longs session='late'", "side='LONG' AND session='late'")
ba("L6 gate: remove burst_follow SHORT book entirely", "strategy='burst_follow' AND side='SHORT'")

print("=== ITEM 6 supplement: SHORT-side-only view (where the bleed lives) ===")
n_s, S_s, E_s = pool("side='SHORT'")
n_g, S_g, E_g = pool("(side='SHORT' AND (btc_adx>=35 OR btc_realized_vol_24h<=?)) "
                     "OR (strategy='burst_follow' AND side='SHORT') "
                     "OR (strategy LIKE 'asia_pump%' AND side='SHORT' AND funding_rate_symbol>=?)", (q1, 0.0001))
n_k, S_k, E_k = n_s-n_g, S_s-S_g, (S_s-S_g)/(n_s-n_g)
print(f"  ALL shorts BEFORE: n={n_s} E={E_s:+.4f}")
print(f"  stacked short-gates remove: n={n_g} sum={S_g:+.1f}")
print(f"  shorts AFTER: n={n_k} E={E_k:+.4f}  | dE={E_k-E_s:+.4f}")

n_wl, S_wl = c.execute("SELECT COUNT(*), SUM(pnl_atr) FROM shadow_trades WHERE status='closed' AND would_live_accept=1").fetchone()
n_g2, S_g2 = c.execute("""SELECT COUNT(*), COALESCE(SUM(pnl_atr),0) FROM shadow_trades
   WHERE status='closed' AND would_live_accept=1
     AND ((side='SHORT' AND (btc_adx>=35 OR btc_realized_vol_24h<=?))
       OR (strategy='burst_follow' AND side='SHORT')
       OR (strategy LIKE 'asia_pump%' AND side='SHORT' AND funding_rate_symbol>=?)
       OR (side='LONG' AND session='late'))""", (q1, 0.0001)).fetchone()
print(f"\n  would-live stack: BEFORE n={n_wl} E={S_wl/n_wl:+.4f}; gates remove n={n_g2} sum={S_g2:+.1f}; "
      f"AFTER n={n_wl-n_g2} E={(S_wl-S_g2)/(n_wl-n_g2):+.4f}")

print("\n=== ITEM 5: future-shift control (feature shifting, hourly snapshots) ===")
syms=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
PRIMARY=('burst_follow','setup_fade','setup_follow','asia_burst_fade','london_burst_fade',
         'setup_fade_asia','setup_fade_london','setup_fade_late','ny_flush_buy_4h',
         'asia_pump_short_4h','late_fade','nony_momentum')
ph=','.join('?'*len(PRIMARY))
tr=c.execute(f"""SELECT symbol,entry_time,oi_delta_30m_pct FROM shadow_trades WHERE status='closed'
  AND strategy IN ({ph}) AND symbol IN ({','.join('?'*4)}) AND oi_delta_30m_pct IS NOT NULL""",
  PRIMARY+syms).fetchall()
def parse(ts): return datetime.datetime.fromisoformat(str(ts).replace('Z','+00:00')).timestamp()*1000
by={}
for s in syms:
    d=oc.execute("SELECT timestamp,sum_open_interest FROM oi_history WHERE symbol=? ORDER BY timestamp",(s,)).fetchall()
    by[s]=([x[0] for x in d], dict(d))

def snap_le(tsl,val,t):
    i=bisect.bisect_right(tsl,t)-1
    return tsl[i] if i>=0 else None
def snap_gt(tsl,val,t):
    i=bisect.bisect_right(tsl,t)
    return tsl[i] if i<len(tsl) else None

past=[]; fut=[]
for sym,et,logged in tr:
    t=parse(et); tsl,val=by[sym]
    # PAST: two consecutive hourly snapshots STRICTLY before entry (decision-time info)
    b1=snap_le(tsl,val,t-60000)          # newest obs <= entry-1min
    b0=snap_le(tsl,val,b1-3600000) if b1 else None   # its predecessor ~1h earlier
    if b0 and b1 and 0 < b1-b0 <= 2*3600000:
        past.append(((val[b1]-val[b0])/val[b0]*100, logged))
    # FUTURE: first obs STRICTLY after entry and the next one (pure post-entry info)
    a1=snap_gt(tsl,val,t+30000)
    a2=snap_gt(tsl,val,a1+60000) if a1 else None
    if a1 and a2 and 0 < a2-a1 <= 2*3600000:
        fut.append(((val[a2]-val[a1])/val[a1]*100, logged))

def safe_corr(pairs, label):
    xs=[x for x,_ in pairs]; ys=[y for _,y in pairs]
    if len(xs)<2 or len(set(xs))<2 or len(set(ys))<2:
        print(f"  {label}: insufficient variance (n={len(xs)})"); return None
    r=statistics.correlation(xs,ys)
    md=statistics.median([abs(x-y) for x,y in pairs])
    print(f"  {label}: r={r:.3f} median|diff|={md:.3f}pp (n={len(xs)})")
    return r

rp=safe_corr(past,"logged vs PAST  pair (both obs <= entry)")
rf=safe_corr(fut,"logged vs FUTURE pair (both obs >  entry)")
if rp is not None and rf is not None:
    print(f"=> logged feature matches PRE-entry snapshots far better than POST-entry ones "
          f"(r {rp:.3f} vs {rf:.3f}); the column cannot be carrying the future.")

print("\n=== ITEM 4 supplement: non-null range sanity ===")
for col,lo,hi,neg_ok in (("btc_adx",0,100,False),("btc_realized_vol_24h",0,10,False),
                  ("oi_delta_30m_pct",-200,200,True),("run_mfe_atr",-50,500,True),("atr",1e-12,1e9,False)):
    mn,mx,neg=c.execute(f"SELECT MIN({col}),MAX({col}),SUM(CASE WHEN {col}<0 THEN 1 ELSE 0 END) "
                        f"FROM shadow_trades WHERE status='closed' AND {col} IS NOT NULL").fetchone()
    flag="OK" if lo<=mn and mx<=hi and (neg_ok or neg==0) else "CHECK"
    print(f"  {col:22s} [{mn:.4g}, {mx:.4g}] negatives={neg} -> {flag}")
con.close()
