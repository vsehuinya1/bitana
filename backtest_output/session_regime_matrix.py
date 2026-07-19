"""Session x regime trading matrix from enriched shadow logs (for Monday go-live)."""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict

DB = "storage/signal_shadow.db"

BOOKS = {
    "asia": ["asia_pump_short_4h", "asia_pump_short_4h_tsl", "fade_6h_asia", "follow_6h_asia"],
    "london": ["london_burst_fade", "follow_3h_london", "fade_3h_london"],
    "ny": ["ny_flush_buy_4h_open", "ny_flush_buy_4h", "ny_flush_buy_4h_open_tsl"],
    "late": ["fade_6h_late", "late_fade", "follow_3h_late"],
}


def metrics(rows):
    pnl = [float(r["pnl_atr"]) for r in rows]
    if not pnl:
        return None
    wins = sum(v > 0 for v in pnl)
    return dict(
        n=len(pnl), tot=round(sum(pnl), 2), avg=round(statistics.mean(pnl), 3),
        med=round(statistics.median(pnl), 3), wr=round(100 * wins / len(pnl)),
        days=len(set(r["entry_time"][:10] for r in rows)),
    )


def cap_sim(rows, cap=3):
    rows = sorted(rows, key=lambda r: (r["entry_time"], r["id"]))
    active, accepted = [], []
    for row in rows:
        active = [r for r in active if r["exit_time"] > row["entry_time"]]
        if len(active) >= cap or any(r["symbol"] == row["symbol"] for r in active):
            continue
        active.append(row)
        accepted.append(row)
    gross = sum(float(r["pnl_atr"]) for r in accepted)
    cost = sum(0.12 / float(r["entry_atr_pct"]) for r in accepted if r["entry_atr_pct"])
    return len(accepted), round(gross, 2), round(gross - cost, 2)


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
ALL = [dict(r) for r in conn.execute(
    "SELECT * FROM shadow_trades WHERE entry_time >= '2026-07-08' AND status='closed' "
    "AND btc_trend_state IS NOT NULL"
).fetchall()]
print(f"closed trades with regime tag Jul 8+: {len(ALL)}")

print("\n=== SESSION x REGIME (all shadow variants, gross ATR) ===")
for sess in ("asia", "london", "ny", "late"):
    for reg in ("bear", "neutral", "bull"):
        rows = [r for r in ALL if r["session"] == sess and r["btc_trend_state"] == reg]
        m = metrics(rows)
        if m:
            print(f"  {sess:7s} {reg:8s} n={m['n']:4d} tot={m['tot']:+9.2f} avg={m['avg']:+.3f} days={m['days']}")

print("\n=== BOOK x REGIME (strategy-level, with cap-3 net) ===")
for sess, strats in BOOKS.items():
    print(f"-- {sess} --")
    for strat in strats:
        for reg in ("bear", "neutral", "bull"):
            rows = [r for r in ALL if r["strategy"] == strat and r["btc_trend_state"] == reg]
            m = metrics(rows)
            if m and m["n"] >= 3:
                acc, gross, net = cap_sim(rows)
                print(f"  {strat:28s} {reg:8s} n={m['n']:3d} tot={m['tot']:+8.2f} avg={m['avg']:+.3f} "
                      f"wr={m['wr']:3d}% days={m['days']} | cap3 acc={acc} net={net:+.2f}")

print("\n=== FRIDAY NEUTRAL (post 04:06 flip) — live-book forward test ===")
fri = [r for r in ALL if r["entry_time"] >= "2026-07-17T04:06" and r["btc_trend_state"] == "neutral"]
print(f"neutral closed trades Fri: {len(fri)}")
for sess in ("asia", "london", "ny", "late"):
    rows = [r for r in fri if r["session"] == sess]
    m = metrics(rows)
    if m:
        print(f"  {sess:7s} n={m['n']:3d} tot={m['tot']:+8.2f} avg={m['avg']:+.3f}")
for strat in ("asia_pump_short_4h", "ny_flush_buy_4h_open", "london_burst_fade", "fade_6h_late"):
    rows = [r for r in fri if r["strategy"] == strat]
    m = metrics(rows)
    if m:
        print(f"  {strat:28s} n={m['n']:3d} tot={m['tot']:+8.2f} avg={m['avg']:+.3f} wr={m['wr']}%")

print("\n=== LIVE BOOKS: neutral+bear only (what Monday trades), Jul 8+ ===")
for strat in ("asia_pump_short_4h", "ny_flush_buy_4h_open"):
    rows = [r for r in ALL if r["strategy"] == strat and r["btc_trend_state"] in ("neutral", "bear")]
    m = metrics(rows)
    if m:
        acc, gross, net = cap_sim(rows)
        print(f"  {strat:28s} n={m['n']:3d} tot={m['tot']:+8.2f} avg={m['avg']:+.3f} wr={m['wr']}% "
              f"days={m['days']} | cap3 acc={acc} net={net:+.2f}")
    # Monday-only check for NY
    mon = [r for r in rows if r["entry_time"][:10] in ("2026-07-13", "2026-07-06")]
    mm = metrics(mon)
    if mm:
        print(f"    (Mondays only: n={mm['n']} tot={mm['tot']:+.2f})")

print("\n=== BULL candidates (shadow-only book), full bull window Jul 15-17 ===")
bull = [r for r in ALL if r["btc_trend_state"] == "bull" and r["entry_time"] >= "2026-07-15"]
for strat in ("asia_pump_short_4h", "asia_pump_short_4h_tsl", "london_burst_fade", "ny_flush_buy_4h_open"):
    rows = [r for r in bull if r["strategy"] == strat]
    m = metrics(rows)
    if m:
        acc, gross, net = cap_sim(rows)
        print(f"  {strat:28s} n={m['n']:3d} tot={m['tot']:+8.2f} avg={m['avg']:+.3f} wr={m['wr']}% "
              f"days={m['days']} | cap3 acc={acc} net={net:+.2f}")

conn.close()
