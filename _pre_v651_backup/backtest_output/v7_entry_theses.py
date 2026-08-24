"""Isolated v7 entry theses — alternatives to v6.4.5 cascade-breakout.

Used only from path backtest via ENTRY_THESIS env (not wired into live engine).
Each thesis returns the same Signal shape as LiqClusterEngineV5.evaluate().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from core.models import Candle, EngineType, Side, Signal
from engines.liq_cluster_engine_v5 import (
    CFG,
    LiqClusterEngineV5,
    _atr,
    _compute_aggression,
    _ema,
    _score_to_decile,
    _z_score,
)

# Pre-registered exhaustion-reversal gates (v7.0 thesis A)
EXH_IMB_MIN = 0.15           # long-liq dominated day (long_liq >> short_liq)
EXH_RET5D_MAX = -3.0         # price already down over 5d
EXH_MIN_CASCADE = 1.0
EXH_MIN_VOL_Z = 0.5
EXH_MIN_IMB_Z = 0.25         # taker buy emerging on entry bar
EXH_BODY_MIN = 0.55


def _bar_time(bar: Candle) -> datetime:
    t = bar.close_time if hasattr(bar, "close_time") and bar.close_time else datetime.now(timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _shared_entry_gates(engine: LiqClusterEngineV5, symbol: str, candles: list[Candle]) -> tuple | None:
    """Cooldown / cascade / lookback gates shared by all theses."""
    st = engine._get_state(symbol)
    if st.cooldown > 0:
        st.cooldown -= 1
        return None
    if CFG.no_reentry_after_stop and st.stopped_in_window:
        return None
    if not st.cascade_active:
        return None
    if CFG.min_cascade_strength > 0 and st.cascade_strength < CFG.min_cascade_strength:
        return None
    if st.stop_cooldown > 0:
        st.stop_cooldown -= 1
        return None

    n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3)
    if len(candles) < n_needed:
        return None

    closes = np.array([c.close for c in candles])
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    volumes = np.array([c.volume for c in candles])
    bar = candles[-1]
    atr = _atr(highs, lows, closes, CFG.atr_period)
    if atr <= 0:
        return None
    return st, closes, highs, lows, volumes, bar, atr


def evaluate_exhaustion_reversal(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis A — liq-exhaustion reversal (NOT range breakout).

    Mechanism: after long-liq cascade + drawdown, forced selling is exhausted.
    Enter first strong green impulse with emerging taker buy — before/without chasing
    range_high breakout.
    """
    packed = _shared_entry_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if st.cascade_strength < EXH_MIN_CASCADE:
        return None
    if st.liq_direction_imb < EXH_IMB_MIN:
        return None
    if st.ret_5d > EXH_RET5D_MAX:
        return None

    vol_z = _z_score(volumes, CFG.z_lookback)
    taker_buys = np.array([c.taker_buy_volume for c in candles])
    if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
        imb_z = _z_score(taker_buys / np.maximum(volumes, 1e-10), CFG.z_lookback)
    else:
        return None

    if closes[-1] <= bar.open:
        return None
    total_range = highs[-1] - lows[-1]
    body = abs(closes[-1] - bar.open)
    if total_range <= 0 or (body / total_range) < EXH_BODY_MIN:
        return None
    if vol_z < EXH_MIN_VOL_Z or imb_z < EXH_MIN_IMB_Z:
        return None

    ema = _ema(closes, CFG.ema_period)
    if len(highs) > CFG.range_lookback:
        range_high = float(np.max(highs[-(CFG.range_lookback + 1):-1]))
    else:
        range_high = float(np.max(highs[:-1])) if len(highs) > 1 else highs[0]
    bd_pct = (closes[-1] - range_high) / range_high * 100 if range_high > 0 else 0.0

    # Deliberately allow sub-breakout entries (thesis differentiator)
    if bd_pct > 1.0:
        return None

    aggression = _compute_aggression(candles)
    decile = _score_to_decile(aggression)
    entry_price = closes[-1]
    stop_price = entry_price - atr * CFG.initial_stop_atr
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return None

    st.aggression_score = aggression
    st.decile = decile
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

    confirmations = {
        "breakout": bool(closes[-1] > range_high),
        "imb": bool(imb_z > CFG.imb_z_threshold),
        "vol": bool(vol_z > CFG.vol_z_threshold),
        "body": bool(body / total_range >= EXH_BODY_MIN),
        "impulse": bool(abs(closes[-1] - bar.open) / bar.open * 100 >= CFG.impulse_min_pct),
        "momentum": bool(closes[-1] > ema),
    }

    return Signal(
        trade_uuid=str(uuid.uuid4()),
        symbol=symbol,
        side=Side.LONG,
        engine=EngineType.LIQ_CLUSTER,
        entry_price=entry_price,
        stop_price=stop_price,
        signal_data={
            "thesis": "exhaustion_reversal",
            "confirmations": confirmations,
            "aggression_score": aggression,
            "decile": decile,
            "cascade_strength": st.cascade_strength,
            "vol_z": vol_z,
            "atr": atr,
            "range_high": range_high,
            "breakout_distance_pct": round(bd_pct, 4),
            "imb_z": round(imb_z, 4),
            "ret_5d": st.ret_5d,
            "liq_direction_imb": st.liq_direction_imb,
            "n_confirmations": sum(confirmations.values()),
        },
        timestamp=_bar_time(bar),
    )


def evaluate_squeeze_release(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis B — vol compression → expansion during active cascade (no range breakout).

    Mechanism: liquidation pauses compress ATR; expansion bar with volume signals release.
    """
    packed = _shared_entry_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if st.cascade_strength < 1.0:
        return None

    ranges = highs - lows
    if len(ranges) < 25:
        return None
    prior = ranges[-21:-1]
    cur = ranges[-1]
    atr_pctile = float(np.mean(cur > prior))
    if atr_pctile > 0.35:
        return None

    vol_z = _z_score(volumes, CFG.z_lookback)
    avg_range = float(np.mean(prior)) + 1e-10
    if cur / avg_range < 1.4 or vol_z < 1.0:
        return None
    if closes[-1] <= bar.open:
        return None
    clv = (closes[-1] - lows[-1]) / max(cur, 1e-10)
    if clv < 0.65:
        return None

    aggression = _compute_aggression(candles)
    decile = _score_to_decile(aggression)
    entry_price = closes[-1]
    stop_price = entry_price - atr * CFG.initial_stop_atr
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return None

    st.aggression_score = aggression
    st.decile = decile
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

    return Signal(
        trade_uuid=str(uuid.uuid4()),
        symbol=symbol,
        side=Side.LONG,
        engine=EngineType.LIQ_CLUSTER,
        entry_price=entry_price,
        stop_price=stop_price,
        signal_data={
            "thesis": "squeeze_release",
            "decile": decile,
            "cascade_strength": st.cascade_strength,
            "vol_z": vol_z,
            "atr": atr,
            "n_confirmations": 0,
        },
        timestamp=_bar_time(bar),
    )


THESES = {
    "exhaustion": evaluate_exhaustion_reversal,
    "squeeze": evaluate_squeeze_release,
}


def evaluate_thesis(
    name: str,
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    fn = THESES.get(name)
    if fn is None:
        raise ValueError(f"unknown ENTRY_THESIS: {name!r} (choose from {list(THESES)})")
    return fn(engine, symbol, candles)
