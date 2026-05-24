"""
Funding rate features.

- Funding percentile (historical rank)
- Funding extremes (threshold-based flags)
- Funding momentum (direction and acceleration)
- Funding compression (realized funding vol compression)
"""
import pandas as pd
import numpy as np
from research.features.base import rolling_zscore, percentile_rank


def funding_percentile(df: pd.DataFrame, lookbacks: list[int] = None, col: str = "funding_rate") -> pd.DataFrame:
    """Rolling percentile rank of funding rate."""
    if lookbacks is None:
        lookbacks = [100, 500, 1000]

    df = df.copy()
    for lb in lookbacks:
        df[f"funding_pctl_{lb}"] = percentile_rank(df[col], lb)

    return df


def funding_extremes(
    df: pd.DataFrame,
    high_pctl: float = 90,
    low_pctl: float = 10,
    lookback: int = 500,
    col: str = "funding_rate",
) -> pd.DataFrame:
    """
    Flag extreme funding conditions.

    - funding_extreme_long: funding > high_pctl (market heavily long)
    - funding_extreme_short: funding < low_pctl (market heavily short)
    """
    df = df.copy()

    rolling_high = df[col].rolling(lookback).quantile(high_pctl / 100)
    rolling_low = df[col].rolling(lookback).quantile(low_pctl / 100)

    df["funding_extreme_long"] = df[col] > rolling_high
    df["funding_extreme_short"] = df[col] < rolling_low
    df["funding_zscore"] = rolling_zscore(df[col], lookback)

    return df


def funding_momentum(df: pd.DataFrame, periods: list[int] = None, col: str = "funding_rate") -> pd.DataFrame:
    """
    Funding momentum: direction and acceleration.

    - funding_ma: moving average of funding
    - funding_mom: change in funding over period
    - funding_accel: acceleration (second derivative)
    """
    if periods is None:
        periods = [3, 8, 21]  # In funding intervals (~1d, ~2.7d, ~7d)

    df = df.copy()

    for p in periods:
        df[f"funding_ma_{p}"] = df[col].rolling(p).mean()
        df[f"funding_mom_{p}"] = df[col] - df[col].shift(p)

    # Acceleration (using shortest period)
    df["funding_accel"] = df[col].diff().diff()

    return df


def funding_compression(df: pd.DataFrame, period: int = 21, lookback: int = 100, col: str = "funding_rate") -> pd.DataFrame:
    """
    Funding compression: low range of funding → potential breakout in positioning.

    Uses rolling std of funding as a proxy for funding volatility.
    Low values indicate compressed/stable positioning.
    """
    df = df.copy()

    funding_vol = df[col].rolling(period).std()
    df["funding_vol"] = funding_vol
    df["funding_compression_pctl"] = percentile_rank(funding_vol, lookback)

    # Flag extreme compression (bottom 10th percentile of funding volatility)
    df["funding_compressed"] = df["funding_compression_pctl"] < 10

    return df


def add_all_funding_features(
    df: pd.DataFrame,
    col: str = "funding_rate",
    pctl_lookbacks: list[int] = None,
    extreme_lookback: int = 500,
    mom_periods: list[int] = None,
    compression_period: int = 21,
) -> pd.DataFrame:
    """Add all funding features at once."""
    df = funding_percentile(df, pctl_lookbacks, col)
    df = funding_extremes(df, lookback=extreme_lookback, col=col)
    df = funding_momentum(df, mom_periods, col)
    df = funding_compression(df, compression_period, col=col)
    return df
