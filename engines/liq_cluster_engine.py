"""
Liquidation Cluster Expansion Engine — Production V3.

Frozen parameters from research. Translates signals_liq_v2.py logic
into a production-safe engine operating on Candle lists and numpy arrays.

NO PANDAS DEPENDENCY at runtime.
"""
from __future__ import annotations

import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core.logging_setup import get_logger
from core.models import Candle, EngineType, Side, Signal

logger = get_logger("liq_cluster_engine")


# ═══════════════════════════════════════════════════
# Frozen V3 Config (do NOT modify)
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class V3Config:
    # Context (daily)
    liq_lookback: int = 90
    liq_percentile: float = 0.90
    liq_min_lookback: int = 30
    liq_window: int = 2
    require_short_squeeze: bool = True
    ret5d_min: float = -5.0

    # Entry confirmation (5m)
    range_lookback: int = 60
    imb_z_threshold: float = 2.0
    vol_z_threshold: float = 3.0
    body_strength_min: float = 0.60
    impulse_min_pct: float = 0.30
    ema_period: int = 20
    z_lookback: int = 100
    min_confirmations: int = 4

    # Selectivity
    cooldown_bars: int = 36
    no_reentry_after_stop: bool = True

    # Risk
    atr_period: int = 14
    initial_stop_atr: float = 2.5

    # Exits
    vol_trail_atr: float = 2.0
    struct_lookback: int = 12
    decay_threshold: float = 0.30
    partial_r: float = 2.5
    partial_fraction: float = 0.50
    max_hold_bars: int = 288


CFG = V3Config()


# ═══════════════════════════════════════════════════
# Per-Symbol State
# ═══════════════════════════════════════════════════

@dataclass
class SymbolState:
    """Mutable per-symbol state."""
    # Cascade context (updated daily)
    cascade_active: bool = False
    cascade_strength: float = 0.0
    liq_direction_imb: float = 0.0
    ret_5d: float = 0.0

    # Trade state
    cooldown: int = 0
    stopped_in_window: bool = False
    last_cascade_state: bool = False

    # Position tracking (if in trade)
    in_trade: bool = False
    entry_price: float = 0.0
    risk_per_unit: float = 0.0
    bars_held: int = 0
    partial_taken: bool = False
    best_price: float = 0.0
    vol_trail: float = 0.0
    struct_trail: float = 0.0

    # MAE/MFE tracking
    mae: float = 0.0  # max adverse excursion (negative)
    mfe: float = 0.0  # max favorable excursion (positive)


# ═══════════════════════════════════════════════════
# Helper Functions (numpy-based)
# ═══════════════════════════════════════════════════

def _ema(values: np.ndarray, span: int) -> float:
    """EMA of last `span` values, return most recent."""
    if len(values) < 2:
        return values[-1] if len(values) else 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
    """ATR using EMA method, return most recent."""
    if len(highs) < 2:
        return highs[0] - lows[0] if len(highs) else 0.0
    tr = np.empty(len(highs))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(highs)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return _ema(tr, period)


def _z_score(values: np.ndarray, lookback: int) -> float:
    """Z-score of last value vs rolling window."""
    if len(values) < 20:
        return 0.0
    window = values[-lookback:] if len(values) >= lookback else values
    mean = np.mean(window)
    std = np.std(window)
    if std < 1e-12:
        return 0.0
    return (values[-1] - mean) / std


# ═══════════════════════════════════════════════════
# Cascade Context (updated daily)
# ═══════════════════════════════════════════════════

class CascadeTracker:
    """Maintains daily cascade state for one symbol."""

    def __init__(self):
        self._liq_history: deque = deque(maxlen=CFG.liq_lookback + 5)
        # Each entry: (date_str, total_liq, long_liq, short_liq, close, ret_5d)

    def update(self, daily_rows: list[dict]) -> tuple[bool, float, float, float]:
        """
        Update with daily liq data.

        Each dict: {date, total_liq, long_liq, short_liq, close}
        Returns: (cascade_active, cascade_strength, liq_direction_imb, ret_5d)
        """
        for row in daily_rows:
            self._liq_history.append(row)

        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        liqs = [r["total_liq"] for r in self._liq_history]
        if len(liqs) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        # P90 threshold
        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p90 = np.percentile(lookback, CFG.liq_percentile * 100)
        if p90 <= 0:
            return False, 0.0, 0.0, 0.0

        # Check spike in last liq_window+1 days
        cascade_active = False
        for i in range(CFG.liq_window + 1):
            idx = -(i + 1)
            if abs(idx) <= len(liqs) and liqs[idx] > p90:
                cascade_active = True
                break

        # Strength
        strength = liqs[-1] / p90 if p90 > 0 else 0

        # Direction imbalance
        last = self._liq_history[-1]
        total = last.get("total_liq", 0)
        if total > 0:
            imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total
        else:
            imb = 0.0

        # 5-day return
        closes = [r.get("close", 0) for r in self._liq_history]
        if len(closes) >= 6 and closes[-6] > 0:
            ret_5d = ((closes[-1] / closes[-6]) - 1) * 100
        else:
            ret_5d = 0.0

        # Short-squeeze filter
        if CFG.require_short_squeeze and imb >= 0:
            cascade_active = False

        # Momentum filter
        if CFG.ret5d_min is not None and ret_5d <= CFG.ret5d_min:
            cascade_active = False

        return cascade_active, strength, imb, ret_5d


# ═══════════════════════════════════════════════════
# Main Engine
# ═══════════════════════════════════════════════════

class LiqClusterEngine:
    """
    Production V3 liq-cluster signal engine.

    Multi-symbol, maintains per-symbol state.
    Call update_daily_liq() once per UTC day.
    Call evaluate() on each 5m candle close.
    Call manage_position() for open position management.

    STATE OWNERSHIP:
        Engine owns:  entry_price, risk_per_unit, MAE/MFE, trailing stops,
                      cooldown, cascade context, bars_held, partial_taken
        Runner owns:  realized PnL, position sizing (qty/leverage), DB persistence
        Runner reads: MAE/MFE from engine exit payloads only (no duplicate tracking)
    """

    def __init__(self):
        self._states: dict[str, SymbolState] = {}
        self._cascades: dict[str, CascadeTracker] = {}

    def _get_state(self, symbol: str) -> SymbolState:
        if symbol not in self._states:
            self._states[symbol] = SymbolState()
        return self._states[symbol]

    def _get_cascade(self, symbol: str) -> CascadeTracker:
        if symbol not in self._cascades:
            self._cascades[symbol] = CascadeTracker()
        return self._cascades[symbol]

    def update_daily_liq(self, symbol: str, daily_rows: list[dict]) -> None:
        """
        Update cascade context for a symbol.
        Called once per UTC day with all available daily data.

        daily_rows: list of {date, total_liq, long_liq, short_liq, close}
        """
        ct = self._get_cascade(symbol)
        st = self._get_state(symbol)

        cascade_active, strength, imb, ret_5d = ct.update(daily_rows)

        # Track cascade transitions
        if cascade_active and not st.last_cascade_state:
            st.stopped_in_window = False  # reset on new cascade window
        st.last_cascade_state = cascade_active

        st.cascade_active = cascade_active
        st.cascade_strength = strength
        st.liq_direction_imb = imb
        st.ret_5d = ret_5d

        if cascade_active:
            logger.info(
                "Cascade active",
                symbol=symbol, strength=f"{strength:.2f}",
                imb=f"{imb:.2f}", ret_5d=f"{ret_5d:.1f}",
            )

    def evaluate(
        self,
        symbol: str,
        candles_5m: list[Candle],
    ) -> Optional[Signal]:
        """
        Evaluate entry signal on latest 5m candle close.
        Returns Signal if entry conditions met, None otherwise.
        """
        st = self._get_state(symbol)

        # Cooldown
        if st.cooldown > 0:
            st.cooldown -= 1
            return None

        # No re-entry after stop in same cascade window
        if CFG.no_reentry_after_stop and st.stopped_in_window:
            return None

        # Must have cascade active
        if not st.cascade_active:
            return None

        # Need enough candles
        n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
        if len(candles_5m) < n_needed:
            return None

        # Extract arrays
        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        volumes = np.array([c.volume for c in candles_5m])

        bar = candles_5m[-1]

        # ATR
        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            return None

        # EMA
        ema = _ema(closes, CFG.ema_period)

        # Range high (excluding current bar)
        range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1])) if len(highs) > CFG.range_lookback else float(np.max(highs[:-1]))

        # Volume z-score
        vol_z = _z_score(volumes, CFG.z_lookback)

        # Taker imbalance z-score (restored from Binance kline field 9)
        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        has_taker = taker_buys[-1] > 0
        if has_taker:
            taker_sells = volumes - taker_buys
            totals = taker_buys + taker_sells
            # Avoid division by zero
            safe_totals = np.where(totals > 0, totals, 1.0)
            imb_raw = (taker_buys - taker_sells) / safe_totals
            imb_z = _z_score(imb_raw, CFG.z_lookback)
        else:
            imb_z = 0.0

        # Body strength
        candle_range = bar.high - bar.low
        candle_body = abs(bar.close - bar.open)
        body_strength = candle_body / candle_range if candle_range > 0 else 0

        # Bar return %
        bar_return_pct = ((bar.close - bar.open) / bar.open * 100) if bar.open > 0 else 0

        # Score confirmations (true 6-confirmation stack)
        confirmations = {
            "breakout": bar.close > range_high,
            "imb": imb_z > CFG.imb_z_threshold if has_taker else False,
            "vol": vol_z > CFG.vol_z_threshold,
            "body": body_strength > CFG.body_strength_min,
            "impulse": bar_return_pct > CFG.impulse_min_pct,
            "momentum": bar.close > ema,
        }
        confirm_count = sum(1 for v in confirmations.values() if v)

        if confirm_count < CFG.min_confirmations:
            return None

        # Entry!
        entry_price = bar.close
        stop_distance = atr * CFG.initial_stop_atr
        stop_price = entry_price - stop_distance

        # Set trade state
        st.in_trade = True
        st.entry_price = entry_price
        st.risk_per_unit = stop_distance
        st.bars_held = 0
        st.partial_taken = False
        st.best_price = entry_price
        st.vol_trail = 0.0
        st.struct_trail = 0.0
        st.mae = 0.0
        st.mfe = 0.0
        st.cooldown = CFG.cooldown_bars

        signal = Signal(
            trade_uuid=str(uuid.uuid4()),
            engine=EngineType.LIQ_CLUSTER,
            symbol=symbol,
            side=Side.LONG,
            entry_price=entry_price,
            stop_price=stop_price,
            signal_data={
                "confirmations": confirmations,
                "confirm_count": confirm_count,
                "cascade_strength": st.cascade_strength,
                "liq_direction_imb": st.liq_direction_imb,
                "ret_5d": st.ret_5d,
                "imb_z": round(imb_z, 2),
                "vol_z": round(vol_z, 2),
                "body_strength": round(body_strength, 2),
                "bar_return_pct": round(bar_return_pct, 3),
                "atr": round(atr, 6),
            },
        )

        logger.info(
            "V3 signal",
            symbol=symbol,
            confirms=confirm_count,
            vol_z=round(vol_z, 2),
            strength=round(st.cascade_strength, 2),
        )

        return signal

    def manage_position(
        self,
        symbol: str,
        candle: Candle,
        candles_5m: list[Candle],
    ) -> Optional[dict]:
        """
        Manage an open position on 5m close.

        Returns action dict or None:
        - {"action": "partial", "fraction": 0.5, ...}
        - {"action": "close", "reason": "...", ...}
        - None = hold
        """
        st = self._get_state(symbol)
        if not st.in_trade:
            return None

        st.bars_held += 1
        price = candle.close

        # best_price tracks intra-candle high for expansion decay
        if candle.high > st.best_price:
            st.best_price = candle.high

        # MAE/MFE — engine is sole owner, uses fixed risk_per_unit from entry
        current_r = (price - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0
        low_r = (candle.low - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0
        high_r = (candle.high - st.entry_price) / st.risk_per_unit if st.risk_per_unit > 0 else 0

        if low_r < st.mae:
            st.mae = low_r
        if high_r > st.mfe:
            st.mfe = high_r

        # ATR for trails
        highs = np.array([c.high for c in candles_5m[-50:]])
        lows_arr = np.array([c.low for c in candles_5m[-50:]])
        closes = np.array([c.close for c in candles_5m[-50:]])
        atr = _atr(highs, lows_arr, closes, CFG.atr_period)

        # ── Stop loss ──
        stop_price = st.entry_price - st.risk_per_unit
        if candle.low <= stop_price:
            st.in_trade = False
            st.stopped_in_window = True
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close",
                "reason": "stop_loss",
                "exit_price": stop_price,
                "r": (stop_price - st.entry_price) / st.risk_per_unit,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Partial TP at 2.5R — trigger on wick (candle.high), not close ──
        if not st.partial_taken and high_r >= CFG.partial_r:
            st.partial_taken = True
            # Partial does NOT end the trade — position remains active
            return {
                "action": "partial",
                "fraction": CFG.partial_fraction,
                "reason": f"partial_{CFG.partial_r:.1f}R",
                "r": high_r,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Volatility trail ──
        new_vol_trail = price - atr * CFG.vol_trail_atr
        if new_vol_trail > st.vol_trail:
            st.vol_trail = new_vol_trail
        if st.vol_trail > st.entry_price and candle.low <= st.vol_trail:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close",
                "reason": "vol_trail",
                "exit_price": st.vol_trail,
                "r": (st.vol_trail - st.entry_price) / st.risk_per_unit,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Structure trail ──
        if len(candles_5m) >= CFG.struct_lookback:
            swing_low = min(c.low for c in candles_5m[-CFG.struct_lookback:])
            if swing_low > st.struct_trail:
                st.struct_trail = swing_low
            if st.struct_trail > st.entry_price and candle.low <= st.struct_trail:
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                return {
                    "action": "close",
                    "reason": "struct_trail",
                    "exit_price": st.struct_trail,
                    "r": (st.struct_trail - st.entry_price) / st.risk_per_unit,
                    "mae": st.mae,
                    "mfe": st.mfe,
                    "bars_held": st.bars_held,
                }

        # ── Expansion decay ──
        if st.bars_held > 6 and current_r > 0.5:
            peak_r = (st.best_price - st.entry_price) / st.risk_per_unit
            if peak_r > 0 and (current_r / peak_r) < (1 - CFG.decay_threshold):
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                return {
                    "action": "close",
                    "reason": "expansion_decay",
                    "exit_price": price,
                    "r": current_r,
                    "mae": st.mae,
                    "mfe": st.mfe,
                    "bars_held": st.bars_held,
                }

        # ── Time stop ──
        if st.bars_held >= CFG.max_hold_bars:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            return {
                "action": "close",
                "reason": "time_stop",
                "exit_price": price,
                "r": current_r,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        return None  # hold

    def get_btc_aligned(self, btc_candles_5m: list[Candle]) -> bool:
        """Check BTC momentum alignment for risk boost."""
        if len(btc_candles_5m) < 21:
            return False
        closes = np.array([c.close for c in btc_candles_5m])
        ema20 = _ema(closes, 20)
        above_ema = closes[-1] > ema20
        above_12_ago = closes[-1] > closes[-13] if len(closes) > 13 else False
        return above_ema and above_12_ago

    def reset_symbol(self, symbol: str) -> None:
        """Reset a symbol's trade state after position close."""
        st = self._get_state(symbol)
        st.in_trade = False
