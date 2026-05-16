"""
Risk model.

Supports:
- Fixed fractional sizing (1%, 2%, 5%)
- ATR-based stops
- Structure-based stops
- Trailing exits
- Scale-out logic
"""
import pandas as pd
import numpy as np
from research.config.settings import RISK_FRACTIONS


def fixed_fractional_size(
    capital: float,
    risk_fraction: float,
    entry_price: float,
    stop_price: float,
) -> float:
    """
    Calculate position size using fixed fractional risk.

    Args:
        capital: Current account equity
        risk_fraction: Fraction of capital to risk (e.g., 0.01 = 1%)
        entry_price: Entry price
        stop_price: Stop loss price

    Returns: Position size in base asset units
    """
    risk_amount = capital * risk_fraction
    risk_per_unit = abs(entry_price - stop_price)

    if risk_per_unit <= 0:
        return 0.0

    return risk_amount / risk_per_unit


def atr_stop(
    entry_price: float,
    atr_value: float,
    multiplier: float = 2.0,
    side: str = "long",
) -> float:
    """
    Calculate ATR-based stop loss.

    Args:
        entry_price: Entry price
        atr_value: Current ATR value
        multiplier: ATR multiplier (e.g., 2.0 = 2x ATR)
        side: 'long' or 'short'

    Returns: Stop loss price
    """
    distance = atr_value * multiplier
    if side == "long":
        return entry_price - distance
    return entry_price + distance


def structure_stop(
    df: pd.DataFrame,
    idx: int,
    lookback: int = 20,
    side: str = "long",
    buffer_pct: float = 0.1,
) -> float:
    """
    Calculate structure-based stop loss using recent swing low/high.

    Args:
        df: DataFrame with OHLCV
        idx: Current bar index
        lookback: Bars to look back for swing
        side: 'long' or 'short'
        buffer_pct: Additional buffer below/above swing (percentage)

    Returns: Stop loss price
    """
    start = max(0, idx - lookback)
    window = df.iloc[start:idx]

    if window.empty:
        return 0.0

    if side == "long":
        swing_low = window["low"].min()
        return swing_low * (1 - buffer_pct / 100)
    else:
        swing_high = window["high"].max()
        return swing_high * (1 + buffer_pct / 100)


def trailing_stop_update(
    current_stop: float,
    current_price: float,
    trailing_distance: float,
    side: str = "long",
) -> float:
    """
    Update trailing stop.

    Only ratchets in the profitable direction, never backwards.
    """
    if side == "long":
        new_stop = current_price - trailing_distance
        return max(current_stop, new_stop)
    else:
        new_stop = current_price + trailing_distance
        return min(current_stop, new_stop)


def scale_out_levels(
    entry_price: float,
    atr_value: float,
    n_levels: int = 3,
    atr_multiples: list[float] = None,
    fractions: list[float] = None,
    side: str = "long",
) -> list[dict]:
    """
    Generate scale-out levels.

    Args:
        entry_price: Entry price
        atr_value: ATR for distance calculation
        n_levels: Number of scale-out levels
        atr_multiples: ATR multiples for each level
        fractions: Fraction to close at each level (must sum to <= 1.0)
        side: 'long' or 'short'

    Returns: List of {price, fraction} dicts
    """
    if atr_multiples is None:
        atr_multiples = [1.5, 3.0, 5.0][:n_levels]
    if fractions is None:
        fractions = [0.33, 0.33, 0.34][:n_levels]

    levels = []
    for mult, frac in zip(atr_multiples, fractions):
        if side == "long":
            price = entry_price + atr_value * mult
        else:
            price = entry_price - atr_value * mult
        levels.append({"price": price, "fraction": frac})

    return levels
