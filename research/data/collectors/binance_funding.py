"""
Binance Futures funding rate collector.

Fetches full funding rate history from /fapi/v1/fundingRate.
Supports incremental collection.
"""
import time
import requests
import pandas as pd
from pathlib import Path
from loguru import logger

from research.config.settings import (
    BINANCE_FUTURES_BASE,
    BINANCE_FUNDING_ENDPOINT,
    BINANCE_FUNDING_LIMIT,
    FUNDING_DIR,
    ALL_SYMBOLS,
    SYMBOL_LISTING_MS,
)
from research.data.storage.parquet_store import save_parquet, get_last_timestamp


def _fetch_funding(
    symbol: str,
    start_time: int,
    end_time: int | None = None,
    limit: int = BINANCE_FUNDING_LIMIT,
) -> list[dict]:
    """Fetch raw funding rate data from Binance."""
    url = f"{BINANCE_FUTURES_BASE}{BINANCE_FUNDING_ENDPOINT}"
    params = {
        "symbol": symbol,
        "startTime": start_time,
        "limit": limit,
    }
    if end_time:
        params["endTime"] = end_time

    for attempt in range(5):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Rate limited, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Request error (attempt {attempt+1}): {e}, retrying in {wait}s")
            time.sleep(wait)

    logger.error(f"Failed to fetch funding for {symbol} after 5 attempts")
    return []


def _parse_funding(raw: list[dict], symbol: str) -> pd.DataFrame:
    """Parse funding rate response."""
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)

    # Rename and convert
    df = df.rename(columns={
        "fundingTime": "timestamp",
        "fundingRate": "funding_rate",
        "markPrice": "mark_price",
    })

    df["timestamp"] = pd.to_numeric(df["timestamp"]).astype(int)
    df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
    df["mark_price"] = pd.to_numeric(df.get("mark_price", 0), errors="coerce")
    df["symbol"] = symbol

    return df[["timestamp", "symbol", "funding_rate", "mark_price"]]


def collect_funding(
    symbol: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> pd.DataFrame:
    """Collect full funding rate history for a symbol."""
    out_path = FUNDING_DIR / f"{symbol}_funding.parquet"

    # Funding is every 8h = 28_800_000 ms
    funding_interval_ms = 28_800_000

    if start_ms is None:
        last_ts = get_last_timestamp(out_path)
        if last_ts:
            start_ms = last_ts + 1
            logger.info(f"Resuming {symbol} funding from {pd.Timestamp(start_ms, unit='ms')}")
        else:
            start_ms = SYMBOL_LISTING_MS.get(symbol, 1_568_592_000_000)

    if end_ms is None:
        end_ms = int(time.time() * 1000)

    all_data = []
    cursor = start_ms
    batch = 0

    while cursor < end_ms:
        raw = _fetch_funding(symbol, cursor, end_ms)
        if not raw:
            break

        df = _parse_funding(raw, symbol)
        if df.empty:
            break

        all_data.append(df)
        batch += 1

        last_received = int(df["timestamp"].max())
        if last_received <= cursor:
            break
        cursor = last_received + 1

        if batch % 5 == 0:
            logger.info(f"  {symbol} funding: {batch} batches, "
                        f"up to {pd.Timestamp(last_received, unit='ms')}")

        time.sleep(0.2)

    if not all_data:
        logger.warning(f"No funding data for {symbol}")
        return pd.DataFrame()

    combined = pd.concat(all_data, ignore_index=True)
    rows = save_parquet(combined, out_path, schema_name="funding")
    logger.info(f"✓ {symbol} funding: {rows} total rows stored")
    return combined


def collect_all_funding(symbols: list[str] | None = None):
    """Collect funding for all configured symbols."""
    symbols = symbols or ALL_SYMBOLS
    for symbol in symbols:
        logger.info(f"── Collecting {symbol} funding ──")
        try:
            collect_funding(symbol)
        except Exception as e:
            logger.error(f"Failed {symbol} funding: {e}")


if __name__ == "__main__":
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    collect_all_funding()
