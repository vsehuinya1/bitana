"""
Liquidation Cluster Expansion Engine — Production V4.

V4 changes from V3 (confined to this file only):
  - Aggression score computed at entry (10-component composite)
  - Decile-specific exit parameters (trail width, decay sensitivity, max hold)
  - Consecutive red bar tracking for decay conditions
  - V3 engine is completely untouched in liq_cluster_engine.py

Revert: change import in forward tester from engines.liq_cluster_engine_v4
        back to engines.liq_cluster_engine (one line).
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

logger = get_logger("liq_cluster_engine_v4")


# ═══════════════════════════════════════════════════
# Frozen V4 Config
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class V4Config:
    # Context (daily) — same as V3
    liq_lookback: int = 90
    liq_percentile: float = 0.90
    liq_min_lookback: int = 30
    liq_window: int = 2
    require_short_squeeze: bool = True
    ret5d_min: float = -5.0

    # V4 regime filter: suppress cascades when recent liq is too low vs historical
    # min_regime_ratio: if recent 10-day mean / full lookback mean < this, block cascades
    # 0.0 = disabled. 0.30 = block when recent liq < 30% of historical mean
    min_regime_ratio: float = 0.30
    regime_lookback_days: int = 10

    # V4 minimum cascade strength gate: block entries when cascade is too weak
    # Strength = latest_liq / p90_lookback. Backtest minimum was ~1.3x (all trades above p90).
    # Set to 0.10 to block noise entries (e.g. strength 0.01) while keeping all backtest trades.
    # 0.0 = disabled.
    min_cascade_strength: float = 0.10

    # V4 trend filter: require price above/below daily EMA-20 for long/short entries
    # DISABLED: backtest shows this blocks +16.4R of winners. Our edge IS counter-trend.
    # require_trend_filter: if True, block long entries when close < daily EMA-20.
    require_trend_filter: bool = False
    daily_ema_period: int = 20

    # V4 per-symbol loss limit: disable pair after N consecutive stop_losses
    # This prevents bleeding on pairs that have changed regime (e.g. NEAR May 2026)
    # max_consecutive_stops: N (default 3). 0 = disabled.
    # When triggered, the pair is paused until a new cascade activates.
    max_consecutive_stops: int = 3

    # Entry confirmation (5m) — same as V3
    range_lookback: int = 60
    imb_z_threshold: float = 2.0
    vol_z_threshold: float = 3.0
    body_strength_min: float = 0.60
    impulse_min_pct: float = 0.30
    ema_period: int = 20
    z_lookback: int = 100
    min_confirmations: int = 4

    # Selectivity — same as V3
    cooldown_bars: int = 36
    no_reentry_after_stop: bool = True

    # Risk — same as V3
    atr_period: int = 14
    initial_stop_atr: float = 2.5

    # Exits — V4 defaults (used when decile lookup fails)
    vol_trail_atr: float = 2.0
    struct_lookback: int = 12
    decay_threshold: float = 0.30
    partial_r: float = 2.5
    partial_fraction: float = 0.50
    max_hold_bars: int = 288


CFG = V4Config()


# ═══════════════════════════════════════════════════
# V4 Exit Parameters by Aggression Decile
# ═══════════════════════════════════════════════════
# Format: (trail_atr_mult, decay_bars_min, decay_mfe_min, decay_pullback_atr, decay_consec_red, max_hold_bars)
# Refined from backtest results (agg_exit_v2_backtest.py)

EXIT_PARAMS_BY_DECILE: dict[int, tuple] = {
    1:  (3.0, 99999, 999.0, 999.0, 99, 500, 48),   # D1:  wide trail, NO decay, long hold, wide struct
    2:  (3.0, 99999, 999.0, 999.0, 99, 500, 48),   # D2:  wide trail, NO decay, long hold, wide struct
    3:  (2.0, 99999, 999.0, 999.0, 99, 288, 24),   # D3:  standard trail, NO decay, medium struct
    4:  (2.0, 15, 1.5, 0.6, 3, 288, 12),           # D4:  standard trail, relaxed decay
    5:  (2.0, 15, 1.5, 0.6, 3, 288, 12),           # D5:  standard trail, relaxed decay
    6:  (2.0, 12, 1.5, 0.6, 3, 288, 12),           # D6:  standard
    7:  (2.5, 20, 2.0, 0.8, 4, 358, 36),           # D7:  wide trail, suppressed decay, wider struct
    8:  (2.5, 20, 2.0, 0.8, 4, 358, 36),           # D8:  wide trail, suppressed decay, wider struct (36b = ~3h, matches backtest avg hold)
    9:  (1.5, 8,  1.5, 0.5, 3, 100, 8),            # D9:  tight trail, moderate decay
    10: (1.5, 8,  1.5, 0.5, 3, 100, 8),            # D10: tight trail, moderate decay
}


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
    daily_ema: float = 0.0
    trend_ok: bool = True  # False when price is on wrong side of daily EMA

    # Trade state
    cooldown: int = 0
    stopped_in_window: bool = False
    last_cascade_state: bool = False
    consecutive_stops: int = 0  # consecutive stop_losses for this symbol
    stop_cooldown: int = 0  # bars remaining in stop-triggered cooldown

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
    mae: float = 0.0
    mfe: float = 0.0

    # V4 additions
    aggression_score: float = 0.0
    decile: int = 5
    consecutive_red: int = 0


# ═══════════════════════════════════════════════════
# Helper Functions (numpy-based)
# ═══════════════════════════════════════════════════

def _ema(values: np.ndarray, span: int) -> float:
    if len(values) < 2:
        return values[-1] if len(values) else 0.0
    alpha = 2.0 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> float:
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
    if len(values) < 20:
        return 0.0
    window = values[-lookback:] if len(values) >= lookback else values
    mean = np.mean(window)
    std = np.std(window)
    if std < 1e-12:
        return 0.0
    return (values[-1] - mean) / std


# ═══════════════════════════════════════════════════
# Aggression Score (10 components)
# ═══════════════════════════════════════════════════

def _compute_aggression(candles_5m: list[Candle]) -> float:
    """
    Compute 10-component aggression score at the latest bar.
    Returns 0-100 score.
    """
    if len(candles_5m) < 25:
        return 0.0

    lookback = 20
    window = candles_5m[-(lookback + 1):]
    c = np.array([w.close for w in window])
    h = np.array([w.high for w in window])
    l = np.array([w.low for w in window])
    v = np.array([w.volume for w in window])
    o = np.array([w.open for w in window])

    scores = {}

    # 1. Taker imbalance z-score
    mid = (h + l) / 2
    denom = h - l
    denom[denom == 0] = 1e-10
    taker_imb = (c - mid) / denom
    recent_imb = taker_imb[-1]
    hist_imb = taker_imb[:-1]
    std_hist = np.std(hist_imb)
    scores['taker_imb_z'] = (recent_imb - np.mean(hist_imb)) / (std_hist + 1e-10)

    # 2. Delta persistence
    diffs = np.diff(c)
    sign = np.sign(diffs[-1])
    persistence = 0
    for d in reversed(diffs):
        if np.sign(d) == sign:
            persistence += 1
        else:
            break
    scores['delta_persistence'] = persistence / lookback

    # 3. Volume acceleration (OI proxy)
    vol_short = np.mean(v[-5:])
    vol_long = np.mean(v[:-5]) + 1e-10
    scores['oi_acceleration'] = (vol_short - vol_long) / vol_long

    # 4. Range expansion percentile
    ranges = h[:-1] - l[:-1]
    current_range = h[-1] - l[-1]
    scores['range_expansion_pctile'] = np.mean(current_range > ranges) if len(ranges) > 0 else 0.5

    # 5. Volume concentration
    vol_3 = np.sum(v[-3:])
    vol_10 = np.sum(v[-10:]) + 1e-10
    scores['volume_concentration'] = vol_3 / vol_10

    # 6. CLV
    range_hl = h[-1] - l[-1]
    if range_hl > 0:
        scores['clv'] = (c[-1] - l[-1]) / range_hl * 2 - 1
    else:
        scores['clv'] = 0

    # 7. Wick rejection
    upper_wick = h[-1] - max(c[-1], o[-1])
    lower_wick = min(c[-1], o[-1]) - l[-1]
    total_range = h[-1] - l[-1]
    if total_range > 0:
        if c[-1] > o[-1]:
            scores['wick_rejection'] = lower_wick / total_range
        else:
            scores['wick_rejection'] = upper_wick / total_range
    else:
        scores['wick_rejection'] = 0

    # 8. Spread expansion
    avg_range = np.mean(ranges) + 1e-10
    scores['spread_expansion'] = (current_range - avg_range) / avg_range

    # 9. Velocity
    scores['velocity'] = (c[-1] - c[0]) / (np.mean(ranges) * np.sqrt(lookback) + 1e-10)

    # 10. Cascade intensity
    vol_z = (v[-1] - np.mean(v[:-1])) / (np.std(v[:-1]) + 1e-10)
    range_z = (current_range - np.mean(ranges)) / (np.std(ranges) + 1e-10) if len(ranges) > 0 and np.std(ranges) > 0 else 0
    scores['cascade_intensity'] = (vol_z + range_z) / 2

    weights = {
        'taker_imb_z': 0.10, 'delta_persistence': 0.10, 'oi_acceleration': 0.08,
        'range_expansion_pctile': 0.15, 'volume_concentration': 0.10, 'clv': 0.07,
        'wick_rejection': 0.08, 'spread_expansion': 0.10, 'velocity': 0.07,
        'cascade_intensity': 0.15,
    }
    composite = sum(scores.get(k, 0) * w for k, w in weights.items())
    return max(0, min(100, (composite + 2) / 4 * 100))


def _score_to_decile(score: float) -> int:
    """
    Map aggression score to decile using boundaries derived from FULL baseline backtest.
    Boundaries from final_baseline_trades.csv (386 trades, Feb-Apr 2026):
      D1: ≤68.2, D2: ≤71.4, D3: ≤73.6, D4: ≤75.2, D5: ≤77.2,
      D6: ≤78.8, D7: ≤80.8, D8: ≤82.5, D9: ≤84.1, D10: >84.1
    Score range in backtest: 61.9-90.4 (all scores ×100 from 0-1 normalized).
    These boundaries ensure 95-100% alignment between backtest decile labels and live assignment.
    """
    if score <= 68.2:
        return 1
    elif score <= 71.4:
        return 2
    elif score <= 73.6:
        return 3
    elif score <= 75.2:
        return 4
    elif score <= 77.2:
        return 5
    elif score <= 78.8:
        return 6
    elif score <= 80.8:
        return 7
    elif score <= 82.5:
        return 8
    elif score <= 84.1:
        return 9
    else:
        return 10


# ═══════════════════════════════════════════════════
# Cascade Context (updated daily)
# ═══════════════════════════════════════════════════

class CascadeTracker:
    """Maintains daily cascade state for one symbol."""

    def __init__(self):
        self._liq_history: deque = deque(maxlen=CFG.liq_lookback + 5)

    def update(self, daily_rows: list[dict]) -> tuple[bool, float, float, float]:
        for row in daily_rows:
            self._liq_history.append(row)

        if len(self._liq_history) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        liqs = [r["total_liq"] for r in self._liq_history]
        if len(liqs) < CFG.liq_min_lookback:
            return False, 0.0, 0.0, 0.0

        lookback = liqs[-CFG.liq_lookback:] if len(liqs) >= CFG.liq_lookback else liqs
        p90 = np.percentile(lookback, CFG.liq_percentile * 100)
        if p90 <= 0:
            return False, 0.0, 0.0, 0.0

        cascade_active = False
        for i in range(CFG.liq_window + 1):
            idx = -(i + 1)
            if abs(idx) <= len(liqs) and liqs[idx] > p90:
                cascade_active = True
                break

        strength = liqs[-1] / p90 if p90 > 0 else 0

        # V4 regime filter: suppress cascades when recent liq is too low vs historical
        if CFG.min_regime_ratio > 0 and len(liqs) >= CFG.regime_lookback_days:
            recent_mean = np.mean(liqs[-CFG.regime_lookback_days:])
            full_mean = np.mean(liqs)
            if full_mean > 0 and recent_mean / full_mean < CFG.min_regime_ratio:
                cascade_active = False

        last = self._liq_history[-1]
        total = last.get("total_liq", 0)
        imb = (last.get("long_liq", 0) - last.get("short_liq", 0)) / total if total > 0 else 0.0

        closes_hist = [r.get("close", 0) for r in self._liq_history]
        if len(closes_hist) >= 6 and closes_hist[-6] > 0:
            ret_5d = ((closes_hist[-1] / closes_hist[-6]) - 1) * 100
        else:
            ret_5d = 0.0

        if CFG.require_short_squeeze and imb >= 0:
            cascade_active = False
        if CFG.ret5d_min is not None and ret_5d <= CFG.ret5d_min:
            cascade_active = False

        return cascade_active, strength, imb, ret_5d


# ═══════════════════════════════════════════════════
# V4 Engine
# ═══════════════════════════════════════════════════

class LiqClusterEngineV4:
    """
    V4 Liq-Cluster Engine with aggression-decile-specific exits.

    Changes from V3 (LiqClusterEngine):
      - Aggression score computed at entry
      - Decile-specific trail width, decay sensitivity, max hold
      - Consecutive red bar tracking for decay conditions
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

    def update_daily_liq(self, symbol: str, daily_rows: list[dict]):
        """Update daily cascade context for a symbol."""
        ct = self._get_cascade(symbol)
        st = self._get_state(symbol)
        cascade_active, strength, imb, ret_5d = ct.update(daily_rows)

        if cascade_active and not st.last_cascade_state:
            st.stopped_in_window = False
            # V4: reset consecutive stops when a new cascade activates
            st.consecutive_stops = 0
            st.stop_cooldown = 0
        st.last_cascade_state = cascade_active
        st.cascade_active = cascade_active
        st.cascade_strength = strength
        st.liq_direction_imb = imb
        st.ret_5d = ret_5d

        # V4 trend filter: compute daily EMA and check trend alignment
        if CFG.require_trend_filter and len(ct._liq_history) >= CFG.daily_ema_period:
            daily_closes = np.array([r.get("close", 0) for r in ct._liq_history])
            st.daily_ema = _ema(daily_closes, CFG.daily_ema_period)
            latest_close = daily_closes[-1]
            # For long entries (short_squeeze = imb < 0), require close > EMA
            # For short entries (imb > 0), require close < EMA
            if imb < 0:  # long setup — short squeeze
                st.trend_ok = latest_close > st.daily_ema
            else:  # short setup
                st.trend_ok = latest_close < st.daily_ema
        else:
            st.trend_ok = True

        if cascade_active:
            logger.info(
                "Cascade active",
                symbol=symbol, strength=f"{strength:.2f}",
                imb=f"{imb:.2f}", ret_5d=f"{ret_5d:.1f}",
                ema=f"{st.daily_ema:.4f}", trend_ok=st.trend_ok,
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

        if st.cooldown > 0:
            st.cooldown -= 1
            return None

        if CFG.no_reentry_after_stop and st.stopped_in_window:
            return None

        if not st.cascade_active:
            return None

        # V4: minimum cascade strength gate — block entries on weak cascades
        if CFG.min_cascade_strength > 0 and st.cascade_strength < CFG.min_cascade_strength:
            return None

        # V4: trend filter — block counter-trend entries
        if CFG.require_trend_filter and not st.trend_ok:
            return None

        # V4: per-symbol loss limit — block after consecutive stop_losses
        if CFG.max_consecutive_stops > 0 and st.stop_cooldown > 0:
            return None

        n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
        if len(candles_5m) < n_needed:
            return None

        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        volumes = np.array([c.volume for c in candles_5m])

        bar = candles_5m[-1]

        atr = _atr(highs, lows, closes, CFG.atr_period)
        if atr <= 0:
            return None

        ema = _ema(closes, CFG.ema_period)

        if len(highs) > CFG.range_lookback:
            range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
        else:
            range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]

        vol_z = _z_score(volumes, CFG.z_lookback)

        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        has_taker = taker_buys[-1] > 0
        if has_taker:
            taker_sells = volumes - taker_buys
            totals = taker_buys + taker_sells
            safe_totals = np.where(totals > 0, totals, 1.0)
            imb_raw = (taker_buys - taker_sells) / safe_totals
            imb_z = _z_score(imb_raw, CFG.z_lookback)
        else:
            imb_z = 0.0

        candle_range = bar.high - bar.low
        candle_body = abs(bar.close - bar.open)
        body_strength = candle_body / candle_range if candle_range > 0 else 0
        bar_return_pct = ((bar.close - bar.open) / bar.open * 100) if bar.open > 0 else 0

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

        # V4: compute aggression score and decile at entry
        aggression_score = _compute_aggression(candles_5m)
        decile = _score_to_decile(aggression_score)

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
        st.aggression_score = aggression_score
        st.decile = decile
        st.consecutive_red = 0

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
                # V4 additions
                "aggression_score": round(aggression_score, 2),
                "decile": decile,
            },
        )

        logger.info(
            "V4 signal",
            symbol=symbol,
            confirms=confirm_count,
            vol_z=round(vol_z, 2),
            strength=round(st.cascade_strength, 2),
            aggression=round(aggression_score, 1),
            decile=decile,
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
        Uses V4 decile-specific exit parameters.
        """
        st = self._get_state(symbol)
        if not st.in_trade:
            return None

        st.bars_held += 1
        price = candle.close

        # Track consecutive red bars (V4)
        if price <= candle.open:
            st.consecutive_red += 1
        else:
            st.consecutive_red = 0

        if candle.high > st.best_price:
            st.best_price = candle.high

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

        # ── V4: Get decile-specific exit parameters ──
        decile = st.decile
        trail_atr, decay_bars, decay_mfe, decay_pullback, decay_red, max_hold, struct_lb = EXIT_PARAMS_BY_DECILE.get(
            decile, (2.0, 8, 1.5, 0.3, 99, 288, 12)  # fallback to V3-like
        )

        # ── Stop loss ──
        stop_price = st.entry_price - st.risk_per_unit
        if candle.low <= stop_price:
            st.in_trade = False
            st.stopped_in_window = True
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops += 1
            if st.consecutive_stops >= CFG.max_consecutive_stops:
                st.stop_cooldown = 999999  # effectively permanent until reset
            return {
                "action": "close",
                "reason": "stop_loss",
                "exit_price": stop_price,
                "r": (stop_price - st.entry_price) / st.risk_per_unit,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Partial TP at 2.5R — trigger on wick ──
        if not st.partial_taken and high_r >= CFG.partial_r:
            st.partial_taken = True
            return {
                "action": "partial",
                "fraction": CFG.partial_fraction,
                "reason": f"partial_{CFG.partial_r:.1f}R",
                "r": high_r,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Volatility trail (V4: decile-specific width) ──
        new_vol_trail = price - atr * trail_atr
        if new_vol_trail > st.vol_trail:
            st.vol_trail = new_vol_trail
        if st.vol_trail > st.entry_price and candle.low <= st.vol_trail:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops = 0
            return {
                "action": "close",
                "reason": "vol_trail",
                "exit_price": st.vol_trail,
                "r": (st.vol_trail - st.entry_price) / st.risk_per_unit,
                "mae": st.mae,
                "mfe": st.mfe,
                "bars_held": st.bars_held,
            }

        # ── Structure trail (V4: decile-specific lookback) ──
        if len(candles_5m) >= struct_lb:
            swing_low = min(c.low for c in candles_5m[-struct_lb:])
            if swing_low > st.struct_trail:
                st.struct_trail = swing_low
            if st.struct_trail > st.entry_price and candle.low <= st.struct_trail:
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                st.consecutive_stops = 0
                return {
                    "action": "close",
                    "reason": "struct_trail",
                    "exit_price": st.struct_trail,
                    "r": (st.struct_trail - st.entry_price) / st.risk_per_unit,
                    "mae": st.mae,
                    "mfe": st.mfe,
                    "bars_held": st.bars_held,
                }

        # ── Expansion decay (V4: decile-specific) ──
        # Only check if decay is enabled for this decile (decay_bars < 99999)
        if decay_bars < 99999:
            if st.bars_held >= decay_bars and current_r > 0.5:
                peak_r = (st.best_price - st.entry_price) / st.risk_per_unit
                if peak_r >= decay_mfe and st.consecutive_red >= decay_red:
                    pullback_r = (st.best_price - price) / st.risk_per_unit
                    if pullback_r >= decay_pullback:
                        st.in_trade = False
                        st.cooldown = CFG.cooldown_bars
                        st.consecutive_stops = 0
                        return {
                            "action": "close",
                            "reason": "expansion_decay",
                            "exit_price": price,
                            "r": current_r,
                            "mae": st.mae,
                            "mfe": st.mfe,
                            "bars_held": st.bars_held,
                        }

        # ── Time stop (V4: decile-specific) ──
        if st.bars_held >= max_hold:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops = 0
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
        if len(btc_candles_5m) < 21:
            return False
        closes = np.array([c.close for c in btc_candles_5m])
        ema20 = _ema(closes, 20)
        above_ema = closes[-1] > ema20
        above_12_ago = closes[-1] > closes[-13] if len(closes) > 13 else False
        return above_ema and above_12_ago

    def reset_symbol(self, symbol: str) -> None:
        st = self._get_state(symbol)
        st.in_trade = False
