"""Two-week live + shadow + telemetry review for v65-revert paper bot."""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/root/bitana/storage/v5_forward_test.db")
FORCE = Path("/root/bitana/storage/force_orders.db")


def hour_of(ts: str) -> int:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
    except (ValueError, TypeError):
        return -1


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    def state(k):
        r = conn.execute("SELECT value FROM state WHERE key=?", (k,)).fetchone()
        return r[0] if r else None

    print("=" * 60)
    print("STATE")
    print("=" * 60)
    for k in ["equity", "peak_equity", "last_startup", "last_session_report_date",
              "last_asia_report_date", "promotion_halt", "promotion_alert_status"]:
        print(f"  {k}: {state(k)}")

    # ---- live trades ----
    rows = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]
    v65 = [r for r in rows if r.get("strategy_version") == "v65_revert"]
    recent = [r for r in rows if r["entry_time"] >= "2026-06-10"]
    print("\n" + "=" * 60)
    print(f"LIVE TRADES — total db={len(rows)}, v65_revert={len(v65)}, since Jun10={len(recent)}")
    print("=" * 60)
    for r in recent:
        print(f"  {r['entry_time'][:16]} {r['symbol']:12} {r['side']:5} "
              f"D{r.get('decile','?')} R={r['pnl_r']:+.3f} {r['exit_reason']}")
    if recent:
        tot = sum(r["pnl_r"] for r in recent)
        wins = sum(1 for r in recent if r["pnl_r"] > 0)
        print(f"  --> n={len(recent)} totR={tot:+.3f} wr={wins/len(recent):.0%}")

    print("\nOPEN POSITIONS:")
    for r in conn.execute("SELECT symbol, side, entry_time, candles_held, decile FROM open_positions").fetchall():
        print("  ", dict(r))

    # ---- shadow ----
    print("\n" + "=" * 60)
    print("ASIA SHADOW")
    print("=" * 60)
    st = conn.execute("SELECT COUNT(*) FROM shadow_trades").fetchone()[0]
    sp = conn.execute("SELECT COUNT(*) FROM shadow_positions").fetchone()[0]
    sr = conn.execute("SELECT COALESCE(SUM(pnl_r),0) FROM shadow_trades").fetchone()[0]
    print(f"  shadow_trades={st} open_positions={sp} cumR={sr:+.3f}")
    srows = [dict(r) for r in conn.execute("SELECT * FROM shadow_trades ORDER BY id").fetchall()]
    for r in srows:
        print(f"  {r['entry_time'][:16]} {r['symbol']:12} R={r['pnl_r']:+.3f} "
              f"{r['exit_reason']:14} bars={r.get('hold_candles','?')} D{r.get('decile','?')}")
    if srows:
        wins = sum(1 for r in srows if r["pnl_r"] > 0)
        print(f"  --> n={len(srows)} wr={wins/len(srows):.0%}")
        by_h = defaultdict(lambda: [0, 0.0])
        for r in srows:
            h = hour_of(r["entry_time"])
            by_h[h][0] += 1
            by_h[h][1] += r["pnl_r"]
        print("  by entry hour UTC:")
        for h in sorted(by_h):
            print(f"    {h:02d}: n={by_h[h][0]} totR={by_h[h][1]:+.3f}")
    print("  open shadow positions:")
    for r in conn.execute("SELECT symbol, entry_time, candles_held, decile FROM shadow_positions").fetchall():
        print("    ", dict(r))

    # ---- telemetry tables ----
    print("\n" + "=" * 60)
    print("TELEMETRY TABLES")
    print("=" * 60)
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    for t in tabs:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {n}")
        except sqlite3.Error:
            pass

    # shadow entry filters
    if "shadow_entry_filters" in tabs:
        print("\n  shadow_entry_filters sample (last 5):")
        for r in conn.execute("SELECT * FROM shadow_entry_filters ORDER BY rowid DESC LIMIT 5").fetchall():
            print("    ", dict(r))

    conn.close()

    # ---- force orders accumulation ----
    print("\n" + "=" * 60)
    print("FORCE-ORDER CAPTURE (raw WS liquidations)")
    print("=" * 60)
    if FORCE.exists():
        fc = sqlite3.connect(FORCE)
        n, lo, hi = fc.execute(
            "SELECT COUNT(*), MIN(event_time_ms), MAX(event_time_ms) FROM force_order_events").fetchone()
        nsym = fc.execute("SELECT COUNT(DISTINCT symbol) FROM force_order_events").fetchone()[0]
        f = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ms else "-"
        print(f"  events={n} symbols={nsym}")
        print(f"  span: {f(lo)} -> {f(hi)}")
        days = (hi - lo) / 1000 / 86400 if lo else 0
        print(f"  ~{days:.1f} days, ~{n/max(days,1):.0f} events/day")
        print("  top symbols by event count:")
        for sym, c in fc.execute(
                "SELECT symbol, COUNT(*) c FROM force_order_events GROUP BY symbol ORDER BY c DESC LIMIT 10").fetchall():
            print(f"    {sym:12} {c}")
        fc.close()
    else:
        print("  force_orders.db not found")


if __name__ == "__main__":
    main()
