#!/usr/bin/env python3
"""OI-inflow filter evaluation + TAO/XLM/BNB triage (burst_follow, Jul15+).
R convention (shadow): R = pnl_atr / stop_atr. Read-only.
"""
import sqlite3
from collections import defaultdict

DB = "/root/bitana/storage/signal_shadow.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

BASE = ("SELECT symbol, side, entry_time, pnl_atr, stop_atr, status, "
        "oi_delta_30m_pct, would_live_accept, session, hour, decile "
        "FROM shadow_trades "
        "WHERE strategy='burst_follow' AND entry_time>='2026-07-15T' "
        "AND status='closed' AND stop_atr>0")
rows = [dict(r) for r in c.execute(BASE)]
for r in rows:
    r["R"] = r["pnl_atr"] / r["stop_atr"]
    r["date"] = r["entry_time"][:10]
    r["hh"] = int(r["entry_time"][11:13])

LIVE10 = {"BTCUSDT", "SOLUSDT", "ETHUSDT", "WLDUSDT", "XRPUSDT",
          "NEARUSDT", "ZECUSDT", "APTUSDT", "ADAUSDT", "UNIUSDT"}


def stats(subset, label):
    n = len(subset)
    if n == 0:
        print(f"{label}: n=0")
        return
    sr = sum(r["R"] for r in subset)
    wr = sum(r["R"] > 0 for r in subset) / n * 100
    days = defaultdict(float)
    for r in subset:
        days[r["date"]] += r["R"]
    top_day, top_r = max(days.items(), key=lambda kv: kv[1])
    worst_day, worst_r = min(days.items(), key=lambda kv: kv[1])
    print(f"{label}: n={n} sumR={sr:+.1f} avgR={sr/n:+.3f} WR={wr:.0f}% "
          f"days={len(days)} top_day={top_day}({top_r:+.1f}R, "
          f"{abs(top_r)/abs(sr)*100 if sr else 0:.0f}% of sum) "
          f"worst_day={worst_day}({worst_r:+.1f}R)")


# ---------- PART A: TAO/XLM/BNB triage ----------
print("=" * 72)
print("PART A — TAO/XLM/BNB triage (burst_follow, Jul15+, closed)")
for sym in ("TAOUSDT", "XLMUSDT", "BNBUSDT"):
    sub = [r for r in rows if r["symbol"] == sym]
    stats(sub, f"{sym:9s} ALL")
    sub_wla = [r for r in sub if r["would_live_accept"]]
    stats(sub_wla, f"{sym:9s} WLA")
    if sub:
        by_day = defaultdict(lambda: [0, 0.0])
        for r in sub:
            by_day[r["date"]][0] += 1
            by_day[r["date"]][1] += r["R"]
        worst3 = sorted(by_day.items(), key=lambda kv: kv[1][1])[:3]
        print(f"          worst days: " +
              "; ".join(f"{d} {v[1]:+.1f}R/{v[0]}tr" for d, v in worst3))
# context: full shadow book symbol ranking tail
sym_r = defaultdict(float)
sym_n = defaultdict(int)
for r in rows:
    sym_r[r["symbol"]] += r["R"]
    sym_n[r["symbol"]] += 1
print(f"\nbook context: {len(sym_r)} symbols, total sumR={sum(sym_r.values()):+.1f}")
tail = sorted(sym_r.items(), key=lambda kv: kv[1])[:8]
print("bottom-8 symbols by sumR: " +
      "; ".join(f"{s} {v:+.1f}R(n={sym_n[s]})" for s, v in tail))

# ---------- PART B: OI filter ----------
print("=" * 72)
print("PART B — OI inflow filter (burst_follow, Jul15+, closed, oi!=null)")
elig = [r for r in rows if r["oi_delta_30m_pct"] is not None]
print(f"eligible: n={len(elig)} of {len(rows)} closed burst_follow legs")

for thr in (0.25, 0.5, 1.0, 2.0):
    blocked = [r for r in elig if r["oi_delta_30m_pct"] > thr]
    kept = [r for r in elig if r["oi_delta_30m_pct"] <= thr]
    print(f"\n-- S1 as-stated: block oi_delta_30m > +{thr}% --")
    stats(blocked, "  BLOCKED")
    stats(kept, "  KEPT   ")

blocked = [r for r in elig if abs(r["oi_delta_30m_pct"]) > 0.5]
kept = [r for r in elig if abs(r["oi_delta_30m_pct"]) <= 0.5]
print("\n-- S2 abs variant: block |oi_delta_30m| > 0.5% --")
stats(blocked, "  BLOCKED")
stats(kept, "  KEPT   ")

# forensic on S1 blocked @0.5
blocked = [r for r in elig if r["oi_delta_30m_pct"] > 0.5]
print("\n-- S1 blocked-set forensics (@0.5) --")
by_day = defaultdict(lambda: [0, 0.0])
by_hour = defaultdict(lambda: [0, 0.0])
by_sym = defaultdict(lambda: [0, 0.0])
for r in blocked:
    by_day[r["date"]][0] += 1
    by_day[r["date"]][1] += r["R"]
    by_hour[r["hh"]][0] += 1
    by_hour[r["hh"]][1] += r["R"]
    by_sym[r["symbol"]][0] += 1
    by_sym[r["symbol"]][1] += r["R"]
print("top-6 loss days: " + "; ".join(
    f"{d} {v[1]:+.1f}R/{v[0]}tr"
    for d, v in sorted(by_day.items(), key=lambda kv: kv[1][1])[:6]))
print("worst hours: " + "; ".join(
    f"h{h:02d} {v[1]:+.1f}R/{v[0]}tr"
    for h, v in sorted(by_hour.items(), key=lambda kv: kv[1][1])[:5]))
print("worst symbols: " + "; ".join(
    f"{s} {v[1]:+.1f}R/{v[0]}tr"
    for s, v in sorted(by_sym.items(), key=lambda kv: kv[1][1])[:6]))

# live-real scopes
print("\n-- live-real scopes of S1 split @0.5 --")
wla = [r for r in elig if r["would_live_accept"]]
b_w = [r for r in wla if r["oi_delta_30m_pct"] > 0.5]
k_w = [r for r in wla if r["oi_delta_30m_pct"] <= 0.5]
stats(b_w, "  WLA  BLOCKED")
stats(k_w, "  WLA  KEPT")
lv = [r for r in elig if r["symbol"] in LIVE10]
b_l = [r for r in lv if r["oi_delta_30m_pct"] > 0.5]
k_l = [r for r in lv if r["oi_delta_30m_pct"] <= 0.5]
stats(b_l, "  LIV10 BLOCKED")
stats(k_l, "  LIV10 KEPT")

# side check
sides = defaultdict(int)
for r in elig:
    sides[r["side"]] += 1
print(f"\nside mix: {dict(sides)}")
db.close()
