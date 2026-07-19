#!/usr/bin/env python3
"""Monday fade stats from shadow_trades DB only."""
import sqlite3
import statistics as st
from datetime import datetime

DAY = "2026-06-29"
DB = "/root/bitana/storage/signal_shadow.db"


def session_from_entry(entry_time: str) -> str:
    h = datetime.fromisoformat(entry_time).hour
    if 0 <= h < 8:
        return "asia"
    if 8 <= h < 14:
        return "london"
    if 14 <= h < 22:
        return "ny"
    return "late"


def stats(trs):
    if not trs:
        return "n=0"
    pnls = [t["pnl_atr"] for t in trs]
    wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
    return f"n={len(trs)} sum={sum(pnls):+.3f}R mean={st.mean(pnls):+.3f}R WR={wr:.0f}%"


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    print("=== shadow_trades overview ===")
    for row in c.execute(
        "SELECT strategy, status, COUNT(*) n FROM shadow_trades "
        "GROUP BY strategy, status ORDER BY strategy, status"
    ):
        print(dict(row))

    rows = c.execute(
        """
        SELECT * FROM shadow_trades
        WHERE status='closed'
          AND (date(entry_time)=? OR date(exit_time)=?)
        ORDER BY entry_time
        """,
        (DAY, DAY),
    ).fetchall()

    print(f"\n=== Monday {DAY} closed: {len(rows)} ===")
    by_strat = {}
    for r in rows:
        by_strat.setdefault(r["strategy"], []).append(r)

    for strat, trs in sorted(by_strat.items()):
        print(f"\n{strat}: {stats(trs)}")
        for t in trs:
            sess = session_from_entry(t["entry_time"])
            print(
                f"  {t['entry_time'][:16]} {sess:5s} {t['symbol']:12s} "
                f"{t['side']:5s} {t['pnl_atr']:+.3f}R {t['exit_reason']}"
            )

    print("\n=== FADE (late_fade) from shadow_trades ===")
    filters = [
        ("late_fade ALL Mon", lambda t: t["strategy"] == "late_fade"),
        (
            "late_fade LATE session",
            lambda t: t["strategy"] == "late_fade"
            and session_from_entry(t["entry_time"]) == "late",
        ),
        (
            "late_fade NY session",
            lambda t: t["strategy"] == "late_fade"
            and session_from_entry(t["entry_time"]) == "ny",
        ),
        (
            "late_fade NY+LATE",
            lambda t: t["strategy"] == "late_fade"
            and session_from_entry(t["entry_time"]) in ("ny", "late"),
        ),
    ]
    for label, fn in filters:
        sub = [t for t in rows if fn(t)]
        print(f"{label}: {stats(sub)}")

    print("\n=== ANY strategy by session (Monday) ===")
    for sess in ("asia", "london", "ny", "late"):
        sub = [t for t in rows if session_from_entry(t["entry_time"]) == sess]
        if sub:
            print(f"  {sess}: {stats(sub)}")


if __name__ == "__main__":
    main()
