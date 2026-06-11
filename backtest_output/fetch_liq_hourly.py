"""Fetch coinalyze HOURLY liquidation history for the proven-28 universe.

Populates backtest_data/coinalyze_liq.db table liq_hourly(symbol, ts, long_liq, short_liq).
Used by LIQ_INTRADAY=1 backtests (rolling-24h cascade context, no same-day lookahead).

Usage:
  python backtest_output/fetch_liq_hourly.py
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

FETCH_START = int(datetime(2025, 12, 25, tzinfo=timezone.utc).timestamp())
FETCH_END = int(datetime(2026, 5, 22, tzinfo=timezone.utc).timestamp())
CHUNK_S = 30 * 86400


def main() -> None:
    api_key = yaml.safe_load(open(CFG_PATH))["coinalyze"]["api_key"]
    conn = sqlite3.connect(LIQ_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS liq_hourly (
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
            data = None
            for attempt in range(6):
                try:
                    resp = requests.get(
                        "https://api.coinalyze.net/v1/liquidation-history",
                        params={
                            "symbols": f"{sym}_PERP.A",
                            "interval": "1hour",
                            "from": fr,
                            "to": to,
                            "api_key": api_key,
                        },
                        timeout=30,
                    )
                    if resp.status_code == 429:
                        wait = (attempt + 1) * 20
                        print(f"  429 rate limit, waiting {wait}s", flush=True)
                        time.sleep(wait)
                        continue
                    if resp.status_code != 200:
                        print(f"  HTTP {resp.status_code} {sym} chunk {fr}", flush=True)
                        time.sleep(5)
                        continue
                    data = resp.json()
                    break
                except Exception as e:
                    print(f"  fetch error {sym}: {e}", flush=True)
                    time.sleep(5)

            if isinstance(data, list) and data:
                hist = data[0].get("history", [])
                conn.executemany(
                    "INSERT OR REPLACE INTO liq_hourly(symbol, ts, long_liq, short_liq) "
                    "VALUES (?, ?, ?, ?)",
                    [(sym, int(h["t"]), float(h.get("l", 0)), float(h.get("s", 0)))
                     for h in hist],
                )
                conn.commit()
                n_rows += len(hist)

            fr = to
            time.sleep(1.8)

        lo, hi = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM liq_hourly WHERE symbol=?", (sym,)
        ).fetchone()
        rng = ""
        if lo:
            rng = (f"{datetime.fromtimestamp(lo, tz=timezone.utc):%Y-%m-%d} → "
                   f"{datetime.fromtimestamp(hi, tz=timezone.utc):%Y-%m-%d}")
        print(f"[{i+1}/{len(V5_SYMBOLS)}] {sym}: +{n_rows} rows | {rng}", flush=True)

    # Sanity: hourly-summed dailies vs daily table for one liquid symbol
    sym = "ETHUSDT"
    hr = conn.execute(
        "SELECT ts/86400*86400 AS day, SUM(long_liq+short_liq) FROM liq_hourly "
        "WHERE symbol=? GROUP BY day ORDER BY day LIMIT 200", (sym,)
    ).fetchall()
    daily = {r[0]: r[1] for r in conn.execute(
        "SELECT timestamp, long_liq+short_liq FROM liquidation_history WHERE symbol=?",
        (sym,))}
    diffs = []
    for day_ts, h_total in hr:
        if day_ts in daily and daily[day_ts] > 0:
            diffs.append(abs(h_total - daily[day_ts]) / daily[day_ts])
    if diffs:
        print(f"\nsanity {sym}: {len(diffs)} overlapping days, "
              f"median rel diff hourly-sum vs daily = {sorted(diffs)[len(diffs)//2]:.1%}",
              flush=True)
    conn.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
