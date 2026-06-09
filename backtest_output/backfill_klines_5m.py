"""Backfill missing symbols into backtest_data/klines_5m.db from Binance REST.

Usage:
  python backtest_output/backfill_klines_5m.py
  python backtest_output/backfill_klines_5m.py --symbols DOGEUSDT,LINKUSDT
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "backtest_data" / "klines_5m.db"
CFG = REPO / "config" / "v5_forward_test.yaml"
BASE = "https://fapi.binance.com"
INTERVAL = "5m"
LIMIT = 1500
SLEEP = 0.25


def configured_symbols() -> list[str]:
    cfg = yaml.safe_load(open(CFG))
    return (
        cfg["symbols"]["tier_a"]
        + cfg["symbols"]["tier_b"]
        + cfg["symbols"].get("tier_c_experimental", [])
    )


def db_symbols(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("select distinct symbol from klines")}


def fetch_chunk(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = requests.get(
            f"{BASE}/fapi/v1/klines",
            params={"symbol": symbol, "interval": INTERVAL, "startTime": cursor,
                    "endTime": end_ms, "limit": LIMIT},
            timeout=30,
        )
        if resp.status_code == 429:
            time.sleep(30)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < LIMIT:
            break
        cursor = int(batch[-1][0]) + 1
        time.sleep(SLEEP)
    return out


def insert_rows(conn: sqlite3.Connection, symbol: str, rows: list[list]) -> int:
    data = [
        (symbol, int(r[0]), int(r[6]), float(r[1]), float(r[2]), float(r[3]),
         float(r[4]), float(r[5]), float(r[9]))
        for r in rows
    ]
    conn.executemany(
        "insert or ignore into klines "
        "(symbol,open_time,close_time,open,high,low,close,volume,taker_buy_volume) "
        "values (?,?,?,?,?,?,?,?,?)",
        data,
    )
    conn.commit()
    return len(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-05-22")
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)

    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")
    have = db_symbols(conn)
    if args.symbols:
        missing = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        missing = sorted(set(configured_symbols()) - have)

    print(f"backfill targets: {len(missing)} symbols", flush=True)
    for i, sym in enumerate(missing):
        try:
            rows = fetch_chunk(sym, start_ms, end_ms)
            n = insert_rows(conn, sym, rows)
            print(f"[{i+1}/{len(missing)}] {sym}: {n} bars", flush=True)
        except Exception as e:
            print(f"ERROR {sym}: {e}", flush=True)
            time.sleep(2)
    conn.close()


if __name__ == "__main__":
    main()
