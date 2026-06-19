"""Report forward expectancy from the logging-only signal shadow.

Reads storage/signal_shadow.db and, for each gate variant, reports the
ATR-normalised forward drift at every horizon plus MFE/MAE — for BOTH the long
and short interpretation. This is the "direction" readout: does a variant's
candidate bar predict positive forward move, and in which direction.

Usage:  python3 research/signal_shadow_report.py [path-to-db]
"""
from __future__ import annotations

import sqlite3
import statistics as stats
import sys
from pathlib import Path

HORIZONS = (3, 6, 12, 24, 48, 96)
HORIZON_LABEL = {3: "15m", 6: "30m", 12: "1h", 24: "2h", 48: "4h", 96: "8h"}
VARIANTS = ("v_strict", "v_allhours", "v_confirms3", "v_loose")


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def main(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM snapshots WHERE status='done'").fetchone()[0]
    open_ = conn.execute("SELECT COUNT(*) FROM snapshots WHERE status='open'").fetchone()[0]
    orph = conn.execute("SELECT COUNT(*) FROM snapshots WHERE status='orphaned'").fetchone()[0]
    span = conn.execute("SELECT MIN(bar_time), MAX(bar_time) FROM snapshots").fetchone()

    print("=" * 70)
    print("SIGNAL SHADOW — forward expectancy report")
    print("=" * 70)
    print(f"snapshots: {total}  (done={done}, open={open_}, orphaned={orph})")
    if span[0]:
        print(f"span: {span[0][:16]} -> {span[1][:16]}")
    if total == 0:
        print("\nNo snapshots yet — let it run through some cascade bars.")
        return

    print("\nNote: forward returns are ATR units, LONG convention.")
    print("      SHORT edge = negate the LONG number. n = available rows at each horizon.\n")

    for v in VARIANTS:
        # Use all non-orphaned snapshots; each horizon column fills progressively
        # as bars elapse, so we get a directional read long before full 8h maturity.
        rows = conn.execute(
            f"SELECT * FROM snapshots WHERE {v}=1 AND status!='orphaned'"
        ).fetchall()
        n_fire = conn.execute(f"SELECT COUNT(*) FROM snapshots WHERE {v}=1").fetchone()[0]
        print("-" * 70)
        print(f"{v}: fired {n_fire} | tracked {len(rows)}")
        if not rows:
            print("  (no samples yet)")
            continue

        # Forward drift per horizon
        line = "  drift(ATR): "
        for h in HORIZONS:
            col = f"fwd_atr_{h}"
            xs = [r[col] for r in rows if r[col] is not None]
            if xs:
                m = _mean(xs)
                line += f"{HORIZON_LABEL[h]}={m:+.2f}(n{len(xs)}) "
        print(line)

        # Win rate at the 2h horizon (representative)
        xs24 = [r["fwd_atr_24"] for r in rows if r["fwd_atr_24"] is not None]
        if xs24:
            wr_long = sum(1 for x in xs24 if x > 0) / len(xs24) * 100
            print(f"  2h: long_win={wr_long:.0f}%  short_win={100 - wr_long:.0f}%  "
                  f"median={stats.median(xs24):+.2f}ATR")

        # MFE / MAE — tradeability under a 2.5-ATR stop
        mfe = [r["mfe_atr"] for r in rows]
        mae = [r["mae_atr"] for r in rows]
        print(f"  MFE avg={_mean(mfe):+.2f}ATR  MAE avg={_mean(mae):+.2f}ATR  "
              f"(long: target vs 2.5-ATR stop)")

    # Session breakdown for the loose set (where does edge concentrate?)
    print("-" * 70)
    print("loose candidates by session (2h drift, ATR):")
    for sess in ("asia", "london", "ny", "late"):
        rows = conn.execute(
            "SELECT fwd_atr_24 FROM snapshots WHERE v_loose=1 AND status!='orphaned' "
            "AND session=? AND fwd_atr_24 IS NOT NULL",
            (sess,),
        ).fetchall()
        xs = [r[0] for r in rows]
        if xs:
            print(f"  {sess:7} n={len(xs):4d}  drift={_mean(xs):+.2f}  "
                  f"long_win={sum(1 for x in xs if x > 0) / len(xs) * 100:.0f}%")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "storage/signal_shadow.db"
    if not Path(path).exists():
        print(f"DB not found: {path}")
        sys.exit(1)
    main(path)
