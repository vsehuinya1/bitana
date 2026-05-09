"""
Shared test fixtures.
"""
import asyncio
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def sample_config():
    """Load config from settings.yaml."""
    from config.loader import load_config
    return load_config(
        config_path=Path(__file__).parent.parent / "config" / "settings.yaml",
        env_path=Path(__file__).parent.parent / ".env.example",
    )


@pytest.fixture
def sample_candles():
    """Generate sample candle data for testing."""
    from core.models import Candle
    from datetime import datetime, timezone, timedelta

    candles = []
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    base_price = 100.0

    for i in range(200):
        # Simulate some price movement
        import math
        noise = math.sin(i * 0.1) * 2 + (i * 0.01)
        price = base_price + noise

        candle = Candle(
            symbol="SOLUSDT",
            timeframe="5m",
            open_time=base_time + timedelta(minutes=5 * i),
            close_time=base_time + timedelta(minutes=5 * (i + 1)),
            open=price,
            high=price + abs(noise) * 0.1 + 0.5,
            low=price - abs(noise) * 0.1 - 0.5,
            close=price + noise * 0.05,
            volume=1000 + i * 10,
            is_closed=True,
        )
        candles.append(candle)

    return candles
