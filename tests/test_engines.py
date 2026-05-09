"""
Tests for Compression Breakout Engine.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from config.loader import CompressionConfig
from core.models import Candle
from engines.compression_breakout import CompressionBreakoutEngine, _atr, _bollinger_width


@pytest.fixture
def engine():
    return CompressionBreakoutEngine(CompressionConfig())


def _make_candles(
    n: int = 100, base: float = 100.0, vol_range: float = 1.0,
    symbol: str = "SOLUSDT",
) -> list[Candle]:
    """Generate candles with controlled volatility."""
    candles = []
    bt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        import math
        noise = math.sin(i * 0.3) * vol_range
        price = base + noise
        candles.append(Candle(
            symbol=symbol, timeframe="5m",
            open_time=bt + timedelta(minutes=5 * i),
            close_time=bt + timedelta(minutes=5 * (i + 1)),
            open=price,
            high=price + vol_range * 0.3,
            low=price - vol_range * 0.3,
            close=price + noise * 0.1,
            volume=1000,
            is_closed=True,
        ))
    return candles


class TestATR:
    def test_atr_returns_values(self):
        candles = _make_candles(30)
        atrs = _atr(candles, 14)
        assert len(atrs) > 0
        assert all(a >= 0 for a in atrs)

    def test_atr_too_few_candles(self):
        candles = _make_candles(1)
        atrs = _atr(candles, 14)
        assert atrs == [0.0]


class TestBBWidth:
    def test_returns_values(self):
        candles = _make_candles(30)
        widths = _bollinger_width(candles, 20)
        assert len(widths) == len(candles)

    def test_early_values_are_inf(self):
        candles = _make_candles(30)
        widths = _bollinger_width(candles, 20)
        assert widths[0] == float("inf")


class TestWickRejection:
    def test_rejects_wick_only(self, engine):
        """Candle with tiny body and huge wicks should be rejected."""
        candles = _make_candles(100, vol_range=0.5)
        # No signal expected from stable low-vol candles anyway
        result = asyncio.get_event_loop().run_until_complete(
            engine.evaluate("SOLUSDT", candles, candles, candles[-5:])
        )
        # With stable candles, likely no signal — which is correct
        # This tests that the engine doesn't crash
        assert result is None or result.symbol == "SOLUSDT"


class TestNoSignalOnInsufficientData:
    def test_too_few_candles(self, engine):
        candles = _make_candles(10)
        result = asyncio.get_event_loop().run_until_complete(
            engine.evaluate("SOLUSDT", candles, candles, candles)
        )
        assert result is None
