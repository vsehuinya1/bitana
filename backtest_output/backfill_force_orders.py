"""Backfill Binance force orders into force_orders.db for WS-aligned backtests.

NOTE: /fapi/v1/allForceOrders REST is out of maintenance (400). Prefer:
  - LIQ_SOURCE=ws_cache / ws_merged in v6_path_backtest (live liq_cache export)
  - ongoing WS logging via tools/v5_forward_test.py

This script remains for if/when a working historical endpoint exists.
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
CFG_PATH = REPO / "config" / "v5_forward_test.yaml"
OUT_DB = REPO / "backtest_data" / "force_orders.db"
BASE = "https://fapi.binance.com"
CHUNK_MS = 7 * 24 * 3600 * 1000
SLEEP_S = 0.35  # ~3 req/s → safe under futures weight limits


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS force_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_ms INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL NOT NULL,
            volume_usd REAL NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_foe_symbol_time
            ON force_order_events(symbol, event_time_ms);
        CREATE TABLE IF NOT EXISTS backfill_state (
            symbol TEXT NOT NULL,
            chunk_end_ms INTEGER NOT NULL,
            PRIMARY KEY (symbol, chunk_end_ms)
        );
    """)
    conn.commit()
    return conn


def symbols_from_config() -> list[str]:
    cfg = yaml.safe_load(open(CFG_PATH))
    return (
        cfg["symbols"]["tier_a"]
        + cfg["symbols"]["tier_b"]
        + cfg["symbols"].get("tier_c_experimental", [])
    )


def fetch_chunk(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = requests.get(
            f"{BASE}/fapi/v1/allForceOrders",
            params={"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000},
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
        if len(batch) < 1000:
            break
        cursor = int(batch[-1]["time"]) + 1
        time.sleep(SLEEP_S)
    return out


def insert_events(conn: sqlite3.Connection, orders: list[dict]) -> int:
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for o in orders:
        try:
            qty = float(o.get("executedQty") or o.get("origQty") or 0)
            price = float(o.get("avgPrice") or o.get("price") or 0)
            vol = qty * price
            if vol <= 0:
                continue
            rows.append((
                int(o["time"]),
                o["symbol"],
                o.get("side", ""),
                qty,
                price,
                vol,
                now,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    conn.executemany(
        """INSERT INTO force_order_events
           (event_time_ms, symbol, side, qty, price, volume_usd, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def already_done(conn: sqlite3.Connection, symbol: str, chunk_end_ms: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM backfill_state WHERE symbol=? AND chunk_end_ms=?",
        (symbol, chunk_end_ms),
    ).fetchone()
    return row is not None


def mark_done(conn: sqlite3.Connection, symbol: str, chunk_end_ms: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO backfill_state(symbol, chunk_end_ms) VALUES(?,?)",
        (symbol, chunk_end_ms),
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-05-22")
    ap.add_argument("--db", default=str(OUT_DB))
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    syms = symbols_from_config()
    conn = init_db(Path(args.db))

    total = 0
    for si, sym in enumerate(syms):
        sym_n = 0
        chunk_start = start_ms
        while chunk_start < end_ms:
            chunk_end = min(chunk_start + CHUNK_MS, end_ms)
            if already_done(conn, sym, chunk_end):
                chunk_start = chunk_end
                continue
            try:
                orders = fetch_chunk(sym, chunk_start, chunk_end)
                n = insert_events(conn, orders)
                mark_done(conn, sym, chunk_end)
                sym_n += n
                total += n
                print(f"[{si+1}/{len(syms)}] {sym} {chunk_start}→{chunk_end}: {n} events", flush=True)
            except Exception as e:
                print(f"ERROR {sym} {chunk_start}→{chunk_end}: {e}", flush=True)
                time.sleep(5)
                continue
            chunk_start = chunk_end
            time.sleep(SLEEP_S)
        print(f"  {sym} done: {sym_n} events", flush=True)

    n_rows = conn.execute("SELECT COUNT(*) FROM force_order_events").fetchone()[0]
    print(f"\nTotal inserted this run: {total} | DB rows: {n_rows}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
