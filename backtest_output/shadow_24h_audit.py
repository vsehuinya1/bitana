"""24h shadow + plumbing audit for VPS."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/bitana")
now = datetime.now(timezone.utc)
now_ts = now.timestamp()
now_ms = int(now_ts * 1000)
HORIZONS = {3: "15m", 6: "30m", 12: "1h", 24: "2h", 48: "4h", 96: "8h"}


def age_fmt(s):
    if s is None:
        return "n/a"
    s = max(0, int(s))
    if s < 120:
        return f"{s}s"
    if s < 7200:
        return f"{s // 60}m"
    return f"{s // 3600}h{s % 3600 // 60}m"


def age_iso(ts):
    if not ts:
        return None
    return (now - datetime.fromisoformat(ts)).total_seconds()


def table_stats(conn, table):
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    open_n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status='open'").fetchone()[0]
    done = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status='done'").fetchone()[0]
    orph = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status='orphaned'").fetchone()[0]
    h24 = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE created_at > ?", (now_ts - 86400,)
    ).fetchone()[0]
    syms = conn.execute(
        f"SELECT COUNT(DISTINCT symbol) FROM {table} WHERE created_at > ?",
        (now_ts - 86400,),
    ).fetchone()[0]
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {table} "
        "WHERE atr IS NULL OR atr<=0 OR close IS NULL OR close<=0"
    ).fetchone()[0]
    by_sym = conn.execute(
        f"SELECT symbol, COUNT(*) c FROM {table} WHERE created_at > ? "
        "GROUP BY symbol ORDER BY c DESC",
        (now_ts - 86400,),
    ).fetchall()
    return dict(
        total=total, open=open_n, done=done, orphaned=orph,
        h24=h24, syms=syms, bad=bad, by_sym=by_sym,
    )


def analyze_burst(conn, min_bars=12):
    rows = conn.execute(
        """
        SELECT symbol, session, bars_tracked, liq_imbalance_30m, burst_volume_30m,
               fwd_atr_3, fwd_atr_6, fwd_atr_12, fwd_atr_24, fwd_atr_48, fwd_atr_96,
               mfe_atr, mae_atr
        FROM burst_snapshots
        WHERE status IN ('open', 'done') AND bars_tracked >= ?
        """,
        (min_bars,),
    ).fetchall()
    if not rows:
        return None

    col_map = {3: 5, 6: 6, 12: 7, 24: 8, 48: 9, 96: 10}
    out = {}
    for h, label in HORIZONS.items():
        idx = col_map[h]
        vals = [r[idx] for r in rows if r[idx] is not None]
        if vals:
            out[label] = dict(
                n=len(vals),
                mean=sum(vals) / len(vals),
                wr=sum(1 for v in vals if v > 0) / len(vals) * 100,
                med=sorted(vals)[len(vals) // 2],
            )

    follow, fade = [], []
    for r in rows:
        if r[3] is None or abs(r[3]) < 0.3 or r[7] is None:
            continue
        if r[3] > 0:
            follow.append(r[7])
            fade.append(-r[7])
        else:
            follow.append(-r[7])
            fade.append(r[7])
    if follow:
        out["follow_liq_1h"] = dict(
            n=len(follow),
            mean=sum(follow) / len(follow),
            wr=sum(1 for v in follow if v > 0) / len(follow) * 100,
        )
    if fade:
        out["fade_liq_1h"] = dict(
            n=len(fade),
            mean=sum(fade) / len(fade),
            wr=sum(1 for v in fade if v > 0) / len(fade) * 100,
        )

    # volume quartile split at 1h
    vols = sorted(r[4] for r in rows if r[4])
    if len(vols) >= 8:
        q75 = vols[int(len(vols) * 0.75)]
        big = [r[7] for r in rows if r[4] and r[4] >= q75 and r[7] is not None]
        small = [r[7] for r in rows if r[4] and r[4] < q75 and r[7] is not None]
        if big:
            out["big_burst_1h"] = dict(
                n=len(big), mean=sum(big) / len(big),
                wr=sum(1 for v in big if v > 0) / len(big) * 100,
            )
        if small:
            out["small_burst_1h"] = dict(
                n=len(small), mean=sum(small) / len(small),
                wr=sum(1 for v in small if v > 0) / len(small) * 100,
            )
    return out, len(rows)


def analyze_cascade(conn, min_bars=12):
    rows = conn.execute(
        """
        SELECT symbol, session, bars_tracked, cascade_strength, n_confirms, decile,
               v_strict, v_loose,
               fwd_atr_3, fwd_atr_6, fwd_atr_12, fwd_atr_24, mfe_atr, mae_atr
        FROM snapshots
        WHERE status IN ('open', 'done') AND bars_tracked >= ?
        """,
        (min_bars,),
    ).fetchall()
    if not rows:
        return None
    out = {}
    vals12 = [r[10] for r in rows if r[10] is not None]
    vals24 = [r[11] for r in rows if r[11] is not None]
    if vals12:
        out["1h"] = dict(n=len(vals12), mean=sum(vals12) / len(vals12),
                         wr=sum(1 for v in vals12 if v > 0) / len(vals12) * 100)
    if vals24:
        out["2h"] = dict(n=len(vals24), mean=sum(vals24) / len(vals24),
                         wr=sum(1 for v in vals24 if v > 0) / len(vals24) * 100)
    strict = [r[10] for r in rows if r[6] and r[10] is not None]
    loose = [r[10] for r in rows if r[7] and r[10] is not None]
    if strict:
        out["v_strict_1h"] = dict(n=len(strict), mean=sum(strict) / len(strict),
                                  wr=sum(1 for v in strict if v > 0) / len(strict) * 100)
    if loose:
        out["v_loose_1h"] = dict(n=len(loose), mean=sum(loose) / len(loose),
                                 wr=sum(1 for v in loose if v > 0) / len(loose) * 100)
    return out, len(rows)


def main():
    print("=" * 60)
    print("SERVICE / PLUMBING")
    print("=" * 60)
    for cmd in (
        "systemctl is-active bitana-v5-paper",
        "systemctl show bitana-v5-paper -p ExecMainStartTimestamp -p NRestarts -p Result",
    ):
        print(subprocess.check_output(cmd, shell=True, text=True).strip())

    fo = sqlite3.connect(ROOT / "storage/force_orders.db")
    total, mx = fo.execute(
        "SELECT COUNT(*), MAX(event_time_ms) FROM force_order_events"
    ).fetchone()
    for label, secs in [("10m", 600), ("1h", 3600), ("24h", 86400)]:
        n = fo.execute(
            "SELECT COUNT(*) FROM force_order_events WHERE event_time_ms > ?",
            (now_ms - secs * 1000,),
        ).fetchone()[0]
        print(f"force_orders {label}: {n}")
    sym24 = fo.execute(
        "SELECT COUNT(DISTINCT symbol) FROM force_order_events WHERE event_time_ms > ?",
        (now_ms - 86400000,),
    ).fetchone()[0]
    print(
        f"force_orders total={total} last_age={age_fmt((now_ms - mx) / 1000 if mx else None)} "
        f"sym24h={sym24}"
    )

    db = sqlite3.connect(ROOT / "storage/v5_forward_test.db")
    hb = db.execute("SELECT value FROM state WHERE key='heartbeat'").fetchone()[0]
    print(f"heartbeat_age={age_fmt(age_iso(hb))}")
    trades = db.execute("SELECT COUNT(*), COALESCE(SUM(pnl_r),0) FROM trades").fetchone()
    print(f"live_trades={trades[0]} total_R={trades[1]:+.2f}")

    ss = sqlite3.connect(ROOT / "storage/signal_shadow.db")
    print("\n" + "=" * 60)
    print("COLLECTION (24h)")
    print("=" * 60)
    for table, label in [("snapshots", "cascade"), ("burst_snapshots", "burst")]:
        s = table_stats(ss, table)
        print(
            f"{label}: total={s['total']} 24h={s['h24']} open={s['open']} "
            f"done={s['done']} orph={s['orphaned']} syms={s['syms']} bad={s['bad']}"
        )
        if s["by_sym"]:
            print("  ", ", ".join(f"{r[0]}:{r[1]}" for r in s["by_sym"][:12]))

    print("\n" + "=" * 60)
    print("EARLY FORWARD-PATH READ (>=1h tracked)")
    print("=" * 60)
    for fn, label in [(analyze_burst, "BURST"), (analyze_cascade, "CASCADE")]:
        res = fn(ss, min_bars=12)
        if not res:
            print(f"{label}: not enough tracked rows yet")
            continue
        stats, n = res
        print(f"\n{label} (n={n} with >=1h tracking):")
        for k, v in stats.items():
            med = f" med={v['med']:+.3f}" if "med" in v else ""
            print(f"  {k:18s} n={v['n']:3d} mean={v['mean']:+.3f}ATR{med} WR={v['wr']:.0f}%")

    # errors since deploy
    print("\n" + "=" * 60)
    print("RECENT ERRORS (tail log)")
    print("=" * 60)
    import subprocess as sp
    out = sp.check_output(
        "tail -300 /var/log/bitana-v5.log | sed 's/\\x1b\\[[0-9;]*m//g' | "
        "grep -E 'Watchdog|critical|Signal shadow on_bar error|on_5m_close timed out' | tail -8 || true",
        shell=True, text=True,
    )
    print(out.strip() or "(none recent)")

    du = shutil.disk_usage(ROOT)
    print(f"\nDisk: {du.free / du.total * 100:.1f}% free")
    print(f"now={now.isoformat()}")

    print("\n" + "=" * 60)
    print("BURST BY SESSION (1h)")
    print("=" * 60)
    for r in ss.execute(
        """
        SELECT session, COUNT(*) n, AVG(fwd_atr_12) mean12,
               SUM(CASE WHEN fwd_atr_12>0 THEN 1 ELSE 0 END)*1.0/COUNT(*) wr
        FROM burst_snapshots
        WHERE bars_tracked>=12 AND fwd_atr_12 IS NOT NULL
        GROUP BY session ORDER BY n DESC
        """
    ):
        print(f"  {r[0]:6s} n={r[1]:3d} mean1h={r[2]:+.3f} WR={r[3]*100:.0f}%")

    print("\n" + "=" * 60)
    print("8h OUTLIER CHECK")
    print("=" * 60)
    vals = [
        r[0]
        for r in ss.execute(
            "SELECT fwd_atr_96 FROM burst_snapshots WHERE fwd_atr_96 IS NOT NULL"
        ).fetchall()
    ]
    if vals:
        vals.sort()
        trim = vals[: int(len(vals) * 0.9)]
        print(
            f"  n={len(vals)} mean={sum(vals)/len(vals):+.3f} "
            f"median={vals[len(vals)//2]:+.3f}"
        )
        print(
            f"  trim10% mean={sum(trim)/len(trim):+.3f} "
            f"median={trim[len(trim)//2]:+.3f}"
        )


if __name__ == "__main__":
    main()
