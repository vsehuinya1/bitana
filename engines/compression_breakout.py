"""
Compression Breakout Engine

Detects volatility compression (ATR + BB + Volume) and breakout signals.
SOL priority. Uses closed candles only.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from config.loader import CompressionConfig
from core.logging_setup import get_logger
from core.models import Candle, EngineType, Side, Signal
from engines.base_engine import BaseEngine

logger = get_logger("compression_breakout")


def _atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Calculate ATR series."""
    if len(candles) < 2:
        return [0.0]
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)

    if not trs:
        return [0.0]

    atrs = []
    # SMA for first ATR
    if len(trs) < period:
        atrs = [sum(trs) / len(trs)] * len(trs)
        return atrs

    first_atr = sum(trs[:period]) / period
    atrs = [0.0] * (period - 1) + [first_atr]
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)
    return atrs


def _bollinger_width(candles: list[Candle], period: int = 20, std: float = 2.0) -> list[float]:
    """Calculate Bollinger Band width series (normalized by middle band)."""
    closes = [c.close for c in candles]
    widths = []
    for i in range(len(closes)):
        if i < period - 1:
            widths.append(float("inf"))
            continue
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        if mean == 0:
            widths.append(float("inf"))
            continue
        variance = sum((x - mean) ** 2 for x in window) / period
        std_dev = variance ** 0.5
        width = (2 * std * std_dev) / mean
        widths.append(width)
    return widths


def _volume_avg(candles: list[Candle], period: int = 20) -> list[float]:
    """Rolling volume average."""
    vols = [c.volume for c in candles]
    avgs = []
    for i in range(len(vols)):
        if i < period - 1:
            avgs.append(vols[i] if vols[i] > 0 else 1.0)
            continue
        window = vols[i - period + 1 : i + 1]
        avgs.append(sum(window) / period)
    return avgs


class CompressionBreakoutEngine(BaseEngine):
    """Detects compression zones and breakout signals."""

    def __init__(self, config: CompressionConfig) -> None:
        self._cfg = config

    @property
    def name(self) -> str:
        return "CompressionBreakout"

    async def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_15m: list[Candle],
        candles_1m: list[Candle],
    ) -> Optional[Signal]:
        """Evaluate for compression breakout signal."""
        cfg = self._cfg

        if len(candles_5m) < max(cfg.atr_lookback, cfg.bb_period, cfg.volume_avg_period, cfg.min_compression_candles + 5):
            return None

        # 1. ATR percentile check
        atrs = _atr(candles_5m, cfg.atr_period)
        if not atrs or atrs[-1] == 0:
            return None

        lookback = min(cfg.atr_lookback, len(atrs))
        recent_atrs = atrs[-lookback:]
        current_atr = atrs[-1]
        percentile = (sum(1 for a in recent_atrs if a <= current_atr) / len(recent_atrs)) * 100

        if percentile > cfg.atr_percentile_threshold:
            return None

        # 2. BB width contraction
        bb_widths = _bollinger_width(candles_5m, cfg.bb_period, cfg.bb_std)
        if not bb_widths or bb_widths[-1] == float("inf"):
            return None

        bb_lookback = min(cfg.atr_lookback, len(bb_widths))
        recent_bbs = [w for w in bb_widths[-bb_lookback:] if w != float("inf")]
        if not recent_bbs:
            return None
        bb_pct = (sum(1 for w in recent_bbs if w <= bb_widths[-1]) / len(recent_bbs)) * 100
        if bb_pct > cfg.bb_width_percentile_threshold:
            return None

        # 3. Volume below average
        vol_avgs = _volume_avg(candles_5m, cfg.volume_avg_period)
        # Check volume of candles in compression zone (excluding breakout candle)
        if candles_5m[-2].volume >= vol_avgs[-2]:
            return None

        # 4. Compression range — min N candles inside range
        range_candles = candles_5m[-(cfg.min_compression_candles + 1) : -1]
        if len(range_candles) < cfg.min_compression_candles:
            return None

        range_high = max(c.high for c in range_candles)
        range_low = min(c.low for c in range_candles)
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        # Verify all candles are inside the range
        for c in range_candles:
            if c.high > range_high * 1.001 or c.low < range_low * 0.999:
                return None

        # 5. Breakout candle check (most recent closed 5m candle)
        breakout = candles_5m[-1]

        # Volume confirmation
        vol_threshold = vol_avgs[-1] * cfg.breakout_volume_multiplier
        if breakout.volume < vol_threshold:
            return None

        # Wick rejection
        if breakout.body > 0 and breakout.total_range / breakout.body > cfg.max_wick_body_ratio:
            return None

        # Determine direction
        side: Optional[Side] = None
        stop_price = 0.0

        if breakout.close > range_high:
            # Long breakout — close above range high
            if breakout.close <= breakout.open:
                return None  # bearish candle, not a valid long breakout
            side = Side.LONG
            stop_price = min(range_low, breakout.close - 1.5 * current_atr)
            # Ensure stop makes sense
            stop_price = max(stop_price, breakout.close * 0.95)

        elif breakout.close < range_low:
            # Short breakout — close below range low
            if breakout.close >= breakout.open:
                return None  # bullish candle, not a valid short breakout
            side = Side.SHORT
            stop_price = max(range_high, breakout.close + 1.5 * current_atr)
            stop_price = min(stop_price, breakout.close * 1.05)

        if side is None:
            return None

        # 6. 1m confirmation (optional, check last few 1m candles)
        if candles_1m and len(candles_1m) >= 1:
            confirm = candles_1m[-1]
            if side == Side.LONG and confirm.close < breakout.close * 0.998:
                logger.debug("1m confirmation failed for LONG", symbol=symbol)
                return None
            if side == Side.SHORT and confirm.close > breakout.close * 1.002:
                logger.debug("1m confirmation failed for SHORT", symbol=symbol)
                return None

        signal = Signal(
            engine=EngineType.COMPRESSION,
            symbol=symbol,
            side=side,
            entry_price=breakout.close,
            stop_price=stop_price,
            signal_data={
                "atr": round(current_atr, 6),
                "atr_percentile": round(percentile, 1),
                "bb_width": round(bb_widths[-1], 6),
                "bb_percentile": round(bb_pct, 1),
                "range_high": range_high,
                "range_low": range_low,
                "range_candles": len(range_candles),
                "breakout_volume_ratio": round(breakout.volume / vol_avgs[-1], 2),
            },
        )

        logger.info(
            "Compression signal generated",
            symbol=symbol, side=side.value,
            entry=breakout.close, stop=stop_price,
            atr_pct=round(percentile, 1),
        )
        return signal
