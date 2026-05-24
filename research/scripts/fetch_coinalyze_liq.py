"""
Fetch real Coinalyze liquidation data for all approved symbols.
Uses the liquidation-history endpoint with daily interval.
"""
import requests
import time
import datetime
import sqlite3
from pathlib import Path

API_KEY = "be291954-992e-489d-8ab5-5d34a0dfcf41"
URL = "https://api.coinalyze.net/v1/liquidation-history"
DB_PATH = Path("/root/bitana/backtest_data/coinalyze_liq.db")

ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT", "TONUSDT", "SOLUSDT", "1000LUNCUSDT",
    "ENAUSDT", "1000PEPEUSDT", "XRPUSDT", "FILUSDT", "BNBUSDT",
    "TAOUSDT", "CHZUSDT", "DASHUSDT", "QNTUSDT", "ICPUSDT",
    "XLMUSDT", "APTUSDT", "ETHUSDT",
]

# Coinalyze uses _PERP.A suffix
def to_coinalyze_symbol(sym):
    return f"{sym}_PERP.A"

# Date range: Jan 1 2026 to Apr 30 2026
FROM_TS = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp())
TO_TS = int(datetime.datetime(2026, 4, 30, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp())


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS liquidation_history (
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            long_liq REAL,
            short_liq REAL,
            PRIMARY KEY (symbol, timestamp)
        ) WITHOUT ROWID
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_liquidation (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            long_liq REAL,
            short_liq REAL,
            total_liq REAL,
            close REAL,
            PRIMARY KEY (symbol, date)
        ) WITHOUT ROWID
    """)
    conn.commit()
    return conn


def fetch_symbol(conn, sym):
    ca_sym = to_coinalyze_symbol(sym)
    
    # Check if we already have data
    existing = conn.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM liquidation_history WHERE symbol=?",
        (ca_sym,)
    ).fetchone()
    if existing[0] > 50:
        first = datetime.datetime.fromtimestamp(existing[1], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        last = datetime.datetime.fromtimestamp(existing[2], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        print(f"  {sym}: already have {existing[0]} rows ({first} → {last}), skipping")
        return existing[0]

    try:
        resp = requests.get(URL, params={
            "symbols": ca_sym,
            "interval": "daily",
            "from": FROM_TS,
            "to": TO_TS,
            "api_key": API_KEY,
        }, timeout=20)
        
        if resp.status_code != 200:
            print(f"  {sym}: HTTP {resp.status_code}")
            return 0
        
        data = resp.json()
        if not isinstance(data, list) or not data:
            print(f"  {sym}: no data")
            return 0
        
        history = data[0].get("history", [])
        if not history:
            print(f"  {sym}: empty history")
            return 0
        
        rows = []
        for h in history:
            rows.append((
                ca_sym,
                h["t"],
                h.get("l", 0),
                h.get("s", 0),
            ))
        
        conn.executemany("INSERT OR REPLACE INTO liquidation_history VALUES (?,?,?,?)", rows)
        conn.commit()
        
        first = datetime.datetime.fromtimestamp(rows[0][1], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        last = datetime.datetime.fromtimestamp(rows[-1][1], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        print(f"  {sym}: {len(rows)} rows ({first} → {last})")
        return len(rows)
        
    except Exception as e:
        print(f"  {sym}: ERROR {e}")
        return 0


def main():
    print("Fetching Coinalyze liquidation data for all symbols...")
    print(f"Period: 2026-01-01 to 2026-04-30")
    print(f"Symbols: {len(ALL_SYMBOLS)}")
    print()

    conn = init_db()
    total = 0
    start = time.time()

    for i, sym in enumerate(ALL_SYMBOLS, 1):
        n = fetch_symbol(conn, sym)
        total += n
        elapsed = time.time() - start
        print(f"    [{i}/{len(ALL_SYMBOLS)}] elapsed={elapsed:.0f}s total_rows={total:,}")
        time.sleep(1.5)  # rate limit

    print()
    print("=" * 60)
    print("FETCH COMPLETE")
    print("=" * 60)
    
    # Summary
    for sym in ALL_SYMBOLS:
        ca_sym = to_coinalyze_symbol(sym)
        row = conn.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM liquidation_history WHERE symbol=?",
            (ca_sym,)
        ).fetchone()
        if row[0] > 0:
            first = datetime.datetime.fromtimestamp(row[1], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            last = datetime.datetime.fromtimestamp(row[2], tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            print(f"  {sym}: {row[0]} rows ({first} → {last})")
        else:
            print(f"  {sym}: NO DATA")

    grand_total = conn.execute("SELECT COUNT(*) FROM liquidation_history").fetchone()[0]
    print(f"\nTotal: {grand_total:,} rows")
    conn.close()


if __name__ == "__main__":
    main()
