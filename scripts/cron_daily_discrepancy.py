#!/usr/bin/env python3
"""Daily live-vs-shadow discrepancy report for Bitana (UTC day)."""
import json, sqlite3, datetime, math, collections, re, sys

DAY = "2026-08-31"
BASE = "/root/bitana"

def r2(x, d=2):
    return round(x, d) if x is not None and not (isinstance(x, float) and math.isnan(x)) else 0.0

def stats(rs):
    n = len(rs)
    sumR = sum(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp = sum(wins); gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (float('inf') if gp > 0 else 0.0)
    wr = (len(wins) / n * 100) if n else 0.0
    return dict(n=n, sumR=r2(sumR), pf=r2(pf) if pf != float('inf') else 999.0,
                wr=round(wr, 1), gp=r2(gp), gl=r2(gl))

# ---------- 1) LIVE fills ----------
live_rows = []
with open(f"{BASE}/logs/trades-live-burst.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        ts = j.get("timestamp", "")
        hold = float(j.get("hold_time_s") or 0)
        try:
            close_dt = datetime.datetime.fromisoformat(ts)
        except Exception:
            continue
        entry_dt = close_dt - datetime.timedelta(seconds=hold)
        if entry_dt.strftime("%Y-%m-%d") != DAY:
            continue
        sd = j.get("signal_data") or {}
        r = j.get("pnl_r")
        if r is None:
            r = (j.get("pnl_usd") or 0) / 1.0
        live_rows.append(dict(
            symbol=j.get("symbol"), side=j.get("side"),
            session=sd.get("session"), strat=sd.get("shadow_strategy"),
            entry_time=entry_dt.isoformat(), hour=entry_dt.hour,
            pnl_r=float(r), pnl_usd=j.get("pnl_usd"),
            exit_reason=j.get("exit_reason"), liq_imb=sd.get("liq_imbalance"),
            qty=j.get("quantity"), notional=(j.get("entry_price") or 0) * (j.get("quantity") or 0),
            cluster=sd.get("cluster_bucket"), close_ts=ts,
        ))

print("=== LIVE total today:", len(live_rows))
by_key = collections.defaultdict(list)
for r in live_rows:
    by_key[(r["session"], r["strat"])].append(r)
for k in sorted(by_key, key=lambda x: (-len(by_key[x]), str(x))):
    rs = [x["pnl_r"] for x in by_key[k]]
    print(f"  session={k[0]} strat={k[1]}: n={len(rs)} sumR={r2(sum(rs))}")

live_sessions = {}
for sess in ("london", "ny"):
    rs = [x["pnl_r"] for x in live_rows if x["session"] == sess]
    live_sessions[sess] = stats(rs)
    print(f"LIVE {sess}: {live_sessions[sess]}")
    for x in live_rows:
        if x["session"] == sess:
            print(f"    {x['entry_time'][11:16]} {x['symbol']} {x['side']} strat={x['strat']} R={r2(x['pnl_r'])} exit={x['exit_reason']} notional=${r2(x['notional'])} liq_imb={x['liq_imb']}")

# ---------- 2) SHADOW ----------
con = sqlite3.connect(f"{BASE}/storage/signal_shadow.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

def shadow_stats(where, args):
    rows = cur.execute(f"""
        SELECT strategy, session, symbol, side, entry_time, hour, liq_imb,
               pnl_atr, stop_atr, status, exit_reason, exit_time, would_live_accept
        FROM shadow_trades
        WHERE entry_time >= ? AND entry_time < date(?, '+1 day') AND {where}
    """, [DAY, DAY] + args).fetchall()
    out = []
    for r in rows:
        stop_atr = r["stop_atr"] or 0
        pnl_atr = r["pnl_atr"] or 0
        r_mult = (pnl_atr / stop_atr) if stop_atr else 0.0
        out.append(dict(r=r_mult, symbol=r["symbol"], side=r["side"],
                        entry_time=r["entry_time"], hour=r["hour"],
                        liq_imb=r["liq_imb"], status=r["status"],
                        exit_reason=r["exit_reason"], strategy=r["strategy"]))
    return out

# London: burst_follow session=london, config-eligible hour in (9,10,11,13), liq_imb>=0.5
sh_london_all = shadow_stats("strategy='burst_follow' AND session='london' AND would_live_accept=1", [])
sh_london = [x for x in sh_london_all if x["hour"] in (9, 10, 11, 13) and (x["liq_imb"] or 0) >= 0.5]
# NY: ny_flush_buy_4h session=ny
sh_ny = shadow_stats("strategy='ny_flush_buy_4h' AND session='ny' AND would_live_accept=1", [])

print("\n=== SHADOW london burst_follow: eligible", len(sh_london), "of", len(sh_london_all), "would_live_accept=1")
print("LIVE london:", live_sessions["london"])
print("SHADOW london:", stats([x["r"] for x in sh_london]))
for x in sorted(sh_london, key=lambda y: y["entry_time"]):
    print(f"    {x['entry_time'][11:16]} {x['symbol']} {x['side']} R={r2(x['r'])} {x['status']}/{x['exit_reason']} imb={x['liq_imb']}")
print("excluded-by-config (london):", len(sh_london_all) - len(sh_london))
for x in sh_london_all:
    if not (x["hour"] in (9, 10, 11, 13) and (x["liq_imb"] or 0) >= 0.5):
        print(f"    EXCL {x['entry_time'][11:16]} h={x['hour']} imb={x['liq_imb']} {x['symbol']} R={r2(x['r'])}")

print("\n=== SHADOW ny ny_flush_buy_4h:", len(sh_ny))
print("SHADOW ny:", stats([x["r"] for x in sh_ny]))
for x in sorted(sh_ny, key=lambda y: y["entry_time"]):
    print(f"    {x['entry_time'][11:16]} {x['symbol']} {x['side']} R={r2(x['r'])} {x['status']}/{x['exit_reason']} imb={x['liq_imb']}")

# ---------- 4) shadow signals live never saw ----------
pend = cur.execute("""
    SELECT strategy, symbol, side, signal_time, status, session, hour, liq_imb, fill_time
    FROM shadow_pending_entries
    WHERE signal_time >= ? AND signal_time < date(?, '+1 day')
      AND (strategy LIKE 'burst_follow%' OR strategy LIKE 'ny_flush_buy_4h%')
    ORDER BY signal_time
""", [DAY, DAY]).fetchall()
print("\n=== shadow_pending_entries today (burst/ny strategies):", len(pend))
byst = collections.Counter()
for p in pend:
    byst[p["status"]] += 1
print("  status counts:", dict(byst))
for p in pend:
    print(f"    {p['signal_time'][11:16]} {p['strategy']} {p['symbol']} {p['side']} sess={p['session']} h={p['hour']} imb={round(p['liq_imb'],2) if p['liq_imb'] is not None else None} status={p['status']} fill={p['fill_time']}")

# ---------- 5) risk/brake state ----------
con2 = sqlite3.connect(f"{BASE}/data/bitana-live-burst.db")
con2.row_factory = sqlite3.Row
bs = dict(con2.execute("SELECT * FROM brake_state WHERE id=1").fetchone())
rs_ = dict(con2.execute("SELECT * FROM risk_state WHERE id=1").fetchone())
print("\n=== brake_state:", bs)
print("=== risk_state:", rs_)

# brake pause windows today from log
print("\n=== regime_state.json:")
try:
    print(open(f"{BASE}/storage/regime_state.json").read())
except Exception as e:
    print("ERR", e)
