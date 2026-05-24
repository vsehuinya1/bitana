"""
Dataset catalog — lists available data, date ranges, row counts.
"""
import pandas as pd
from pathlib import Path
from loguru import logger
from research.config.settings import (
    OHLCV_DIR, FUNDING_DIR, OI_DIR, LIQUIDATION_DIR, DATA_DIR,
)


def scan_parquet_files(directory: Path) -> list[dict]:
    """Scan a directory for parquet files and return metadata."""
    results = []
    if not directory.exists():
        return results

    for f in sorted(directory.glob("*.parquet")):
        try:
            df = pd.read_parquet(f, columns=["timestamp"])
            results.append({
                "file": f.name,
                "path": str(f),
                "rows": len(df),
                "ts_min": str(pd.Timestamp(df["timestamp"].min(), unit="ms")),
                "ts_max": str(pd.Timestamp(df["timestamp"].max(), unit="ms")),
                "size_mb": round(f.stat().st_size / 1_048_576, 2),
            })
        except Exception as e:
            results.append({
                "file": f.name,
                "path": str(f),
                "error": str(e),
            })

    return results


def catalog() -> pd.DataFrame:
    """Generate a full catalog of all available datasets."""
    all_files = []

    for name, directory in [
        ("ohlcv", OHLCV_DIR),
        ("funding", FUNDING_DIR),
        ("oi", OI_DIR),
        ("liquidations", LIQUIDATION_DIR),
    ]:
        files = scan_parquet_files(directory)
        for f in files:
            f["category"] = name
        all_files.extend(files)

    df = pd.DataFrame(all_files)
    if not df.empty:
        df = df.sort_values(["category", "file"])
    return df


def print_catalog():
    """Print the data catalog to console."""
    cat = catalog()
    if cat.empty:
        logger.info("No data files found")
        return

    logger.info(f"\n{'='*80}")
    logger.info("DATA CATALOG")
    logger.info(f"{'='*80}")

    for category in cat["category"].unique():
        subset = cat[cat["category"] == category]
        logger.info(f"\n── {category.upper()} ──")
        for _, row in subset.iterrows():
            if "error" in row and pd.notna(row.get("error")):
                logger.warning(f"  {row['file']}: ERROR - {row['error']}")
            else:
                logger.info(f"  {row['file']}: {row.get('rows', '?')} rows | "
                            f"{row.get('ts_min', '?')} → {row.get('ts_max', '?')} | "
                            f"{row.get('size_mb', '?')} MB")

    total_mb = cat.get("size_mb", pd.Series([0])).sum()
    logger.info(f"\nTotal: {len(cat)} files, {total_mb:.1f} MB")


if __name__ == "__main__":
    import sys
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    print_catalog()
