"""
Download daily liquidation history from Coinalyze for 5-19M volume symbols.
Stores in the same coinalyze_liq.db format as existing data.
"""
import sqlite3
import urllib.request
import json
import time
from datetime import datetime, timezone
from pathlib import Path

LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")
KEY = "be291954-992e-489d-8ab5-5d34a0dfcf41"
BASE_URL = "https://api.coinalyze.net/v1"

# Symbols available on Coinalyze (5-19M volume tier)
SYMBOLS = [
    "1000BONKUSDT", "RAVEUSDT", "MEGAUSDT", "EDGEUSDT", "BERAUSDT",
    "IRYSUSDT", "CFGUSDT", "KITEUSDT", "AIAUSDT", "WIFUSDT", "AVNTUSDT", "HBARUSDT",
    "MITOUSDT", "MUSDT", "NEIROUSDT", "TRUTHUSDT", "BASEDUSDT", "ONTUSDT",
    "FOGOUSDT", "OPENUSDT", "FFUSDT", "CRVUSDT", "SAGAUSDT", "ENJUSDT", "KAITOUSDT",
    "SEIUSDT", "MONUSDT", "APRUSDT", "GIGGLEUSDT", "BLUAIUSDT", "SAPIENUSDT", "LDOUSDT",
    "SIRENUSDT", "QUSDT", "STORJUSDT", "DYDXUSDT", "PYTHUSDT", "OPGUSDT",
    "TAGUSDT", "XVGUSDT", "DEXEUSDT", "ZKUSDT",
]

# Date range: Jan 1 to May 20, 2026
FROM_TS = int(datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp())  # Start a bit earlier for warmup
TO_TS = int(datetime(2026, 5, 21, tzinfo=timezone.utc).timestamp())


def fetch_liq_history(symbol):
    """Fetch daily liquidation history for a symbol from Coinalyze."""
    csym = f"{symbol}_PERP.A"
    all_records = []
    fr = FROM_TS

    while fr < TO_TS:
        to = min(fr + 86400 * 30, TO_TS)  # 30-day chunks
        url = f"{BASE_URL}/liquidation-history?symbols={csym}&interval=daily&from={fr}&to={to}&limit=2000&api_key={KEY}"

        for attempt in range(3):
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())

                if data and len(data) > 0 and "history" in data[0]:
                    for rec in data[0]["history"]:
                        all_records.append((
                            rec["t"],  # timestamp (seconds)
                            symbol,
                            rec.get("l", 0),  # long liq
                            rec.get("s", 0),  # short liq
                        ))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", 60))
                    print(f"    Rate limited, waiting {retry_after:.0f}s...")
                    time.sleep(retry_after)
                else:
                    print(f"    HTTP {e.code}: {e.read().decode()[:200]}")
                    break
            except Exception as e:
                print(f"    Error: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                break

        fr = to
        time.sleep(2.0)  # Rate limit: 40/min max

    return all_records


def main():
    conn = sqlite3.connect(str(LIQ_DB))
    cur = conn.cursor()

    # Ensure tables exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS liquidation_history (
            timestamp INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            long_liq REAL,
            short_liq REAL,
            PRIMARY KEY (timestamp, symbol)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_closes (
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close REAL,
            PRIMARY KEY (date, symbol)
        )
    """)
    conn.commit()

    # Check existing
    cur.execute("SELECT DISTINCT symbol FROM liquidation_history")
    existing = {r[0] for r in cur.fetchall()}
    to_download = [s for s in SYMBOLS if s not in existing]

    print(f"Downloading liq history for {len(to_download)} symbols ({len(SYMBOLS) - len(to_download)} already in DB)")
    print(f"Period: {datetime.fromtimestamp(FROM_TS, tz=timezone.utc)} to {datetime.fromtimestamp(TO_TS, tz=timezone.utc)}")
    print()

    total_records = 0
    for i, sym in enumerate(to_download, 1):
        t0 = time.time()
        records = fetch_liq_history(sym)

        if records:
            cur.executemany(
                "INSERT OR IGNORE INTO liquidation_history (timestamp, symbol, long_liq, short_liq) VALUES (?,?,?,?)",
                records
            )
            conn.commit()
            total_records += len(records)

        elapsed = time.time() - t0
        print(f"  [{i}/{len(to_download)}] {sym}: {len(records)} records ({elapsed:.0f}s)", flush=True)

        # Also fetch daily closes from Coinalyze OI endpoint (uses 'c' = close OI, not price)
        # We need price closes — skip for now, will use klines to derive daily closes
        # The backtest's load_daily_closes reads from daily_closes table in liq DB

        time.sleep(2.0)

    print(f"\n{'='*60}")
    print(f"DONE: {total_records} total liq records")
    print(f"{'='*60}")

    conn.close()


def populate_daily_closes_from_klines():
    """Populate daily_closes table from klines data for symbols that need it."""
    klines_db = Path("/root/bitana/backtest_data/klines_5m.db")
    if not klines_db.exists():
        print("klines DB not found, skipping daily closes population")
        return

    conn_liq = sqlite3.connect(str(LIQ_DB))
    conn_klines = sqlite3.connect(str(klines_db))

    cur_liq = conn_liq.cursor()
    cur_klines = conn_klines.cursor()

    # Get symbols we just downloaded liq data for
    cur_liq.execute("SELECT DISTINCT symbol FROM liquidation_history")
    liq_symbols = {r[0] for r in cur_liq.fetchall()}

    # Check which need daily closes
    cur_liq.execute("SELECT DISTINCT symbol FROM daily_closes")
    have_closes = {r[0] for r in cur_liq.fetchall()}

    need_closes = liq_symbols - have_closes
    print(f"\nPopulating daily closes for {len(need_closes)} symbols from klines...")

    from datetime import datetime, timezone
    for sym in sorted(need_closes):
        # Get the last 5m candle of each day
        cur_klines.execute("""
            SELECT DATE(open_time/1000, 'unixepoch') as d, close
            FROM klines
            WHERE symbol = ?
            AND open_time = (
                SELECT MAX(open_time) FROM klines k2
                WHERE k2.symbol = klines.symbol
                AND DATE(k2.open_time/1000, 'unixepoch') = DATE(klines.open_time/1000, 'unixepoch')
            )
            ORDER BY d
        """, (sym,))
        rows = cur_klines.fetchall()
        if rows:
            cur_liq.executemany(
                "INSERT OR IGNORE INTO daily_closes (date, symbol, close) VALUES (?,?,?)",
                [(r[0], sym, r[1]) for r in rows]
            )
            conn_liq.commit()
            print(f"  {sym}: {len(rows)} daily closes")

    conn_liq.close()
    conn_klines.close()


if __name__ == "__main__":
    main()
    populate_daily_closes_from_klines()
