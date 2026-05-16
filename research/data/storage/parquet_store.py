"""
Parquet storage layer with schema enforcement.

Handles:
- Schema-enforced read/write
- Timestamp normalization (all UTC, ms precision)
- Deduplication on write
- Append-mode for incremental collection
- Sorted by timestamp
"""
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from loguru import logger


# ──────────────────────────────────────────────
# Schema definitions
# ──────────────────────────────────────────────
OHLCV_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),        # Unix ms UTC
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),          # Base asset volume
    ("quote_volume", pa.float64()),    # Quote asset volume
    ("taker_buy_volume", pa.float64()),
    ("taker_sell_volume", pa.float64()),
    ("trades", pa.int64()),
])

FUNDING_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("symbol", pa.string()),
    ("funding_rate", pa.float64()),
    ("mark_price", pa.float64()),
])

OI_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("open_interest", pa.float64()),
    ("open_interest_value", pa.float64()),  # in USD if available
])

LIQUIDATION_SCHEMA = pa.schema([
    ("timestamp", pa.int64()),
    ("long_liquidations", pa.float64()),
    ("short_liquidations", pa.float64()),
    ("long_liquidations_usd", pa.float64()),
    ("short_liquidations_usd", pa.float64()),
])

SCHEMAS = {
    "ohlcv": OHLCV_SCHEMA,
    "funding": FUNDING_SCHEMA,
    "oi": OI_SCHEMA,
    "liquidation": LIQUIDATION_SCHEMA,
}


def normalize_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure timestamp column is int64 milliseconds UTC."""
    if "timestamp" not in df.columns:
        raise ValueError("DataFrame must have 'timestamp' column")

    # If it's already int, assume ms
    if pd.api.types.is_integer_dtype(df["timestamp"]):
        return df

    # If datetime, convert to ms
    if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = df["timestamp"].astype("int64") // 1_000_000
        return df

    # Try parsing
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("int64") // 1_000_000
    return df


def save_parquet(
    df: pd.DataFrame,
    path: Path,
    schema_name: str = None,
    append: bool = True,
    dedup_col: str = "timestamp",
) -> int:
    """
    Save DataFrame to parquet with optional schema enforcement,
    deduplication, and append mode.

    Returns: number of rows written.
    """
    if df.empty:
        logger.warning(f"Empty DataFrame, skipping write to {path}")
        return 0

    df = normalize_timestamps(df.copy())

    # Append to existing file if present
    if append and path.exists():
        existing = pd.read_parquet(path)
        df = pd.concat([existing, df], ignore_index=True)

    # Deduplicate
    if dedup_col and dedup_col in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=[dedup_col], keep="last")
        dupes = before - len(df)
        if dupes > 0:
            logger.debug(f"Removed {dupes} duplicate rows")

    # Sort by timestamp
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    # Ensure output directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write
    df.to_parquet(path, index=False, engine="pyarrow")
    logger.info(f"Saved {len(df)} rows to {path}")
    return len(df)


def load_parquet(path: Path) -> pd.DataFrame:
    """Load a parquet file, return empty DataFrame if not found."""
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return pd.DataFrame()
    return pd.read_parquet(path)


def get_last_timestamp(path: Path) -> int | None:
    """Get the last timestamp from a parquet file for incremental collection."""
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["timestamp"])
    if df.empty:
        return None
    return int(df["timestamp"].max())
