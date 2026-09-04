#!/usr/bin/env python3
"""Verify Gemini 3.8's bear-regime claims against signal_shadow.db. Read-only.
R = pnl_atr/stop_atr (shadow convention). 'Weekdays' = is_weekend=0."""
import sqlite3
from collections import defaultdict

db = sqlite3.connect("/root/bitana/storage/signal_shadow.db")
db.row_factory = sqlite3.Row
c = db.cursor()

BEAR = "btc_trend_state='bear' AND status='closed' AND stop_atr>0"


def q(sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def show(label, rows, rkey="R"):
    n = len(rows)
    if not n:
        print(f"{label}: n=0")
        return
    sr = sum(r[rkey] for r in rows)
    wr = sum(r[rkey] > 0 for r in rows) / n * 100
    days = defaultdict(float)
    for r in rows:
        days[r["date"]] += r[rkey]
    top_d, top_r = max(days.items(), key=lambda kv: kv[1])
    print(f"{label}: n={n} sumR={sr:+.2f} avgR={sr/n:+.4f} WR={wr:.1f}% "
          f"days={len(days)} top_day={top_d}({top_r:+.1f}R="
          f"{abs(top_r)/abs(sr)*100 if sr else 0:.0f}%)")


# ---- A. headline table ----
print("== A. bear weekday book (all strategies, closed) ==")
rows = q(f"SELECT side, COUNT(*) n, ROUND(AVG(pnl_atr/NULLIF(stop_atr,0)),4) avgR, "
         f"ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR, "
         f"ROUND(100.0*SUM(CASE WHEN pnl_atr>0 THEN 1 ELSE 0 END)/COUNT(*),1) wr "
         f"FROM shadow_trades WHERE {BEAR} AND is_weekend=0 GROUP BY side")
for r in rows:
    print(dict(r))
tot = sum(r["n"] for r in rows)
print(f"total n={tot} (Gemini claimed 4,923)")
print("-- composition by strategy/side (top 6 by sumR) --")
comp = q(f"SELECT strategy, side, COUNT(*) n, ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR "
         f"FROM shadow_trades WHERE {BEAR} AND is_weekend=0 GROUP BY 1,2 ORDER BY sumR DESC LIMIT 8")
for r in comp:
    print(f"  {r['strategy']:22s} {r['side']:5s} n={r['n']:5d} sumR={r['sumR']:+.2f}")
print("-- bear legs by month (era dominance) --")
mon = q(f"SELECT strftime('%Y-%m', entry_time) m, COUNT(*) n, "
        f"ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR FROM shadow_trades "
        f"WHERE {BEAR} AND is_weekend=0 GROUP BY 1 ORDER BY 1")
for r in mon:
    print(f"  {r['m']}: n={r['n']:5d} sumR={r['sumR']:+.2f}")

# ---- B. NY flush bear ----
print("\n== B. ny_flush_buy_4h bear (Gemini: +5.47R n=80 WR65%) ==")
ny = q(f"SELECT pnl_atr/NULLIF(stop_atr,0) R, hour, date(entry_time) date "
       f"FROM shadow_trades WHERE {BEAR} AND strategy='ny_flush_buy_4h'")
show("  all hours", ny)
gem_hours = [r for r in ny if r["hour"] in (14, 15, 20)]
other = [r for r in ny if r["hour"] not in (14, 15, 20)]
show("  Gemini hours 14/15/20", gem_hours)
show("  other hours 16-19", other)

# ---- C. London bear ----
print("\n== C. London bear (Gemini: burst LONG h9-13 +6.93R n=87 WR65.5; SHORTs -38.62R) ==")
lon = q(f"SELECT side, pnl_atr/NULLIF(stop_atr,0) R, date(entry_time) date, strategy, hour "
        f"FROM shadow_trades WHERE {BEAR} AND strategy='burst_follow' AND hour IN (9,10,11,13)")
show("  burst_follow LON LONG", [r for r in lon if r["side"] == "LONG"])
show("  burst_follow LON SHORT", [r for r in lon if r["side"] == "SHORT"])
sfl = q(f"SELECT side, pnl_atr/NULLIF(stop_atr,0) R, date(entry_time) date "
        f"FROM shadow_trades WHERE {BEAR} AND strategy='setup_fade_london' "
        f"AND hour IN (10,11,12) AND side='LONG'")
show("  setup_fade_london LONG h10-12", sfl)

# ---- D. asia bear ----
print("\n== D. asia_pump_short_4h bear (Gemini: -1.26R) ==")
show("  asia bear closed", q(f"SELECT pnl_atr/NULLIF(stop_atr,0) R, date(entry_time) date "
                              f"FROM shadow_trades WHERE {BEAR} AND strategy='asia_pump_short_4h'"))

# ---- E. symbols in bear ----
print("\n== E. per-symbol bear (all strategies) ==")
sym = q(f"SELECT symbol, COUNT(*) n, ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR, "
        f"ROUND(AVG(pnl_atr/NULLIF(stop_atr,0)),4) avgR, "
        f"COUNT(DISTINCT date(entry_time)) days FROM shadow_trades "
        f"WHERE {BEAR} AND is_weekend=0 GROUP BY 1")
sym.sort(key=lambda r: -r["sumR"])
for r in sym[:6]:
    print(f"  TOP {r['symbol']:15s} n={r['n']:4d} sumR={r['sumR']:+.2f} avg={r['avgR']:+.4f} days={r['days']}")
for r in sym:
    if r["symbol"] in ("FILUSDT", "PENDLEUSDT", "XRPUSDT", "UNIUSDT", "WLDUSDT"):
        rule = "REMOVE-eligible" if (r["n"] >= 100 and r["days"] >= 5 and r["avgR"] < -0.10) else "fails remove rule"
        print(f"  BLEED {r['symbol']:15s} n={r['n']:4d} sumR={r['sumR']:+.2f} avg={r['avgR']:+.4f} days={r['days']} -> {rule}")

# ---- F. h21 NY short (Gemini BEAR-B: n=36 +2.84R WR75%) ----
print("\n== F. burst_follow NY SHORT h21 bear (BEAR-B) ==")
h21 = q(f"SELECT pnl_atr/NULLIF(stop_atr,0) R, date(entry_time) date, symbol "
        f"FROM shadow_trades WHERE {BEAR} AND strategy='burst_follow' AND hour=21 AND side='SHORT'")
show("  h21 SHORT bear", h21)
if h21:
    byday = defaultdict(lambda: [0, 0.0])
    for r in h21:
        byday[r["date"]][0] += 1
        byday[r["date"]][1] += r["R"]
    for d, v in sorted(byday.items(), key=lambda kv: kv[1][1])[:4]:
        print(f"    worst: {d} {v[1]:+.2f}R/{v[0]}tr")

# ---- G. 8h/24h exits via trade_r_path (bar96=8h, bar288=24h of 5m) ----
print("\n== G. ny_flush_buy_4h bear exit-horizon (BEAR-A; via trade_r_path r_close) ==")
p = q("""SELECT t.rowid id, t.pnl_atr/NULLIF(t.stop_atr,0) R4h
         FROM shadow_trades t WHERE """ + BEAR + """ AND t.strategy='ny_flush_buy_4h'""")
print(f"  bear ny_flush legs: {len(p)} (path sim needs trade_r_path join — checking)")
for bars, lbl in ((96, "8h"), (288, "24h")):
    r = c.execute(f"""SELECT COUNT(*) n,
        ROUND(100.0*SUM(CASE WHEN p.r_close>0 THEN 1 ELSE 0 END)/COUNT(*),1) wr,
        ROUND(AVG(p.r_close),4) avgR, ROUND(SUM(p.r_close),2) sumR
        FROM trade_r_path p JOIN shadow_trades t ON t.rowid=p.trade_id
        WHERE p.phase='pos' AND p.bar_num={bars} AND t.strategy='ny_flush_buy_4h'
        AND t.btc_trend_state='bear' AND t.status='closed' AND t.stop_atr>0""").fetchone()
    if r and r["n"]:
        print(f"  r_close@{lbl}: n={r['n']} WR={r['wr']}% avgR={r['avgR']:+.4f} sumR={r['sumR']:+.2f}")
    else:
        print(f"  r_close@{lbl}: no path data")

# ---- H. BEAR-C cascade filter ----
print("\n== H. BEAR-C: cascade>=0.4 AND liq_flow>=500k (bear LONG legs) ==")
cs = q(f"""SELECT CASE WHEN entry_cascade_strength>=0.4 AND market_liq_flow_usd>=500000
                THEN 'hi-cascade' ELSE 'lo-cascade' END bucket,
        COUNT(*) n, ROUND(100.0*SUM(CASE WHEN pnl_atr>0 THEN 1 ELSE 0 END)/COUNT(*),1) wr,
        ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR
        FROM shadow_trades WHERE {BEAR} AND side='LONG'
        AND entry_cascade_strength IS NOT NULL AND market_liq_flow_usd IS NOT NULL
        GROUP BY 1""")
for r in cs:
    print(f"  {r['bucket']}: n={r['n']} WR={r['wr']}% sumR={r['sumR']:+.2f}")

# ---- I. BEAR-D ADX>35 ----
print("\n== I. BEAR-D: btc_adx>35 (bear) ==")
adx = q(f"""SELECT side, CASE WHEN btc_adx>35 THEN 'adx>35' ELSE 'adx<=35' END bucket,
        COUNT(*) n, ROUND(100.0*SUM(CASE WHEN pnl_atr>0 THEN 1 ELSE 0 END)/COUNT(*),1) wr,
        ROUND(SUM(pnl_atr/NULLIF(stop_atr,0)),2) sumR
        FROM shadow_trades WHERE {BEAR} AND btc_adx IS NOT NULL GROUP BY 1,2 ORDER BY 1,2""")
for r in adx:
    print(f"  {r['side']:5s} {r['bucket']}: n={r['n']} WR={r['wr']}% sumR={r['sumR']:+.2f}")
db.close()
