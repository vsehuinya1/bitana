"""
Base feature pipeline class.

All feature modules follow the same pattern:
- Take a DataFrame
- Return it with new columns added
- No global state
- No lookahead
"""
import pandas as pd
from abc import ABC, abstractmethod


class FeaturePipeline(ABC):
    """Base class for feature pipelines."""

    @abstractmethod
    def compute(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Compute features and return DataFrame with added columns."""
        pass

    def __call__(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        return self.compute(df, **kwargs)


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Compute rolling z-score."""
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mean) / std.replace(0, float("nan"))


def percentile_rank(series: pd.Series, window: int) -> pd.Series:
    """Compute rolling percentile rank (0-100)."""
    return series.rolling(window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100,
        raw=False,
    )
