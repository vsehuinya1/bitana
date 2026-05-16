"""
Volatility features.

- ATR (Average True Range)
- Realized volatility (log-return std)
- Bollinger Band Width
- Compression percentile (ATR percentile rank)
- Range contraction (HL range vs ATR)
- Volatility expansion (current vol vs rolling median)
"""
import pandas as pd
import numpy as np
from research.features.base import percentile_rank


def true_range(df: pd.DataFrame) -> pd.Series:
    """Compute True Range."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Add ATR column."""
    df = df.copy()
    tr = true_range(df)
    df[f"atr_{period}"] = tr.ewm(span=period, adjust=False).mean()
    return df


def realized_volatility(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Add realized volatility (annualized log-return std)."""
    df = df.copy()
    log_ret = np.log(df["close"] / df["close"].shift(1))
    df[f"rvol_{period}"] = log_ret.rolling(period).std()
    return df


def bollinger_band_width(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """Add Bollinger Band Width."""
    df = df.copy()
    sma = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    df[f"bbw_{period}"] = (upper - lower) / sma
    return df


def compression_percentile(df: pd.DataFrame, atr_period: int = 14, lookback: int = 100) -> pd.DataFrame:
    """
    ATR percentile rank over lookback window.
    Low values = compressed market. High values = expanded.
    """
    df = df.copy()
    if f"atr_{atr_period}" not in df.columns:
        df = atr(df, atr_period)
    df[f"compression_pctl_{lookback}"] = percentile_rank(df[f"atr_{atr_period}"], lookback)
    return df


def range_contraction(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Current bar HL range as fraction of ATR.
    Values < 1.0 indicate contraction. << 0.5 = extreme compression.
    """
    df = df.copy()
    if f"atr_{period}" not in df.columns:
        df = atr(df, period)
    cur_range = df["high"] - df["low"]
    df[f"range_contraction_{period}"] = cur_range / df[f"atr_{period}"].replace(0, float("nan"))
    return df


def volatility_expansion(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Current realized vol vs rolling median.
    Values > 1.0 = expanding. Values < 1.0 = contracting.
    """
    df = df.copy()
    if f"rvol_{period}" not in df.columns:
        df = realized_volatility(df, period)
    median_vol = df[f"rvol_{period}"].rolling(period * 5).median()
    df[f"vol_expansion_{period}"] = df[f"rvol_{period}"] / median_vol.replace(0, float("nan"))
    return df


def add_all_volatility(
    df: pd.DataFrame,
    atr_period: int = 14,
    rvol_period: int = 20,
    bbw_period: int = 20,
    compression_lookback: int = 100,
) -> pd.DataFrame:
    """Add all volatility features at once."""
    df = atr(df, atr_period)
    df = realized_volatility(df, rvol_period)
    df = bollinger_band_width(df, bbw_period)
    df = compression_percentile(df, atr_period, compression_lookback)
    df = range_contraction(df, atr_period)
    df = volatility_expansion(df, rvol_period)
    return df
