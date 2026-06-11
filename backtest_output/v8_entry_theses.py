"""V8 entry theses — structurally different from cascade-breakout filters.

Grounded in Phase 5: entry-time features weak (d~0.4); bar-3+ path dominates (d>2.5).
These theses change WHEN/HOW we enter, not which confirms stack on bar 0.

Pre-registered kill criteria (same as v7):
  test_n >= 50, test_avg > 0, full_avg > -0.05
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
from backtest_output.v7_entry_theses import _bar_time, _shared_entry_gates

# ── Thesis C: bar-3 shadow proof (Phase 5 pathway-aligned) ──
SHADOW_PROOF_R = 0.25
SHADOW_WINDOW = 3
SHADOW_MAX_WAIT = 8

# ── Thesis D: liquidity spring (stop hunt + reclaim) ──
SPRING_LOOKBACK = 20
SPRING_SWEEP_PCT = 0.15
SPRING_MIN_CASCADE = 1.0
SPRING_MIN_VOL_Z = 0.0

# ── Thesis E: failed breakout reclaim ──
FAIL_RECLAIM_VOL_Z = 0.5
FAIL_RECLAIM_MAX_BARS = 12


@dataclass
class _Bar3Watch:
    active: bool = False
    bars: int = 0
    shadow_entry: float = 0.0
    shadow_stop: float = 0.0
    shadow_rpu: float = 0.0
    max_high: float = 0.0


@dataclass
class _FailReclaimWatch:
    broke: bool = False
    failed: bool = False
    bars_since_break: int = 0
    range_high: float = 0.0


_bar3: dict[str, _Bar3Watch] = {}
_fail: dict[str, _FailReclaimWatch] = {}


def _clear_watches(symbol: str) -> None:
    _bar3.pop(symbol, None)
    _fail.pop(symbol, None)


def _range_high(highs: np.ndarray) -> float:
    if len(highs) > CFG.range_lookback:
        return float(np.max(highs[-(CFG.range_lookback + 1):-1]))
    return float(np.max(highs[:-1])) if len(highs) > 1 else float(highs[0])


def _open_trade(
    engine: LiqClusterEngineV5,
    st,
    symbol: str,
    candles: list[Candle],
    entry_price: float,
    atr: float,
    thesis: str,
    extra: dict,
) -> Signal:
    aggression = _compute_aggression(candles)
    decile = _score_to_decile(aggression)
    stop_price = entry_price - atr * CFG.initial_stop_atr
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return None  # type: ignore[return-value]

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

    bar = candles[-1]
    sd = {
        "thesis": thesis,
        "decile": decile,
        "aggression_score": aggression,
        "cascade_strength": st.cascade_strength,
        "atr": atr,
        "ret_5d": st.ret_5d,
        "liq_direction_imb": st.liq_direction_imb,
        "n_confirmations": 0,
        **extra,
    }
    return Signal(
        trade_uuid=str(uuid.uuid4()),
        symbol=symbol,
        side=Side.LONG,
        engine=EngineType.LIQ_CLUSTER,
        entry_price=entry_price,
        stop_price=stop_price,
        signal_data=sd,
        timestamp=_bar_time(bar),
    )


def evaluate_bar3_proof(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis C — delayed entry after 3-bar shadow proof.

    Mechanism (Phase 5): winners show +0.25R+ within 3 bars; losers don't.
    Arm virtual long at cascade bar 0; enter real trade at bar 3 only if shadow
    MFE >= 0.25R. Skips instant breakout entries entirely.
    """
    st = engine._get_state(symbol)
    if not st.cascade_active:
        _clear_watches(symbol)
        return None

    packed = _shared_entry_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if st.cascade_strength < SPRING_MIN_CASCADE:
        return None

    w = _bar3.setdefault(symbol, _Bar3Watch())
    hi, lo, cl = float(highs[-1]), float(lows[-1]), float(closes[-1])

    if not w.active:
        w.active = True
        w.bars = 1
        w.shadow_entry = cl
        w.shadow_stop = cl - atr * CFG.initial_stop_atr
        w.shadow_rpu = w.shadow_entry - w.shadow_stop
        w.max_high = hi
        return None

    w.bars += 1
    w.max_high = max(w.max_high, hi)

    if w.shadow_rpu <= 0:
        _clear_watches(symbol)
        return None

    if w.bars > SHADOW_MAX_WAIT:
        _clear_watches(symbol)
        return None

    if w.bars < SHADOW_WINDOW:
        return None

    shadow_mfe = (w.max_high - w.shadow_entry) / w.shadow_rpu
    vol_z = _z_score(volumes, CFG.z_lookback)
    _clear_watches(symbol)

    if shadow_mfe < SHADOW_PROOF_R or vol_z <= 0:
        return None

    rh = _range_high(highs)
    bd_pct = (cl - rh) / rh * 100 if rh > 0 else 0.0
    return _open_trade(
        engine, st, symbol, candles, cl, atr, "bar3_proof",
        {"vol_z": vol_z, "shadow_mfe": round(shadow_mfe, 4),
         "breakout_distance_pct": round(bd_pct, 4)},
    )


def evaluate_liq_spring(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis D — post-liquidation spring (stop hunt + reclaim).

    Mechanism: during cascade, price sweeps below structural low then closes
    back above it — classic liquidity grab after forced selling, not breakout chase.
    """
    packed = _shared_entry_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    if st.cascade_strength < SPRING_MIN_CASCADE:
        return None

    if len(lows) < SPRING_LOOKBACK + 2:
        return None

    struct_low = float(np.min(lows[-(SPRING_LOOKBACK + 1):-1]))
    sweep_level = struct_low * (1.0 - SPRING_SWEEP_PCT / 100.0)
    hi, lo, cl = float(highs[-1]), float(lows[-1]), float(closes[-1])

    if lo > sweep_level or cl <= struct_low:
        return None

    vol_z = _z_score(volumes, CFG.z_lookback)
    if vol_z <= SPRING_MIN_VOL_Z:
        return None

    rh = _range_high(highs)
    bd_pct = (cl - rh) / rh * 100 if rh > 0 else 0.0
    return _open_trade(
        engine, st, symbol, candles, cl, atr, "liq_spring",
        {"vol_z": vol_z, "struct_low": struct_low,
         "breakout_distance_pct": round(bd_pct, 4)},
    )


def evaluate_fail_reclaim(
    engine: LiqClusterEngineV5,
    symbol: str,
    candles: list[Candle],
) -> Optional[Signal]:
    """
    Thesis E — failed breakout then reclaim.

    Mechanism: first range_high break often false during cascades; enter on
    second reclaim with volume after price failed back below structure.
    """
    st = engine._get_state(symbol)
    if not st.cascade_active:
        _clear_watches(symbol)
        return None

    packed = _shared_entry_gates(engine, symbol, candles)
    if packed is None:
        return None
    st, closes, highs, lows, volumes, bar, atr = packed

    rh = _range_high(highs)
    cl = float(closes[-1])
    vol_z = _z_score(volumes, CFG.z_lookback)
    w = _fail.setdefault(symbol, _FailReclaimWatch())

    if w.range_high != rh:
        w.range_high = rh
        w.broke = False
        w.failed = False
        w.bars_since_break = 0

    if cl > rh and not w.broke:
        w.broke = True
        w.failed = False
        w.bars_since_break = 0
        return None

    if w.broke and not w.failed and cl < rh:
        w.failed = True
        w.bars_since_break = 0
        return None

    if w.failed:
        w.bars_since_break += 1
        if w.bars_since_break > FAIL_RECLAIM_MAX_BARS:
            _clear_watches(symbol)
            return None
        if cl > rh and vol_z >= FAIL_RECLAIM_VOL_Z:
            bd_pct = (cl - rh) / rh * 100 if rh > 0 else 0.0
            _clear_watches(symbol)
            return _open_trade(
                engine, st, symbol, candles, cl, atr, "fail_reclaim",
                {"vol_z": vol_z, "breakout_distance_pct": round(bd_pct, 4)},
            )

    return None


THESES = {
    "bar3_proof": evaluate_bar3_proof,
    "liq_spring": evaluate_liq_spring,
    "fail_reclaim": evaluate_fail_reclaim,
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


def reset_thesis_state() -> None:
    """Clear per-symbol watch state between backtest runs."""
    _bar3.clear()
    _fail.clear()
