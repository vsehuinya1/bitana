"""
Signal / event base class.

Signals are regime-aware event detectors, NOT indicators.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd
from typing import Optional
from research.engine.backtest import Position


@dataclass
class Signal:
    """Signal event."""
    name: str
    direction: str  # 'long', 'short', 'close'
    strength: float  # 0-1 confidence
    timestamp: int
    metadata: dict = None


class SignalGenerator(ABC):
    """Base class for signal generators."""

    @abstractmethod
    def name(self) -> str:
        """Signal name."""
        pass

    @abstractmethod
    def evaluate(
        self,
        bar: pd.Series,
        position: Optional[Position],
        context: dict,
    ) -> dict | None:
        """
        Evaluate signal on current bar.

        Args:
            bar: Current bar (OHLCV + features)
            position: Current position (or None)
            context: Additional context (higher TF data, etc.)

        Returns: Action dict compatible with BacktestEngine, or None
        """
        pass
