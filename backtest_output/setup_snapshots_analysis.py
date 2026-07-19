"""Analyze setup_snapshots for edge — honest stop/tp sim where possible."""
from __future__ import annotations

import sqlite3
import statistics as stats
from pathlib import Path

DB = Path("/root/bitana/storage/signal_shadow.db")
STOP = 2.5
TP = 3.0


def sim_row(mfe, mae, fwd12, stop=STOP, tp=TP):
    """Conservative: if both reachable, assume stop first."""
    hit_tp = mfe >= tp
    hit_sl = mae <= -stop
    if hit_sl and hit_tp:
        return -stop, "both_stop_first"
    if hit_sl:
        return -stop, "stop"
    if hit_tp:
        return tp, "tp"
    if fwd12 is not None:
        return max(min(fwd12, tp), -stop), "time_1h"
    return 0.0, "open"


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, symbol, session, hour, v_strict, v_confirms3, decile,
               cascade_active, bars_tracked, status,
               fwd_atr_12, fwd_atr_24, mfe_atr, mae_atr
        FROM setup_snapshots
        WHERE bars_tracked >= 12
        """
    ).fetchall()
    print(f"setup_snapshots with >=1h tracking: {len(rows)}")
    if not rows:
        return

    def report(label, subset):
        if not subset:
            print(f"\n{label}: n=0")
            return
        fwd = [r["fwd_atr_12"] for r in subset if r["fwd_atr_12"] is not None]
        sims = [sim_row(r["mfe_atr"], r["mae_atr"], r["fwd_atr_12"]) for r in subset]
        pnls = [s[0] for s in sims]
        both = sum(1 for s in sims if s[1] == "both_stop_first")
        print(f"\n{label}: n={len(subset)}")
        if fwd:
            print(
                f"  1h drift: mean={stats.mean(fwd):+.3f} med={stats.median(fwd):+.3f} "
                f"WR={sum(1 for x in fwd if x>0)/len(fwd)*100:.0f}%"
            )
        print(
            f"  sim({STOP}SL/{TP}TP): mean={stats.mean(pnls):+.3f}R "
            f"WR={sum(1 for x in pnls if x>0)/len(pnls)*100:.0f}% "
            f"ambiguous={both} ({both/len(pnls)*100:.0f}%)"
        )

    report("ALL", rows)
    report("v_strict only", [r for r in rows if r["v_strict"]])
    report("v_strict + cascade_active", [r for r in rows if r["v_strict"] and r["cascade_active"]])
    report("v_strict + NOT cascade", [r for r in rows if r["v_strict"] and not r["cascade_active"]])

    print("\nBy session (v_strict, sim):")
    for sess in ("asia", "london", "ny", "late"):
        sub = [r for r in rows if r["v_strict"] and r["session"] == sess]
        if sub:
            pnls = [sim_row(r["mfe_atr"], r["mae_atr"], r["fwd_atr_12"])[0] for r in sub]
            fwd = [r["fwd_atr_12"] for r in sub if r["fwd_atr_12"] is not None]
            print(
                f"  {sess:6s} n={len(sub):2d} drift1h={stats.mean(fwd):+.2f} "
                f"sim={stats.mean(pnls):+.2f}R WR={sum(1 for x in pnls if x>0)/len(pnls)*100:.0f}%"
            )

    print("\nBy decile (v_strict, sim):")
    for d in sorted({r["decile"] for r in rows if r["v_strict"]}):
        sub = [r for r in rows if r["v_strict"] and r["decile"] == d]
        pnls = [sim_row(r["mfe_atr"], r["mae_atr"], r["fwd_atr_12"])[0] for r in sub]
        print(f"  D{d} n={len(sub):2d} sim={stats.mean(pnls):+.2f}R")

    total = conn.execute("SELECT COUNT(*) FROM setup_snapshots").fetchone()[0]
    strict_total = conn.execute("SELECT COUNT(*) FROM setup_snapshots WHERE v_strict=1").fetchone()[0]
    print(f"\nTotal setups logged: {total} | v_strict fired: {strict_total}")
    print("NOTE: n<30 per slice = noise. ambiguous=both SL+TP reachable (conservative stop-first).")


if __name__ == "__main__":
    main()
