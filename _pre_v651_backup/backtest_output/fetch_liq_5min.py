"""Fetch coinalyze 5-MINUTE liquidation history for the proven-28 universe.

Populates backtest_data/coinalyze_liq.db table liq_5min(symbol, ts, long_liq, short_liq).
Probes history depth first (ETHUSDT January) and aborts if 5min data doesn't
reach the backtest window.

Usage:
  python backtest_output/fetch_liq_5min.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtest_output.v65_revert_config import V5_SYMBOLS  # noqa: E402

LIQ_DB = REPO / "backtest_data" / "coinalyze_liq.db"
CFG_PATH = REPO / "config" / "v5_forward_test.yaml"

FETCH_START = int(datetime(2025, 12, 30, tzinfo=timezone.utc).timestamp())
FETCH_END = int(datetime(2026, 5, 22, tzinfo=timezone.utc).timestamp())
CHUNK_S = 10 * 86400  # 2880 5min buckets per request


def get(api_key: str, sym: str, fr: int, to: int):
    for attempt in range(6):
        try:
            resp = requests.get(
                "https://api.coinalyze.net/v1/liquidation-history",
                params={"symbols": f"{sym}_PERP.A", "interval": "5min",
                        "from": fr, "to": to, "api_key": api_key},
                timeout=30,
            )
            if resp.status_code == 429:
                wait = (attempt + 1) * 20
                print(f"  429, waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code} {sym}", flush=True)
                time.sleep(5)
                continue
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("history", [])
            return []
        except Exception as e:
            print(f"  fetch error {sym}: {e}", flush=True)
            time.sleep(5)
    return []


def main() -> None:
    api_key = yaml.safe_load(open(CFG_PATH))["coinalyze"]["api_key"]

    # Probe: does 5min history reach January?
    probe = get(api_key, "ETHUSDT", FETCH_START, FETCH_START + 5 * 86400)
    print(f"probe ETHUSDT {datetime.fromtimestamp(FETCH_START, tz=timezone.utc):%Y-%m-%d}: "
          f"{len(probe)} buckets", flush=True)
    if len(probe) < 100:
        print("ABORT: 5min history does not reach backtest window", flush=True)
        sys.exit(2)

    conn = sqlite3.connect(LIQ_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS liq_5min (
            symbol TEXT NOT NULL, ts INTEGER NOT NULL,
            long_liq REAL NOT NULL, short_liq REAL NOT NULL,
            PRIMARY KEY (symbol, ts))"""
    )
    conn.commit()

    for i, sym in enumerate(V5_SYMBOLS):
        n_rows = 0
        fr = FETCH_START
        while fr < FETCH_END:
            to = min(fr + CHUNK_S, FETCH_END)
            hist = get(api_key, sym, fr, to)
            if hist:
                conn.executemany(
                    "INSERT OR REPLACE INTO liq_5min(symbol, ts, long_liq, short_liq) "
                    "VALUES (?, ?, ?, ?)",
                    [(sym, int(h["t"]), float(h.get("l", 0)), float(h.get("s", 0)))
                     for h in hist],
                )
                conn.commit()
                n_rows += len(hist)
            fr = to
            time.sleep(1.8)

        lo, hi = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM liq_5min WHERE symbol=?", (sym,)
        ).fetchone()
        rng = ""
        if lo:
            rng = (f"{datetime.fromtimestamp(lo, tz=timezone.utc):%Y-%m-%d} → "
                   f"{datetime.fromtimestamp(hi, tz=timezone.utc):%Y-%m-%d}")
        print(f"[{i+1}/{len(V5_SYMBOLS)}] {sym}: +{n_rows} rows | {rng}", flush=True)

    conn.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
