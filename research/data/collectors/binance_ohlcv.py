"""
Binance Futures OHLCV collector.

Fetches kline data from /fapi/v1/klines for all configured symbols and timeframes.
Includes taker buy/sell volume derivation.
Supports incremental collection (resumes from last stored timestamp).
"""
import time
import requests
import pandas as pd
from pathlib import Path
from loguru import logger

from research.config.settings import (
    BINANCE_FUTURES_BASE,
    BINANCE_KLINES_ENDPOINT,
    BINANCE_KLINES_LIMIT,
    BINANCE_INTERVAL_MAP,
    TF_TO_MS,
    OHLCV_DIR,
    ALL_SYMBOLS,
    OHLCV_TIMEFRAMES,
    SYMBOL_LISTING_MS,
)
from research.data.storage.parquet_store import save_parquet, get_last_timestamp


KLINE_COLUMNS = [
    "timestamp",      # Open time
    "open",
    "high",
    "low",
    "close",
    "volume",          # Base asset vol
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_volume",  # Taker buy base asset vol
    "taker_buy_quote_volume",
    "ignore",
]


def _fetch_klines(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int | None = None,
    limit: int = BINANCE_KLINES_LIMIT,
) -> list[list]:
    """Fetch raw kline data from Binance."""
    url = f"{BINANCE_FUTURES_BASE}{BINANCE_KLINES_ENDPOINT}"
    params = {
        "symbol": symbol,
        "interval": interval,
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
            if resp.status_code == 418:
                logger.error("IP banned by Binance, sleeping 300s")
                time.sleep(300)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Request error (attempt {attempt+1}): {e}, retrying in {wait}s")
            time.sleep(wait)

    logger.error(f"Failed to fetch klines for {symbol} {interval} after 5 attempts")
    return []


def _parse_klines(raw: list[list]) -> pd.DataFrame:
    """Parse raw kline response into DataFrame."""
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=KLINE_COLUMNS)

    # Convert types
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                 "taker_buy_volume", "taker_buy_quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["timestamp"] = df["timestamp"].astype(int)
    df["trades"] = df["trades"].astype(int)

    # Derive taker sell volume
    df["taker_sell_volume"] = df["volume"] - df["taker_buy_volume"]

    # Keep only needed columns
    df = df[[
        "timestamp", "open", "high", "low", "close",
        "volume", "quote_volume", "taker_buy_volume",
        "taker_sell_volume", "trades",
    ]]

    return df


def collect_ohlcv(
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> pd.DataFrame:
    """
    Collect full OHLCV history for a symbol/timeframe.
    Resumes from last stored timestamp if available.
    """
    interval = BINANCE_INTERVAL_MAP[timeframe]
    tf_ms = TF_TO_MS[timeframe]

    # Output path
    out_path = OHLCV_DIR / f"{symbol}_{timeframe}.parquet"

    # Determine start
    if start_ms is None:
        last_ts = get_last_timestamp(out_path)
        if last_ts:
            start_ms = last_ts + tf_ms  # Next candle after last stored
            logger.info(f"Resuming {symbol} {timeframe} from {pd.Timestamp(start_ms, unit='ms')}")
        else:
            start_ms = SYMBOL_LISTING_MS.get(symbol, 1_568_592_000_000)
            logger.info(f"Starting {symbol} {timeframe} from listing date")

    if end_ms is None:
        end_ms = int(time.time() * 1000)

    all_data = []
    cursor = start_ms
    batch = 0

    while cursor < end_ms:
        raw = _fetch_klines(symbol, interval, cursor, end_ms)
        if not raw:
            break

        df = _parse_klines(raw)
        if df.empty:
            break

        all_data.append(df)
        batch += 1

        # Move cursor past last received candle
        last_received = int(df["timestamp"].max())
        if last_received <= cursor:
            break  # No progress
        cursor = last_received + tf_ms

        if batch % 10 == 0:
            logger.info(f"  {symbol} {timeframe}: {batch} batches, "
                        f"up to {pd.Timestamp(last_received, unit='ms')}")

        # Rate limit: ~2400 weight/min, each klines call is 5-10 weight
        time.sleep(0.15)

    if not all_data:
        logger.warning(f"No data collected for {symbol} {timeframe}")
        return pd.DataFrame()

    combined = pd.concat(all_data, ignore_index=True)
    rows = save_parquet(combined, out_path, schema_name="ohlcv")
    logger.info(f"✓ {symbol} {timeframe}: {rows} total rows stored")

    return combined


def collect_all_ohlcv(
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
):
    """Collect OHLCV for all configured symbols and timeframes."""
    symbols = symbols or ALL_SYMBOLS
    timeframes = timeframes or OHLCV_TIMEFRAMES

    for symbol in symbols:
        for tf in timeframes:
            logger.info(f"── Collecting {symbol} {tf} ──")
            try:
                collect_ohlcv(symbol, tf)
            except Exception as e:
                logger.error(f"Failed {symbol} {tf}: {e}")
                continue


if __name__ == "__main__":
    from loguru import logger
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    collect_all_ohlcv()
