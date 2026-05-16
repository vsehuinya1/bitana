"""
Regime classification and performance analysis.

- Volatility regime (low/medium/high)
- Trend regime (trending/ranging)
- Performance split by regime
"""
import pandas as pd
import numpy as np
from research.features.base import percentile_rank


def classify_volatility_regime(
    df: pd.DataFrame,
    vol_col: str = "atr_14",
    lookback: int = 200,
    thresholds: tuple = (33, 66),
) -> pd.DataFrame:
    """
    Classify into volatility regimes based on percentile rank.

    - low: bottom third
    - medium: middle third  
    - high: top third
    """
    df = df.copy()
    pctl = percentile_rank(df[vol_col], lookback)

    conditions = [
        pctl <= thresholds[0],
        (pctl > thresholds[0]) & (pctl <= thresholds[1]),
        pctl > thresholds[1],
    ]
    df["vol_regime"] = np.select(conditions, ["low", "medium", "high"], default="unknown")
    df["vol_regime_pctl"] = pctl

    return df


def classify_trend_regime(
    df: pd.DataFrame,
    period: int = 50,
    threshold: float = 0.1,
) -> pd.DataFrame:
    """
    Classify trending vs ranging using ADX-like measure.

    Uses price displacement / ATR over period.
    """
    df = df.copy()

    displacement = (df["close"] - df["close"].shift(period)).abs()
    if f"atr_{14}" in df.columns:
        normalized = displacement / (df["atr_14"] * np.sqrt(period))
    else:
        # Fallback: use range
        rolling_range = (df["high"].rolling(period).max() - df["low"].rolling(period).min())
        normalized = displacement / rolling_range.replace(0, float("nan"))

    df["trend_strength"] = normalized

    conditions = [
        normalized > (1 + threshold),
        normalized < (1 - threshold),
    ]
    df["trend_regime"] = np.select(conditions, ["trending", "ranging"], default="neutral")

    return df


def performance_by_regime(
    trades: pd.DataFrame,
    regime_data: pd.DataFrame,
    regime_col: str = "vol_regime",
    ts_col: str = "entry_time",
) -> pd.DataFrame:
    """
    Break down trade performance by regime.

    Args:
        trades: Trade log with entry_time
        regime_data: DataFrame with timestamp and regime columns
        regime_col: Column containing regime labels
        ts_col: Trade timestamp column to match

    Returns: Performance summary per regime
    """
    if trades.empty or regime_data.empty:
        return pd.DataFrame()

    # Merge regime at entry time (nearest timestamp)
    regime_data = regime_data[["timestamp", regime_col]].copy()
    regime_data = regime_data.sort_values("timestamp")

    # For each trade, find the regime at entry
    trade_regimes = []
    for _, trade in trades.iterrows():
        entry_ts = trade[ts_col]
        idx = regime_data["timestamp"].searchsorted(entry_ts)
        if idx > 0:
            regime = regime_data.iloc[min(idx, len(regime_data) - 1)][regime_col]
        else:
            regime = "unknown"
        trade_regimes.append(regime)

    trades = trades.copy()
    trades["regime"] = trade_regimes

    # Aggregate by regime
    summary = trades.groupby("regime").agg(
        count=("pnl_net", "count"),
        total_pnl=("pnl_net", "sum"),
        avg_pnl=("pnl_net", "mean"),
        win_rate=("pnl_net", lambda x: (x > 0).mean() * 100),
        pf=("pnl_net", lambda x: x[x > 0].sum() / abs(x[x <= 0].sum()) if (x <= 0).any() else float("inf")),
    ).round(2)

    return summary
