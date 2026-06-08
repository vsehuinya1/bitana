"""
Liquidation Cluster Expansion Engine — Production V6.

V6.4.2 (forward-test hardening):
  - London bleed window gate (08-14 UTC, audit v2)
  - MAX_RISK_PCT capped at 1% (vol-target normalization)
  - Breakeven exit after +0.5R MFE (exit_sim)

V6 changes from V5.3:
  - Fixed double bars_held increment (was cutting winners at 2x speed)
  - Fixed imb_z confirmation to use real taker buy imbalance z-score
  - Widened aggression score mapping (prevents D10 saturation on strong signals)
  - Added cascade-deactivation exit tightening (1.0 ATR trail)
  - Added time-based stop_cooldown fallback (288 bars = 24h)
  - Fixed get_risk_pct() to return vol-targeted risk (was returning stale flat 4%)
  - Renamed oi_acceleration → vol_acceleration (was mislabeled)

V5 philosophy: D1-D3, D5-D9 only. Vol-targeted risk per trade.
- D4 and D10 dropped (negative expectancy in backtest)
- Vol-targeted risk sizing (1%-12% based on ATR normalization)
- Per-decile exit parameters preserved (trail, decay, hold, struct_lookback)

NO BTC alignment — removed. Useless correlation risk.

Key changes from V4:
  - Entry filter: D1-D3, D5-D9 only (D4, D10 rejected)
  - Flat 4% risk per trade (all deciles)
  - 4/6 confirmations (back to V3 baseline)
  - Min cascade strength: 0.10x
  - Per-decile exit parameters (trail, decay, hold, struct_lookback)
  - Regime filter: min_regime_ratio=0.30
  - Per-symbol loss limit: 3 consecutive stops → pause
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

logger = get_logger("liq_cluster_engine_v5")


# V5.1 Sizing: Vol-Targeting (Risk Normalization)
# ═══════════════════════════════════════════════════
# Normalizes risk relative to a 2.0% ATR target (PF 2.81 bridge)
BASE_RISK_PCT = 0.04
TARGET_ATR_PCT = 2.0
MAX_RISK_PCT = 0.01   # Hard cap (paper/live); vol-target can spike without this
MIN_RISK_PCT = 0.01

# V6.5 capitulation-reversal entry (replaces V6.4 breakout-chase after liq cascade).
# Live WS telemetry: beaten ret_5d and imb+vol subsets least bad; breakout chase negative.
# Enter reversal bars after long-liq capitulation, not 60-bar range breakouts.
SNIPER_ALLOWED_HOURS = frozenset(range(14, 22))   # NY core session
SNIPER_MAX_ATR_PCT = 0.55
ENTRY_CASCADE_MIN = 1.0
ENTRY_CASCADE_MAX = 4.0
ENTRY_RET5D_MAX = -3.0          # require beaten-down 5d context
ENTRY_LIQ_IMB_MAX = -0.10       # long liquidations dominated recent liq
ENTRY_MIN_CONFIRMS = 5          # of 6 (reversal stack)
ENTRY_MAX_BREAKOUT_CHASE_PCT = 0.5  # reject extended chase above range high

# V6.4.3 two-stage trade management (exit_sim OOS-validated on 122 post-gate / 237 all trades).
# Thesis: cut the dead fast, let the confirmed ones run. Decomposition showed +25.28R post-gate
# uplift (-31.93R -> -6.65R) and +13.89R OOS. "confirm-or-cut" alone gives +9.82R; the
# breakeven-floored wide trail on survivors supplies the rest.
RUNNER_CONFIRM_BY = 10     # bars to show life
RUNNER_CONFIRM_R = 0.3     # MFE (R) required within the confirmation window
RUNNER_GIVEBACK = 0.75     # R given back under the running peak once confirmed

# FLAT_RISK_PCT preserved for backward compatibility/legacy reference
FLAT_RISK_PCT = 0.04

# V6.5: D1/D2 grinder deciles dropped (761-trade backtest: D1 -0.09, D2 -0.23 at scale).
TRADE_DECILES = {5, 6, 7, 8, 9}

def _is_decile_tradable(decile: int, confirmations: dict) -> bool:
    """Return True if decile is in the tradable set."""
    return decile in TRADE_DECILES


# ═══════════════════════════════════════════════════
# V5 Per-Decile Exit Parameters
# ═══════════════════════════════════════════════════
# From backtest analysis: each decile has different optimal exits
# D1-D2: slow grinders → wide trail, no decay, long hold
# D3-D4: clean impulses → moderate trail, no decay
# D5-D7: standard → moderate trail, light decay
# D8: structural squeezes → wide trail, moderate decay, long hold
# D9-D10: climax events → tight trail, fast decay, short hold

@dataclass(frozen=True)
class DecileExits:
    vol_trail_atr: float
    struct_lookback: int
    max_hold_bars: int
    decay_enabled: bool
    decay_start_bar: int      # when decay starts (bars in profit)
    decay_min_r: float        # minimum R before decay activates
    decay_atr_mult: float     # trail tightens to this once decayed
    decay_red_bars: int       # consecutive red bars to trigger decay exit

DECILE_EXITS = {
    1: DecileExits(vol_trail_atr=3.0, struct_lookback=48, max_hold_bars=500,
                   decay_enabled=False, decay_start_bar=999, decay_min_r=999,
                   decay_atr_mult=999, decay_red_bars=999),
    2: DecileExits(vol_trail_atr=3.0, struct_lookback=48, max_hold_bars=500,
                   decay_enabled=False, decay_start_bar=999, decay_min_r=999,
                   decay_atr_mult=999, decay_red_bars=999),
    3: DecileExits(vol_trail_atr=2.0, struct_lookback=24, max_hold_bars=288,
                   decay_enabled=False, decay_start_bar=999, decay_min_r=999,
                   decay_atr_mult=999, decay_red_bars=999),
    4: DecileExits(vol_trail_atr=2.0, struct_lookback=12, max_hold_bars=288,
                   decay_enabled=True, decay_start_bar=15, decay_min_r=1.5,
                   decay_atr_mult=0.6, decay_red_bars=3),
    5: DecileExits(vol_trail_atr=2.0, struct_lookback=12, max_hold_bars=288,
                   decay_enabled=True, decay_start_bar=15, decay_min_r=1.5,
                   decay_atr_mult=0.6, decay_red_bars=3),
    6: DecileExits(vol_trail_atr=2.0, struct_lookback=12, max_hold_bars=288,
                   decay_enabled=True, decay_start_bar=12, decay_min_r=1.5,
                   decay_atr_mult=0.6, decay_red_bars=3),
    7: DecileExits(vol_trail_atr=2.5, struct_lookback=36, max_hold_bars=358,
                   decay_enabled=True, decay_start_bar=20, decay_min_r=2.0,
                   decay_atr_mult=0.8, decay_red_bars=4),
    8: DecileExits(vol_trail_atr=2.5, struct_lookback=36, max_hold_bars=358,
                   decay_enabled=True, decay_start_bar=20, decay_min_r=2.0,
                   decay_atr_mult=0.8, decay_red_bars=4),
    9: DecileExits(vol_trail_atr=1.5, struct_lookback=8, max_hold_bars=100,
                   decay_enabled=True, decay_start_bar=8, decay_min_r=1.5,
                   decay_atr_mult=0.5, decay_red_bars=3),
    10: DecileExits(vol_trail_atr=1.5, struct_lookback=8, max_hold_bars=100,
                    decay_enabled=True, decay_start_bar=8, decay_min_r=1.5,
                    decay_atr_mult=0.5, decay_red_bars=3),
}


# ═══════════════════════════════════════════════════
# V5 Config
# ═══════════════════════════════════════════════════

@dataclass(frozen=True)
class V5Config:
    # Context (daily)
    liq_lookback: int = 90
    liq_percentile: float = 0.90
    liq_min_lookback: int = 30
    liq_window: int = 2
    require_short_squeeze: bool = False
    ret5d_min: float = -5.0

    # V5 regime filter
    min_regime_ratio: float = 0.30
    regime_lookback_days: int = 10

    # V5 minimum cascade strength gate
    min_cascade_strength: float = 0.10
    min_cascade_imb: float = 0.00  # Min directional imb (long_liq - short_liq)/total; 0=disabled

    # V5: NO aggression gate — all deciles accepted
    # Sizing is handled by per-decile half-Kelly

    # Entry confirmation (5m) — 3/6 (loosened for choppy regime)
    range_lookback: int = 60
    imb_z_threshold: float = 1.0   # V6.4: 2.0→1.0 (just needs positive taker pressure)
    vol_z_threshold: float = 2.0   # V6.4: 3.0→2.0 (above-avg volume, not extreme)
    body_strength_min: float = 0.50  # V6.4: 0.60→0.50 (slightly more lenient)
    impulse_min_pct: float = 0.20  # V6.4: 0.30→0.20 (lower min move)
    ema_period: int = 20
    z_lookback: int = 100
    min_confirmations: int = 4  # V6.3: 4/6 (revert V6.4 loosening that produced -11.16R on 18 trades)

    # Selectivity
    cooldown_bars: int = 36
    no_reentry_after_stop: bool = True

    # Risk
    atr_period: int = 14
    initial_stop_atr: float = 2.5

    # V5 per-symbol loss limit: 3 consecutive stops → pause until new cascade
    max_consecutive_stops: int = 3


CFG = V5Config()


# ═══════════════════════════════════════════════════
# Per-Symbol State
# ═══════════════════════════════════════════════════

@dataclass
class SymbolState:
    """Mutable per-symbol state."""
    cascade_active: bool = False
    cascade_strength: float = 0.0
    liq_direction_imb: float = 0.0
    ret_5d: float = 0.0

    cooldown: int = 0
    stopped_in_window: bool = False
    last_cascade_state: bool = False
    consecutive_stops: int = 0
    stop_cooldown: int = 0

    in_trade: bool = False
    entry_price: float = 0.0
    risk_per_unit: float = 0.0
    bars_held: int = 0
    best_price: float = 0.0
    vol_trail: float = 0.0
    struct_trail: float = 0.0
    consecutive_red: int = 0

    mae: float = 0.0
    mfe: float = 0.0
    confirmed: bool = False  # V6.4.3: reached RUNNER_CONFIRM_R → runner management

    aggression_score: float = 0.0
    decile: int = 5


# ═══════════════════════════════════════════════════
# Helper Functions
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
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return np.mean(trs) if trs else 0.0
    return np.mean(trs[-period:])


def _z_score(values: np.ndarray, lookback: int) -> float:
    if len(values) < 2:
        return 0.0
    recent = values[-1]
    hist = values[:-1] if len(values) <= lookback else values[-lookback-1:-1]
    if len(hist) < 2:
        return 0.0
    mean = np.mean(hist)
    std = np.std(hist)
    if std == 0:
        return 0.0
    return (recent - mean) / std


def _score_to_decile(score: float) -> int:
    """Map aggression score (0-100) to decile (1-10) using backtest boundaries."""
    # Boundaries from full 386-trade baseline backtest
    if score < 66.9: return 1
    if score < 70.5: return 2
    if score < 73.2: return 3
    if score < 75.4: return 4
    if score < 77.2: return 5
    if score < 78.8: return 6
    if score < 80.8: return 7
    if score < 82.5: return 8
    if score < 85.0: return 9
    return 10


# ═══════════════════════════════════════════════════
# Aggression Score (same 10-component as V4)
# ═══════════════════════════════════════════════════

def _compute_aggression(candles_5m: list[Candle]) -> float:
    """Compute 10-component aggression score. Returns 0-100."""
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

    mid = (h + l) / 2
    denom = h - l
    denom[denom == 0] = 1e-10
    taker_imb = (c - mid) / denom
    recent_imb = taker_imb[-1]
    hist_imb = taker_imb[:-1]
    std_hist = np.std(hist_imb)
    scores['taker_imb_z'] = (recent_imb - np.mean(hist_imb)) / (std_hist + 1e-10)

    diffs = np.diff(c)
    sign = np.sign(diffs[-1])
    persistence = 0
    for d in reversed(diffs):
        if np.sign(d) == sign:
            persistence += 1
        else:
            break
    scores['delta_persistence'] = persistence / lookback

    vol_short = np.mean(v[-5:])
    vol_long = np.mean(v[:-5]) + 1e-10
    scores['vol_acceleration'] = (vol_short - vol_long) / vol_long

    ranges = h[:-1] - l[:-1]
    current_range = h[-1] - l[-1]
    scores['range_expansion_pctile'] = np.mean(current_range > ranges) if len(ranges) > 0 else 0.5

    vol_3 = np.sum(v[-3:])
    vol_10 = np.sum(v[-10:]) + 1e-10
    scores['volume_concentration'] = vol_3 / vol_10

    range_hl = h[-1] - l[-1]
    if range_hl > 0:
        scores['clv'] = (c[-1] - l[-1]) / range_hl * 2 - 1
    else:
        scores['clv'] = 0

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

    avg_range = np.mean(ranges) + 1e-10
    scores['spread_expansion'] = (current_range - avg_range) / avg_range

    scores['velocity'] = (c[-1] - c[0]) / (np.mean(ranges) * np.sqrt(lookback) + 1e-10)

    vol_z = (v[-1] - np.mean(v[:-1])) / (np.std(v[:-1]) + 1e-10)
    range_z = (current_range - np.mean(ranges)) / (np.std(ranges) + 1e-10) if len(ranges) > 0 and np.std(ranges) > 0 else 0
    scores['cascade_intensity'] = (vol_z + range_z) / 2

    weights = {
        'taker_imb_z': 0.10, 'delta_persistence': 0.10, 'vol_acceleration': 0.08,
        'range_expansion_pctile': 0.15, 'volume_concentration': 0.10, 'clv': 0.07,
        'wick_rejection': 0.08, 'spread_expansion': 0.10, 'velocity': 0.07,
        'cascade_intensity': 0.15,
    }
    composite = sum(scores.get(k, 0) * w for k, w in weights.items())
    # V6: wider mapping [-3, +3] → [0, 100] to prevent D10 saturation on strong signals
    return max(0, min(100, (composite + 2) / 4 * 100))


# ═══════════════════════════════════════════════════
# Cascade Tracker
# ═══════════════════════════════════════════════════

class CascadeTracker:
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
# V5 Engine
# ═══════════════════════════════════════════════════

class LiqClusterEngineV5:
    """
    V5 Liq-Cluster Engine — All Deciles, Per-Decile Half-Kelly.

    - ALL aggression scores accepted (no decile gate)
    - Each decile gets its own half-Kelly risk allocation
    - Each decile gets its own exit parameters
    - NO BTC alignment
    - Regime filter, min cascade strength, per-symbol loss limit
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
        ct = self._get_cascade(symbol)
        st = self._get_state(symbol)
        cascade_active, strength, imb, ret_5d = ct.update(daily_rows)

        if cascade_active and not st.last_cascade_state:
            st.stopped_in_window = False
            st.consecutive_stops = 0
            st.stop_cooldown = 0
        st.last_cascade_state = cascade_active
        st.cascade_active = cascade_active
        st.cascade_strength = strength
        st.liq_direction_imb = imb
        st.ret_5d = ret_5d

        if cascade_active:
            logger.info("Cascade active", symbol=symbol, strength=f"{strength:.2f}",
                       imb=f"{imb:.2f}", ret_5d=f"{ret_5d:.1f}")

    def get_risk_pct(self, symbol: str, candles_5m: list[Candle]) -> float:
        """Return vol-targeted risk pct based on ATR normalization."""
        if len(candles_5m) < CFG.atr_period + 1:
            return BASE_RISK_PCT
        closes = np.array([c.close for c in candles_5m])
        highs = np.array([c.high for c in candles_5m])
        lows = np.array([c.low for c in candles_5m])
        atr = _atr(highs, lows, closes, CFG.atr_period)
        entry_price = closes[-1]
        atr_pct = (atr / entry_price) * 100 if entry_price > 0 else 0
        if atr_pct > 0:
            risk_pct = BASE_RISK_PCT * (TARGET_ATR_PCT / atr_pct)
            return max(MIN_RISK_PCT, min(MAX_RISK_PCT, risk_pct))
        return BASE_RISK_PCT

    def evaluate(self, symbol: str, candles_5m: list[Candle]) -> Optional[Signal]:
        """V5 entry: ALL deciles, 4/6 confirms, per-decile half-Kelly sizing."""
        st = self._get_state(symbol)

        if st.cooldown > 0:
            st.cooldown -= 1
            return None

        if CFG.no_reentry_after_stop and st.stopped_in_window:
            return None

        if not st.cascade_active:
            return None

        if CFG.min_cascade_strength > 0 and st.cascade_strength < CFG.min_cascade_strength:
            return None

        if st.cascade_strength < ENTRY_CASCADE_MIN or st.cascade_strength >= ENTRY_CASCADE_MAX:
            logger.info("CASCADE_BAND_REJECT", symbol=symbol,
                        strength=round(st.cascade_strength, 4),
                        band=f"{ENTRY_CASCADE_MIN}-{ENTRY_CASCADE_MAX}")
            return None

        if st.ret_5d > ENTRY_RET5D_MAX:
            logger.info("CAPITULATION_REJECT", symbol=symbol,
                        ret_5d=round(st.ret_5d, 2), max_ret5d=ENTRY_RET5D_MAX,
                        reason="not_beaten_down")
            return None

        if st.liq_direction_imb > ENTRY_LIQ_IMB_MAX:
            logger.info("CAPITULATION_REJECT", symbol=symbol,
                        liq_imb=round(st.liq_direction_imb, 4), max_imb=ENTRY_LIQ_IMB_MAX,
                        reason="no_long_liq_capitulation")
            return None

        # V6: time-based stop_cooldown fallback (288 bars = 24h)
        if st.stop_cooldown > 0:
            st.stop_cooldown -= 1
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

        # V6.4.1: Fixed imb_z — compute real taker buy imbalance z-score only.
        # When taker_buy_volume is unavailable/zero/invalid, imb_z = 0.0 (gate fails).
        # NO synthetic fallback — confirmation stack requires actual order-flow evidence.
        taker_buys = np.array([c.taker_buy_volume for c in candles_5m])
        _imb_fallback_triggered = False
        if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
            # Taker buy ratio per bar: taker_buy_volume / total_volume
            taker_ratios = taker_buys / np.maximum(volumes, 1e-10)
            imb_z = _z_score(taker_ratios, CFG.z_lookback)
        else:
            imb_z = 0.0
            _imb_fallback_triggered = True
        logger.info("imb_gate", symbol=symbol,
                    raw_taker_total=float(np.sum(taker_buys[-CFG.z_lookback:])),
                    imb_z=round(imb_z, 4),
                    fallback_triggered=_imb_fallback_triggered,
                    passed=bool(imb_z > CFG.imb_z_threshold))

        is_breakout = closes[-1] > range_high
        body = abs(closes[-1] - bar.open)
        total_range = highs[-1] - lows[-1]
        lower_wick = min(closes[-1], bar.open) - lows[-1]
        wick_rejection = (lower_wick / total_range >= 0.35) if total_range > 0 else False
        green_reversal = (
            closes[-1] > bar.open
            and len(closes) >= 2
            and closes[-1] > closes[-2]
        )
        impulse = abs(closes[-1] - bar.open) / bar.open * 100 if bar.open > 0 else 0.0

        confirmations = {
            'reversal': green_reversal or wick_rejection,
            'imb': imb_z > CFG.imb_z_threshold,
            'vol': vol_z > CFG.vol_z_threshold,
            'body': (body / total_range) >= CFG.body_strength_min if total_range > 0 else False,
            'impulse': impulse >= CFG.impulse_min_pct,
            'momentum': closes[-1] > ema,
        }

        _breakout_distance_abs = closes[-1] - range_high
        _breakout_distance_pct = (_breakout_distance_abs / range_high * 100) if range_high > 0 else 0.0

        if _breakout_distance_pct < -2.0:
            logger.info("BD_FILTER_REJECT", symbol=symbol,
                        breakout_distance_pct=round(_breakout_distance_pct, 4),
                        reason="BD_FILTER_<-2%")
            return None

        if is_breakout and _breakout_distance_pct > ENTRY_MAX_BREAKOUT_CHASE_PCT:
            logger.info("CHASE_FILTER_REJECT", symbol=symbol,
                        breakout_distance_pct=round(_breakout_distance_pct, 4),
                        max_chase_pct=ENTRY_MAX_BREAKOUT_CHASE_PCT)
            return None

        bar_time = bar.close_time if hasattr(bar, "close_time") and bar.close_time else datetime.now(timezone.utc)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        if bar_time.hour not in SNIPER_ALLOWED_HOURS:
            logger.info("SESSION_FILTER_REJECT", symbol=symbol,
                        hour_utc=bar_time.hour, window="14-22_UTC")
            return None

        if not (confirmations['reversal'] and confirmations['imb'] and confirmations['vol']
                and confirmations['momentum']):
            return None

        n_confirms = sum(1 for v in confirmations.values() if bool(v))
        if n_confirms < ENTRY_MIN_CONFIRMS:
            return None

        # Cast all confirmations to native bool for JSON serialization
        confirmations = {k: bool(v) for k, v in confirmations.items()}

        # Compute aggression score → decile → flat 4% sizing
        aggression = _compute_aggression(candles_5m)
        decile = _score_to_decile(aggression)

        # V6.4.1: Unified decile filter — D4/D10 never traded, D1/D2 require imb or vol
        if not _is_decile_tradable(decile, confirmations):
            return None

        # V5.1 Vol-Targeting Normalization
        # Reuse already-computed closes/highs/lows/atr from above
        entry_price = closes[-1]
        atr_pct = (atr / entry_price) * 100 if entry_price > 0 else 0
        if atr_pct >= SNIPER_MAX_ATR_PCT:
            logger.info("ATR_FILTER_REJECT", symbol=symbol,
                        atr_pct=round(atr_pct, 4), max_atr_pct=SNIPER_MAX_ATR_PCT)
            return None
        if atr_pct > 0:
            risk_pct = BASE_RISK_PCT * (TARGET_ATR_PCT / atr_pct)
            risk_pct = max(MIN_RISK_PCT, min(MAX_RISK_PCT, risk_pct))
        else:
            risk_pct = BASE_RISK_PCT

        st.aggression_score = aggression
        st.decile = decile

        stop_price = entry_price - atr * CFG.initial_stop_atr
        risk_per_unit = entry_price - stop_price
        if risk_per_unit <= 0:
            return None

        st.in_trade = True
        st.entry_price = entry_price
        st.risk_per_unit = risk_per_unit
        st.bars_held = 0
        st.best_price = entry_price
        st.vol_trail = 0.0
        st.struct_trail = 0.0
        st.consecutive_red = 0
        st.mae = 0.0
        st.mfe = 0.0
        st.confirmed = False

        logger.info("V6.5 signal", symbol=symbol, aggression=f"{aggression:.1f}",
                   decile=decile, risk_pct=f"{risk_pct:.1%}",
                   confirms=n_confirms, vol_z=f"{vol_z:.2f}",
                   cascade_strength=f"{st.cascade_strength:.2f}",
                   ret_5d=f"{st.ret_5d:.1f}", liq_imb=f"{st.liq_direction_imb:.2f}")

        return Signal(
            trade_uuid=str(uuid.uuid4()),
            symbol=symbol,
            side=Side.LONG,
            engine=EngineType.LIQ_CLUSTER,
            entry_price=entry_price,
            stop_price=stop_price,
            signal_data={
                "confirmations": confirmations,
                "aggression_score": aggression,
                "decile": decile,
                "cascade_strength": st.cascade_strength,
                "vol_z": vol_z,
                "atr": atr,
                "risk_pct": risk_pct,
                "range_high": range_high,
                "breakout_distance_pct": round(_breakout_distance_pct, 4),
                "imb_z": round(imb_z, 4),
                "imb_fallback_triggered": _imb_fallback_triggered,
                "ret_5d": st.ret_5d,
                "liq_direction_imb": st.liq_direction_imb,
                "entry_mode": "capitulation_reversal",
            },
            timestamp=bar.close_time if hasattr(bar, 'close_time') else datetime.now(timezone.utc),
        )

    def manage_position(self, symbol: str, candles_5m: list[Candle]) -> Optional[dict]:
        """V6 exits: per-decile parameters + cascade-deactivation tightening."""
        st = self._get_state(symbol)
        if not st.in_trade:
            return None
        if len(candles_5m) < 2:
            return None

        # V6: bars_held is managed by the runner, NOT incremented here
        # (was double-counted in V5, cutting winners at 2x speed)
        candle = candles_5m[-1]
        price = candle.close
        high = candle.high
        low = candle.low

        if high > st.best_price:
            st.best_price = high

        current_r = (price - st.entry_price) / st.risk_per_unit
        if current_r > st.mfe:
            st.mfe = current_r
        if current_r < st.mae:
            st.mae = current_r

        # Decile params retained for max_hold_bars (time stop)
        exits = DECILE_EXITS.get(st.decile, DECILE_EXITS[5])

        # Stop loss
        stop_price = st.entry_price - st.risk_per_unit
        if low <= stop_price:
            st.in_trade = False
            st.stopped_in_window = True
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops += 1
            if st.consecutive_stops >= CFG.max_consecutive_stops:
                st.stop_cooldown = 288  # V6: 24h cooldown (was 999999 = permanent)
            return {"action": "close", "reason": "stop_loss", "exit_price": stop_price,
                    "r": (stop_price - st.entry_price) / st.risk_per_unit,
                    "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                    "decile": st.decile, "aggression": st.aggression_score}

        # ── V6.4.3 two-stage management (exit_sim OOS-validated) ──
        # Once a trade shows real expansion (MFE >= confirm_r) it earns "runner" status.
        if st.mfe >= RUNNER_CONFIRM_R:
            st.confirmed = True

        if not st.confirmed:
            # Stage 1 — kill the dead: no +0.3R MFE inside the confirmation window → cut at
            # market. ~half of losers never show life; cutting here avoids the full -1R stop.
            if st.bars_held >= RUNNER_CONFIRM_BY:
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                st.consecutive_stops += 1
                if st.consecutive_stops >= CFG.max_consecutive_stops:
                    st.stop_cooldown = 288
                return {"action": "close", "reason": "unconfirmed_cut", "exit_price": price,
                        "r": current_r, "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                        "decile": st.decile, "aggression": st.aggression_score}
            # Inside confirmation window: only the hard stop (above) applies.
            if st.bars_held >= exits.max_hold_bars:
                st.in_trade = False
                st.cooldown = CFG.cooldown_bars
                st.consecutive_stops = 0
                return {"action": "close", "reason": "time_stop", "exit_price": price,
                        "r": current_r, "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                        "decile": st.decile, "aggression": st.aggression_score}
            return None  # hold (still proving itself)

        # Stage 2 — let it run: breakeven-floored wide trail. Once confirmed, the trade never
        # gives back below entry and trails RUNNER_GIVEBACK R under the running peak (st.mfe).
        stop_r = max(st.mfe - RUNNER_GIVEBACK, 0.0)
        runner_stop_price = st.entry_price + stop_r * st.risk_per_unit
        if low <= runner_stop_price:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops = 0
            return {"action": "close", "reason": "runner_trail", "exit_price": runner_stop_price,
                    "r": stop_r, "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                    "decile": st.decile, "aggression": st.aggression_score}

        # Time stop (safety; decile-specific max hold)
        if st.bars_held >= exits.max_hold_bars:
            st.in_trade = False
            st.cooldown = CFG.cooldown_bars
            st.consecutive_stops = 0
            return {"action": "close", "reason": "time_stop", "exit_price": price,
                    "r": current_r, "mae": st.mae, "mfe": st.mfe, "bars_held": st.bars_held,
                    "decile": st.decile, "aggression": st.aggression_score}

        return None  # hold (confirmed, trailing)
