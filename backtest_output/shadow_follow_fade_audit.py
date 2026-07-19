"""Explicit follow vs fade (mean-reversion) test by day — no narrative."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/bitana")
IMB_MIN = 0.3  # same threshold used in live collector


def pack(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    s = sorted(vals)
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "med": s[len(s) // 2],
        "wr": sum(1 for v in vals if v > 0) / len(vals) * 100,
    }


def fmt(p):
    if not p:
        return "n=0"
    return f"n={p['n']:4d} mean={p['mean']:+.3f} med={p['med']:+.3f} WR={p['wr']:.0f}%"


def load(conn):
    return conn.execute(
        """
        SELECT date(bar_time) AS d, session, fwd_atr_12, fwd_atr_24, fwd_atr_96,
               liq_imbalance_30m, burst_volume_30m
        FROM burst_snapshots
        WHERE bars_tracked >= 12
          AND liq_imbalance_30m IS NOT NULL
          AND abs(liq_imbalance_30m) >= ?
        """,
        (IMB_MIN,),
    ).fetchall()


def follow_return(fwd_long, imb):
    """Trade WITH liq: long after long-liq burst, short after short-liq burst."""
    if imb > 0:
        return fwd_long
    return -fwd_long


def fade_return(fwd_long, imb):
    """Trade AGAINST liq (mean reversion): fade the burst direction."""
    return -follow_return(fwd_long, imb)


def horizon_stats(rows, day_filter, ret_fn, col_idx):
    subset = [r for r in rows if day_filter(r[0])]
    vals = [ret_fn(r[col_idx], r[5]) for r in subset if r[col_idx] is not None]
    return pack(vals)


def print_block(title, rows, day_filter):
    print(f"\n{title}")
    print("-" * 72)
    for h, label in [(2, "1h"), (3, "2h"), (4, "8h")]:
        fol = horizon_stats(rows, day_filter, follow_return, h)
        fad = horizon_stats(rows, day_filter, fade_return, h)
        print(f"  {label:3s}  FOLLOW (with liq):  {fmt(fol)}")
        print(f"       FADE (reversion): {fmt(fad)}")


def main():
    conn = sqlite3.connect(ROOT / "storage/signal_shadow.db")
    rows = load(conn)
    days = sorted({r[0] for r in rows})
    print("=" * 72)
    print("FOLLOW vs FADE — explicit returns in ATR units (LONG convention base)")
    print(f"Filter: |liq_imbalance_30m| >= {IMB_MIN}")
    print(f"Days in sample: {days[0]} .. {days[-1]}")
    print("=" * 72)

    # Full sample
    print_block("ALL DAYS", rows, lambda d: True)

    # Mon-Wed Jun 22-24 specifically
    mon_wed = {"2026-06-22", "2026-06-23", "2026-06-24"}
    print_block("MON-WED (Jun 22-24)", rows, lambda d: d in mon_wed)

    # Thu-Sat when follow looked good
    thu_sat = {"2026-06-19", "2026-06-20", "2026-06-21"}
    print_block("THU-SAT (Jun 19-21)", rows, lambda d: d in thu_sat)

    # Per day
    print("\n" + "=" * 72)
    print("PER DAY — FADE 1h (mean reversion trade)")
    print("=" * 72)
    for d in days:
        fad1 = horizon_stats(rows, lambda x, dd=d: x == dd, fade_return, 2)
        fol1 = horizon_stats(rows, lambda x, dd=d: x == dd, follow_return, 2)
        print(f"  {d}  FADE 1h: {fmt(fad1)}  |  FOLLOW 1h: {fmt(fol1)}")

    # Mon-Wed by session — fade 1h
    print("\n" + "=" * 72)
    print("MON-WED FADE 1h BY SESSION")
    print("=" * 72)
    for sess in ("asia", "london", "ny", "late"):
        subset = [
            r for r in rows
            if r[0] in mon_wed and r[1] == sess and r[2] is not None
        ]
        vals = [fade_return(r[2], r[5]) for r in subset]
        print(f"  {sess:6s} {fmt(pack(vals))}")

    # Mon-Wed big burst fade
    print("\n" + "=" * 72)
    print("MON-WED BIG BURST (top quartile within Mon-Wed) — FADE vs FOLLOW 1h")
    print("=" * 72)
    mw = [r for r in rows if r[0] in mon_wed and r[6]]
    if len(mw) >= 8:
        vols = sorted(r[6] for r in mw)
        q75 = vols[int(len(vols) * 0.75)]
        big = [r for r in mw if r[6] >= q75]
        fad = pack([fade_return(r[2], r[5]) for r in big if r[2] is not None])
        fol = pack([follow_return(r[2], r[5]) for r in big if r[2] is not None])
        print(f"  q75 vol30m >= ${q75:,.0f}")
        print(f"  FADE  1h: {fmt(fad)}")
        print(f"  FOLLOW 1h: {fmt(fol)}")
    else:
        print("  insufficient Mon-Wed big-burst sample")

    # Honest comparison: is Mon-Wed fade BETTER than Thu-Sat fade?
    print("\n" + "=" * 72)
    print("HEAD-TO-HEAD: is Mon-Wed fade actually better than other days?")
    print("=" * 72)
    mw_fade = horizon_stats(rows, lambda d: d in mon_wed, fade_return, 2)
    other_fade = horizon_stats(rows, lambda d: d not in mon_wed, fade_return, 2)
    mw_fol = horizon_stats(rows, lambda d: d in mon_wed, follow_return, 2)
    other_fol = horizon_stats(rows, lambda d: d not in mon_wed, follow_return, 2)
    print(f"  Mon-Wed  FADE 1h:  {fmt(mw_fade)}")
    print(f"  Other    FADE 1h:  {fmt(other_fade)}")
    print(f"  Mon-Wed  FOLLOW 1h: {fmt(mw_fol)}")
    print(f"  Other    FOLLOW 1h: {fmt(other_fol)}")

    # 8h on Mon-Wed — where I claimed reversion
    print("\n" + "=" * 72)
    print("MON-WED 8h — where 'reversal' narrative came from")
    print("=" * 72)
    for d in sorted(mon_wed):
        fad8 = horizon_stats(rows, lambda x, dd=d: x == dd, fade_return, 4)
        fol8 = horizon_stats(rows, lambda x, dd=d: x == dd, follow_return, 4)
        raw8 = pack([r[4] for r in rows if r[0] == d and r[4] is not None])
        print(f"  {d}  raw_long_8h={fmt(raw8)}")
        print(f"         FADE 8h={fmt(fad8)}  FOLLOW 8h={fmt(fol8)}")


if __name__ == "__main__":
    main()
