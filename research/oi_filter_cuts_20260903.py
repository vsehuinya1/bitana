#!/usr/bin/env python3
"""OI filter robustness cuts: side, month, regime. Read-only."""
import sqlite3
from collections import defaultdict

DB = "/root/bitana/storage/signal_shadow.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()
rows = [dict(r) for r in c.execute(
    "SELECT symbol, side, entry_time, pnl_atr, stop_atr, oi_delta_30m_pct, "
    "would_live_accept, btc_trend_state, hour "
    "FROM shadow_trades WHERE strategy='burst_follow' "
    "AND entry_time>='2026-07-15T' AND status='closed' AND stop_atr>0 "
    "AND oi_delta_30m_pct IS NOT NULL")]
for r in rows:
    r["R"] = r["pnl_atr"] / r["stop_atr"]
    r["date"] = r["entry_time"][:10]
    r["month"] = r["entry_time"][:7]


def stats(sub, label):
    n = len(sub)
    if n == 0:
        print(f"  {label}: n=0")
        return
    sr = sum(r["R"] for r in sub)
    print(f"  {label}: n={n} sumR={sr:+.1f} avgR={sr/n:+.3f} "
          f"WR={sum(r['R']>0 for r in sub)/n*100:.0f}%")


THR = 0.5
for scope, fn in (("ALL", lambda r: True),
                  ("WLA", lambda r: r["would_live_accept"])):
    sub = [r for r in rows if fn(r)]
    print(f"== scope={scope} (n={len(sub)}) ==")
    for side in ("LONG", "SHORT"):
        s2 = [r for r in sub if r["side"] == side]
        stats([r for r in s2 if r["oi_delta_30m_pct"] > THR], f"  {side} BLOCKED")
        stats([r for r in s2 if r["oi_delta_30m_pct"] <= THR], f"  {side} KEPT")
    print("  -- by month --")
    for m in sorted({r["month"] for r in sub}):
        sm = [r for r in sub if r["month"] == m]
        stats([r for r in sm if r["oi_delta_30m_pct"] > THR], f"  {m} BLOCKED")
        stats([r for r in sm if r["oi_delta_30m_pct"] <= THR], f"  {m} KEPT")
    print("  -- by btc_trend_state --")
    for st in sorted({str(r["btc_trend_state"]) for r in sub}):
        ss = [r for r in sub if str(r["btc_trend_state"]) == st]
        stats([r for r in ss if r["oi_delta_30m_pct"] > THR], f"  {st} BLOCKED")
        stats([r for r in ss if r["oi_delta_30m_pct"] <= THR], f"  {st} KEPT")

# Aug24 decomposition: was the kept-set edge just one storm?
print("== kept-set (delta<=0.5) daily totals, top/bottom ==")
kept = [r for r in rows if r["oi_delta_30m_pct"] <= THR]
by_day = defaultdict(float)
for r in kept:
    by_day[r["date"]] += r["R"]
top = sorted(by_day.items(), key=lambda kv: -kv[1])[:3]
bot = sorted(by_day.items(), key=lambda kv: kv[1])[:3]
print("  top days: " + "; ".join(f"{d} {v:+.1f}R" for d, v in top))
print("  worst days: " + "; ".join(f"{d} {v:+.1f}R" for d, v in bot))
db.close()
