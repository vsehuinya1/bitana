"""Bootstrap backtest_data/ for v6/v7 path backtests on a fresh checkout.

Creates:
  - klines_5m.db schema (populate via backfill_klines_5m.py)
  - coinalyze_liq.db from live paper liq_cache + kline daily closes

Usage:
  python backtest_output/bootstrap_backtest_data.py
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
KLINES_DB = REPO / "backtest_data" / "klines_5m.db"
LIQ_DB = REPO / "backtest_data" / "coinalyze_liq.db"
WS_DB = REPO / "storage" / "v5_forward_test.db"
CFG = REPO / "config" / "v5_forward_test.yaml"
BACKFILL_DAYS = 120  # pad pre-liq-cache dates for cascade lookback


def init_klines_db() -> sqlite3.Connection:
    KLINES_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(KLINES_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            close_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            taker_buy_volume REAL NOT NULL,
            PRIMARY KEY (symbol, open_time)
        );
        CREATE INDEX IF NOT EXISTS idx_klines_sym_time ON klines(symbol, open_time);
    """)
    conn.commit()
    return conn


def init_liq_db() -> sqlite3.Connection:
    LIQ_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LIQ_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS liquidation_history (
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            long_liq REAL NOT NULL,
            short_liq REAL NOT NULL,
            PRIMARY KEY (symbol, timestamp)
        );
        CREATE TABLE IF NOT EXISTS daily_closes (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (symbol, date)
        );
    """)
    conn.commit()
    return conn


def configured_symbols() -> list[str]:
    cfg = yaml.safe_load(open(CFG))
    return (
        cfg["symbols"]["tier_a"]
        + cfg["symbols"]["tier_b"]
        + cfg["symbols"].get("tier_c_experimental", [])
    )


def export_liq_from_ws(kconn: sqlite3.Connection, lconn: sqlite3.Connection) -> int:
    rows = kconn.execute(
        "SELECT symbol, date, long_liq, short_liq FROM liq_cache ORDER BY symbol, date"
    ).fetchall()
    if not rows:
        return 0

    by_sym: dict[str, list[tuple[str, float, float]]] = {}
    for sym, d, ll, sl in rows:
        by_sym.setdefault(sym, []).append((d, float(ll), float(sl)))

    inserted = 0
    for sym, days in by_sym.items():
        days.sort(key=lambda x: x[0])
        med_total = sorted(ll + sl for _, ll, sl in days)[len(days) // 2]
        pad_ll = med_total * 0.55
        pad_sl = med_total * 0.45
        first_dt = datetime.strptime(days[0][0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        pad_start = first_dt.timestamp() - BACKFILL_DAYS * 86400
        ts = int(pad_start)
        while ts < int(first_dt.timestamp()):
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            lconn.execute(
                "INSERT OR IGNORE INTO liquidation_history VALUES (?,?,?,?)",
                (sym, ts, pad_ll, pad_sl),
            )
            inserted += 1
            ts += 86400

        for d, ll, sl in days:
            ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            lconn.execute(
                "INSERT OR REPLACE INTO liquidation_history VALUES (?,?,?,?)",
                (sym, ts, ll, sl),
            )
            inserted += 1
    lconn.commit()
    return inserted


def export_daily_closes(kconn: sqlite3.Connection, lconn: sqlite3.Connection) -> int:
    syms = configured_symbols()
    n = 0
    for sym in syms:
        rows = kconn.execute(
            "SELECT open_time, close FROM klines WHERE symbol=? ORDER BY open_time",
            (sym,),
        ).fetchall()
        if not rows:
            continue
        daily: dict[str, float] = {}
        for ot, close in rows:
            d = datetime.fromtimestamp(ot / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            daily[d] = float(close)
        for d, close in daily.items():
            lconn.execute(
                "INSERT OR REPLACE INTO daily_closes VALUES (?,?,?)",
                (sym, d, close),
            )
            n += 1
    lconn.commit()
    return n


def main() -> None:
    kconn = init_klines_db()
    kconn.close()
    print(f"klines schema ready: {KLINES_DB}", flush=True)

    if not WS_DB.exists():
        print(f"WARN: {WS_DB} missing — run backfill_klines_5m.py then re-run after paper DB exists", flush=True)
        return

    lconn = init_liq_db()
    ws = sqlite3.connect(str(WS_DB))
    n_liq = export_liq_from_ws(ws, lconn)
    ws.close()
    print(f"liquidation_history rows: {n_liq}", flush=True)

    kconn = sqlite3.connect(str(KLINES_DB))
    n_cl = export_daily_closes(kconn, lconn)
    kconn.close()
    lconn.close()
    print(f"daily_closes rows: {n_cl} (0 until klines backfilled)", flush=True)
    print("Next: python backtest_output/backfill_klines_5m.py", flush=True)


if __name__ == "__main__":
    main()
