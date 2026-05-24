"""
Step 1: Fetch all 5m klines from Binance and save to local SQLite DB.
"""
import sqlite3
import time
import requests
import datetime
from pathlib import Path

DATA_DIR = Path("/root/bitana/backtest_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
KLINES_DB = DATA_DIR / "klines_5m.db"

ALL_SYMBOLS = [
    "BTCUSDT", "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT", "TONUSDT", "SOLUSDT", "1000LUNCUSDT",
    "ENAUSDT", "1000PEPEUSDT", "XRPUSDT", "FILUSDT", "BNBUSDT",
    "TAOUSDT", "CHZUSDT", "DASHUSDT", "QNTUSDT", "ICPUSDT",
    "XLMUSDT", "APTUSDT", "ETHUSDT",
]

JAN1_MS = 1767225600000
APR30_MS = 1777593599000
URL = "https://fapi.binance.com/fapi/v1/klines"


def init_db():
    conn = sqlite3.connect(str(KLINES_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            close_time INTEGER NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL,
            taker_buy_volume REAL,
            PRIMARY KEY (symbol, open_time)
        ) WITHOUT ROWID
    """)
    conn.commit()
    return conn


def fetch_symbol(sym, conn):
    """Fetch all 5m klines for a symbol with pagination."""
    # Check existing
    existing = conn.execute(
        "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM klines WHERE symbol=?",
        (sym,)
    ).fetchone()
    if existing[0] > 30000:
        print(f"  {sym}: already have {existing[0]} candles ({datetime.datetime.fromtimestamp(existing[1]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')} → {datetime.datetime.fromtimestamp(existing[2]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')}), skipping")
        return existing[0]

    all_klines = []
    current_start = JAN1_MS
    retries = 0

    while current_start < APR30_MS:
        try:
            params = {"symbol": sym, "interval": "5m", "startTime": current_start, "limit": 1000}
            r = requests.get(URL, params=params, timeout=20)
            if r.status_code == 429:
                print(f"  RATE LIMITED, waiting 10s...")
                time.sleep(10)
                continue
            if r.status_code != 200:
                print(f"  ERROR {sym}: HTTP {r.status_code}")
                retries += 1
                if retries > 3:
                    break
                time.sleep(2)
                continue

            data = r.json()
            if not data:
                break

            all_klines.extend(data)
            current_start = data[-1][6] + 1
            retries = 0

            if len(data) < 1000:
                break
            time.sleep(0.05)
        except Exception as e:
            print(f"  ERROR {sym}: {e}")
            retries += 1
            if retries > 3:
                break
            time.sleep(2)

    if all_klines:
        rows = [
            (sym, k[0], k[6], float(k[1]), float(k[2]), float(k[3]), float(k[4]),
             float(k[5]), float(k[9]) if len(k) > 9 else 0.0)
            for k in all_klines
        ]
        conn.executemany("INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        print(f"  {sym}: saved {len(rows)} candles ({datetime.datetime.fromtimestamp(all_klines[0][0]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')} → {datetime.datetime.fromtimestamp(all_klines[-1][0]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')})")
        return len(rows)
    else:
        print(f"  {sym}: NO DATA")
        return 0


def main():
    print("Fetching 5m klines for all symbols...")
    print(f"Period: 2026-01-01 to 2026-04-30")
    print(f"Symbols: {len(ALL_SYMBOLS)}")
    print()

    conn = init_db()
    total_candles = 0
    start_time = time.time()

    for i, sym in enumerate(ALL_SYMBOLS, 1):
        n = fetch_symbol(sym, conn)
        total_candles += n
        elapsed = time.time() - start_time
        print(f"  [{i}/{len(ALL_SYMBOLS)}] elapsed={elapsed:.0f}s total_candles={total_candles:,}")

    print()
    # Final summary
    print("=" * 60)
    print("DATA FETCH COMPLETE")
    print("=" * 60)
    for sym in ALL_SYMBOLS:
        row = conn.execute(
            "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM klines WHERE symbol=?",
            (sym,)
        ).fetchone()
        if row[0] > 0:
            first = datetime.datetime.fromtimestamp(row[1]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            last = datetime.datetime.fromtimestamp(row[2]/1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            print(f"  {sym}: {row[0]:,} candles ({first} → {last})")
        else:
            print(f"  {sym}: NO DATA")

    total = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    print(f"\nTotal: {total:,} candles")
    conn.close()


if __name__ == "__main__":
    main()
