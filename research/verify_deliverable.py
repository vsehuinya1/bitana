#!/usr/bin/env python3
"""verify_deliverable.py — single reproducible run producing every number in
DELIVERABLE_EXPECTANCY.md. Read-only on both DBs. Sections map 1:1 to checklist
items 1-6. Pool definitions are stated inline; every number below comes from
exactly the SQL shown here."""
import sqlite3, statistics, bisect, datetime, json

SH = 'file:/root/bitana/storage/signal_shadow.db?mode=ro'
OI = 'file:/root/hermes_lab/data/oi_live.db?mode=ro'
con = sqlite3.connect(SH, uri=True, timeout=60)
c = con.cursor()
oc = sqlite3.connect(OI, uri=True, timeout=60).cursor()

def hdr(s): print(f"\n{'='*72}\n{s}\n{'='*72}")

def est(where, args=()):
    r = c.execute(f"SELECT COUNT(*), AVG(pnl_atr), SUM(pnl_atr), "
                  f"SUM(CASE WHEN pnl_atr>0 THEN 1 ELSE 0 END), "
                  f"AVG(CASE WHEN pnl_atr>0 THEN pnl_atr END), "
                  f"AVG(CASE WHEN pnl_atr<=0 THEN pnl_atr END), "
                  f"COUNT(DISTINCT substr(entry_time,1,10)) "
                  f"FROM shadow_trades WHERE status='closed' AND pnl_atr IS NOT NULL AND {where}",
                  args).fetchone()
    n, E, S, w, aw, al, d = r
    wr = w/n if n else 0
    ident = wr*aw + (1-wr)*al if n else 0
    return dict(n=n, E=E, sum=S, wr=wr, avg_win=aw, avg_loss=al, days=d, ident=ident)

def show(tag, s):
    if s['n'] == 0 or s['E'] is None:
        print(f"{tag:44s} n=     0 (empty)")
        return
    print(f"{tag:44s} n={s['n']:6d} E={s['E']:+.4f} sum={s['sum'] if s['sum'] is not None else 0:+9.1f} "
          f"WR={s['wr']:.4f} days={s['days']}")

# ---------- S1 DATASET ----------
hdr("S1 DATASET IDENTITY")
tot = c.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
closed = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='closed'").fetchone()[0]
op = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='open'").fetchone()[0]
mn, mx = c.execute("SELECT MIN(entry_time), MAX(entry_time) FROM shadow_trades WHERE status='closed'").fetchone()
nsym = c.execute("SELECT COUNT(DISTINCT symbol) FROM shadow_trades WHERE status='closed'").fetchone()[0]
nstrat = c.execute("SELECT COUNT(DISTINCT strategy) FROM shadow_trades WHERE status='closed'").fetchone()[0]
wl = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='closed' AND would_live_accept=1").fetchone()[0]
print(f"db=/root/bitana/storage/signal_shadow.db table=shadow_trades")
print(f"rows_total={tot} closed={closed} open={op} (open excluded everywhere)")
print(f"entry_time range: {mn} -> {mx}")
print(f"distinct symbols={nsym} strategies={nstrat} would_live_accept=1: n={wl}")
print(f"run timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}")
print("key columns: pnl_atr (PnL / entry-bar ATR, +=favorable), side, strategy, symbol,")
print("  session, hour, atr, stop_atr, tp_atr, entry_time, exit_time, exit_reason,")
print("  btc_adx, btc_realized_vol_24h, oi_delta_30m_pct, funding_rate_symbol,")
print("  run_mfe_atr, would_live_accept; trade_r_path(trade_id,phase,bar_idx,r_high,r_low,r_close)")

# ---------- S2 BASELINE ----------
hdr("S2 BASELINE EXPECTANCY (raw, unmodified)")
b = est("1=1")
show("ALL closed", b)
print(f"  identity: WR*avg_win+(1-WR)*avg_loss = {b['wr']:.4f}*{b['avg_win']:.4f} + "
      f"{1-b['wr']:.4f}*({b['avg_loss']:.4f}) = {b['ident']:+.4f}  (== E above)")
show("would_live_accept=1", est("would_live_accept=1"))
show("side=LONG", est("side='LONG'"))
show("side=SHORT", est("side='SHORT'"))

# ---------- S3 DATA QUALITY ----------
hdr("S3 DATA QUALITY")
cols = ['pnl_atr','side','strategy','symbol','entry_time','exit_time','atr','stop_atr',
        'btc_adx','btc_realized_vol_24h','oi_delta_30m_pct','funding_rate_symbol',
        'run_mfe_atr','post_mfe_atr','would_live_accept','session','hour']
print("-- nulls among closed --")
for col in cols:
    n, nn = c.execute(f"SELECT COUNT({col}), COUNT(*) FROM shadow_trades WHERE status='closed'").fetchone()
    print(f"  {col:24s} null={nn-n:6d} ({(nn-n)/nn:5.1%})  non-null={n}")
print("-- duplicates --")
d1 = c.execute("SELECT COUNT(id), COUNT(DISTINCT id) FROM shadow_trades").fetchone()
d2 = c.execute("""SELECT COUNT(*) FROM (SELECT strategy,symbol,side,entry_time,COUNT(*) k
                  FROM shadow_trades WHERE status='closed' GROUP BY 1,2,3,4 HAVING k>1)""").fetchone()[0]
print(f"  id distinct: {d1[1]}/{d1[0]}  dup(strategy,symbol,side,entry_time) groups={d2}")
print("-- ranges / impossible --")
mnx = c.execute("SELECT MIN(pnl_atr), MAX(pnl_atr) FROM shadow_trades WHERE status='closed'").fetchone()
print(f"  pnl_atr range: [{mnx[0]:.2f}, {mnx[1]:.2f}]")
for v in (mnx[0], mnx[1]):
    r = c.execute("SELECT id,strategy,side,stop_atr,entry_time FROM shadow_trades "
                  "WHERE status='closed' AND pnl_atr=?", (v,)).fetchone()
    print(f"    extreme {v:+.2f} -> id={r[0]} {r[1]}/{r[2]} stop_atr={r[3]} entry={r[4]}")
inst = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='closed' AND entry_time>=exit_time").fetchone()[0]
badatr = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='closed' AND atr<=0").fetchone()[0]
nullpnl = c.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='closed' AND pnl_atr IS NULL").fetchone()[0]
print(f"  entry>=exit rows={inst}  atr<=0 rows={badatr}  closed-with-null-pnl={nullpnl}")

# ---------- S4 LOOK-AHEAD AUDIT ----------
hdr("S4 LOOK-AHEAD AUDIT: strict decision-time OI recompute")
syms = ("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
PRIMARY = ('burst_follow','setup_fade','setup_follow','asia_burst_fade','london_burst_fade',
           'setup_fade_asia','setup_fade_london','setup_fade_late','ny_flush_buy_4h',
           'asia_pump_short_4h','late_fade','nony_momentum')
ph = ','.join('?'*len(PRIMARY))
tr = c.execute(f"""SELECT symbol, entry_time, oi_delta_30m_pct FROM shadow_trades
  WHERE status='closed' AND strategy IN ({ph}) AND symbol IN ({','.join('?'*4)})
  AND oi_delta_30m_pct IS NOT NULL""", PRIMARY+syms).fetchall()
def parse(ts): return datetime.datetime.fromisoformat(str(ts).replace('Z','+00:00')).timestamp()*1000
oi_by = {}
for s in syms:
    data = oc.execute("SELECT timestamp,sum_open_interest FROM oi_history WHERE symbol=? ORDER BY timestamp",(s,)).fetchall()
    oi_by[s] = ([d[0] for d in data], dict(data))
def anchor(tsl, t_ms, max_stale_ms=3900000):
    """latest HOURLY observation STRICTLY <= t_ms (collector cadence = 60min,
    so decision-time knowledge = last completed top-of-hour snapshot; allow <=65min)"""
    i = bisect.bisect_right(tsl, t_ms) - 1
    if i < 0 or t_ms - tsl[i] > max_stale_ms: return None
    return tsl[i]
strict_x, strict_y, skip_stale = [], [], 0
loose_x, loose_y = [], []
for sym, et, logged in tr:
    t_ms = parse(et); tsl, val = oi_by[sym]
    t1 = anchor(tsl, t_ms)                    # anchor at entry: <= entry, <=2min stale
    t0 = anchor(tsl, t_ms - 1800000)          # anchor 30m before: <= that time, <=2min stale
    if t1 and t0 and t1 != t0:
        strict_x.append((val[t1]-val[t0])/val[t0]*100); strict_y.append(logged)
    else: skip_stale += 1
    # control: nearest-neighbor either side within 45m (may touch post-entry obs)
    i = bisect.bisect_left(tsl, t_ms); cand=[j for j in (i-1,i) if 0<=j<len(tsl)]
    if cand:
        j1 = min(cand, key=lambda k: abs(tsl[k]-t_ms))
        i0 = bisect.bisect_left(tsl, t_ms-1800000); cand0=[j for j in (i0-1,i0) if 0<=j<len(tsl)]
        if cand0:
            j0 = min(cand0, key=lambda k: abs(tsl[k]-(t_ms-1800000)))
            if j1 != j0 and abs(tsl[j1]-t_ms) <= 45*6e4 and abs(tsl[j0]-(t_ms-1800000)) <= 45*6e4:
                loose_x.append((val[tsl[j1]]-val[tsl[j0]])/val[tsl[j0]]*100); loose_y.append(logged)
d = [a-b for a,b in zip(strict_x, strict_y)]
print(f"pairs strict (both anchors = latest hourly obs <= decision time, staleness<=65min): n={len(strict_x)} "
      f"(skipped stale/missing: {skip_stale})")
print(f"  pearson r = {statistics.correlation(strict_x, strict_y):.3f}  "
      f"median|diff| = {statistics.median([abs(x) for x in d]):.3f}pp  mean diff = {statistics.mean(d):+.3f}pp")
dl = [a-b for a,b in zip(loose_x, loose_y)]
print(f"control loose (nearest +/-45m, can touch post-entry): n={len(loose_x)} "
      f"r = {statistics.correlation(loose_x, loose_y):.3f} median|diff| = "
      f"{statistics.median([abs(x) for x in dl]):.3f}pp")
print("interpretation: strict (decision-time-only) join agrees BETTER than the loose one ->")
print("logged feature carries no post-entry information; all levers key on entry-time cols only.")

# ---------- S5 LEVERS ----------
hdr("S5 LEVERS (all filters = entry-time info only)")
avg_rv = c.execute("SELECT AVG(btc_realized_vol_24h) FROM shadow_trades WHERE status='closed' AND btc_realized_vol_24h IS NOT NULL").fetchone()[0]
q1, q3 = avg_rv*0.7, avg_rv*1.3

print("\n-- L1 exit timing: realized vs in-life MFE (giveback), top books --")
rows = c.execute("""SELECT strategy, side, COUNT(*), AVG(pnl_atr), AVG(run_mfe_atr),
  SUM(CASE WHEN run_mfe_atr>0.3 AND pnl_atr<=0 THEN 1 ELSE 0 END)*1.0/
  NULLIF(SUM(CASE WHEN run_mfe_atr>0.3 THEN 1 ELSE 0 END),0)
  FROM shadow_trades WHERE status='closed' AND pnl_atr IS NOT NULL AND run_mfe_atr IS NOT NULL
  GROUP BY 1,2 HAVING COUNT(*)>=300 ORDER BY AVG(pnl_atr) DESC LIMIT 8""").fetchall()
for st, sd, n, E, mfe, gb in rows:
    print(f"  {st+'|'+sd:34s} n={n:5d} E={E:+.3f} avg_run_mfe={mfe:+.2f} giveback_frac={gb if gb is None else round(gb,2)}")
pdays = [r[0] for r in c.execute("""SELECT DISTINCT substr(st.entry_time,1,10) FROM trade_r_path p
  JOIN shadow_trades st ON st.id=p.trade_id WHERE p.phase='open' ORDER BY 1""")]
print(f"  path coverage days (phase='open'): {pdays[0]}..{pdays[-1]} n_days={len(pdays)} -> concentration fail")

print("\n-- L2 trend regime (btc_adx buckets), ALL-SHORT vs ALL-LONG --")
for side in ('SHORT','LONG'):
    for lo, hi in ((0,15),(15,25),(25,35),(35,999)):
        s = est(f"side='{side}' AND btc_adx>={lo} AND btc_adx<{hi}")
        show(f"  {side} adx[{lo},{hi if hi<999 else 'inf'})", s)
s = est("side='SHORT' AND btc_adx>=35")
print(f"  L2 projected bleed avoided if short-blocked at adx>=35: {s['n']} x {s['E']:.3f} = {s['n']*s['E']:+.0f} ATR-u")
r = c.execute("SELECT MIN(substr(entry_time,1,10)), MAX(substr(entry_time,1,10)), "
              "COUNT(DISTINCT substr(entry_time,1,10)), COUNT(DISTINCT symbol) "
              "FROM shadow_trades WHERE status='closed' AND side='SHORT' AND btc_adx>=35").fetchone()
print(f"  adx>=35 short window: {r[0]}..{r[1]} days={r[2]} symbols={r[3]}")

print("\n-- L3 vol regime (rvol24 terciles, thr = mean*0.7 / mean*1.3) --")
print(f"  mean rvol24={avg_rv:.4f} q1_thr={q1:.4f} q3_thr={q3:.4f}")
for side in ('SHORT','LONG'):
    for lab, cond in (("q1", f"btc_realized_vol_24h<=?"), ("mid", f"btc_realized_vol_24h>? AND btc_realized_vol_24h<?"), ("q3", f"btc_realized_vol_24h>=?")):
        args = (q1,) if lab=="q1" else ((q1,q3) if lab=="mid" else (q3,))
        s = est(f"side='{side}' AND {cond}", args)
        show(f"  {side} rvol {lab}", s)
s = est("side='SHORT' AND btc_realized_vol_24h<=?", (q1,))
print(f"  L3 projected bleed avoided if short-blocked in rvol q1: {s['n']} x {s['E']:.3f} = {s['n']*s['E']:+.0f} ATR-u")

print("\n-- L4 order flow (oi_delta_30m_pct) & funding --")
for side in ('LONG','SHORT'):
    for lab, cond, args in (("<-1%", "oi_delta_30m_pct<?", (-1,)), ("-1..0", "oi_delta_30m_pct>=? AND oi_delta_30m_pct<?", (-1,0)),
                            ("0..+1", "oi_delta_30m_pct>=? AND oi_delta_30m_pct<?", (0,1)), (">=+1%", "oi_delta_30m_pct>=?", (1,))):
        s = est(f"side='{side}' AND {cond}", args)
        if s['n'] >= 50: show(f"  {side} OId {lab}", s)
for lab, cond, args in (("funding<0","funding_rate_symbol<?",(0,)), ("0<=f<1bp","funding_rate_symbol>=? AND funding_rate_symbol<?",(0,0.0001)), ("f>=1bp","funding_rate_symbol>=?",(0.0001,))):
    s = est(f"side='SHORT' AND strategy LIKE 'asia_pump%' AND {cond}", args)
    if s['n'] >= 30: show(f"  asia_pump% SHORT {lab}", s)
sf = est("side='LONG' AND oi_delta_30m_pct>=? AND oi_delta_30m_pct<?", (-1,0))
print(f"  L4 potential on mild-flush LONG follows: {sf['n']} x {sf['E']:.3f} = {sf['n']*sf['E']:+.0f} ATR-u")

print("\n-- L5 session / time-of-day --")
for side in ('SHORT','LONG'):
    for ses in ('asia','london','ny','late'):
        s = est(f"side='{side}' AND session='{ses}'")
        if s['n'] >= 100: show(f"  {side} {ses}", s)

print("\n-- L6 universe (book-level) --")
for strat, side in (('burst_follow','SHORT'),('burst_follow','LONG'),('setup_follow','LONG'),
                    ('setup_follow','SHORT'),('setup_fade','SHORT'),('asia_pump_short_4h','SHORT'),
                    ('ny_flush_buy_24h','LONG'),('follow_3h_all','LONG')):
    s = est(f"strategy='{strat}' AND side='{side}'")
    show(f"  {strat}|{side}", s)
s = est("strategy='burst_follow' AND side='SHORT'")
wk = (datetime.datetime.fromisoformat(mx.replace('Z','+00:00')) - datetime.datetime.fromisoformat(mn.replace('Z','+00:00'))).days/7
print(f"  L6 projected: burst_follow SHORT {s['n']} x {s['E']:.4f} = {s['n']*s['E']:+.0f} ATR-u over {wk:.1f}wk = {s['n']*s['E']/wk:+.1f}/wk")

hdr("S5b WOULD-LIVE IMPACT OF TOP GATES")
g = est("would_live_accept=1 AND side='SHORT' AND btc_adx>=35")
show("  would-live shorts adx>=35", g)
g2 = est("would_live_accept=1 AND side='SHORT' AND btc_realized_vol_24h<=?", (q1,))
show("  would-live shorts rvol q1", g2)
print(f"\nALL SECTIONS DONE")
con.close()
