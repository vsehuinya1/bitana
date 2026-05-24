"""
Coinalyze OI (Open Interest) history collector.

Fetches from /v1/open-interest-history
- Daily: full multi-year history (2000+ points)
- Hourly: last ~2000 data points (~80 days)

API requires: symbols, interval, from, to
Rate limit: 40 requests/minute.
"""
import time
import requests
import pandas as pd
from pathlib import Path
from loguru import logger

from research.config.settings import (
    COINALYZE_BASE,
    COINALYZE_OI_HISTORY,
    COINALYZE_SYMBOL_MAP,
    OI_DIR,
    ALL_SYMBOLS,
    SYMBOL_LISTING_MS,
)
from research.config.secrets import get_coinalyze_key
from research.data.storage.parquet_store import save_parquet, get_last_timestamp


class RateLimiter:
    """Simple sliding window rate limiter."""

    def __init__(self, max_calls: int = 40, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self.calls: list[float] = []

    def wait(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < self.period]
        if len(self.calls) >= self.max_calls:
            sleep_time = self.period - (now - self.calls[0]) + 0.5
            if sleep_time > 0:
                logger.debug(f"Rate limit: sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
        self.calls.append(time.time())


_limiter = RateLimiter(max_calls=35, period=60)


def _fetch_oi_history(
    symbol_coinalyze: str,
    interval: str = "daily",
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> list[dict]:
    """
    Fetch OI history from Coinalyze.

    Args:
        symbol_coinalyze: Coinalyze symbol (e.g. 'SOLUSDT_PERP.A')
        interval: 'daily', 'hour', '4hour', '15minute', '5minute', 'minute'
        from_ts: Start timestamp (unix seconds, required)
        to_ts: End timestamp (unix seconds, required)
    """
    _limiter.wait()

    if from_ts is None:
        from_ts = 1_599_782_400  # Sep 2020 fallback
    if to_ts is None:
        to_ts = int(time.time())

    url = f"{COINALYZE_BASE}{COINALYZE_OI_HISTORY}"
    params = {
        "symbols": symbol_coinalyze,
        "interval": interval,
        "from": from_ts,
        "to": to_ts,
        "api_key": get_coinalyze_key(),
    }

    for attempt in range(5):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Coinalyze rate limit hit, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            if resp.status_code == 401:
                logger.error("Coinalyze auth failed — check API key")
                return []
            if resp.status_code == 400:
                logger.error(f"Coinalyze 400 Bad Request: {resp.text[:200]}")
                return []
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("history", [])
            return []
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Coinalyze request error (attempt {attempt+1}): {e}, retrying in {wait}s")
            time.sleep(wait)

    logger.error(f"Failed to fetch Coinalyze OI for {symbol_coinalyze} after 5 attempts")
    return []


def _parse_oi(raw: list[dict]) -> pd.DataFrame:
    """
    Parse Coinalyze OI response.

    Response format: [{t: unix_sec, o: open_oi, h: high_oi, l: low_oi, c: close_oi}]
    """
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)

    # Rename fields
    df = df.rename(columns={
        "t": "timestamp",
        "o": "oi_open",
        "h": "oi_high",
        "l": "oi_low",
        "c": "open_interest",  # Use close as primary OI value
    })

    # Convert timestamp from seconds to milliseconds
    df["timestamp"] = (df["timestamp"] * 1000).astype(int)

    # Add OI value column (same as close OI, for compatibility)
    df["open_interest_value"] = 0.0  # Not provided by Coinalyze in base units

    for col in ["oi_open", "oi_high", "oi_low", "open_interest"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[["timestamp", "open_interest", "oi_open", "oi_high", "oi_low", "open_interest_value"]]


def collect_oi(
    symbol: str,
    interval: str = "daily",
) -> pd.DataFrame:
    """
    Collect OI history for a symbol.

    Args:
        symbol: Standard symbol name (e.g. 'SOLUSDT')
        interval: 'daily' for full history, 'hour' for recent ~2000 points
    """
    coinalyze_sym = COINALYZE_SYMBOL_MAP.get(symbol)
    if not coinalyze_sym:
        logger.error(f"No Coinalyze symbol mapping for {symbol}")
        return pd.DataFrame()

    suffix = "daily" if interval == "daily" else "1h"
    out_path = OI_DIR / f"{symbol}_oi_{suffix}.parquet"

    # Determine time range
    listing_sec = SYMBOL_LISTING_MS.get(symbol, 1_599_782_400_000) // 1000
    now_sec = int(time.time())

    # Check for existing data to resume
    last_ts = get_last_timestamp(out_path)
    if last_ts:
        from_ts = last_ts // 1000 + 1  # Resume from next second
        logger.info(f"Resuming {symbol} OI ({interval}) from {pd.Timestamp(last_ts, unit='ms')}")
    else:
        from_ts = listing_sec

    logger.info(f"Fetching Coinalyze OI for {symbol} ({interval})")

    raw = _fetch_oi_history(coinalyze_sym, interval=interval, from_ts=from_ts, to_ts=now_sec)
    df = _parse_oi(raw)

    if df.empty:
        logger.warning(f"No OI data returned for {symbol} ({interval})")
        return pd.DataFrame()

    rows = save_parquet(df, out_path, schema_name="oi")
    logger.info(f"✓ {symbol} OI ({interval}): {rows} rows, "
                f"range: {pd.Timestamp(df['timestamp'].min(), unit='ms')} → "
                f"{pd.Timestamp(df['timestamp'].max(), unit='ms')}")
    return df


def collect_all_oi(symbols: list[str] | None = None):
    """Collect OI for all symbols (daily + hourly)."""
    symbols = symbols or ALL_SYMBOLS

    for symbol in symbols:
        logger.info(f"── Collecting {symbol} OI ──")
        try:
            collect_oi(symbol, interval="daily")
            collect_oi(symbol, interval="hour")
        except Exception as e:
            logger.error(f"Failed {symbol} OI: {e}")


if __name__ == "__main__":
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    collect_all_oi()
