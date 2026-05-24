"""
Tests for Data Integrity — closed-candle-only logic, no lookahead.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from core.models import Candle
from data.candle_manager import CandleManager


@pytest.fixture
def candle_mgr():
    return CandleManager(history_limit=100)


class TestClosedCandleOnly:
    def test_unclosed_candle_not_stored(self, candle_mgr):
        """Non-closed candle should not appear in history."""
        data = {
            "k": {
                "s": "BTCUSDT", "i": "5m",
                "t": 1000000, "T": 1300000,
                "o": "100", "h": "101", "l": "99", "c": "100.5",
                "v": "1000", "x": False,
            }
        }
        asyncio.get_event_loop().run_until_complete(
            candle_mgr.handle_ws_kline(data)
        )
        candles = candle_mgr.get_candles("BTCUSDT", "5m")
        assert len(candles) == 0

    def test_closed_candle_is_stored(self, candle_mgr):
        """Closed candle should be stored."""
        data = {
            "k": {
                "s": "BTCUSDT", "i": "5m",
                "t": 1000000, "T": 1300000,
                "o": "100", "h": "101", "l": "99", "c": "100.5",
                "v": "1000", "x": True,
            }
        }
        asyncio.get_event_loop().run_until_complete(
            candle_mgr.handle_ws_kline(data)
        )
        candles = candle_mgr.get_candles("BTCUSDT", "5m")
        assert len(candles) == 1
        assert candles[0].is_closed is True


class TestNoDuplicates:
    def test_same_candle_not_duplicated(self, candle_mgr):
        """Same candle sent twice should not create duplicate."""
        data = {
            "k": {
                "s": "BTCUSDT", "i": "5m",
                "t": 1000000, "T": 1300000,
                "o": "100", "h": "101", "l": "99", "c": "100.5",
                "v": "1000", "x": True,
            }
        }
        for _ in range(3):
            asyncio.get_event_loop().run_until_complete(
                candle_mgr.handle_ws_kline(data)
            )
        candles = candle_mgr.get_candles("BTCUSDT", "5m")
        assert len(candles) == 1


class TestHistoryLimit:
    def test_respects_limit(self):
        mgr = CandleManager(history_limit=5)
        for i in range(10):
            data = {
                "k": {
                    "s": "BTCUSDT", "i": "5m",
                    "t": 1000000 + i * 300000,
                    "T": 1300000 + i * 300000,
                    "o": "100", "h": "101", "l": "99", "c": "100.5",
                    "v": "1000", "x": True,
                }
            }
            asyncio.get_event_loop().run_until_complete(
                mgr.handle_ws_kline(data)
            )
        candles = mgr.get_candles("BTCUSDT", "5m")
        assert len(candles) <= 5


class TestCandleProperties:
    def test_body_calculation(self):
        c = Candle(
            symbol="TEST", timeframe="5m",
            open_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            close_time=datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc),
            open=100, high=105, low=95, close=103, volume=1000,
        )
        assert c.body == 3.0
        assert c.upper_wick == 2.0
        assert c.lower_wick == 5.0
        assert c.total_range == 10.0
