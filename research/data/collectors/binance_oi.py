"""
Binance futures OI snapshot collector (30-day history).

Uses /futures/data/openInterestHist for supplementary high-res recent OI.
This complements Coinalyze's multi-year daily data with 5m/15m/1h granularity
for the recent 30-day window.
"""
import time
import requests
import pandas as pd
from loguru import logger

from research.config.settings import (
    BINANCE_FUTURES_BASE,
    BINANCE_OI_HIST_ENDPOINT,
    OI_DIR,
    ALL_SYMBOLS,
)
from research.data.storage.parquet_store import save_parquet, get_last_timestamp


def _fetch_oi_hist(
    symbol: str,
    period: str = "1h",
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch OI history from Binance."""
    # This endpoint uses a different base
    url = f"https://fapi.binance.com{BINANCE_OI_HIST_ENDPOINT}"
    params = {
        "symbol": symbol,
        "period": period,
        "limit": limit,
    }
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time

    for attempt in range(5):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(60)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"OI hist request error: {e}, retry in {wait}s")
            time.sleep(wait)
    return []


def collect_binance_oi(
    symbol: str,
    period: str = "5m",
) -> pd.DataFrame:
    """Collect recent 30-day OI from Binance at specified granularity."""
    out_path = OI_DIR / f"{symbol}_oi_binance_{period}.parquet"

    logger.info(f"Fetching Binance OI for {symbol} ({period})")

    all_data = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (30 * 24 * 3600 * 1000)  # 30 days back

    cursor = start_ms
    while cursor < end_ms:
        raw = _fetch_oi_hist(symbol, period=period, start_time=cursor, end_time=end_ms)
        if not raw:
            break

        df = pd.DataFrame(raw)
        if df.empty:
            break

        df = df.rename(columns={
            "timestamp": "timestamp",
            "sumOpenInterest": "open_interest",
            "sumOpenInterestValue": "open_interest_value",
        })

        df["timestamp"] = pd.to_numeric(df["timestamp"]).astype(int)
        df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce")
        df["open_interest_value"] = pd.to_numeric(df["open_interest_value"], errors="coerce")

        df = df[["timestamp", "open_interest", "open_interest_value"]]
        all_data.append(df)

        last_ts = int(df["timestamp"].max())
        if last_ts <= cursor:
            break
        cursor = last_ts + 1

        time.sleep(0.5)

    if not all_data:
        logger.warning(f"No Binance OI data for {symbol}")
        return pd.DataFrame()

    combined = pd.concat(all_data, ignore_index=True)
    rows = save_parquet(combined, out_path, schema_name="oi")
    logger.info(f"✓ {symbol} Binance OI ({period}): {rows} rows")
    return combined


def collect_all_binance_oi(symbols: list[str] | None = None):
    """Collect Binance OI for all symbols at 5m and 1h."""
    symbols = symbols or ALL_SYMBOLS
    for symbol in symbols:
        for period in ["5m", "1h"]:
            try:
                collect_binance_oi(symbol, period=period)
            except Exception as e:
                logger.error(f"Failed Binance OI {symbol} {period}: {e}")
            time.sleep(0.5)


if __name__ == "__main__":
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    collect_all_binance_oi()
