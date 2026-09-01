#!/usr/bin/env python3
"""Daily live-vs-shadow discrepancy report for ny-wire (burst engine)."""
import json, sqlite3, re, subprocess
from datetime import datetime, timezone
from collections import defaultdict

TODAY = "2026-08-30"
W = "/root/bitana"

def pf(win_sum, loss_sum):
    return round(win_sum / abs(loss_sum), 2) if loss_sum else float("inf")

def stats(rs):
    n = len(rs)
    sumr = sum(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    wr = 100.0 * len(wins) / n if n else 0.0
    return n, round(sumr, 2), pf(sum(wins), sum(losses)), round(wr, 1)

# ---------- 1. LIVE fills ----------
live_rows = []
with open(f"{W}/logs/trades-live-burst.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = d.get("timestamp", "")
        hold = d.get("hold_time_s", 0) or 0
        entry_dt = None
        try:
            t0 = datetime.fromisoformat(ts)
            entry_dt = t0 - __import__("datetime").timedelta(seconds=hold)
        except Exception:
            pass
        sd = d.get("signal_data", {}) or {}
        row = {
            "close_ts": ts, "entry_ts": entry_dt.isoformat() if entry_dt else "",
            "symbol": d.get("symbol"), "side": d.get("side"),
            "pnl_usd": d.get("pnl_usd", 0), "pnl_r": d.get("pnl_r", 0),
            "session": sd.get("session"), "strategy": sd.get("shadow_strategy"),
            "cluster": sd.get("cluster_bucket"),
        }
        if row["entry_ts"].startswith(TODAY) or ts.startswith(TODAY):
            live_rows.append(row)

live_by_sess = defaultdict(list)
for r in live_rows:
    live_by_sess[r["session"] or "?"].append(r["pnl_r"])

# cross-check DB trades table
con = sqlite3.connect(f"file:{W}/data/bitana-live-burst.db?mode=ro", uri=True)
db_live = con.execute("select count(*) from trades where timestamp like ?", (TODAY + "%",)).fetchone()[0]
db_open = con.execute("select count(*) from positions where state='OPEN'").fetchone()[0]
con.close()

# ---------- 2. SHADOW ----------
scon = sqlite3.connect(f"file:{W}/storage/signal_shadow.db?mode=ro", uri=True)
scon.row_factory = sqlite3.Row
shadow = {}
for sess, strat in (("london", "burst_follow"), ("ny", "ny_flush_buy_4h")):
    q = """SELECT entry_time, hour, liq_imb, pnl_atr, stop_atr, status, symbol, side, exit_reason
           FROM shadow_trades WHERE entry_time LIKE ? AND strategy=? AND session=? AND would_live_accept=1"""
    rows = [dict(r) for r in scon.execute(q, (TODAY + "%", strat, sess))]
    # config-eligible filter for london burst_follow
    elig, inelig = [], []
    for r in rows:
        if sess == "london" and strat == "burst_follow":
            if r["hour"] in (9, 10, 11, 13) and (r["liq_imb"] or 0) >= 0.5:
                elig.append(r)
            else:
                inelig.append(r)
        else:
            elig.append(r)
    closed = [r for r in elig if r["status"] == "closed"]
    rs = [r["pnl_atr"] / r["stop_atr"] if r["stop_atr"] else 0 for r in closed]
    shadow[sess] = {
        "all_wla": len(rows), "eligible": len(elig), "excluded": len(inelig),
        "closed": len(closed), "open": len(elig) - len(closed),
        "stats_closed": stats(rs), "stats_incl_open": stats(rs + [0] * (len(elig) - len(closed))),
        "excluded_rows": [(r["hour"], round(r["liq_imb"], 2), round(r["pnl_atr"] / (r["stop_atr"] or 1), 2), r["symbol"]) for r in inelig],
        "detail": [(r["symbol"], r["side"], r["hour"], round(r["pnl_atr"] / (r["stop_atr"] or 1), 2), r["status"]) for r in elig],
    }

# shadow_pending_entries today for these strategies
pend = scon.execute("""SELECT strategy, session, status, symbol, signal_time FROM shadow_pending_entries
    WHERE signal_time LIKE ? AND strategy IN ('burst_follow','ny_flush_buy_4h')""",
    (TODAY + "%",)).fetchall()
scon.close()

print("=== LIVE ===")
print("jsonl today rows:", len(live_rows), "| db trades today:", db_live, "| db open positions:", db_open)
for s, rs in live_by_sess.items():
    print(s, stats(rs))
print("=== SHADOW ===")
for s, d in shadow.items():
    print(s, json.dumps(d, default=str))
print("=== PENDING ===")
for p in pend:
    print(dict(p))
