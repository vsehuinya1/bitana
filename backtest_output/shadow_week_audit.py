"""~1 week burst/cascade shadow audit since v6.5.3 deploy."""
from __future__ import annotations

import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/bitana")
DEPLOY = datetime(2026, 6, 19, 13, 29, 43, tzinfo=timezone.utc)
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


def pack(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return (
        len(vals),
        sum(vals) / len(vals),
        sum(1 for v in vals if v > 0) / len(vals) * 100,
        sorted(vals)[len(vals) // 2],
    )


def burst_rows(conn, day=None, min_bars=12):
    sql = (
        "SELECT date(bar_time) d, session, fwd_atr_12, fwd_atr_24, fwd_atr_96, "
        "burst_volume_30m, liq_imbalance_30m FROM burst_snapshots "
        "WHERE bars_tracked>=?"
    )
    params: list = [min_bars]
    if day:
        sql += " AND date(bar_time)=?"
        params.append(day)
    return conn.execute(sql, params).fetchall()


def analyze_rows(rows):
    out = {}
    for label, idx in [("1h", 2), ("2h", 3), ("8h", 4)]:
        p = pack(r[idx] for r in rows)
        if p:
            out[label] = p
    vols = sorted(r[5] for r in rows if r[5])
    if len(vols) >= 8:
        q75 = vols[int(len(vols) * 0.75)]
        big = [r[2] for r in rows if r[5] and r[5] >= q75 and r[2] is not None]
        p = pack(big)
        if p:
            out["big1h"] = p
    fade = []
    for r in rows:
        if r[2] is None or r[6] is None or abs(r[6]) < 0.3:
            continue
        fade.append(-r[2] if r[6] > 0 else r[2])
    p = pack(fade)
    if p:
        out["fade1h"] = p
    follow = []
    for r in rows:
        if r[2] is None or r[6] is None or abs(r[6]) < 0.3:
            continue
        follow.append(r[2] if r[6] > 0 else -r[2])
    p = pack(follow)
    if p:
        out["follow1h"] = p
    return out


def print_stats(title, stats):
    if not stats:
        print(f"{title}: no data")
        return
    print(title + ":")
    for k, v in stats.items():
        print(f"  {k:8s} n={v[0]:4d} mean={v[1]:+.3f} med={v[3]:+.3f} WR={v[2]:.0f}%")


def main():
    ss = sqlite3.connect(ROOT / "storage/signal_shadow.db")
    fo = sqlite3.connect(ROOT / "storage/force_orders.db")
    db = sqlite3.connect(ROOT / "storage/v5_forward_test.db")
    now_ms = int(now.timestamp() * 1000)
    uptime_h = (now - DEPLOY).total_seconds() / 3600

    print(f"now={now.isoformat()}  uptime_since_deploy={uptime_h:.1f}h ({uptime_h/24:.1f}d)")
    print("=" * 64)
    print("PLUMBING")
    print("=" * 64)
    print(
        subprocess.check_output(
            "systemctl is-active bitana-v5-paper && "
            "systemctl show bitana-v5-paper -p NRestarts -p ExecMainStartTimestamp",
            shell=True,
            text=True,
        ).strip()
    )
    mxfo = fo.execute("SELECT MAX(event_time_ms) FROM force_order_events").fetchone()[0]
    fo_total = fo.execute("SELECT COUNT(*) FROM force_order_events").fetchone()[0]
    fo24 = fo.execute(
        "SELECT COUNT(*) FROM force_order_events WHERE event_time_ms>?",
        (now_ms - 86400000,),
    ).fetchone()[0]
    fo_week = fo.execute(
        "SELECT COUNT(*) FROM force_order_events WHERE event_time_ms>?",
        (int(DEPLOY.timestamp() * 1000),),
    ).fetchone()[0]
    print(
        f"force_orders: total={fo_total} week={fo_week} 24h={fo24} "
        f"last={age_fmt((now_ms - mxfo) / 1000 if mxfo else None)}"
    )
    hb = db.execute("SELECT value FROM state WHERE key='heartbeat'").fetchone()[0]
    print(f"heartbeat={age_fmt((now - datetime.fromisoformat(hb)).total_seconds())}")

    burst_n = ss.execute("SELECT COUNT(*) FROM burst_snapshots").fetchone()[0]
    burst_syms = ss.execute("SELECT COUNT(DISTINCT symbol) FROM burst_snapshots").fetchone()[0]
    burst_bad = ss.execute(
        "SELECT COUNT(*) FROM burst_snapshots "
        "WHERE atr IS NULL OR atr<=0 OR close IS NULL OR close<=0"
    ).fetchone()[0]
    cas_n = ss.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    cas_syms = ss.execute("SELECT COUNT(DISTINCT symbol) FROM snapshots").fetchone()[0]

    print("\n" + "=" * 64)
    print("COLLECTION")
    print("=" * 64)
    print(f"burst:     n={burst_n} syms={burst_syms} bad={burst_bad}")
    print(f"cascade:   n={cas_n} syms={cas_syms} (ICP/DASH funnel only)")
    by_day = ss.execute(
        "SELECT date(bar_time), COUNT(*), COUNT(DISTINCT symbol) "
        "FROM burst_snapshots GROUP BY date(bar_time) ORDER BY 1"
    ).fetchall()
    print("\nBurst snapshots by day:")
    for d, n, s in by_day:
        print(f"  {d}: {n:4d} snapshots, {s} symbols")

    all_rows = burst_rows(ss)
    print("\n" + "=" * 64)
    print("BURST ALL-TIME (tracked >=1h)")
    print("=" * 64)
    print_stats("", analyze_rows(all_rows))

    print("\n  By session (1h):")
    by_sess = defaultdict(list)
    for r in all_rows:
        if r[2] is not None:
            by_sess[r[1]].append(r[2])
    for sess in ("asia", "london", "ny", "late"):
        p = pack(by_sess.get(sess, []))
        if p:
            print(f"    {sess:6s} n={p[0]:4d} mean={p[1]:+.3f} med={p[3]:+.3f} WR={p[2]:.0f}%")

    print("\n" + "=" * 64)
    print("BURST BY DAY (1h drift)")
    print("=" * 64)
    days = sorted({r[0] for r in all_rows})
    for d in days:
        day_rows = [r for r in all_rows if r[0] == d]
        stats = analyze_rows(day_rows)
        if not stats or "1h" not in stats:
            continue
        s = stats["1h"]
        s2 = stats.get("2h")
        s8 = stats.get("8h")
        line = f"  {d} n={len(day_rows):3d} 1h={s[1]:+.3f}(WR{s[2]:.0f}%)"
        if s2:
            line += f" 2h={s2[1]:+.3f}"
        if s8:
            line += f" 8h={s8[1]:+.3f}(WR{s8[2]:.0f}%)"
        if "big1h" in stats:
            b = stats["big1h"]
            line += f" big1h={b[1]:+.3f}(n{b[0]})"
        print(line)

    print("\n" + "=" * 64)
    print("CASCADE (all-time, >=1h)")
    print("=" * 64)
    cas = ss.execute(
        "SELECT fwd_atr_12, fwd_atr_24, v_strict, v_loose, session "
        "FROM snapshots WHERE bars_tracked>=12"
    ).fetchall()
    p1 = pack(r[0] for r in cas if r[0] is not None)
    p2 = pack(r[1] for r in cas if r[1] is not None)
    if p1:
        print(f"  1h n={p1[0]} mean={p1[1]:+.3f} med={p1[3]:+.3f} WR={p1[2]:.0f}%")
    if p2:
        print(f"  2h n={p2[0]} mean={p2[1]:+.3f} med={p2[3]:+.3f} WR={p2[2]:.0f}%")
    strict = [r[0] for r in cas if r[2] and r[0] is not None]
    loose = [r[0] for r in cas if r[3] and r[0] is not None]
    ps = pack(strict)
    pl = pack(loose)
    if ps:
        print(f"  v_strict 1h n={ps[0]} mean={ps[1]:+.3f}")
    else:
        print("  v_strict: 0 fires")
    if pl:
        print(f"  v_loose  1h n={pl[0]} mean={pl[1]:+.3f} med={pl[3]:+.3f} WR={pl[2]:.0f}%")

    tr = db.execute("SELECT COUNT(*), COALESCE(SUM(pnl_r),0) FROM trades").fetchone()
    tr_week = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(pnl_r),0) FROM trades WHERE entry_time>=?",
        (DEPLOY.isoformat(),),
    ).fetchone()
    print("\n" + "=" * 64)
    print("LIVE PAPER")
    print("=" * 64)
    print(f"since deploy: {tr_week[0]}t {tr_week[1]:+.2f}R")
    print(f"all-time:     {tr[0]}t {tr[1]:+.2f}R")

    errs = subprocess.check_output(
        "grep -E 'Watchdog|critical|Signal shadow on_bar error' /var/log/bitana-v5.log "
        "| awk '$1 >= \"2026-06-19T13:29\"' | tail -5 || true",
        shell=True,
        text=True,
    ).strip()
    print("\nErrors since deploy:", errs or "(none)")


if __name__ == "__main__":
    main()
