"""
Open Interest features.

- OI delta (change)
- OI ROC (rate of change)
- OI acceleration
- OI z-score
- Price/OI interaction states (4 quadrants)
"""
import pandas as pd
import numpy as np
from research.features.base import rolling_zscore


def oi_delta(df: pd.DataFrame, col: str = "open_interest") -> pd.DataFrame:
    """Absolute change in OI."""
    df = df.copy()
    df["oi_delta"] = df[col].diff()
    return df


def oi_roc(df: pd.DataFrame, periods: list[int] = None, col: str = "open_interest") -> pd.DataFrame:
    """OI rate of change (percentage)."""
    if periods is None:
        periods = [1, 5, 10, 24]

    df = df.copy()
    for p in periods:
        prev = df[col].shift(p)
        df[f"oi_roc_{p}"] = (df[col] / prev.replace(0, float("nan")) - 1) * 100

    return df


def oi_acceleration(df: pd.DataFrame, col: str = "open_interest") -> pd.DataFrame:
    """OI acceleration (second derivative — rate of change of the change)."""
    df = df.copy()
    if "oi_delta" not in df.columns:
        df["oi_delta"] = df[col].diff()
    df["oi_acceleration"] = df["oi_delta"].diff()
    return df


def oi_zscore(df: pd.DataFrame, lookbacks: list[int] = None, col: str = "open_interest") -> pd.DataFrame:
    """Rolling z-score of OI level and OI delta."""
    if lookbacks is None:
        lookbacks = [50, 100, 200]

    df = df.copy()
    if "oi_delta" not in df.columns:
        df["oi_delta"] = df[col].diff()

    for lb in lookbacks:
        df[f"oi_zscore_{lb}"] = rolling_zscore(df[col], lb)
        df[f"oi_delta_zscore_{lb}"] = rolling_zscore(df["oi_delta"], lb)

    return df


def price_oi_state(df: pd.DataFrame, price_col: str = "close", oi_col: str = "open_interest") -> pd.DataFrame:
    """
    Categorize into 4 price/OI interaction states:

    1. price_up_oi_up:    New longs entering → bullish continuation
    2. price_up_oi_down:  Short covering → potential exhaustion
    3. price_down_oi_up:  New shorts entering → bearish continuation
    4. price_down_oi_down: Long liquidation → potential capitulation
    """
    df = df.copy()

    price_change = df[price_col].diff()
    oi_change = df[oi_col].diff()

    price_up = price_change > 0
    price_down = price_change < 0
    oi_up = oi_change > 0
    oi_down = oi_change < 0

    # Boolean columns
    df["price_up_oi_up"] = price_up & oi_up
    df["price_up_oi_down"] = price_up & oi_down
    df["price_down_oi_up"] = price_down & oi_up
    df["price_down_oi_down"] = price_down & oi_down

    # Categorical column
    conditions = [
        price_up & oi_up,
        price_up & oi_down,
        price_down & oi_up,
        price_down & oi_down,
    ]
    choices = ["long_build", "short_cover", "short_build", "long_liquidation"]
    df["poi_state"] = np.select(conditions, choices, default="neutral")

    return df


def add_all_oi_features(
    df: pd.DataFrame,
    oi_col: str = "open_interest",
    price_col: str = "close",
    roc_periods: list[int] = None,
    zscore_lookbacks: list[int] = None,
) -> pd.DataFrame:
    """Add all OI features at once."""
    df = oi_delta(df, oi_col)
    df = oi_roc(df, roc_periods, oi_col)
    df = oi_acceleration(df, oi_col)
    df = oi_zscore(df, zscore_lookbacks, oi_col)
    df = price_oi_state(df, price_col, oi_col)
    return df
