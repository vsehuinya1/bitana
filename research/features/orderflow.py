"""
Order flow features derived from taker buy/sell volume.

- Taker imbalance
- Delta persistence (rolling signed delta)
- Aggressive volume spikes (z-score based)
- Sustained buy/sell pressure (consecutive direction)
- Volume acceleration (volume ROC)
"""
import pandas as pd
import numpy as np
from research.features.base import rolling_zscore


def taker_imbalance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute taker imbalance: (buy - sell) / (buy + sell).
    Range: -1 (all selling) to +1 (all buying).
    """
    df = df.copy()
    buy = df["taker_buy_volume"]
    sell = df["taker_sell_volume"]
    total = buy + sell
    df["taker_imbalance"] = (buy - sell) / total.replace(0, float("nan"))
    return df


def delta_persistence(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    """
    Rolling sum of signed taker delta (buy - sell).
    Persistent positive = sustained buying pressure.
    """
    if periods is None:
        periods = [10, 30, 60]

    df = df.copy()
    delta = df["taker_buy_volume"] - df["taker_sell_volume"]

    for p in periods:
        df[f"delta_persist_{p}"] = delta.rolling(p).sum()

    return df


def aggressive_volume_spikes(
    df: pd.DataFrame,
    lookback: int = 100,
    threshold: float = 2.0,
) -> pd.DataFrame:
    """
    Detect aggressive volume spikes using z-score of taker volumes.
    Returns z-scores and boolean spike flags.
    """
    df = df.copy()

    # Total taker volume z-score
    total_vol = df["taker_buy_volume"] + df["taker_sell_volume"]
    df["taker_vol_zscore"] = rolling_zscore(total_vol, lookback)

    # Buy-side spike
    df["buy_vol_zscore"] = rolling_zscore(df["taker_buy_volume"], lookback)

    # Sell-side spike
    df["sell_vol_zscore"] = rolling_zscore(df["taker_sell_volume"], lookback)

    # Boolean flags
    df["aggressive_buy_spike"] = df["buy_vol_zscore"] > threshold
    df["aggressive_sell_spike"] = df["sell_vol_zscore"] > threshold

    return df


def sustained_pressure(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    """
    Count consecutive bars of same-direction taker imbalance.
    Positive values = consecutive buying. Negative = consecutive selling.
    """
    df = df.copy()

    if "taker_imbalance" not in df.columns:
        df = taker_imbalance(df)

    sign = np.sign(df["taker_imbalance"])

    # Count consecutive same-sign bars
    change = (sign != sign.shift(1)).cumsum()
    streak = sign.groupby(change).cumcount() + 1
    df[f"sustained_pressure_{period}"] = streak * sign

    # Also add rolling win rate (what % of last N bars were buying)
    is_buying = (df["taker_imbalance"] > 0).astype(float)
    df[f"buy_pressure_pct_{period}"] = is_buying.rolling(period).mean() * 100

    return df


def volume_acceleration(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    """
    Volume rate of change.
    Positive = accelerating, negative = decelerating.
    """
    if periods is None:
        periods = [5, 10, 20]

    df = df.copy()
    vol = df["volume"]

    for p in periods:
        prev_vol = vol.rolling(p).mean().shift(p)
        cur_vol = vol.rolling(p).mean()
        df[f"vol_accel_{p}"] = (cur_vol / prev_vol.replace(0, float("nan")) - 1) * 100

    return df


def add_all_orderflow(
    df: pd.DataFrame,
    delta_periods: list[int] = None,
    spike_lookback: int = 100,
    spike_threshold: float = 2.0,
    pressure_period: int = 5,
    accel_periods: list[int] = None,
) -> pd.DataFrame:
    """Add all order flow features at once."""
    df = taker_imbalance(df)
    df = delta_persistence(df, delta_periods)
    df = aggressive_volume_spikes(df, spike_lookback, spike_threshold)
    df = sustained_pressure(df, pressure_period)
    df = volume_acceleration(df, accel_periods)
    return df
