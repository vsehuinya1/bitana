"""
Squeeze Engine — Phase 2 Stub

Implements base engine interface, returns None always.
Full config present for future activation.
"""
from __future__ import annotations

from typing import Optional

from config.loader import SqueezeConfig
from core.logging_setup import get_logger
from core.models import Candle, Signal
from engines.base_engine import BaseEngine

logger = get_logger("squeeze_engine")


class SqueezeEngine(BaseEngine):
    """Squeeze engine (phase 2 — not active in v0.1)."""

    def __init__(self, config: SqueezeConfig) -> None:
        self._cfg = config
        self._warned = False

    @property
    def name(self) -> str:
        return "Squeeze"

    async def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        candles_1m: list[Candle],
    ) -> Optional[Signal]:
        if not self._warned:
            logger.info("Squeeze engine not active in v0.1 — skipping")
            self._warned = True
        return None
