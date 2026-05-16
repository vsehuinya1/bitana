"""
Walk-forward testing framework.

Supports:
- Rolling or anchored walk-forward windows
- Configurable in-sample / out-of-sample splits
- Aggregates OOS results
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from loguru import logger
from typing import Callable


@dataclass
class WalkForwardConfig:
    """Walk-forward configuration."""
    train_days: int = 90           # In-sample window
    test_days: int = 30            # Out-of-sample window
    step_days: int = 30            # Step between windows
    anchored: bool = False         # If True, training start is fixed
    min_train_bars: int = 100      # Minimum bars for training


def walk_forward_split(
    df: pd.DataFrame,
    config: WalkForwardConfig,
    ts_col: str = "timestamp",
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Generate walk-forward train/test splits.

    Returns: List of (train_df, test_df) tuples
    """
    ms_per_day = 86_400_000
    train_ms = config.train_days * ms_per_day
    test_ms = config.test_days * ms_per_day
    step_ms = config.step_days * ms_per_day

    ts_min = df[ts_col].min()
    ts_max = df[ts_col].max()

    if config.anchored:
        train_start = ts_min
    else:
        train_start = ts_min

    splits = []
    cursor = ts_min

    while True:
        if config.anchored:
            t_start = ts_min
        else:
            t_start = cursor

        t_end = t_start + train_ms
        test_start = t_end
        test_end = test_start + test_ms

        if test_end > ts_max:
            break

        train = df[(df[ts_col] >= t_start) & (df[ts_col] < t_end)]
        test = df[(df[ts_col] >= test_start) & (df[ts_col] < test_end)]

        if len(train) >= config.min_train_bars and len(test) > 0:
            splits.append((train, test))
            logger.debug(f"Split {len(splits)}: "
                         f"train {pd.Timestamp(t_start, unit='ms').date()} → "
                         f"{pd.Timestamp(t_end, unit='ms').date()} ({len(train)} bars) | "
                         f"test {pd.Timestamp(test_start, unit='ms').date()} → "
                         f"{pd.Timestamp(test_end, unit='ms').date()} ({len(test)} bars)")

        cursor += step_ms

    logger.info(f"Walk-forward: {len(splits)} splits generated")
    return splits


def run_walk_forward(
    df: pd.DataFrame,
    config: WalkForwardConfig,
    train_fn: Callable[[pd.DataFrame], dict],
    test_fn: Callable[[pd.DataFrame, dict], pd.DataFrame],
) -> pd.DataFrame:
    """
    Run walk-forward test.

    Args:
        df: Full dataset
        config: Walk-forward config
        train_fn: Function(train_df) -> params dict
        test_fn: Function(test_df, params) -> trades DataFrame

    Returns: Combined OOS trades DataFrame
    """
    splits = walk_forward_split(df, config)
    all_trades = []

    for i, (train, test) in enumerate(splits):
        logger.info(f"Walk-forward fold {i+1}/{len(splits)}")

        # Train
        params = train_fn(train)

        # Test
        trades = test_fn(test, params)
        if not trades.empty:
            trades["wf_fold"] = i + 1
            all_trades.append(trades)

    if not all_trades:
        return pd.DataFrame()

    return pd.concat(all_trades, ignore_index=True)
