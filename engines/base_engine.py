"""
Base Engine — Abstract signal engine interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.models import Signal, Candle


class BaseEngine(ABC):
    """Abstract base for all signal engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine name."""

    @abstractmethod
    async def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        candles_1m: list[Candle],
    ) -> Optional[Signal]:
        """Evaluate candles and optionally return a signal.

        All candles are closed candles only. No lookahead.
        """
