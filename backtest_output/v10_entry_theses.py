"""V10 entry theses — complete break from v645 cascade-breakout + confirm stack.

Pre-registered hypotheses (Jan–May 2026, 57 symbols, CAPTURE_ALL, v6.4.3 exits):

  dip_absorption — Post long-liq flush dip buy inside cascade regime (contrarian).
  squeeze_flow   — Short-liq momentum continuation (no breakout, no cascade gate).

Kill: test_n>=50, test_avg>0, full_avg>-0.05
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
from backtest_output.v7_entry_theses import _bar_time

# ── dip_absorption ──
DIP_IMB_MIN = 0.12
DIP_RET5D_MAX = -2.5
DIP_CASC_MIN = 0.8
DIP_CASC_MAX = 2.5
DIP_RANGE_MAX = 0.35
DIP_VOL_Z = 0.3
DIP_IMB_Z = 0.2
DIP_BODY = 0.45

# ── squeeze_flow ──
SQ_IMB_MAX = -0.12
SQ_RET5D_MIN = 0.5
SQ_VOL_Z = 0.8
SQ_GREEN_BARS = 2


def _base_gates(engine: LiqClusterEngineV5, symbol: str, candles: list[Candle]) -> tuple | None:
    st = engine._get_state(symbol)
    if st.cooldown > 0:
        st.cooldown -= 1
        return None
    if CFG.no_reentry_after_stop and st.stopped_in_window:
        return None
    if st.stop_cooldown > 0:
        st.stop_cooldown -= 1
        return None
    n_needed = max(CFG.range_lookback, CFG.z_lookback, CFG.ema_period * 3, 30)
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


def _taker_imb_z(candles: list[Candle], volumes: np.ndarray) -> float | None:
    taker_buys = np.array([c.taker_buy_volume for c in candles])
    if len(taker_buys) >= CFG.z_lookback and np.any(taker_buys[-CFG.z_lookback:] > 0):
        return _z_score(taker_buys / np.maximum(volumes, 1e-10), CFG.z_lookback)
    return None


def _open_signal(
    engine: LiqClusterEngineV5,
    st,
    symbol: str,
    candles: list[Candle],
    entry: float,
    atr: float,
    thesis: str,
    extra: dict,
) -> Signal:
    stop = entry - atr * CFG.initial_stop_atr
    if entry - stop <= 0:
        return None  # type: ignore[return-value]
    aggression = _compute_aggression(candles)
    decile = _score_to_decile(aggression)
    st.aggression_score = aggression
    st.decile = decile
    st.in_trade = True
    st.entry_price = entry
    st.risk_per_unit = entry - stop
    st.bars_held = 0
    st.best_price = entry
    st.vol_trail = 0.0
    st.struct_trail = 0.0
    st.consecutive_red = 0
    st.mae = 0.0
    st.mfe = 0.0
    st.confirmed = False
    bar = candles[-1]
    return Signal(
        trade_uuid=str(uuid.uuid4()),
        symbol=symbol,
        side=Side.LONG,
        engine=EngineType.LIQ_CLUSTER,
        entry_price=entry,
        stop_price=stop,
        signal_data={
            "thesis": thesis,
            "decile": decile,
            "aggression_score": aggression,
            "atr": atr,
            "n_confirmations": 0,
            **extra,
        },
        timestamp=_bar_time(bar),
    )


def evaluate_dip_absorption(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis 1 — post long-liq dip absorption (NO range breakout, NO confirm stack).

    Regime: cascade active but NOT extreme (Phase 5: high cascade_strength → more dead).
    Setup: long-liq day + drawdown + price in lower third of 20-bar range.
    Trigger: green reversal bar with taker buy emerging.
    """
    packed = _base_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if not st.cascade_active:
        return None
    cs = st.cascade_strength
    if cs < DIP_CASC_MIN or cs > DIP_CASC_MAX:
        return None
    if st.liq_direction_imb < DIP_IMB_MIN:
        return None
    if st.ret_5d > DIP_RET5D_MAX:
        return None

    hi20 = float(np.max(highs[-21:-1]))
    lo20 = float(np.min(lows[-21:-1]))
    rng = hi20 - lo20
    if rng <= 0:
        return None
    cl = float(closes[-1])
    range_pos = (cl - lo20) / rng
    if range_pos > DIP_RANGE_MAX:
        return None

    rh = float(np.max(highs[-(CFG.range_lookback + 1):-1])) if len(highs) > CFG.range_lookback else float(np.max(highs[:-1]))
    if cl >= rh:
        return None

    if cl <= bar.open:
        return None
    total_range = float(highs[-1] - lows[-1])
    body = abs(cl - bar.open)
    if total_range <= 0 or body / total_range < DIP_BODY:
        return None

    vol_z = _z_score(volumes, CFG.z_lookback)
    imb_z = _taker_imb_z(candles, volumes)
    if vol_z < DIP_VOL_Z or imb_z is None or imb_z < DIP_IMB_Z:
        return None

    return _open_signal(
        engine, st, symbol, candles, cl, atr, "dip_absorption",
        {
            "vol_z": vol_z,
            "imb_z": round(imb_z, 4),
            "cascade_strength": cs,
            "liq_direction_imb": st.liq_direction_imb,
            "ret_5d": st.ret_5d,
            "range_position": round(range_pos, 4),
            "breakout_distance_pct": round((cl - rh) / rh * 100, 4) if rh > 0 else 0,
        },
    )


def evaluate_squeeze_flow(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis 2 — short-liq squeeze momentum (NO cascade gate, NO breakout).

    Regime: daily short liquidations dominate (shorts forced out, bid pressure).
    Setup: 5d trend already up; enter continuation after 2+ green 5m bars + vol.
    """
    packed = _base_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if st.liq_direction_imb > SQ_IMB_MAX:
        return None
    if st.ret_5d < SQ_RET5D_MIN:
        return None

    ema = _ema(closes, CFG.ema_period)
    cl = float(closes[-1])
    if cl <= ema:
        return None

    vol_z = _z_score(volumes, CFG.z_lookback)
    if vol_z < SQ_VOL_Z:
        return None

    greens = sum(1 for c in candles[-3:] if c.close > c.open)
    if greens < SQ_GREEN_BARS:
        return None
    if cl <= float(closes[-4]):
        return None

    return _open_signal(
        engine, st, symbol, candles, cl, atr, "squeeze_flow",
        {
            "vol_z": vol_z,
            "liq_direction_imb": st.liq_direction_imb,
            "ret_5d": st.ret_5d,
            "cascade_active": st.cascade_active,
            "cascade_strength": st.cascade_strength,
        },
    )


THESES = {
    "dip_absorption": evaluate_dip_absorption,
    "squeeze_flow": evaluate_squeeze_flow,
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


def all_thesis_names() -> list[str]:
    return list(THESES.keys())
