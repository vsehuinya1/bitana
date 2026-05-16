"""
Market structure features.

- Donchian channel breaks
- VWAP reclaim
- Trend continuation (HH/HL, LL/LH)
- Squeeze release (BBW compression → expansion)
"""
import pandas as pd
import numpy as np


def donchian_break(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    """
    Donchian channel breakout detection.

    Flags when price breaks above/below N-period high/low.
    """
    if periods is None:
        periods = [20, 50, 100]

    df = df.copy()

    for p in periods:
        high_ch = df["high"].rolling(p).max().shift(1)
        low_ch = df["low"].rolling(p).min().shift(1)

        df[f"donchian_high_{p}"] = high_ch
        df[f"donchian_low_{p}"] = low_ch
        df[f"donchian_break_up_{p}"] = df["close"] > high_ch
        df[f"donchian_break_down_{p}"] = df["close"] < low_ch
        df[f"donchian_position_{p}"] = (df["close"] - low_ch) / (high_ch - low_ch).replace(0, float("nan"))

    return df


def session_vwap(df: pd.DataFrame, session_col: str = "session") -> pd.DataFrame:
    """
    Compute session VWAP and detect reclaims.

    Requires 'session' column from session tagger.
    VWAP resets each session change.
    """
    df = df.copy()

    # Typical price * volume
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tpv = tp * df["volume"]

    # Session change detection
    if session_col in df.columns:
        session_change = df[session_col] != df[session_col].shift(1)
        session_group = session_change.cumsum()
    else:
        # If no session column, use daily sessions
        dt = pd.to_datetime(df["timestamp"], unit="ms")
        session_group = dt.dt.date

    # Cumulative within session
    cum_tpv = tpv.groupby(session_group).cumsum()
    cum_vol = df["volume"].groupby(session_group).cumsum()

    df["vwap"] = cum_tpv / cum_vol.replace(0, float("nan"))

    # VWAP position
    df["vwap_position"] = (df["close"] - df["vwap"]) / df["vwap"].replace(0, float("nan")) * 100

    # VWAP reclaim: was below, now above (bullish); was above, now below (bearish)
    was_below = df["close"].shift(1) < df["vwap"].shift(1)
    now_above = df["close"] > df["vwap"]
    was_above = df["close"].shift(1) > df["vwap"].shift(1)
    now_below = df["close"] < df["vwap"]

    df["vwap_reclaim_bull"] = was_below & now_above
    df["vwap_reclaim_bear"] = was_above & now_below

    return df


def trend_continuation(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    """
    Detect trend continuation via swing structure.

    - Higher highs + higher lows → uptrend
    - Lower lows + lower highs → downtrend
    """
    df = df.copy()

    # Rolling swing highs/lows
    roll_high = df["high"].rolling(period).max()
    roll_low = df["low"].rolling(period).min()

    prev_high = roll_high.shift(period)
    prev_low = roll_low.shift(period)

    # Higher highs and higher lows
    hh = roll_high > prev_high
    hl = roll_low > prev_low

    # Lower lows and lower highs
    ll = roll_low < prev_low
    lh = roll_high < prev_high

    df[f"uptrend_{period}"] = hh & hl
    df[f"downtrend_{period}"] = ll & lh

    # Trend score: +1 for HH, +1 for HL, -1 for LL, -1 for LH
    df[f"trend_score_{period}"] = (
        hh.astype(int) + hl.astype(int)
        - ll.astype(int) - lh.astype(int)
    )

    return df


def squeeze_release(
    df: pd.DataFrame,
    bbw_period: int = 20,
    lookback: int = 100,
    compression_pctl: float = 10,
    expansion_mult: float = 1.5,
) -> pd.DataFrame:
    """
    Detect squeeze-to-expansion transitions.

    A squeeze is detected when BBW is in the bottom percentile.
    A release is when BBW expands by expansion_mult from the squeeze low.
    """
    df = df.copy()

    # Compute BBW if not present
    bbw_col = f"bbw_{bbw_period}"
    if bbw_col not in df.columns:
        sma = df["close"].rolling(bbw_period).mean()
        std = df["close"].rolling(bbw_period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        df[bbw_col] = (upper - lower) / sma

    # BBW percentile rank
    bbw = df[bbw_col]
    bbw_pctl = bbw.rolling(lookback).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100,
        raw=False,
    )
    df["squeeze_active"] = bbw_pctl < compression_pctl

    # Detect squeeze release: was squeezed, now expanding
    was_squeezed = df["squeeze_active"].shift(1)
    squeeze_low = bbw.rolling(lookback).min()
    expanding = bbw > squeeze_low * expansion_mult

    df["squeeze_release"] = was_squeezed & expanding & (~df["squeeze_active"])

    # Direction of release
    df["squeeze_release_bull"] = df["squeeze_release"] & (df["close"] > df["close"].shift(1))
    df["squeeze_release_bear"] = df["squeeze_release"] & (df["close"] < df["close"].shift(1))

    return df


def add_all_structure(
    df: pd.DataFrame,
    donchian_periods: list[int] = None,
    trend_period: int = 5,
    bbw_period: int = 20,
) -> pd.DataFrame:
    """Add all market structure features."""
    df = donchian_break(df, donchian_periods)
    df = session_vwap(df)
    df = trend_continuation(df, trend_period)
    df = squeeze_release(df, bbw_period)
    return df
