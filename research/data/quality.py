"""
Data quality audit.

Checks:
- Gap detection (missing expected timestamps)
- Null/NaN audit
- Duplicate detection
- Range validation
- Forward-fill detection (consecutive identical OI values)
- Summary statistics
"""
import pandas as pd
import numpy as np
from pathlib import Path
from loguru import logger
from research.config.settings import TF_TO_MS


def audit_gaps(
    df: pd.DataFrame,
    tf_ms: int,
    ts_col: str = "timestamp",
    tolerance: float = 1.5,
) -> pd.DataFrame:
    """
    Detect gaps in time series data.

    Args:
        df: DataFrame with timestamp column
        tf_ms: Expected interval in milliseconds
        tolerance: Multiplier for gap detection (1.5 = 50% larger than expected)

    Returns: DataFrame of detected gaps with start, end, duration_ms, missing_bars
    """
    if df.empty or ts_col not in df.columns:
        return pd.DataFrame()

    ts = df[ts_col].sort_values()
    diffs = ts.diff().dropna()

    # Find gaps larger than expected
    gap_mask = diffs > tf_ms * tolerance
    gap_indices = diffs[gap_mask].index

    gaps = []
    for idx in gap_indices:
        pos = df.index.get_loc(idx)
        start = int(ts.iloc[pos - 1]) if pos > 0 else None
        end = int(ts.iloc[pos])
        duration = end - start if start else 0
        missing_bars = int(duration / tf_ms) - 1 if tf_ms > 0 else 0
        gaps.append({
            "gap_start": start,
            "gap_end": end,
            "duration_ms": duration,
            "missing_bars": missing_bars,
        })

    return pd.DataFrame(gaps)


def audit_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """Report null/NaN counts per column."""
    null_counts = df.isnull().sum()
    null_pct = (df.isnull().sum() / len(df) * 100).round(2)
    return pd.DataFrame({
        "null_count": null_counts,
        "null_pct": null_pct,
    }).query("null_count > 0")


def audit_duplicates(df: pd.DataFrame, subset: list[str] = None) -> int:
    """Count duplicate rows."""
    if subset is None:
        subset = ["timestamp"] if "timestamp" in df.columns else None
    return df.duplicated(subset=subset).sum()


def audit_ranges(df: pd.DataFrame) -> dict:
    """Validate value ranges for OHLCV data."""
    issues = {}

    if "open" in df.columns:
        neg = (df[["open", "high", "low", "close"]] <= 0).any()
        if neg.any():
            issues["negative_prices"] = neg[neg].index.tolist()

    if "volume" in df.columns:
        neg_vol = (df["volume"] < 0).sum()
        if neg_vol > 0:
            issues["negative_volume"] = neg_vol

    if "high" in df.columns and "low" in df.columns:
        inverted = (df["high"] < df["low"]).sum()
        if inverted > 0:
            issues["inverted_hl"] = inverted

    if "open" in df.columns and "high" in df.columns:
        oob = ((df["open"] > df["high"]) | (df["open"] < df["low"])).sum()
        if oob > 0:
            issues["open_outside_hl"] = oob

    return issues


def audit_forward_fill(
    df: pd.DataFrame,
    col: str = "open_interest",
    max_repeat: int = 5,
) -> dict:
    """
    Detect suspicious forward-fill patterns in OI data.
    Flags runs of identical consecutive values exceeding max_repeat.
    """
    if col not in df.columns:
        return {}

    # Find runs of identical values
    is_same = df[col] == df[col].shift(1)
    groups = (~is_same).cumsum()
    run_lengths = is_same.groupby(groups).sum()

    suspicious = run_lengths[run_lengths >= max_repeat]
    if len(suspicious) > 0:
        return {
            "suspicious_runs": len(suspicious),
            "max_run_length": int(suspicious.max()),
            "total_suspicious_rows": int(suspicious.sum()),
        }
    return {}


def full_audit(
    df: pd.DataFrame,
    name: str = "",
    tf_ms: int | None = None,
) -> dict:
    """
    Run complete data quality audit.

    Returns dict with all audit results.
    """
    report = {"name": name, "rows": len(df)}

    if df.empty:
        report["status"] = "EMPTY"
        return report

    # Timestamp range
    if "timestamp" in df.columns:
        report["ts_min"] = str(pd.Timestamp(df["timestamp"].min(), unit="ms"))
        report["ts_max"] = str(pd.Timestamp(df["timestamp"].max(), unit="ms"))
        report["ts_span_days"] = round(
            (df["timestamp"].max() - df["timestamp"].min()) / 86_400_000, 1
        )

    # Gaps
    if tf_ms and "timestamp" in df.columns:
        gaps = audit_gaps(df, tf_ms)
        report["gaps"] = len(gaps)
        if not gaps.empty:
            report["total_missing_bars"] = int(gaps["missing_bars"].sum())
            report["largest_gap_hours"] = round(
                gaps["duration_ms"].max() / 3_600_000, 1
            )

    # Nulls
    nulls = audit_nulls(df)
    report["null_columns"] = len(nulls)
    if not nulls.empty:
        report["null_details"] = nulls.to_dict()

    # Duplicates
    report["duplicates"] = audit_duplicates(df)

    # Range checks
    range_issues = audit_ranges(df)
    report["range_issues"] = range_issues if range_issues else "CLEAN"

    # Forward-fill check
    if "open_interest" in df.columns:
        ff = audit_forward_fill(df)
        report["forward_fill"] = ff if ff else "CLEAN"

    # Overall status
    has_issues = (
        report.get("gaps", 0) > 0
        or report.get("null_columns", 0) > 0
        or report.get("duplicates", 0) > 0
        or range_issues
    )
    report["status"] = "ISSUES_FOUND" if has_issues else "CLEAN"

    return report


def print_audit(report: dict):
    """Pretty print an audit report."""
    name = report.get("name", "Unknown")
    status = report.get("status", "?")
    rows = report.get("rows", 0)

    emoji = "✓" if status == "CLEAN" else "⚠" if status == "ISSUES_FOUND" else "✗"
    logger.info(f"{emoji} {name}: {rows} rows | {status}")

    if "ts_min" in report:
        logger.info(f"  Range: {report['ts_min']} → {report['ts_max']} "
                     f"({report.get('ts_span_days', '?')} days)")

    if report.get("gaps", 0) > 0:
        logger.warning(f"  Gaps: {report['gaps']} "
                        f"({report.get('total_missing_bars', '?')} missing bars, "
                        f"largest: {report.get('largest_gap_hours', '?')}h)")

    if report.get("null_columns", 0) > 0:
        logger.warning(f"  Null columns: {report['null_columns']}")

    if report.get("duplicates", 0) > 0:
        logger.warning(f"  Duplicates: {report['duplicates']}")

    if isinstance(report.get("range_issues"), dict):
        for k, v in report["range_issues"].items():
            logger.warning(f"  Range issue: {k} = {v}")

    if isinstance(report.get("forward_fill"), dict):
        ff = report["forward_fill"]
        logger.warning(f"  Forward-fill suspects: {ff.get('suspicious_runs', 0)} runs "
                        f"(max length: {ff.get('max_run_length', 0)})")
