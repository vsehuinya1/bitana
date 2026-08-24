"""BTC trend regime — mirrors shadow/v5_forward_test tagging."""
from __future__ import annotations

import numpy as np

from core.models import Candle
from engines.liq_cluster_engine_v5 import _ema
from engines.swing_break_engine import _adx_series


def compute_btc_regime(
    candles_4h: list[Candle],
) -> tuple[str | None, float | None]:
    """BTC bull/bear/neutral from 4h 200EMA + ADX>25."""
    if len(candles_4h) < 200:
        return None, None

    closes = np.array([c.close for c in candles_4h], dtype=float)
    ema200 = _ema(closes, 200)
    price = float(closes[-1])
    if ema200 <= 0:
        return None, None

    dist_pct = (price - ema200) / ema200 * 100.0
    adx_vals = _adx_series(candles_4h, 14)
    adx = float(adx_vals[-1]) if adx_vals else 0.0

    if adx <= 25:
        state = "neutral"
    elif price > ema200:
        state = "bull"
    else:
        state = "bear"

    return state, round(dist_pct, 4)
