"""
Coinalyze liquidation history collector.

Fetches from /v1/liquidation-history
API requires: symbols, interval, from, to
Response format: [{t: unix_sec, l: long_liq, s: short_liq}]

Rate limit: 40 requests/minute (shared limiter with OI collector).
"""
import time
import requests
import pandas as pd
from pathlib import Path
from loguru import logger

from research.config.settings import (
    COINALYZE_BASE,
    COINALYZE_LIQ_HISTORY,
    COINALYZE_SYMBOL_MAP,
    LIQUIDATION_DIR,
    ALL_SYMBOLS,
    SYMBOL_LISTING_MS,
)
from research.config.secrets import get_coinalyze_key
from research.data.collectors.coinalyze_oi import RateLimiter
from research.data.storage.parquet_store import save_parquet, get_last_timestamp


_limiter = RateLimiter(max_calls=35, period=60)


def _fetch_liquidation_history(
    symbol_coinalyze: str,
    interval: str = "daily",
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> list[dict]:
    """
    Fetch liquidation history from Coinalyze.

    Args:
        symbol_coinalyze: Coinalyze symbol (e.g. 'SOLUSDT_PERP.A')
        interval: 'daily', 'hour', '4hour', etc.
        from_ts: Start timestamp (unix seconds, required)
        to_ts: End timestamp (unix seconds, required)
    """
    _limiter.wait()

    if from_ts is None:
        from_ts = 1_599_782_400
    if to_ts is None:
        to_ts = int(time.time())

    url = f"{COINALYZE_BASE}{COINALYZE_LIQ_HISTORY}"
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
                logger.error(f"Coinalyze 400: {resp.text[:200]}")
                return []
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("history", [])
            return []
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt
            logger.warning(f"Request error (attempt {attempt+1}): {e}, retrying in {wait}s")
            time.sleep(wait)

    return []


def _parse_liquidations(raw: list[dict]) -> pd.DataFrame:
    """
    Parse Coinalyze liquidation response.

    Response format: [{t: unix_sec, l: long_liquidations, s: short_liquidations}]
    """
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw)

    df = df.rename(columns={
        "t": "timestamp",
        "l": "long_liquidations",
        "s": "short_liquidations",
    })

    # Convert timestamp to ms
    df["timestamp"] = (df["timestamp"] * 1000).astype(int)

    # Ensure columns exist
    for col in ["long_liquidations", "short_liquidations"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # USD values not provided by this endpoint, set to 0
    df["long_liquidations_usd"] = 0.0
    df["short_liquidations_usd"] = 0.0

    return df[["timestamp", "long_liquidations", "short_liquidations",
               "long_liquidations_usd", "short_liquidations_usd"]]


def collect_liquidations(
    symbol: str,
    interval: str = "daily",
) -> pd.DataFrame:
    """Collect liquidation history for a symbol."""
    coinalyze_sym = COINALYZE_SYMBOL_MAP.get(symbol, f"{symbol}_PERP.A")

    suffix = "daily" if interval == "daily" else "1h"
    out_path = LIQUIDATION_DIR / f"{symbol}_liq_{suffix}.parquet"

    # Determine time range
    listing_sec = SYMBOL_LISTING_MS.get(symbol, 1_599_782_400_000) // 1000
    now_sec = int(time.time())

    last_ts = get_last_timestamp(out_path)
    if last_ts:
        from_ts = last_ts // 1000 + 1
        logger.info(f"Resuming {symbol} liquidations ({interval}) from {pd.Timestamp(last_ts, unit='ms')}")
    else:
        from_ts = listing_sec

    logger.info(f"Fetching Coinalyze liquidations for {symbol} ({interval})")

    raw = _fetch_liquidation_history(coinalyze_sym, interval=interval, from_ts=from_ts, to_ts=now_sec)
    df = _parse_liquidations(raw)

    if df.empty:
        logger.warning(f"No liquidation data for {symbol} ({interval})")
        return pd.DataFrame()

    rows = save_parquet(df, out_path, schema_name="liquidation")
    logger.info(f"✓ {symbol} liquidations ({interval}): {rows} rows, "
                f"range: {pd.Timestamp(df['timestamp'].min(), unit='ms')} → "
                f"{pd.Timestamp(df['timestamp'].max(), unit='ms')}")
    return df


def collect_all_liquidations(symbols: list[str] | None = None):
    """Collect liquidations for all symbols (daily + hourly)."""
    symbols = symbols or ALL_SYMBOLS
    for symbol in symbols:
        logger.info(f"── Collecting {symbol} liquidations ──")
        try:
            collect_liquidations(symbol, interval="daily")
            collect_liquidations(symbol, interval="hour")
        except Exception as e:
            logger.error(f"Failed {symbol} liquidations: {e}")


if __name__ == "__main__":
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    collect_all_liquidations()
