"""
Fetch daily close prices from Binance for ret_5d calculation.
"""
import requests
import time
import datetime
import sqlite3
from pathlib import Path

DB_PATH = Path("/root/bitana/backtest_data/coinalyze_liq.db")

ALL_SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT",
    "NMRUSDT", "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT",
    "FETUSDT", "DOTUSDT", "TONUSDT", "SOLUSDT", "1000LUNCUSDT",
    "ENAUSDT", "1000PEPEUSDT", "XRPUSDT", "FILUSDT", "BNBUSDT",
    "TAOUSDT", "CHZUSDT", "DASHUSDT", "QNTUSDT", "ICPUSDT",
    "XLMUSDT", "APTUSDT", "ETHUSDT",
]

JAN1_MS = 1767225600000
APR30_MS = 1777593599000
URL = "https://fapi.binance.com/fapi/v1/klines"


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_closes (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL,
            PRIMARY KEY (symbol, date)
        ) WITHOUT ROWID
    """)
    conn.commit()

    for i, sym in enumerate(ALL_SYMBOLS, 1):
        existing = conn.execute(
            "SELECT COUNT(*) FROM daily_closes WHERE symbol=?", (sym,)
        ).fetchone()[0]
        if existing > 50:
            print(f"  {sym}: already have {existing} rows, skipping")
            continue

        try:
            params = {"symbol": sym, "interval": "1d", "startTime": JAN1_MS - 10*86400000, "limit": 200}
            r = requests.get(URL, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  {sym}: HTTP {r.status_code}")
                continue
            data = r.json()
            rows = []
            for k in data:
                dt = datetime.datetime.fromtimestamp(k[0] / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
                rows.append((sym, dt, float(k[4])))
            conn.executemany("INSERT OR REPLACE INTO daily_closes VALUES (?,?,?)", rows)
            conn.commit()
            print(f"  {sym}: {len(rows)} daily closes")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
        time.sleep(0.1)

    total = conn.execute("SELECT COUNT(*) FROM daily_closes").fetchone()[0]
    print(f"\nTotal daily closes: {total:,}")
    conn.close()


if __name__ == "__main__":
    main()
