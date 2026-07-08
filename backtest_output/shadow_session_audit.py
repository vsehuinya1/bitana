"""Per-session shadow P&L — the unit that actually matters for correlated bursts.

Groups closed shadow trades into:
  - session-day totals (equal-weight sum of pnl_atr across all fires that day)
  - 15-min entry clusters (shows sign agreement within a cascade)

Run on VPS:
  python3 backtest_output/shadow_session_audit.py
  python3 backtest_output/shadow_session_audit.py storage/signal_shadow.db ny_flush_buy_4h
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path("/root/bitana")
DB = ROOT / "storage/signal_shadow.db"
DEFAULT_STRATEGIES = (
    "ny_flush_buy_4h",
    "ny_flush_buy_4h_tsl",
    "ny_flush_buy_4h_open",
    "ny_flush_buy_4h_open_tsl",
    "asia_pump_short_4h",
    "asia_pump_short_4h_tsl",
)


def load(conn: sqlite3.Connection, strategies: tuple[str, ...]):
    placeholders = ",".join("?" * len(strategies))
    return conn.execute(
        f"""
        SELECT strategy, symbol, entry_time, pnl_atr, run_mae_atr, run_mfe_atr,
               hour, btc_trend_state, concurrent_positions_same_side
        FROM shadow_trades
        WHERE status='closed' AND strategy IN ({placeholders})
        ORDER BY entry_time
        """,
        strategies,
    ).fetchall()


def print_strategy_summary(rows):
    by_strat: dict[str, list] = {}
    for r in rows:
        by_strat.setdefault(r[0], []).append(r)

    print("=" * 72)
    print("STRATEGY SUMMARY (per-trade — noisy when correlated)")
    print("=" * 72)
    for strat in sorted(by_strat):
        trades = by_strat[strat]
        pnls = [t[3] for t in trades if t[3] is not None]
        if not pnls:
            continue
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        print(
            f"  {strat:30s} n={len(pnls):3d}  tot={sum(pnls):+.1f}  "
            f"mean={sum(pnls)/len(pnls):+.2f}  WR={wr:.0f}%"
        )


def print_session_days(rows):
    print("\n" + "=" * 72)
    print("SESSION-DAY UNITS (equal-weight: sum pnl_atr / n_fires = per-fire; sum = session total)")
    print("=" * 72)
    by_key: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        strat, _, entry_time, pnl_atr = r[0], r[1], r[2], r[3]
        if pnl_atr is None:
            continue
        day = entry_time[:10]
        by_key.setdefault((strat, day), []).append(float(pnl_atr))

    for strat in sorted({k[0] for k in by_key}):
        days = sorted(d for s, d in by_key if s == strat)
        session_pnls = [sum(by_key[(strat, d)]) for d in days]
        if not session_pnls:
            continue
        cum = 0.0
        print(f"\n  {strat}")
        for d in days:
            fires = by_key[(strat, d)]
            day_tot = sum(fires)
            cum += day_tot
            wr = sum(1 for p in fires if p > 0) / len(fires) * 100
            print(
                f"    {d}  fires={len(fires):2d}  day_pnl={day_tot:+.1f} ATR  "
                f"mean={day_tot/len(fires):+.2f}  WR={wr:.0f}%  cum={cum:+.1f}"
            )
        n_sess = len(session_pnls)
        mean_sess = sum(session_pnls) / n_sess
        print(
            f"    → {n_sess} session-days  total={sum(session_pnls):+.1f} ATR  "
            f"mean/session={mean_sess:+.1f} ATR"
        )


def print_clusters(rows, min_fires: int = 3):
    print("\n" + "=" * 72)
    print(f"15-MIN CLUSTERS (n>={min_fires} — correlated burst proof)")
    print("=" * 72)
    by_cluster: dict[tuple[str, str], list] = {}
    for r in rows:
        if r[3] is None:
            continue
        cluster_key = r[2][:15]  # YYYY-MM-DDTHH:MM
        by_cluster.setdefault((r[0], cluster_key), []).append(r)

    for (strat, cluster), trades in sorted(by_cluster.items()):
        if len(trades) < min_fires:
            continue
        pnls = [float(t[3]) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        print(
            f"  {strat:28s} {cluster}  n={len(trades)}  "
            f"pnl={sum(pnls):+.1f}  win/lose={wins}/{len(trades)-wins}  "
            f"syms={','.join(t[1].replace('USDT','') for t in trades[:5])}"
            f"{'...' if len(trades)>5 else ''}"
        )


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB
    if len(sys.argv) > 2:
        strategies = tuple(sys.argv[2:])
    else:
        strategies = DEFAULT_STRATEGIES

    conn = sqlite3.connect(str(db_path))
    rows = load(conn, strategies)
    if not rows:
        print(f"No closed trades for {strategies} in {db_path}")
        return

    print(f"DB: {db_path}")
    print(f"Strategies: {', '.join(strategies)}")
    print(f"Closed trades: {len(rows)}")
    print_strategy_summary(rows)
    print_session_days(rows)
    print_clusters(rows)


if __name__ == "__main__":
    main()
