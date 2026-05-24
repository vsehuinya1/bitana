"""
Regime Filters

Modular environment filters. Each independently toggleable.
- 15m ATR minimum threshold
- Ultra-low volatility drift detection
- UTC session windows
- Event blackout windows
"""
from __future__ import annotations

from datetime import datetime, timezone

from config.loader import RegimeFiltersConfig
from core.logging_setup import get_logger
from core.models import Candle

logger = get_logger("regime_filter")


def _atr_single(candles: list[Candle], period: int = 14) -> float:
    """Calculate current ATR value."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


class RegimeFilter:
    """Checks if current market regime is tradeable."""

    def __init__(self, config: RegimeFiltersConfig) -> None:
        self._cfg = config

    def check(
        self,
        symbol: str,
        candles_15m: list[Candle],
        now_utc: datetime | None = None,
    ) -> tuple[bool, str]:
        """Check all regime filters.

        Returns (is_tradeable, reason_if_rejected).
        """
        if not self._cfg.enabled:
            return True, ""

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        # 1. ATR minimum
        if candles_15m and len(candles_15m) > 15:
            atr = _atr_single(candles_15m, 14)
            mid = candles_15m[-1].close
            if mid > 0:
                atr_frac = atr / mid
                if atr_frac < self._cfg.min_atr_15m:
                    return False, f"15m ATR too low: {atr_frac:.6f} < {self._cfg.min_atr_15m}"

        # 2. Ultra-low volatility drift
        if self._cfg.avoid_low_vol_drift and candles_15m and len(candles_15m) >= 10:
            recent = candles_15m[-10:]
            ranges = [c.total_range / c.close if c.close > 0 else 0 for c in recent]
            avg_range = sum(ranges) / len(ranges)
            if avg_range < self._cfg.min_atr_15m * 0.3:
                return False, f"Ultra-low vol drift: avg_range={avg_range:.6f}"

        # 3. Session filter
        if self._cfg.session_filter.enabled:
            hour = now_utc.hour
            if hour not in self._cfg.session_filter.allowed_utc_hours:
                return False, f"Outside allowed session: hour={hour}"

        # 4. Blackout windows
        for bw in self._cfg.blackout_windows:
            try:
                start = datetime.fromisoformat(bw.start.replace("Z", "+00:00"))
                end = datetime.fromisoformat(bw.end.replace("Z", "+00:00"))
                if start <= now_utc <= end:
                    return False, f"Blackout window: {bw.reason or 'scheduled'}"
            except (ValueError, AttributeError):
                continue

        return True, ""
