"""
Swing Break Engine — SB_DATR_ASIA_HALF_WEAK

Encapsulates the exact replay-winning strategy for live forward-testing.
Self-contained: manages its own 4H/1D indicator state.

Entry: 15m swing high/low break with volume > 1.5× avg
Trend: 4H EMA20/50 (NEUTRAL = no trade)
Regime: Daily ATR(14) > 90-day median
Session: 00:00–08:00 UTC only (Asia)
Risk: 1R normal, 0.5R when regime weak (ADX<20 AND EMA slope flat)
"""
from __future__ import annotations

import bisect
from datetime import datetime, timezone
from typing import Optional

from core.logging_setup import get_logger
from core.models import Candle, EngineType, Side, Signal

logger = get_logger("swing_break_engine")


# ─── Indicator helpers ───────────────────────────────────────────────────────

def _ema(vals: list[float], p: int) -> list[float]:
    if not vals:
        return []
    k = 2.0 / (p + 1)
    o = [vals[0]]
    for v in vals[1:]:
        o.append(v * k + o[-1] * (1 - k))
    return o


def _atr_series(candles: list[Candle], period: int = 14) -> list[float]:
    n = len(candles)
    if n < 2:
        return [0.0] * n
    trs = [0.0]
    for i in range(1, n):
        pc = candles[i - 1].close
        c = candles[i]
        trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    atrs = [0.0] * n
    if len(trs) > period:
        a = sum(trs[1:period + 1]) / period
        atrs[period] = a
        for j in range(period + 1, len(trs)):
            a = (a * (period - 1) + trs[j]) / period
            atrs[j] = a
    return atrs


def _adx_series(candles: list[Candle], period: int = 14) -> list[float]:
    n = len(candles)
    if n < period * 2 + 1:
        return [0.0] * n
    pdm = [0.0]
    ndm = [0.0]
    trs = [0.0]
    for i in range(1, n):
        h = candles[i].high
        l = candles[i].low
        ph = candles[i - 1].high
        pl = candles[i - 1].low
        pc = candles[i - 1].close
        up = h - ph
        dn = pl - l
        pdm.append(up if up > dn and up > 0 else 0)
        ndm.append(dn if dn > up and dn > 0 else 0)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def smooth(vals, p):
        s = [0.0] * (p + 1)
        s[p] = sum(vals[1:p + 1])
        for i in range(p + 1, len(vals)):
            s.append(s[-1] - s[-1] / p + vals[i])
        return s

    s_pdm = smooth(pdm, period)
    s_ndm = smooth(ndm, period)
    s_tr = smooth(trs, period)

    dx_vals = [0.0] * n
    for i in range(period, n):
        if i >= len(s_tr) or s_tr[i] == 0:
            continue
        pdi = 100 * s_pdm[i] / s_tr[i] if i < len(s_pdm) else 0
        ndi = 100 * s_ndm[i] / s_tr[i] if i < len(s_ndm) else 0
        denom = pdi + ndi
        dx_vals[i] = abs(pdi - ndi) / denom * 100 if denom > 0 else 0

    adx = [0.0] * n
    si = period * 2
    if si < n:
        adx[si] = sum(dx_vals[period:si + 1]) / (period + 1)
        for i in range(si + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx_vals[i]) / period
    return adx


# ─── Engine ──────────────────────────────────────────────────────────────────

class SwingBreakEngine:
    """Self-contained Swing Break engine with built-in regime filtering."""

    REPLAY_EXPECTANCY = 0.0415  # benchmark from SB_DATR_ASIA 365d replay
    SESSION_START_UTC = 0       # Asia session
    SESSION_END_UTC = 8
    VOL_MULTIPLIER = 1.5
    VOL_AVG_PERIOD = 20
    SWING_LOOKBACK = 11
    ATR_PERIOD = 14

    def __init__(self):
        # 4H state
        self._c4h: list[Candle] = []
        self._ema20_4h: list[float] = []
        self._ema50_4h: list[float] = []
        self._adx_4h: list[float] = []
        self._4h_times: list[datetime] = []

        # 1D state
        self._c1d: list[Candle] = []
        self._datr: list[float] = []
        self._datr_medians: list[float] = []
        self._1d_times: list[datetime] = []

    # ── Public API ───────────────────────────────────────────────────────

    def update_4h(self, candles: list[Candle]) -> None:
        """Rebuild 4H indicators from full history."""
        self._c4h = sorted(candles, key=lambda c: c.close_time)
        closes = [c.close for c in self._c4h]
        self._ema20_4h = _ema(closes, 20)
        self._ema50_4h = _ema(closes, 50)
        self._adx_4h = _adx_series(self._c4h, 14)
        self._4h_times = [c.close_time for c in self._c4h]
        logger.info("4H indicators updated", count=len(self._c4h))

    def update_1d(self, candles: list[Candle]) -> None:
        """Rebuild 1D indicators from full history."""
        self._c1d = sorted(candles, key=lambda c: c.close_time)
        self._datr = _atr_series(self._c1d, 14)
        self._datr_medians = [0.0] * len(self._c1d)
        for i in range(90, len(self._c1d)):
            w = [a for a in self._datr[max(0, i - 90):i] if a > 0]
            if w:
                sw = sorted(w)
                self._datr_medians[i] = sw[len(sw) // 2]
        self._1d_times = [c.close_time for c in self._c1d]
        logger.info("1D indicators updated", count=len(self._c1d))

    def append_4h(self, candle: Candle) -> None:
        """Incrementally add a new 4H candle. Ignores duplicates."""
        if self._4h_times and candle.close_time <= self._4h_times[-1]:
            return
        self._c4h.append(candle)
        closes = [c.close for c in self._c4h]
        self._ema20_4h = _ema(closes, 20)
        self._ema50_4h = _ema(closes, 50)
        self._adx_4h = _adx_series(self._c4h, 14)
        self._4h_times.append(candle.close_time)

    def append_1d(self, candle: Candle) -> None:
        """Incrementally add a new 1D candle. Ignores duplicates."""
        if self._1d_times and candle.close_time <= self._1d_times[-1]:
            return
        self._c1d.append(candle)
        self._datr = _atr_series(self._c1d, 14)
        i = len(self._c1d) - 1
        if i >= 90:
            w = [a for a in self._datr[max(0, i - 90):i] if a > 0]
            if w:
                sw = sorted(w)
                self._datr_medians.append(sw[len(sw) // 2])
            else:
                self._datr_medians.append(0.0)
        else:
            self._datr_medians.append(0.0)
        self._1d_times.append(candle.close_time)

    def evaluate(
        self, candles_15m: list[Candle], at_time: datetime
    ) -> tuple[Optional[Signal], dict]:
        """
        Evaluate swing break conditions on 15m candles.

        Returns:
            (signal_or_none, criteria_dict)

        criteria_dict always returned — logs which conditions pass/fail:
            trend_aligned, regime_ok, session_ok, volume_ok, swing_break_ok
        """
        criteria = {
            "trend_aligned": False,
            "regime_ok": False,
            "session_ok": False,
            "volume_ok": False,
            "swing_break_ok": False,
            "skip_reason": None,
        }

        # Session gate
        h = at_time.hour
        if not (self.SESSION_START_UTC <= h < self.SESSION_END_UTC):
            criteria["skip_reason"] = "session_blocked"
            return None, criteria
        criteria["session_ok"] = True

        # Trend
        side = self._trend_side(at_time)
        if side is None:
            criteria["skip_reason"] = "no_trend"
            return None, criteria
        criteria["trend_aligned"] = True

        # Regime: DATR > median
        if not self._datr_above_median(at_time):
            criteria["skip_reason"] = "regime_weak"
            return None, criteria
        criteria["regime_ok"] = True

        # Swing break check
        if len(candles_15m) < 22:
            criteria["skip_reason"] = "insufficient_data"
            return None, criteria

        curr = candles_15m[-1]
        lookback = candles_15m[-12:-1]

        # Volume check
        vol_avg = sum(c.volume for c in candles_15m[-21:-1]) / self.VOL_AVG_PERIOD
        if vol_avg <= 0:
            criteria["skip_reason"] = "no_volume"
            return None, criteria

        vol_mult = curr.volume / vol_avg
        if vol_mult < self.VOL_MULTIPLIER:
            criteria["skip_reason"] = "low_volume"
            return None, criteria
        criteria["volume_ok"] = True

        # ATR for stop placement
        atr = self._calc_atr(candles_15m)
        if atr <= 0:
            criteria["skip_reason"] = "no_atr"
            return None, criteria

        # Swing break
        entry_price = curr.close
        stop_price = None

        if side == Side.LONG:
            sh = max(c.high for c in lookback)
            if curr.close > sh:
                stop_price = min(c.low for c in lookback[-5:]) - atr * 0.1
        else:
            sl = min(c.low for c in lookback)
            if curr.close < sl:
                stop_price = max(c.high for c in lookback[-5:]) + atr * 0.1

        if stop_price is None:
            criteria["skip_reason"] = "no_swing_break"
            return None, criteria

        # Validate stop makes sense
        if side == Side.LONG and stop_price >= entry_price:
            criteria["skip_reason"] = "invalid_stop"
            return None, criteria
        if side == Side.SHORT and stop_price <= entry_price:
            criteria["skip_reason"] = "invalid_stop"
            return None, criteria

        criteria["swing_break_ok"] = True
        criteria["skip_reason"] = None

        signal = Signal(
            engine=EngineType.SWING_BREAK,
            symbol="BTCUSDT",
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            signal_data={
                "atr": round(atr, 2),
                "vol_mult": round(vol_mult, 2),
                "regime": "STRONG" if self._regime_strong(at_time) else "WEAK",
                "criteria": criteria,
            },
        )
        return signal, criteria

    def get_risk_multiplier(self, at_time: datetime) -> float:
        """HALF_WEAK: 0.5 when regime weak, 1.0 when strong."""
        return 1.0 if self._regime_strong(at_time) else 0.5

    # ── Private ──────────────────────────────────────────────────────────

    def _4h_idx(self, t: datetime) -> int:
        idx = bisect.bisect_right(self._4h_times, t) - 1
        return max(0, idx)

    def _1d_idx(self, t: datetime) -> int:
        idx = bisect.bisect_right(self._1d_times, t) - 1
        return max(0, idx)

    def _trend_side(self, t: datetime) -> Optional[Side]:
        i = self._4h_idx(t)
        if i < 50 or not self._ema20_4h:
            return None
        if self._ema20_4h[i] > self._ema50_4h[i]:
            return Side.LONG
        if self._ema20_4h[i] < self._ema50_4h[i]:
            return Side.SHORT
        return None

    def _datr_above_median(self, t: datetime) -> bool:
        i = self._1d_idx(t)
        if i < 90:
            return True  # allow during warmup
        return (self._datr[i] > self._datr_medians[i]
                and self._datr_medians[i] > 0)

    def _regime_strong(self, t: datetime) -> bool:
        """ADX > 20 AND EMA20 slope positive (3 bars)."""
        i = self._4h_idx(t)
        if i < 53:
            return False
        adx_ok = self._adx_4h[i] > 20
        side = self._trend_side(t)
        if side is None:
            return False
        if side == Side.LONG:
            slope_ok = (self._ema20_4h[i] > self._ema20_4h[i - 1]
                        > self._ema20_4h[i - 2] > self._ema20_4h[i - 3])
        else:
            slope_ok = (self._ema20_4h[i] < self._ema20_4h[i - 1]
                        < self._ema20_4h[i - 2] < self._ema20_4h[i - 3])
        return adx_ok and slope_ok

    @staticmethod
    def _calc_atr(candles: list[Candle], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            pc = candles[i - 1].close
            c = candles[i]
            trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0.0
        a = sum(trs[:period]) / period
        for j in range(period, len(trs)):
            a = (a * (period - 1) + trs[j]) / period
        return a
