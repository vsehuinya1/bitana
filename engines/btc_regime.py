"""BTC trend regime — mirrors shadow/v5_forward_test tagging."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.models import Candle
from engines.liq_cluster_engine_v5 import _ema
from engines.swing_break_engine import _adx_series


@dataclass(frozen=True)
class RegimeSnapshot:
    state: str | None
    distance_from_ema_pct: float | None
    adx: float | None


def compute_btc_regime(
    candles_4h: list[Candle],
) -> tuple[str | None, float | None]:
    """BTC bull/bear/neutral from 4h 200EMA + ADX>25."""
    snap = compute_regime_snapshot(candles_4h)
    return snap.state, snap.distance_from_ema_pct


def compute_regime_snapshot(candles_4h: list[Candle]) -> RegimeSnapshot:
    """Full regime snapshot: state, EMA distance, ADX."""
    if len(candles_4h) < 200:
        return RegimeSnapshot(None, None, None)

    closes = np.array([c.close for c in candles_4h], dtype=float)
    ema200 = _ema(closes, 200)
    price = float(closes[-1])
    if ema200 <= 0:
        return RegimeSnapshot(None, None, None)

    dist_pct = (price - ema200) / ema200 * 100.0
    adx_vals = _adx_series(candles_4h, 14)
    adx = float(adx_vals[-1]) if adx_vals else 0.0

    if adx <= 25:
        state = "neutral"
    elif price > ema200:
        state = "bull"
    else:
        state = "bear"

    return RegimeSnapshot(state, round(dist_pct, 4), round(adx, 4))


def compute_regime_age_bars(candles_4h: list[Candle]) -> int | None:
    """4h bars since the last BTC regime transition (0 = just changed)."""
    if len(candles_4h) < 200:
        return None

    states: list[str | None] = []
    for i in range(199, len(candles_4h)):
        states.append(compute_regime_snapshot(candles_4h[: i + 1]).state)

    current = states[-1]
    if current is None:
        return None

    age = 0
    for s in reversed(states):
        if s != current:
            break
        age += 1
    return max(age - 1, 0)


def compute_realized_vol_24h(candles_5m: list[Candle]) -> float | None:
    """Std of 5m log returns over last 288 bars (24h), annualized-ish scale as pct."""
    if len(candles_5m) < 48:
        return None
    closes = np.array([c.close for c in candles_5m[-288:]], dtype=float)
    if np.any(closes <= 0):
        return None
    rets = np.diff(np.log(closes))
    if len(rets) < 10:
        return None
    # Per-5m std expressed as % (x100), comparable across sessions
    return round(float(np.std(rets)) * 100.0, 6)
