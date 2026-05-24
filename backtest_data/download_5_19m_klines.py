"""
Download 5m klines for new symbols (5-19M volume tier) from Binance Vision.
Jan 1 - May 20, 2026.
"""
import sqlite3
import urllib.request
import zipfile
import io
import time
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

KLINES_DB = Path("/root/bitana/backtest_data/klines_5m.db")
BASE_URL = "https://data.binance.vision/data/futures/um/daily/klines"

# 5-19M volume symbols (excluding 1000LUNCUSDT and RUNEUSDT which already exist)
SYMBOLS = [
    "1000BONKUSDT", "RAVEUSDT", "MEGAUSDT", "EDGEUSDT", "BERAUSDT",
    "IRYSUSDT", "CFGUSDT", "KITEUSDT", "AIAUSDT", "WIFUSDT", "AVNTUSDT", "HBARUSDT",
    "MITOUSDT", "MUSDT", "COINUSDT", "NEIROUSDT", "TRUTHUSDT", "BASEDUSDT", "ONTUSDT",
    "FOGOUSDT", "OPENUSDT", "FFUSDT", "CRVUSDT", "SAGAUSDT", "ENJUSDT", "KAITOUSDT",
    "SEIUSDT", "MONUSDT", "APRUSDT", "GIGGLEUSDT", "BLUAIUSDT", "SAPIENUSDT", "LDOUSDT",
    "SIRENUSDT", "SPYUSDT", "QUSDT", "STORJUSDT", "DYDXUSDT", "PYTHUSDT", "OPGUSDT",
    "AXSUSDT", "MRVLUSDT", "DRAMUSDT", "AINUSDT", "PLUMEUSDT", "ZAMAUSDT", "DUSKUSDT",
    "PIPPINUSDT", "DYMUSDT", "ORCAUSDT", "WUSDT", "MORPHOUSDT", "BOMEUSDT", "CGPTUSDT",
    "ETHFIUSDT", "HYPERUSDT", "COPPERUSDT", "BROCCOLIF3BUSDT", "AKTUSDT", "PIEVERSEUSDT",
    "TRBUSDT", "JSTUSDT", "ZROUSDT", "AEROUSDT", "POLUSDT", "LAYERUSDT", "ZBTUSDT",
    "ZKPUSDT", "IOUSDT", "RKLBUSDT", "CFXUSDT", "UAIUSDT", "ATUSDT", "APEUSDT",
    "ROBOUSDT", "LITEUSDT", "GALAUSDT", "EIGENUSDT", "KAIAUSDT", "IPUSDT", "TSTUSDT",
    "PLTRUSDT", "GWEIUSDT", "XPTUSDT", "PROMUSDT", "ARUSDT", "CAKEUSDT",
    "SANDUSDT", "1000FLOKIUSDT", "TAUSDT", "SPXUSDT", "USUSDT", "DEEPUSDT", "ENSUSDT",
    "HUMAUSDT", "GRASSUSDT", "GENIUSUSDT", "SOONUSDT", "HOODUSDT", "RAYSOLUSDT", "SPKUSDT",
    "STABLEUSDT", "MAGMAUSDT", "ASRUSDT", "SYRUPUSDT", "SUSDT", "INXUSDT", "TAGUSDT",
    "XVGUSDT", "DEXEUSDT", "ZKUSDT",
]

START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 5, 20, tzinfo=timezone.utc)

def date_range(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def download_symbol_klines(symbol, conn):
    """Download 5m klines for a symbol from Binance Vision. Returns count of new rows."""
    cur = conn.cursor()
    total_inserted = 0

    for date in date_range(START_DATE, END_DATE):
        date_str = date.strftime("%Y-%m-%d")
        url = f"{BASE_URL}/{symbol}/5m/{symbol}-5m-{date_str}.zip"
        checksum_url = url + ".CHECKSUM"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            # Parse CSV from zip
            z = zipfile.ZipFile(io.BytesIO(data))
            csv_name = z.namelist()[0]
            rows = []
            with z.open(csv_name) as f:
                for line in f:
                    parts = line.decode().strip().split(",")
                    if len(parts) < 8:
                        continue
                    try:
                        open_time = int(parts[0])
                        close_time = int(parts[6])
                        o = float(parts[1])
                        h = float(parts[2])
                        l = float(parts[3])
                        c = float(parts[4])
                        v = float(parts[5])
                        taker_buy_v = float(parts[9]) if len(parts) > 9 else 0
                        rows.append((symbol, open_time, close_time, o, h, l, c, v, taker_buy_v))
                    except (ValueError, IndexError):
                        continue

            # Insert with IGNORE to avoid duplicates
            cur.executemany(
                "INSERT OR IGNORE INTO klines (symbol, open_time, close_time, open, high, low, close, volume, taker_buy_volume) VALUES (?,?,?,?,?,?,?,?,?)",
                rows
            )
            inserted = cur.rowcount
            total_inserted += inserted
            conn.commit()

        except urllib.error.HTTPError as e:
            if e.code == 404:
                pass  # No data for this date
            else:
                print(f"    HTTP {e.code} for {symbol} {date_str}")
        except Exception as e:
            print(f"    Error for {symbol} {date_str}: {e}")

    return total_inserted


def main():
    conn = sqlite3.connect(str(KLINES_DB))
    total_start = time.time()
    grand_total = 0
    errors = []

    # Check which symbols already have data
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT symbol FROM klines")
    existing = {r[0] for r in cur.fetchall()}

    to_download = [s for s in SYMBOLS if s not in existing]
    print(f"Downloading 5m klines for {len(to_download)} symbols ({len(SYMBOLS) - len(to_download)} already in DB)")
    print(f"Period: {START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}")
    print()

    for i, sym in enumerate(to_download, 1):
        t0 = time.time()
        n = download_symbol_klines(sym, conn)
        elapsed = time.time() - t0
        grand_total += n
        print(f"  [{i}/{len(to_download)}] {sym}: {n} candles ({elapsed:.0f}s)", flush=True)
        if n == 0:
            errors.append(sym)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"DONE: {grand_total} total candles in {total_elapsed:.0f}s")
    if errors:
        print(f"Symbols with 0 candles: {errors}")
    print(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
