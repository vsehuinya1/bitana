"""Monday + cumulative burst/cascade shadow audit."""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/bitana")
now = datetime.now(timezone.utc)


def age_fmt(s):
    if s is None:
        return "n/a"
    s = max(0, int(s))
    if s < 120:
        return f"{s}s"
    if s < 7200:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def monday_window() -> tuple[datetime, datetime]:
    today = now.date()
    days_since_mon = today.weekday()
    if days_since_mon == 0 and now.hour < 6:
        monday = today - timedelta(days=7)
    else:
        monday = today - timedelta(days=days_since_mon)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def summarize(conn, table, start=None, end=None):
    if start and end:
        n, syms = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM {table} "
            "WHERE bar_time>=? AND bar_time<?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        bad = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE bar_time>=? AND bar_time<? "
            "AND (atr IS NULL OR atr<=0 OR close IS NULL OR close<=0)",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]
        done = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE bar_time>=? AND bar_time<? AND status='done'",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]
    else:
        n, syms = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT symbol) FROM {table}"
        ).fetchone()
        bad = conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE atr IS NULL OR atr<=0 OR close IS NULL OR close<=0"
        ).fetchone()[0]
        done = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE status='done'"
        ).fetchone()[0]
    return int(n), int(syms), int(bad), int(done)


def fwd_stats(conn, table, start=None, end=None, min_bars=12):
    if table == "burst_snapshots":
        cols = "fwd_atr_12, fwd_atr_24, fwd_atr_96, burst_volume_30m, liq_imbalance_30m, session"
    else:
        cols = "fwd_atr_12, fwd_atr_24, fwd_atr_96, cascade_strength, n_confirms, session"
    sql = f"SELECT {cols} FROM {table} WHERE bars_tracked>=?"
    params: list = [min_bars]
    if start and end:
        sql += " AND bar_time>=? AND bar_time<?"
        params.extend([start.isoformat(), end.isoformat()])
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None

    def pack(vals):
        if not vals:
            return None
        vals = list(vals)
        return (
            len(vals),
            sum(vals) / len(vals),
            sum(1 for v in vals if v > 0) / len(vals) * 100,
            sorted(vals)[len(vals) // 2],
        )

    out = {}
    for label, idx in [("1h", 0), ("2h", 1), ("8h", 2)]:
        p = pack(r[idx] for r in rows if r[idx] is not None)
        if p:
            out[label] = p

    if table == "burst_snapshots":
        vols = sorted(r[3] for r in rows if r[3])
        if len(vols) >= 8:
            q75 = vols[int(len(vols) * 0.75)]
            big = [r[0] for r in rows if r[3] and r[3] >= q75 and r[0] is not None]
            p = pack(big)
            if p:
                out["big1h"] = p

        fade = []
        for r in rows:
            if r[0] is None or r[4] is None or abs(r[4]) < 0.3:
                continue
            fade.append(-r[0] if r[4] > 0 else r[0])
        p = pack(fade)
        if p:
            out["fade1h"] = p

    return out, len(rows)


def print_block(title, stats):
    if not stats:
        print(f"{title}: no tracked rows")
        return
    s, n = stats
    print(f"{title} (n={n} with >=1h tracking):")
    for k, v in s.items():
        print(f"  {k:6s} n={v[0]:3d} mean={v[1]:+.3f} med={v[3]:+.3f} WR={v[2]:.0f}%")


def session_table(conn, table, start=None, end=None):
    sql = (
        f"SELECT session, COUNT(*) n, AVG(fwd_atr_12) m, "
        f"SUM(CASE WHEN fwd_atr_12>0 THEN 1 ELSE 0 END)*1.0/COUNT(*) wr "
        f"FROM {table} WHERE bars_tracked>=12 AND fwd_atr_12 IS NOT NULL"
    )
    params: list = []
    if start and end:
        sql += " AND bar_time>=? AND bar_time<?"
        params = [start.isoformat(), end.isoformat()]
    sql += " GROUP BY session ORDER BY n DESC"
    return conn.execute(sql, params).fetchall()


def main():
    mon_start, mon_end = monday_window()
    ss = sqlite3.connect(ROOT / "storage/signal_shadow.db")
    fo = sqlite3.connect(ROOT / "storage/force_orders.db")
    db = sqlite3.connect(ROOT / "storage/v5_forward_test.db")
    now_ms = int(now.timestamp() * 1000)

    mx = ss.execute("SELECT MAX(bar_time) FROM burst_snapshots").fetchone()[0]
    print(f"server_now={now.isoformat()}")
    print(f"latest_burst={mx}")
    print(f"monday_window={mon_start.date()} UTC\n")

    print("=" * 60)
    print("PLUMBING")
    print("=" * 60)
    print(
        subprocess.check_output(
            "systemctl is-active bitana-v5-paper && "
            "systemctl show bitana-v5-paper -p NRestarts -p ExecMainStartTimestamp",
            shell=True,
            text=True,
        ).strip()
    )
    mxfo = fo.execute("SELECT MAX(event_time_ms) FROM force_order_events").fetchone()[0]
    fo24 = fo.execute(
        "SELECT COUNT(*) FROM force_order_events WHERE event_time_ms>?",
        (now_ms - 86400000,),
    ).fetchone()[0]
    print(f"force_orders 24h={fo24} last={age_fmt((now_ms - mxfo) / 1000 if mxfo else None)}")
    hb = db.execute("SELECT value FROM state WHERE key='heartbeat'").fetchone()[0]
    print(f"heartbeat={age_fmt((now - datetime.fromisoformat(hb)).total_seconds())}")

    print("\n" + "=" * 60)
    print("COLLECTION")
    print("=" * 60)
    for table, label in [("burst_snapshots", "burst"), ("snapshots", "cascade")]:
        n, syms, bad, done = summarize(ss, table)
        print(f"{label} all-time: n={n} syms={syms} done={done} bad={bad}")

    print("\n" + "=" * 60)
    print(f"MONDAY {mon_start.date()}")
    print("=" * 60)
    for table, label in [("burst_snapshots", "burst"), ("snapshots", "cascade")]:
        n, syms, bad, done = summarize(ss, table, mon_start, mon_end)
        by = ss.execute(
            f"SELECT symbol, COUNT(*) c FROM {table} "
            "WHERE bar_time>=? AND bar_time<? GROUP BY symbol ORDER BY c DESC LIMIT 10",
            (mon_start.isoformat(), mon_end.isoformat()),
        ).fetchall()
        syms_str = ", ".join(f"{r[0]}:{r[1]}" for r in by) if by else "none"
        print(f"{label}: n={n} syms={syms} done={done} bad={bad} | {syms_str}")

    print_block(
        "BURST Monday",
        fwd_stats(ss, "burst_snapshots", mon_start, mon_end),
    )
    print_block(
        "CASCADE Monday",
        fwd_stats(ss, "snapshots", mon_start, mon_end),
    )

    print("\n  BURST Monday by session:")
    for r in session_table(ss, "burst_snapshots", mon_start, mon_end):
        print(f"    {r[0]:6s} n={r[1]:3d} mean1h={r[2]:+.3f} WR={r[3] * 100:.0f}%")

    print("\n" + "=" * 60)
    print("ALL-TIME SINCE DEPLOY")
    print("=" * 60)
    print_block("BURST all", fwd_stats(ss, "burst_snapshots"))
    print_block("CASCADE all", fwd_stats(ss, "snapshots"))

    print("\n  BURST all by session:")
    for r in session_table(ss, "burst_snapshots"):
        print(f"    {r[0]:6s} n={r[1]:3d} mean1h={r[2]:+.3f} WR={r[3] * 100:.0f}%")

    tr = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_r),0) FROM trades "
        "WHERE entry_time>=? AND entry_time<?",
        (mon_start.isoformat(), mon_end.isoformat()),
    ).fetchone()
    print(f"\nLive v65 trades Monday: {tr[0]}t {tr[1]:+.2f}R")


if __name__ == "__main__":
    main()
