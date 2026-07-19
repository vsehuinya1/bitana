"""Exhaustive setup_snapshots sweep — filters, sessions, days, TP/SL grid."""
from __future__ import annotations

import sqlite3
import statistics as stats
from collections import defaultdict
from itertools import product
from pathlib import Path

DB = Path("/root/bitana/storage/signal_shadow.db")

STOPS = (1.5, 2.0, 2.5, 3.0, 4.0)
TPS = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
TIME_BARS = 12  # 1h exit if neither hit


def sim(mfe, mae, fwd, stop, tp, time_bars=TIME_BARS, bars_tracked=None):
    """Conservative: both hit same window → stop first."""
    hit_tp = mfe >= tp
    hit_sl = mae <= -stop
    if hit_sl and hit_tp:
        return -stop, "both"
    if hit_sl:
        return -stop, "stop"
    if hit_tp:
        return tp, "tp"
    if fwd is not None:
        return max(min(fwd, tp), -stop), "time"
    return 0.0, "open"


def load_rows(conn):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT id, date(bar_time) AS day, session, hour, symbol,
               v_strict, v_allhours, v_confirms3, v_loose,
               cascade_active, decile, n_confirms, breakout, above_ema,
               bars_tracked, status,
               fwd_atr_3, fwd_atr_6, fwd_atr_12, fwd_atr_24,
               mfe_atr, mae_atr
        FROM setup_snapshots
        WHERE bars_tracked >= 12
        """
    ).fetchall()


def pack_pnls(pnls):
    if not pnls:
        return None
    return {
        "n": len(pnls),
        "mean": stats.mean(pnls),
        "med": stats.median(pnls),
        "wr": sum(1 for x in pnls if x > 0) / len(pnls) * 100,
        "sum": sum(pnls),
    }


def run_grid(rows, label=""):
    results = []
    for stop, tp in product(STOPS, TPS):
        if tp < stop * 0.5:
            continue
        pnls = [sim(r["mfe_atr"], r["mae_atr"], r["fwd_atr_12"], stop, tp)[0] for r in rows]
        p = pack_pnls(pnls)
        if p and p["n"] >= 10:
            results.append((stop, tp, p))
    results.sort(key=lambda x: x[2]["mean"], reverse=True)
    return results


def print_top(results, title, k=8):
    print(f"\n{title}")
    if not results:
        print("  (none with n>=10)")
        return
    for stop, tp, p in results[:k]:
        print(
            f"  SL={stop:.1f} TP={tp:.1f}  n={p['n']:3d}  "
            f"mean={p['mean']:+.3f}R  med={p['med']:+.3f}R  "
            f"WR={p['wr']:.0f}%  sum={p['sum']:+.1f}R"
        )


def main():
    conn = sqlite3.connect(DB)
    rows = load_rows(conn)
    total = conn.execute("SELECT COUNT(*) FROM setup_snapshots").fetchone()[0]
    print("=" * 72)
    print(f"SETUP SNAPSHOTS SWEEP  |  logged={total}  tracked>=1h={len(rows)}")
    print("=" * 72)
    print("Sim: conservative stop-first if both SL+TP reachable; else 1h time exit.")
    print(f"Grid: SL={STOPS}  TP={TPS}\n")

    # ── baseline filters ──
    filters = {
        "ALL": lambda r: True,
        "v_strict": lambda r: r["v_strict"],
        "v_allhours": lambda r: r["v_allhours"],
        "v_confirms3": lambda r: r["v_confirms3"],
        "v_loose": lambda r: r["v_loose"],
        "v_strict+cascade": lambda r: r["v_strict"] and r["cascade_active"],
        "v_strict+no_cascade": lambda r: r["v_strict"] and not r["cascade_active"],
        "v_strict+breakout": lambda r: r["v_strict"] and r["breakout"],
        "v_strict+ema": lambda r: r["v_strict"] and r["above_ema"],
        "confirms3+ny": lambda r: r["v_confirms3"] and r["session"] == "ny",
        "confirms3+asia": lambda r: r["v_confirms3"] and r["session"] == "asia",
        "confirms4+": lambda r: r["n_confirms"] >= 4,
        "confirms5+": lambda r: r["n_confirms"] >= 5,
    }
    for d in range(1, 11):
        filters[f"D{d}"] = (lambda dec=d: lambda r: r["decile"] == dec)()

    print("── FILTER × TP/SL (best 8 combos per filter) ──")
    best_overall = []
    for name, fn in filters.items():
        sub = [r for r in rows if fn(r)]
        if len(sub) < 10:
            continue
        grid = run_grid(sub)
        if grid:
            stop, tp, p = grid[0]
            best_overall.append((p["mean"], name, stop, tp, p))
            print_top(grid[:5], f"[{name}] n={len(sub)}")

    print("\n── TOP 15 FILTER+COMBO (by mean R) ──")
    best_overall.sort(reverse=True)
    for mean, name, stop, tp, p in best_overall[:15]:
        print(
            f"  {name:22s} SL={stop:.1f} TP={tp:.1f}  n={p['n']:3d}  "
            f"mean={p['mean']:+.3f}R  WR={p['wr']:.0f}%  sum={p['sum']:+.1f}R"
        )

    # ── by session ──
    print("\n── BY SESSION (v_strict, best TP/SL each) ──")
    for sess in ("asia", "london", "ny", "late"):
        sub = [r for r in rows if r["v_strict"] and r["session"] == sess]
        print_top(run_grid(sub), f"session={sess} v_strict n={len(sub)}", k=5)

    print("\n── BY SESSION (confirms3, best TP/SL each) ──")
    for sess in ("asia", "london", "ny", "late"):
        sub = [r for r in rows if r["v_confirms3"] and r["session"] == sess]
        print_top(run_grid(sub), f"session={sess} confirms3 n={len(sub)}", k=5)

    # ── by day ──
    print("\n── BY DAY (confirms3, best TP/SL each) ──")
    days = sorted({r["day"] for r in rows})
    for day in days:
        sub = [r for r in rows if r["v_confirms3"] and r["day"] == day]
        if len(sub) < 5:
            continue
        grid = run_grid(sub)
        if grid:
            stop, tp, p = grid[0]
            print(
                f"  {day} n={len(sub):3d}  best SL={stop:.1f} TP={tp:.1f}  "
                f"mean={p['mean']:+.3f}R  WR={p['wr']:.0f}%  sum={p['sum']:+.1f}R"
            )

    # ── by day × session (confirms3) ──
    print("\n── BY DAY × SESSION (confirms3, n>=5, best combo) ──")
    cells = defaultdict(list)
    for r in rows:
        if r["v_confirms3"]:
            cells[(r["day"], r["session"])].append(r)
    day_sess_results = []
    for (day, sess), sub in sorted(cells.items()):
        if len(sub) < 5:
            continue
        grid = run_grid(sub)
        if grid:
            stop, tp, p = grid[0]
            day_sess_results.append((p["mean"], day, sess, stop, tp, p))
    day_sess_results.sort(reverse=True)
    for mean, day, sess, stop, tp, p in day_sess_results[:12]:
        print(
            f"  {day} {sess:6s} n={p['n']:2d}  SL={stop:.1f} TP={tp:.1f}  "
            f"mean={p['mean']:+.3f}R  WR={p['wr']:.0f}%"
        )
    print("  ... worst:")
    for mean, day, sess, stop, tp, p in day_sess_results[-5:]:
        print(
            f"  {day} {sess:6s} n={p['n']:2d}  SL={stop:.1f} TP={tp:.1f}  "
            f"mean={p['mean']:+.3f}R  WR={p['wr']:.0f}%"
        )

    # ── drift-only (no sim) by filter ──
    print("\n── RAW 1h DRIFT (no SL/TP, by filter) ──")
    drift_rows = []
    for name, fn in filters.items():
        sub = [r for r in rows if fn(r) and r["fwd_atr_12"] is not None]
        if len(sub) < 10:
            continue
        xs = [r["fwd_atr_12"] for r in sub]
        drift_rows.append((stats.mean(xs), name, len(sub), stats.median(xs),
                           sum(1 for x in xs if x > 0) / len(xs) * 100))
    drift_rows.sort(reverse=True)
    for mean, name, n, med, wr in drift_rows[:12]:
        print(f"  {name:22s} n={n:3d}  mean={mean:+.3f}  med={med:+.3f}  WR={wr:.0f}%")

    # ── honest stability: train Thu-Sat vs test Mon-Wed ──
    train_days = {"2026-06-19", "2026-06-20", "2026-06-21", "2026-06-26", "2026-06-27"}
    test_days = {"2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25"}
    print("\n── CHRONO SPLIT (confirms3, best train combo applied to test) ──")
    train = [r for r in rows if r["v_confirms3"] and r["day"] in train_days]
    test = [r for r in rows if r["v_confirms3"] and r["day"] in test_days]
    if len(train) >= 10:
        grid = run_grid(train)
        if grid:
            stop, tp, p = grid[0]
            test_pnls = [sim(r["mfe_atr"], r["mae_atr"], r["fwd_atr_12"], stop, tp)[0] for r in test]
            tp_ = pack_pnls(test_pnls)
            print(f"  TRAIN days {sorted(train_days)} n={len(train)}")
            print(f"    best SL={stop:.1f} TP={tp:.1f}  mean={p['mean']:+.3f}R  WR={p['wr']:.0f}%")
            if tp_:
                print(f"  TEST  days {sorted(test_days)} n={tp_['n']}")
                print(f"    same combo       mean={tp_['mean']:+.3f}R  WR={tp_['wr']:.0f}%  sum={tp_['sum']:+.1f}R")

    print("\n── VERDICT GUIDE ──")
    print("  KEEP candidate: mean>+0.15R AND n>=20 AND WR>=52% on same combo in train+test")
    print("  Anything n<15 or single-day driven = ignore")


if __name__ == "__main__":
    main()
