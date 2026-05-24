"""
Fetch May 2026 data (klines + Coinalyze liq) to extend backtest to today.
"""
import sqlite3
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SYMBOLS = [
    "NEARUSDT", "ZECUSDT", "ADAUSDT", "WLDUSDT", "UNIUSDT", "NMRUSDT",
    "PENDLEUSDT", "ARBUSDT", "RENDERUSDT", "RUNEUSDT", "FETUSDT", "DOTUSDT",
    "TONUSDT", "SOLUSDT", "1000LUNCUSDT", "ENAUSDT", "1000PEPEUSDT",
    "XRPUSDT", "FILUSDT", "BNBUSDT", "TAOUSDT", "CHZUSDT", "DASHUSDT",
    "QNTUSDT", "ICPUSDT", "XLMUSDT", "APTUSDT", "ETHUSDT",
]

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
LIQ_DB = Path("/root/bitana/backtest_data/coinalyze_liq.db")
CA_API_KEY = "be291954-992e-489d-8ab5-5d34a0dfcf41"

# Date range: May 1 to May 20 (today)
START_MS = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 5, 20, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)

BINANCE_FAPI = "https://fapi.binance.com"


def fetch_klines(symbol, start_ms, end_ms):
    """Fetch 5m klines from Binance."""
    all_klines = []
    current = start_ms
    while current < end_ms:
        resp = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": "5m",
                "startTime": current,
                "limit": 1500,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"  ERROR {symbol}: {resp.status_code}")
            break
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current = data[-1][6] + 1  # close_time + 1ms
        if len(data) < 1500:
            break
        time.sleep(0.1)
    return all_klines


def main():
    print(f"Fetching May 2026 data: {datetime.fromtimestamp(START_MS/1000, tz=timezone.utc)} to {datetime.fromtimestamp(END_MS/1000, tz=timezone.utc)}")

    # ── 1. Fetch klines ──
    print("\n=== Fetching 5m klines ===")
    conn_klines = sqlite3.connect(str(KLINES_DB))
    total_klines = 0
    for i, sym in enumerate(SYMBOLS):
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        klines = fetch_klines(sym, START_MS, END_MS)
        if klines:
            for k in klines:
                conn_klines.execute(
                    "INSERT OR IGNORE INTO klines (symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sym, k[0], k[6], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[9]) if len(k) > 9 else 0.0),
                )
            conn_klines.commit()
            print(f"{len(klines)} candles")
            total_klines += len(klines)
        else:
            print("0 candles")
        time.sleep(0.05)
    conn_klines.close()
    print(f"Total klines inserted: {total_klines}")

    # ── 2. Fetch Coinalyze liq data ──
    print("\n=== Fetching Coinalyze liq data ===")
    conn_liq = sqlite3.connect(str(LIQ_DB))
    now = int(time.time())
    fr = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
    total_liq = 0
    for i, sym in enumerate(SYMBOLS):
        ca_sym = f"{sym}_PERP.A"
        print(f"  [{i+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        try:
            resp = requests.get(
                "https://api.coinalyze.net/v1/liquidation-history",
                params={
                    "symbols": ca_sym,
                    "interval": "daily",
                    "from": fr,
                    "to": now,
                    "api_key": CA_API_KEY,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                print(f"ERROR {resp.status_code}")
                time.sleep(2)
                continue
            data = resp.json()
            if not isinstance(data, list) or not data:
                print("no data")
                time.sleep(1.5)
                continue
            history = data[0].get("history", [])
            if not history:
                print("no history")
                time.sleep(1.5)
                continue
            for h in history:
                ts = h["t"]
                conn_liq.execute(
                    "INSERT OR IGNORE INTO liquidation_history (timestamp, symbol, long_liq, short_liq) VALUES (?,?,?,?)",
                    (ts, sym, h.get("l", 0), h.get("s", 0)),
                )
            conn_liq.commit()
            print(f"{len(history)} rows")
            total_liq += len(history)
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(1.5)
            continue
        time.sleep(1.5)
    conn_liq.close()
    print(f"Total liq rows inserted: {total_liq}")

    # ── 3. Verify ──
    print("\n=== Verification ===")
    conn_klines = sqlite3.connect(str(KLINES_DB))
    cur = conn_klines.cursor()
    cur.execute("SELECT MIN(open_time), MAX(open_time), COUNT(*) FROM klines")
    row = cur.fetchone()
    print(f"Klines: {datetime.fromtimestamp(row[0]/1000, tz=timezone.utc)} to {datetime.fromtimestamp(row[1]/1000, tz=timezone.utc)}, {row[2]} total")
    conn_klines.close()

    conn_liq = sqlite3.connect(str(LIQ_DB))
    cur = conn_liq.cursor()
    cur.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM liquidation_history")
    row = cur.fetchone()
    print(f"Liq: {datetime.fromtimestamp(row[0], tz=timezone.utc)} to {datetime.fromtimestamp(row[1], tz=timezone.utc)}, {row[2]} total")
    conn_liq.close()

    print("\nDone!")


if __name__ == "__main__":
    main()
