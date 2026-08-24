#!/usr/bin/env python3
"""Symbol-weighting simulation for NY bear — self-contained (no heavy imports)."""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "storage" / "signal_shadow.db"
COST_BPS = 12.0


def net_pnl(row) -> float:
    gross = float(row["pnl_atr"] or 0.0)
    atr_pct = float(row["entry_atr_pct"] or 0.0)
    cost = (COST_BPS / 100.0) / atr_pct if atr_pct > 0 else 0.0
    return gross - cost


def cap3_accept(rows, cap=3):
    active = []
    accepted = []
    for row in sorted(rows, key=lambda r: (r["entry_time"], r.get("id") or 0)):
        et = row["entry_time"]
        active = [a for a in active if a["exit_time"] > et]
        if len(active) >= cap:
            continue
        if any(a["symbol"] == row["symbol"] for a in active):
            continue
        active.append(row)
        accepted.append(row)
    return accepted


def summarize(rows, label):
    nets = [r["net"] for r in rows]
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["entry_time"][:10]] += r["net"]
    if not rows:
        print(f"{label}: NO ROWS")
        return None
    top_day = max(by_day, key=by_day.get)
    sum_net = sum(nets)
    wins = sum(1 for x in nets if x > 0)
    print(
        f"{label}: n={len(rows)} days={len(by_day)} avg_net={np.mean(nets):+.3f} "
        f"sum_net={sum_net:+.2f} wr={100*wins/len(nets):.0f}% "
        f"top_day={top_day} top_share={abs(by_day[top_day])/abs(sum_net) if sum_net else 0:.3f}"
    )
    return by_day


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row

    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM shadow_trades
            WHERE status='closed' AND session='ny' AND btc_trend_state='bear'
              AND would_live_accept=1 AND stop_atr IS NOT NULL AND stop_atr>0
              AND exit_time IS NOT NULL AND exit_time!=''
            """
        )
    ]
    for r in rows:
        r["net"] = net_pnl(r)
        r["R"] = float(r["pnl_atr"] or 0.0) / float(r["stop_atr"])

    print(f"NY bear live-accept rows: n={len(rows)}")

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)

    print("\n=== PER-SYMBOL NY BEAR (live-accept, pre-cap, R=pnl_atr/stop_atr) ===")
    sym_stats = {}
    for sym in sorted(by_sym, key=lambda s: -len(by_sym[s])):
        v = by_sym[sym]
        Rs = [x["R"] for x in v]
        days = len(set(x["entry_time"][:10] for x in v))
        wins = sum(1 for x in Rs if x > 0)
        w = [x for x in Rs if x > 0]
        l = [x for x in Rs if x <= 0]
        W = float(np.mean(w)) if w else 0.0
        L = float(np.mean(l)) if l else 0.0
        p = len(w) / len(Rs) if Rs else 0
        exp = p * W - (1 - p) * abs(L)
        sym_stats[sym] = {"n": len(v), "days": days, "exp": exp,
                          "avgR": float(np.mean(Rs)), "sumR": float(np.sum(Rs))}
        print(
            f"  {sym:14s} n={len(v):3d} days={days:2d} WR={100*wins/len(Rs):3.0f}% "
            f"avgR={np.mean(Rs):+.3f} sumR={np.sum(Rs):+.2f} exp={exp:+.3f}"
        )

    # Baseline cap-3 equal weight
    acc_eq = cap3_accept(rows, cap=3)
    print("\n=== BASELINE cap-3 equal-weight ===")
    summarize(acc_eq, "  NY bear equal-weight")

    # Hard-filter tilt: drop symbols exp<-0.05, n>=10, days>=4
    keep = {s for s, st in sym_stats.items()
            if not (st["n"] >= 10 and st["exp"] < -0.05 and st["days"] >= 4)}
    dropped = {s for s in sym_stats if s not in keep}
    print(f"\n=== HARD-FILTER TILT ===")
    print(f"  dropped: {sorted(dropped)}")
    acc_hard = cap3_accept([r for r in rows if r["symbol"] in keep], cap=3)
    summarize(acc_hard, "  NY bear hard-filter")

    conn.close()


if __name__ == "__main__":
    main()
